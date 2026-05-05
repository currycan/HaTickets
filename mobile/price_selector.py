"""PriceSelector — ticket price and SKU selection on the Damai app.

Uses delegate pattern: delegates complex operations to DamaiBot while
providing a clean interface for FastPipeline and other consumers.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Sequence, Tuple

from selenium.webdriver.common.by import By

from mobile.ui_primitives import ANDROID_UIAUTOMATOR
from selenium.webdriver.support import expected_conditions as EC

from mobile.logger import get_logger

try:
    from mobile.item_resolver import normalize_text
except ImportError:
    from item_resolver import normalize_text

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)

_PRICE_UNAVAILABLE_TAGS = {
    "无票",
    "缺货",
    "缺货登记",
    "售罄",
    "已售罄",
    "不可选",
    "暂不可售",
}


# ---------------------------------------------------------------------------
# Diagnostic exceptions and price-card data class (P1 #31)
# ---------------------------------------------------------------------------


class PriceSelectorError(RuntimeError):
    """价格选择失败：卡片为空 / index 越界 / 加载超时等。

    Inherits from RuntimeError so existing broad ``except Exception`` /
    ``except RuntimeError`` call sites keep working unchanged.
    """


class SoldOutError(RuntimeError):
    """价格面板检测到全部票档售罄；与越界明确区分以便上层决定是否重试。"""


@dataclass(frozen=True)
class PriceCard:
    """A single clickable price card surfaced from the Damai SKU/detail page."""

    index: int
    price_text: str
    coords: Optional[Tuple[int, int]] = None
    tag: str = ""
    raw_texts: Tuple[str, ...] = field(default_factory=tuple)


def _safe_call_dump_writer(
    dump_writer: Optional[Callable[[Path], None]],
    dump_on_fail: Optional[Path],
) -> Optional[Path]:
    """Persist a UI dump if a writer + path were provided. Best-effort, never raises."""
    if dump_writer is None or dump_on_fail is None:
        return None
    try:
        dump_writer(dump_on_fail)
        return dump_on_fail
    except Exception as exc:  # pragma: no cover - logged for diagnostics
        logger.warning("price dump 写入失败 (%s): %s", dump_on_fail, exc)
        return None


def select_price_by_index(
    cards: Sequence[PriceCard],
    index: int,
    *,
    dump_on_fail: Optional[Path] = None,
    dump_writer: Optional[Callable[[Path], None]] = None,
) -> PriceCard:
    """Return ``cards[index]`` with actionable diagnostics on failure.

    On any failure the optional ``dump_writer`` is invoked with ``dump_on_fail``
    so the caller (e.g. DamaiBot) can persist a hierarchy XML before the
    exception bubbles up. The dump path, if any, is appended to the message.

    Raises:
        PriceSelectorError: when ``cards`` is empty or ``index`` is out of range.
    """

    if not cards:
        saved = _safe_call_dump_writer(dump_writer, dump_on_fail)
        suffix = f"\nUI dump 已保存：{saved}" if saved else ""
        raise PriceSelectorError(
            "未发现可点击价格卡片。可能原因：\n"
            "  1. 演出尚未开售（CTA 显示「待开售」）\n"
            "  2. 价格区已售罄\n"
            "  3. 大麦 App UI 变更，需更新选择器" + suffix
        )

    if index < 0 or index >= len(cards):
        saved = _safe_call_dump_writer(dump_writer, dump_on_fail)
        suffix = f"\nUI dump 已保存：{saved}" if saved else ""
        available = "\n".join(
            f"  [{c.index}] {c.price_text or '(空)'}" + (f" [{c.tag}]" if c.tag else "")
            for c in cards
        )
        raise PriceSelectorError(
            f"price_index={index} 越界（可用 0..{len(cards) - 1}）。\n"
            f"可用价档：\n{available}\n"
            "请修改 mobile/config.jsonc 中 price_index" + suffix
        )

    return cards[index]


_MAGICK_BIN = shutil.which("magick")
_TESSERACT_BIN = shutil.which("tesseract")
_OCR_CHAR_TRANSLATIONS = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
    }
)


class PriceSelector:
    """Handles price/SKU selection on SKU and detail pages."""

    def __init__(self, device, config, probe: PageProbe, bot=None) -> None:
        self._d = device
        self._config = config
        self._probe = probe
        self._bot = bot

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference for delegation."""
        self._bot = bot

    # ------------------------------------------------------------------
    # Public convenience methods (used by FastPipeline etc.)
    # ------------------------------------------------------------------

    def select_by_index(self, xml_root=None) -> bool:
        """Select price option by config.price_index. Returns True on success.

        Before attempting to click, the price panel state is sampled via
        ``page_probe.detect_price_panel_state`` (P1 #31). On ``loading`` the
        method waits up to 2s for the panel to appear; on ``sold_out`` it
        raises ``SoldOutError``; on ``ready`` / ``unknown`` it proceeds with
        the existing coordinate-based click path.
        """
        self._await_price_panel_or_raise()

        coords = self.get_price_coords_by_index(xml_root=xml_root)
        if coords is None:
            logger.warning(f"无法定位 price_index={self._config.price_index} 的坐标")
            return False
        self._click_coordinates(*coords)
        logger.info(f"通过配置索引选择票价: price_index={self._config.price_index}")
        return True

    def _await_price_panel_or_raise(self, *, max_wait_s: float = 2.0) -> str:
        """Sample the price panel state, blocking up to ``max_wait_s`` on loading.

        Returns the final state. Raises ``SoldOutError`` when the panel
        reports sold-out, and ``PriceSelectorError("加载超时")`` if it stays
        in ``loading`` past the timeout.
        """
        # Lazy import to avoid hard dependency on page_probe in unit tests
        # that exercise PriceSelector in isolation (the import below is
        # cheap; we only delay it to keep top-of-file import order minimal).
        try:
            from mobile.page_probe import detect_price_panel_state
        except ImportError:  # pragma: no cover - fallback for non-package runs
            from page_probe import detect_price_panel_state  # type: ignore

        state = detect_price_panel_state(self._d)
        if state == "loading":
            import time as _time

            deadline = _time.monotonic() + max_wait_s
            while _time.monotonic() < deadline:
                _time.sleep(0.1)
                state = detect_price_panel_state(self._d)
                if state != "loading":
                    break
            if state == "loading":
                raise PriceSelectorError(
                    f"价格面板加载超时（>{max_wait_s:.1f}s），疑似设备/网络异常"
                )

        if state == "sold_out":
            raise SoldOutError("价格面板检测到全部票档售罄")

        if state == "unknown":
            logger.warning(
                "price panel state=unknown — 选择器/UI 可能已变更，继续尝试索引点击"
            )

        return state

    def get_price_coords_by_index(self, xml_root=None) -> Optional[Tuple[int, int]]:
        """Get coordinates for price option at config.price_index."""
        if self._bot is not None:
            try:
                return self._bot._get_price_option_coordinates_by_config_index(
                    xml_root=xml_root
                )
            except Exception as exc:
                logger.warning(f"获取票价坐标失败: {exc}")
                return None
        return None

    def get_buy_button_coords(self, xml_root=None) -> Optional[Tuple[int, int]]:
        """Get coordinates for the buy/confirm button."""
        if self._bot is not None:
            try:
                return self._bot._get_buy_button_coordinates(xml_root=xml_root)
            except Exception as exc:
                logger.warning(f"获取购买按钮坐标失败: {exc}")
                return None
        return None

    def _click_coordinates(self, x, y) -> None:
        try:
            self._d.click(int(x), int(y))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Migrated method bodies from DamaiBot
    # ------------------------------------------------------------------

    def _get_buy_button_coordinates(self, xml_root=None):
        """Capture the current buy/confirm button coordinates."""
        bot = self._bot
        if bot._using_u2():
            if xml_root is None:
                xml_root = bot._dump_hierarchy_xml()
            if xml_root is not None:
                for node in xml_root.iter("node"):
                    rid = node.get("resource-id", "")
                    if rid in ("btn_buy_view", "cn.damai:id/btn_buy_view"):
                        bounds = bot._parse_bounds(node.get("bounds", ""))
                        if bounds:
                            left, top, right, bottom = bounds
                            return ((left + right) // 2, (top + bottom) // 2)
                return None

        selectors = [
            (By.ID, "cn.damai:id/btn_buy_view"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")'),
        ]
        for by, value in selectors:
            try:
                elements = bot._find_all(by, value)
            except Exception:
                continue
            if not elements:
                continue
            rect = bot._element_rect(elements[0])
            return (
                rect["x"] + rect["width"] // 2,
                rect["y"] + rect["height"] // 2,
            )
        return None

    def _get_price_option_coordinates_by_config_index(self, xml_root=None):
        """Capture the configured price card center so rush mode can tap by coordinate."""
        bot = self._bot
        if bot._using_u2():
            result = self._get_price_coords_from_xml(xml_root)
            if result is not None:
                return result
            # Retry once after short wait (cards may still be loading)
            if xml_root is not None:
                import time

                time.sleep(0.3)
                result = self._get_price_coords_from_xml(None)  # fresh XML dump
                if result is not None:
                    return result
            return None

        try:
            price_container = bot._find(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
        except Exception:
            return None

        try:
            cards = bot._container_find_elements(
                price_container, By.CLASS_NAME, "android.widget.FrameLayout"
            )
        except Exception:
            return None
        clickable_cards = [card for card in cards if bot._is_clickable(card)]
        if not (0 <= self._config.price_index < len(clickable_cards)):
            return None

        rect = bot._element_rect(clickable_cards[self._config.price_index])
        return (
            rect["x"] + rect["width"] // 2,
            rect["y"] + rect["height"] // 2,
        )

    def _get_price_coords_from_xml(self, xml_root=None):
        """Extract price card coordinates from XML hierarchy.

        Searches multiple container IDs to handle both detail-page and SKU-page layouts.
        Returns (x, y) tuple or None.
        """
        bot = self._bot
        if xml_root is None:
            xml_root = bot._dump_hierarchy_xml()
        if xml_root is None:
            return None

        container_ids = (
            "cn.damai:id/project_detail_perform_price_flowlayout",
            "cn.damai:id/layout_price",
        )
        for container_id in container_ids:
            for node in xml_root.iter("node"):
                if node.get("resource-id") == container_id:
                    cards = [
                        child
                        for child in node
                        if child.get("class") == "android.widget.FrameLayout"
                        and child.get("clickable") == "true"
                    ]
                    if not cards:
                        continue
                    if not (0 <= self._config.price_index < len(cards)):
                        logger.debug(
                            f"price_index={self._config.price_index} 超出 {container_id} "
                            f"中的 {len(cards)} 个可点击卡片"
                        )
                        continue
                    bounds = bot._parse_bounds(
                        cards[self._config.price_index].get("bounds", "")
                    )
                    if bounds:
                        left, top, right, bottom = bounds
                        return ((left + right) // 2, (top + bottom) // 2)
        return None

    def _extract_price_digits(self, text):
        """Extract the numeric portion of a ticket price label."""
        normalized_text = text if isinstance(text, str) else ""
        match = re.search(r"([1-9]\d{1,4})", normalized_text)
        if match:
            return int(match.group(1))
        return None

    def _price_text_matches_target(self, text):
        """Check whether a visible price label matches the configured price."""
        normalized_target = normalize_text(self._config.price)
        normalized_text = normalize_text(text)
        if normalized_target and normalized_text:
            if (
                normalized_target in normalized_text
                or normalized_text in normalized_target
            ):
                return True

        target_digits = self._extract_price_digits(self._config.price)
        text_digits = self._extract_price_digits(text)
        return target_digits is not None and target_digits == text_digits

    def _is_price_option_available(self, option):
        """Return whether a visible price option is actually selectable."""
        tag = (option.get("tag") or "").strip()
        return tag not in _PRICE_UNAVAILABLE_TAGS

    def _click_visible_price_option(self, card_index):
        """Click a visible price card by its clickable-card index."""
        bot = self._bot
        try:
            price_container = bot._find(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
            cards = bot._container_find_elements(
                price_container, By.CLASS_NAME, "android.widget.FrameLayout"
            )
        except Exception:
            return False

        clickable_cards = [card for card in cards if bot._is_clickable(card)]
        if 0 <= card_index < len(clickable_cards):
            bot._click_element_center(clickable_cards[card_index], duration=30)
            return True
        return False

    def _click_price_option_by_config_index(self, burst=False, coords=None):
        """Click the configured price card index directly without reading ticket texts.

        Prefers element-based click (works with Damai's custom Views that
        ignore raw touch coordinates) and falls back to coordinate click.
        """
        bot = self._bot
        # Primary: element click — Damai price cards may not respond to coordinate taps
        if self._click_price_card_element(self._config.price_index):
            logger.info(
                f"通过配置索引直接选择票价: price_index={self._config.price_index}"
            )
            return True
        # Fallback: coordinate click
        target_coords = coords or bot._get_price_option_coordinates_by_config_index()
        if not target_coords:
            return False
        if burst:
            bot._burst_click_coordinates(
                *target_coords, count=2, interval_ms=25, duration=25
            )
        else:
            bot._click_coordinates(*target_coords, duration=30)
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        return True

    def _click_price_card_element(self, index):
        """Click price card by element (Accessibility) rather than coordinate.

        Some Damai App custom Views only respond to Accessibility clicks,
        not raw touch coordinates.
        """
        try:
            container = self._d(
                resourceId="cn.damai:id/project_detail_perform_price_flowlayout"
            )
            if not container.exists:
                return False
            children = container.child(
                className="android.widget.FrameLayout", clickable=True
            )
            if children.count > index:
                children[index].click()
                return True
        except Exception:
            pass
        return False

    def _build_compound_price_text(self, container):
        """Build a human-readable price string from split price fields."""
        bot = self._bot
        prefix_ids = (
            "cn.damai:id/bricks_dm_common_price_prefix",
            "cn.damai:id/project_price_char",
        )
        value_ids = (
            "cn.damai:id/bricks_dm_common_price_des",
            "cn.damai:id/project_price_pre",
            "cn.damai:id/project_price_suffix",
        )
        suffix_ids = ("cn.damai:id/bricks_dm_common_price_suffix",)

        prefix = ""
        value_parts = []
        suffix = ""

        for resource_id in prefix_ids:
            prefix = prefix or bot._safe_element_text(container, By.ID, resource_id)
        for resource_id in value_ids:
            value_parts.extend(bot._safe_element_texts(container, By.ID, resource_id))
        for resource_id in suffix_ids:
            suffix = suffix or bot._safe_element_text(container, By.ID, resource_id)

        value = "".join(value_parts).strip()
        compound = f"{prefix}{value}{suffix}".strip()
        if compound == "¥":
            compound = ""
        if compound and prefix == "¥" and suffix == "起":
            return compound
        if value and value.replace(".", "", 1).isdigit() and not suffix:
            return f"{value}元"
        if compound and compound.startswith("¥"):
            return compound.replace("¥", "¥", 1)
        return compound

    def _price_option_text_from_descendants(self, texts):
        """Collapse descendant texts into a price label."""
        if not texts:
            return ""

        filtered = []
        ignored = {"可预约", "预售", "无票", "已预约", "缺货", "惠", "荐", "热", "售罄"}
        for text in texts:
            value = text.strip()
            if not value or value in ignored:
                continue
            filtered.append(value)

        if not filtered:
            return ""

        merged = "".join(filtered)
        if merged.isdigit():
            return f"{merged}元"
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]+[0-9]{2,5}", merged):
            return f"{merged}元"
        if re.fullmatch(r"[0-9]{2,5}[A-Za-z\u4e00-\u9fff]+", merged):
            return merged
        return merged

    def _select_price_option_fast(self, cached_coords=None):
        """Use config-driven, low-latency ticket selection before OCR-heavy fallbacks."""
        bot = self._bot
        if self._config.rush_mode:
            _burst = self._config.if_commit_order
            if bot._click_price_option_by_config_index(
                burst=_burst, coords=cached_coords
            ):
                return True

        visible_options = bot.get_visible_price_options(allow_ocr=False)

        if visible_options:
            indexed_option = next(
                (
                    option
                    for option in visible_options
                    if option["index"] == self._config.price_index
                ),
                None,
            )
            if indexed_option:
                if not bot._is_price_option_available(indexed_option):
                    logger.warning(
                        f"配置索引对应票档当前不可选: {indexed_option.get('text') or '(未识别)'} "
                        f"[{indexed_option.get('tag') or '不可售'}]"
                    )
                    return False
                if not indexed_option.get("text") or bot._price_text_matches_target(
                    indexed_option.get("text") or ""
                ):
                    if bot._click_visible_price_option(indexed_option["index"]):
                        logger.info(
                            f"通过配置索引快速选择票价: {indexed_option.get('text') or self._config.price} "
                            f"(price_index={self._config.price_index})"
                        )
                        return True
            elif bot._click_price_option_by_config_index():
                return True

            matched_options = [
                option
                for option in visible_options
                if bot._price_text_matches_target(option.get("text") or "")
            ]
            for option in matched_options:
                if not bot._is_price_option_available(option):
                    logger.warning(
                        f"目标票档当前不可选: {option.get('text') or '(未识别)'} [{option.get('tag') or '不可售'}]"
                    )
                    return False
                if bot._click_visible_price_option(option["index"]):
                    logger.info(
                        f"通过可见票档快速匹配选择票价: {option.get('text') or self._config.price} "
                        f"(index={option['index']})"
                    )
                    return True

        price_text_selector = f'new UiSelector().textContains("{self._config.price}")'
        if bot.ultra_fast_click(ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.35):
            logger.info(f"通过文本快速匹配选择票价: {self._config.price}")
            return True

        if bot._click_price_option_by_config_index():
            return True

        return None

    def _select_price_option(self, cached_coords=None):
        """Select the configured price using fast config-driven logic first, then OCR-heavy fallbacks."""
        bot = self._bot
        fast_result = bot._select_price_option_fast(cached_coords=cached_coords)
        if fast_result is not None:
            return fast_result

        visible_options = bot.get_visible_price_options()
        matched_options = [
            option
            for option in visible_options
            if bot._price_text_matches_target(option.get("text") or "")
        ]

        for option in matched_options:
            if not bot._is_price_option_available(option):
                logger.warning(
                    f"目标票档当前不可选: {option.get('text') or '(未识别)'} [{option.get('tag') or '不可售'}]"
                )
                return False
            if bot._click_visible_price_option(option["index"]):
                logger.info(
                    f"通过可见票档匹配选择票价: {option.get('text') or self._config.price} "
                    f"(index={option['index']}, source={option.get('source', 'ui')})"
                )
                return True

        if visible_options:
            indexed_option = next(
                (
                    option
                    for option in visible_options
                    if option["index"] == self._config.price_index
                ),
                None,
            )
            if indexed_option and bot._is_price_option_available(indexed_option):
                if bot._click_visible_price_option(indexed_option["index"]):
                    logger.info(
                        f"文本匹配未命中，使用当前可见票档索引选择: {indexed_option.get('text') or self._config.price} "
                        f"(price_index={self._config.price_index})"
                    )
                    return True
            elif indexed_option and not bot._is_price_option_available(indexed_option):
                logger.warning(
                    f"配置索引对应票档当前不可选: {indexed_option.get('text') or '(未识别)'} "
                    f"[{indexed_option.get('tag') or '不可售'}]"
                )
                return False

        price_text_selector = f'new UiSelector().textContains("{self._config.price}")'
        if bot.ultra_fast_click(ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.8):
            logger.info(f"通过文本匹配选择票价: {self._config.price}")
            return True

        logger.info(
            f"文本匹配失败，使用索引选择票价: price_index={self._config.price_index}"
        )
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        try:
            price_container = bot._find(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
            if not bot._using_u2():
                target_price = price_container.find_element(
                    ANDROID_UIAUTOMATOR,
                    f'new UiSelector().className("android.widget.FrameLayout").index({self._config.price_index}).clickable(true)',
                )
            else:
                cards = bot._container_find_elements(
                    price_container, By.CLASS_NAME, "android.widget.FrameLayout"
                )
                clickable_cards = [card for card in cards if bot._is_clickable(card)]
                if not (0 <= self._config.price_index < len(clickable_cards)):
                    logger.warning(
                        f"price_index={self._config.price_index} 超出可点击卡片数量 {len(clickable_cards)}"
                    )
                    return False
                target_price = clickable_cards[self._config.price_index]
            bot._click_element_center(target_price, duration=30)
            return True
        except Exception as e:
            logger.warning(f"票价选择失败，启动备用方案: {e}")
            try:
                if not bot._using_u2() and bot.wait is not None:
                    price_container = bot.wait.until(
                        EC.presence_of_element_located(
                            (
                                By.ID,
                                "cn.damai:id/project_detail_perform_price_flowlayout",
                            )
                        )
                    )
                else:
                    price_container = bot._wait_for_element(
                        By.ID,
                        "cn.damai:id/project_detail_perform_price_flowlayout",
                        timeout=2,
                    )
                if not bot._using_u2():
                    target_price = price_container.find_element(
                        ANDROID_UIAUTOMATOR,
                        f'new UiSelector().className("android.widget.FrameLayout").index({self._config.price_index}).clickable(true)',
                    )
                else:
                    cards = bot._container_find_elements(
                        price_container, By.CLASS_NAME, "android.widget.FrameLayout"
                    )
                    clickable_cards = [
                        card for card in cards if bot._is_clickable(card)
                    ]
                    if not (0 <= self._config.price_index < len(clickable_cards)):
                        logger.warning(
                            f"备用方案: price_index={self._config.price_index} 超出 {len(clickable_cards)}"
                        )
                        return False
                    target_price = clickable_cards[self._config.price_index]
                bot._click_element_center(target_price, duration=30)
                return True
            except Exception as backup_error:
                logger.warning(f"备用票价选择也失败: {backup_error}")
                return False

    # ------------------------------------------------------------------
    # OCR helpers (migrated from DamaiBot)
    # ------------------------------------------------------------------

    def _normalize_ocr_price_text(self, ocr_output):
        """Extract the leading ticket price from noisy OCR output."""
        normalized_text = (ocr_output if isinstance(ocr_output, str) else "").translate(
            _OCR_CHAR_TRANSLATIONS
        )

        digit_runs = re.findall(r"\d{3,5}", normalized_text)
        for run in digit_runs:
            if len(run) >= 4:
                leading_four = int(run[:4])
                if 1000 <= leading_four <= 1999:
                    return f"{leading_four}元"
            leading_three = int(run[:3])
            if 100 <= leading_three <= 999:
                return f"{leading_three}元"

        digits = "".join(re.findall(r"\d", normalized_text))
        if len(digits) >= 4:
            leading_four = int(digits[:4])
            if 1000 <= leading_four <= 1999:
                return f"{leading_four}元"
        if len(digits) >= 3:
            leading_three = int(digits[:3])
            if 100 <= leading_three <= 999:
                return f"{leading_three}元"
        return ""

    def _price_ocr_focus_rect(self, rect):
        """Crop the left-side price area instead of the whole card.

        Damai's card OCR is often polluted by the center reservation tag and
        the trailing favourite icon. A tighter left-side crop is much more
        reliable for numeric ticket prices.
        """
        if not rect:
            return None

        width = max(1, int(rect.get("width", 0)))
        height = max(1, int(rect.get("height", 0)))
        x = int(rect.get("x", 0))
        y = int(rect.get("y", 0))

        focus_x = x + max(0, int(width * 0.03))
        focus_y = y + max(0, int(height * 0.08))
        focus_width = max(1, int(width * 0.35))
        focus_height = max(1, int(height * 0.60))
        return {
            "x": focus_x,
            "y": focus_y,
            "width": focus_width,
            "height": focus_height,
        }

    @staticmethod
    def _choose_best_ocr_price_candidate(candidates):
        """Pick the most trustworthy OCR price from multiple crop/psm attempts."""
        if not candidates:
            return ""

        by_key = {
            (item["variant"], item["psm"]): item["price"]
            for item in candidates
            if item.get("price")
        }
        price_counts = {}
        for item in candidates:
            price = item.get("price")
            if not price:
                continue
            price_counts[price] = price_counts.get(price, 0) + 1

        focus_13 = by_key.get(("focus", "13"), "")
        full_11 = by_key.get(("full", "11"), "")

        if focus_13 and price_counts.get(focus_13, 0) > 1:
            return focus_13
        if full_11 and price_counts.get(full_11, 0) > 1:
            return full_11
        if focus_13 and full_11 and focus_13 == full_11:
            return focus_13
        if full_11:
            return full_11
        if focus_13:
            return focus_13

        ranked = sorted(
            ((count, price) for price, count in price_counts.items()),
            reverse=True,
        )
        if ranked:
            return ranked[0][1]
        return ""

    def _ocr_price_text_from_card(self, screenshot_path, rect):
        """OCR the price number from a price-card crop as a last-resort fallback."""
        if not (_MAGICK_BIN and _TESSERACT_BIN and screenshot_path and rect):
            return ""

        crop_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                crop_path = tmp_file.name

            crop_variants = []
            focus_rect = self._price_ocr_focus_rect(rect)
            if focus_rect:
                crop_variants.append(
                    (
                        "focus",
                        focus_rect,
                        [
                            "-resize",
                            "500%",
                            "-colorspace",
                            "Gray",
                            "-threshold",
                            "75%",
                        ],
                    )
                )
            crop_variants.append(("full", rect, ["-resize", "300%"]))

            candidates = []
            for variant_name, crop_rect, extra_args in crop_variants:
                subprocess.run(
                    [
                        _MAGICK_BIN,
                        screenshot_path,
                        "-crop",
                        (
                            f"{crop_rect['width']}x{crop_rect['height']}"
                            f"+{crop_rect['x']}+{crop_rect['y']}"
                        ),
                        *extra_args,
                        crop_path,
                    ],
                    check=True,
                    capture_output=True,
                )

                for psm in ("13", "7", "11", "6"):
                    result = subprocess.run(
                        [
                            _TESSERACT_BIN,
                            crop_path,
                            "stdout",
                            "-l",
                            "eng",
                            "--psm",
                            psm,
                            "-c",
                            "tessedit_char_whitelist=0123456789",
                        ],
                        check=False,
                        capture_output=True,
                    )
                    ocr_text = result.stdout.decode("utf-8", "ignore")
                    normalized = self._normalize_ocr_price_text(ocr_text)
                    if normalized:
                        candidates.append(
                            {
                                "variant": variant_name,
                                "psm": psm,
                                "price": normalized,
                            }
                        )

            return self._choose_best_ocr_price_candidate(candidates)
        except Exception:
            return ""
        finally:
            if crop_path and os.path.exists(crop_path):
                try:
                    os.unlink(crop_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Visible price options (migrated from DamaiBot)
    # ------------------------------------------------------------------

    def get_visible_price_options(self, allow_ocr=True, xml_root=None):
        """Return visible price options from the current sku page."""
        bot = self._bot

        # Fast path: work entirely from a pre-parsed hierarchy XML (no ADB round-trips).
        if xml_root is not None and bot._using_u2():
            return self._get_visible_price_options_from_xml(
                xml_root, allow_ocr=allow_ocr
            )

        try:
            price_container = bot._find(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
        except Exception:
            return []

        options = []
        try:
            cards = bot._container_find_elements(
                price_container, By.CLASS_NAME, "android.widget.FrameLayout"
            )
        except Exception:
            cards = []

        cards = [card for card in cards if bot._is_clickable(card)]

        # Dump hierarchy once so each _collect_descendant_texts reuses the same tree.
        cached_xml_root = None
        if bot._using_u2() and cards:
            try:
                cached_xml_root = ET.fromstring(bot.d.dump_hierarchy())
            except Exception:
                pass

        screenshot_path = None
        if allow_ocr and cards and _MAGICK_BIN and _TESSERACT_BIN:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False
                ) as tmp_file:
                    screenshot_path = tmp_file.name
                if not bot._using_u2():
                    bot.driver.get_screenshot_as_file(screenshot_path)
                else:
                    bot.d.screenshot(screenshot_path)
            except Exception:
                screenshot_path = None

        # First pass: collect texts from hierarchy (no ADB round-trips per card).
        card_data = []
        ocr_tasks = []  # (card_index, rect) pairs that need OCR
        for index, card in enumerate(cards):
            texts = bot._collect_descendant_texts(card, xml_root=cached_xml_root)
            text = self._price_option_text_from_descendants(texts)
            source = "ui" if text else ""
            tag = ""
            for candidate in texts:
                if candidate in {
                    "可预约",
                    "预售",
                    "无票",
                    "已预约",
                    "缺货",
                    "售罄",
                    "已售罄",
                    "可选",
                }:
                    tag = candidate
                    break
            card_data.append(
                {
                    "index": index,
                    "text": text,
                    "tag": tag,
                    "raw_texts": texts,
                    "source": source,
                }
            )
            if not text and screenshot_path:
                ocr_tasks.append((index, bot._element_rect(card)))

        # Second pass: OCR in parallel for all cards that need it.
        ocr_results: dict[int, str] = {}
        if ocr_tasks and screenshot_path:

            def _run_ocr(args):
                idx, rect = args
                return idx, self._ocr_price_text_from_card(screenshot_path, rect)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(ocr_tasks), 4)
            ) as executor:
                for idx, ocr_text in executor.map(_run_ocr, ocr_tasks):
                    if ocr_text:
                        ocr_results[idx] = ocr_text

        for entry in card_data:
            index = entry["index"]
            if not entry["text"] and index in ocr_results:
                entry["text"] = ocr_results[index]
                entry["source"] = "ocr"
            if not entry["text"] and not entry["tag"]:
                continue
            options.append(
                {
                    "index": index,
                    "text": entry["text"],
                    "tag": entry["tag"],
                    "raw_texts": entry["raw_texts"],
                    "source": entry["source"] or "ui",
                }
            )

        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.unlink(screenshot_path)
            except OSError:
                pass

        return options

    def _get_visible_price_options_from_xml(self, xml_root, allow_ocr=True):
        """Pure-XML price option scan: zero ADB round-trips except for screenshot."""
        bot = self._bot

        # Locate the price container node by resource-id.
        price_container_node = None
        for node in xml_root.iter("node"):
            if (
                node.get("resource-id")
                == "cn.damai:id/project_detail_perform_price_flowlayout"
            ):
                price_container_node = node
                break
        if price_container_node is None:
            return []

        container_bounds = bot._parse_bounds(price_container_node.get("bounds", ""))
        if not container_bounds:
            return []

        # Direct children that are clickable FrameLayouts = price cards.
        card_nodes = [
            child
            for child in price_container_node
            if child.get("class") == "android.widget.FrameLayout"
            and child.get("clickable") == "true"
        ]
        if not card_nodes:
            return []

        # Screenshot for OCR (one shot).
        screenshot_path = None
        if allow_ocr and _MAGICK_BIN and _TESSERACT_BIN:
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False
                ) as tmp_file:
                    screenshot_path = tmp_file.name
                bot.d.screenshot(screenshot_path)
            except Exception:
                screenshot_path = None

        _UNAVAILABLE = {
            "可预约",
            "预售",
            "无票",
            "已预约",
            "缺货",
            "售罄",
            "已售罄",
            "可选",
        }

        card_data = []
        ocr_tasks = []
        for index, card_node in enumerate(card_nodes):
            # Collect all descendant texts directly from XML nodes.
            texts: list[str] = []
            seen: set[str] = set()
            for desc in card_node.iter("node"):
                text = (desc.get("text") or "").strip()
                if text and text not in seen:
                    texts.append(text)
                    seen.add(text)

            price_text = self._price_option_text_from_descendants(texts)
            source = "ui" if price_text else ""
            tag = next((c for c in texts if c in _UNAVAILABLE), "")

            card_bounds = bot._parse_bounds(card_node.get("bounds", ""))
            if not price_text and screenshot_path and card_bounds:
                left, top, right, bottom = card_bounds
                rect = {
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top,
                }
                ocr_tasks.append((index, rect))

            card_data.append(
                {
                    "index": index,
                    "text": price_text,
                    "tag": tag,
                    "raw_texts": texts,
                    "source": source,
                }
            )

        # Parallel OCR for cards whose price text wasn't in the UI tree.
        ocr_results: dict[int, str] = {}
        if ocr_tasks and screenshot_path:

            def _run_ocr(args):
                idx, rect = args
                return idx, self._ocr_price_text_from_card(screenshot_path, rect)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(ocr_tasks), 4)
            ) as executor:
                for idx, ocr_text in executor.map(_run_ocr, ocr_tasks):
                    if ocr_text:
                        ocr_results[idx] = ocr_text

        options = []
        for entry in card_data:
            idx = entry["index"]
            if not entry["text"] and idx in ocr_results:
                entry["text"] = ocr_results[idx]
                entry["source"] = "ocr"
            if not entry["text"] and not entry["tag"]:
                continue
            options.append(
                {
                    "index": idx,
                    "text": entry["text"],
                    "tag": entry["tag"],
                    "raw_texts": entry["raw_texts"],
                    "source": entry["source"] or "ui",
                }
            )

        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.unlink(screenshot_path)
            except OSError:
                pass

        return options
