"""PriceSelector — ticket price and SKU selection on the Damai app.

Uses delegate pattern: delegates complex operations to DamaiBot while
providing a clean interface for FastPipeline and other consumers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, Tuple

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from mobile.logger import get_logger

try:
    from mobile.item_resolver import normalize_text
except ImportError:
    from item_resolver import normalize_text

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)

_PRICE_UNAVAILABLE_TAGS = {"无票", "缺货", "缺货登记", "售罄", "已售罄", "不可选", "暂不可售"}


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
        """Select price option by config.price_index. Returns True on success."""
        coords = self.get_price_coords_by_index(xml_root=xml_root)
        if coords is None:
            logger.warning(f"无法定位 price_index={self._config.price_index} 的坐标")
            return False
        self._click_coordinates(*coords)
        logger.info(f"通过配置索引选择票价: price_index={self._config.price_index}")
        return True

    def get_price_coords_by_index(self, xml_root=None) -> Optional[Tuple[int, int]]:
        """Get coordinates for price option at config.price_index."""
        if self._bot is not None:
            try:
                return self._bot._get_price_option_coordinates_by_config_index(xml_root=xml_root)
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
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")'),
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
            if xml_root is None:
                xml_root = bot._dump_hierarchy_xml()
            if xml_root is not None:
                for node in xml_root.iter("node"):
                    if node.get("resource-id") == "cn.damai:id/project_detail_perform_price_flowlayout":
                        cards = [
                            child for child in node
                            if child.get("class") == "android.widget.FrameLayout"
                            and child.get("clickable") == "true"
                        ]
                        if not (0 <= self._config.price_index < len(cards)):
                            return None
                        bounds = bot._parse_bounds(cards[self._config.price_index].get("bounds", ""))
                        if bounds:
                            left, top, right, bottom = bounds
                            return ((left + right) // 2, (top + bottom) // 2)
                        return None
                return None

        try:
            price_container = bot._find(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
        except Exception:
            return None

        try:
            cards = bot._container_find_elements(price_container, By.CLASS_NAME, "android.widget.FrameLayout")
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
            if normalized_target in normalized_text or normalized_text in normalized_target:
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
            price_container = bot._find(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
            cards = bot._container_find_elements(price_container, By.CLASS_NAME, "android.widget.FrameLayout")
        except Exception:
            return False

        clickable_cards = [card for card in cards if bot._is_clickable(card)]
        if 0 <= card_index < len(clickable_cards):
            bot._click_element_center(clickable_cards[card_index], duration=30)
            return True
        return False

    def _click_price_option_by_config_index(self, burst=False, coords=None):
        """Click the configured price card index directly without reading ticket texts."""
        bot = self._bot
        target_coords = coords or bot._get_price_option_coordinates_by_config_index()
        if not target_coords:
            return False
        if burst:
            bot._burst_click_coordinates(*target_coords, count=2, interval_ms=25, duration=25)
        else:
            bot._click_coordinates(*target_coords, duration=30)
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        return True

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
        suffix_ids = (
            "cn.damai:id/bricks_dm_common_price_suffix",
        )

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
            if bot._click_price_option_by_config_index(burst=_burst, coords=cached_coords):
                return True

        visible_options = bot.get_visible_price_options(allow_ocr=False)

        if visible_options:
            indexed_option = next((option for option in visible_options if option["index"] == self._config.price_index), None)
            if indexed_option:
                if not bot._is_price_option_available(indexed_option):
                    logger.warning(
                        f"配置索引对应票档当前不可选: {indexed_option.get('text') or '(未识别)'} "
                        f"[{indexed_option.get('tag') or '不可售'}]"
                    )
                    return False
                if not indexed_option.get("text") or bot._price_text_matches_target(indexed_option.get("text") or ""):
                    if bot._click_visible_price_option(indexed_option["index"]):
                        logger.info(
                            f"通过配置索引快速选择票价: {indexed_option.get('text') or self._config.price} "
                            f"(price_index={self._config.price_index})"
                        )
                        return True
            elif bot._click_price_option_by_config_index():
                return True

            matched_options = [
                option for option in visible_options
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
        if bot.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.35):
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
        matched_options = [option for option in visible_options if bot._price_text_matches_target(option.get("text") or "")]

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
            indexed_option = next((option for option in visible_options if option["index"] == self._config.price_index), None)
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
        if bot.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.8):
            logger.info(f"通过文本匹配选择票价: {self._config.price}")
            return True

        logger.info(f"文本匹配失败，使用索引选择票价: price_index={self._config.price_index}")
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        try:
            price_container = bot._find(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
            if not bot._using_u2():
                target_price = price_container.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().className("android.widget.FrameLayout").index({self._config.price_index}).clickable(true)'
                )
            else:
                cards = bot._container_find_elements(price_container, By.CLASS_NAME, "android.widget.FrameLayout")
                clickable_cards = [card for card in cards if bot._is_clickable(card)]
                target_price = clickable_cards[self._config.price_index]
            bot._click_element_center(target_price, duration=30)
            return True
        except Exception as e:
            logger.warning(f"票价选择失败，启动备用方案: {e}")
            try:
                if not bot._using_u2() and bot.wait is not None:
                    price_container = bot.wait.until(
                        EC.presence_of_element_located((By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"))
                    )
                else:
                    price_container = bot._wait_for_element(
                        By.ID,
                        "cn.damai:id/project_detail_perform_price_flowlayout",
                        timeout=2,
                    )
                if not bot._using_u2():
                    target_price = price_container.find_element(
                        AppiumBy.ANDROID_UIAUTOMATOR,
                        f'new UiSelector().className("android.widget.FrameLayout").index({self._config.price_index}).clickable(true)'
                    )
                else:
                    cards = bot._container_find_elements(price_container, By.CLASS_NAME, "android.widget.FrameLayout")
                    clickable_cards = [card for card in cards if bot._is_clickable(card)]
                    target_price = clickable_cards[self._config.price_index]
                bot._click_element_center(target_price, duration=30)
                return True
            except Exception as backup_error:
                logger.warning(f"备用票价选择也失败: {backup_error}")
                return False
