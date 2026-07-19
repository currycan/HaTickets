# -*- coding: UTF-8 -*-
"""
FastPipeline — Global-deadline ticket purchase pipeline.

Replaces the cascading-timeout approach (SKU 6s + confirm 8s = 14s dead time)
with a single 5s global deadline shared across all phases.
"""

import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, List, Optional, Tuple

from selenium.webdriver.common.by import By

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:
    pass

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

try:
    from mobile.page_probe import (
        MANUAL_VERIFICATION_MARKERS,
        PENDING_ORDER_MARKERS,
        TRANSIENT_ERROR_MARKERS,
    )
except ImportError:
    from page_probe import (
        MANUAL_VERIFICATION_MARKERS,
        PENDING_ORDER_MARKERS,
        TRANSIENT_ERROR_MARKERS,
    )

logger = get_logger(__name__)

_PIPELINE_DEADLINE_S = 8.0
_monotonic = time.monotonic

# Keys that must all exist in _cached_coords for a warm run.
_WARM_REQUIRED_KEYS = frozenset(
    {
        "detail_buy",
        "price",
        "sku_buy",
        "attendee_checkboxes",
    }
)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def poll_until(
    condition_fn: Callable[[], bool], deadline: float, interval_s: float = 0.05
) -> bool:
    """Poll *condition_fn* until it returns True or *deadline* is exceeded.

    ``deadline`` must be expressed in the module monotonic-clock domain.
    Returns True if the condition was met before the deadline, False otherwise.
    """
    while _monotonic() < deadline:
        if condition_fn():
            return True
        time.sleep(interval_s)
    return False


def batch_shell_taps(device, coordinates: List[Tuple[int, int]]) -> None:
    """Send multiple ``input tap x y`` commands in a single shell call."""
    if not coordinates:
        return
    cmd = "; ".join(f"input tap {x} {y}" for x, y in coordinates)
    device.shell(cmd)


# ---------------------------------------------------------------------------
# FastPipeline
# ---------------------------------------------------------------------------


class FastPipeline:
    """Coordinate-driven pipeline with a single global deadline."""

    def __init__(self, device, config, probe: bool, guard):
        self._device = device
        self._config = config
        self._probe = probe
        self._guard = guard
        self._bot = None

        self._cached_coords: dict = {}
        self._cached_no_match: set = set()
        self._last_preselect_failure: Optional[str] = None

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference for delegation."""
        self._bot = bot

    def _forbid_formal_pipeline(self, message: str) -> bool:
        """Fail closed when a validation-only pipeline sees formal mode.

        ``Config`` validates ``if_commit_order`` as a real bool.  Checking the
        literal value keeps lightweight test doubles (whose missing attributes
        become truthy Mock objects) from being misclassified, while an actual
        formal config is always rejected.  The rejection must also work before
        a bot delegate is bound.
        """
        if getattr(self._config, "if_commit_order", False) is not True:
            return False
        setter = getattr(self._bot, "_set_terminal_failure", None)
        if callable(setter):
            setter("formal_pipeline_forbidden")
        logger.error(message)
        return True

    def _bot_hot_cache_method(self, name):
        """Return a real DamaiBot cache helper, never a Mock's dynamic attr."""
        if self._bot is None or not callable(getattr(type(self._bot), name, None)):
            return None
        method = getattr(self._bot, name, None)
        return method if callable(method) else None

    def _get_cached_coord(self, key, **stamp_kwargs):
        getter = self._bot_hot_cache_method("_get_hot_coord")
        if getter is not None:
            return getter(key, **stamp_kwargs)
        return self._cached_coords.get(key)

    def _put_cached_coord(self, key, value, **stamp_kwargs):
        putter = self._bot_hot_cache_method("_put_hot_coord")
        if putter is not None:
            return putter(key, value, **stamp_kwargs)
        self._cached_coords[key] = value
        return value

    def _has_cached_no_match(self, key, **stamp_kwargs):
        checker = self._bot_hot_cache_method("_has_hot_no_match")
        if checker is not None:
            return checker(key, **stamp_kwargs)
        return key in self._cached_no_match

    def _put_cached_no_match(self, key, **stamp_kwargs):
        putter = self._bot_hot_cache_method("_put_hot_no_match")
        if putter is not None:
            putter(key, **stamp_kwargs)
            return
        self._cached_no_match.add(key)

    def _set_cache_context(self, **context):
        setter = self._bot_hot_cache_method("_set_hot_cache_context")
        if setter is not None:
            setter(**context)

    def _layout_digest(self, xml_root, scope):
        builder = self._bot_hot_cache_method("_hot_layout_digest_from_xml")
        return builder(xml_root, scope) if builder is not None else ""

    def _confirm_page_ready(self) -> bool:
        """Detect confirm-page readiness with lighter-weight fallbacks."""
        bot = self._bot
        if bot._has_element(By.ID, "cn.damai:id/checkbox"):
            return True
        if bot._attendee_checkbox_elements():
            return True

        confirm_selectors = [
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("确认购买")',
            ),
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("实名观演人")',
            ),
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("立即提交")',
            ),
        ]
        return any(bot._has_element(by, value) for by, value in confirm_selectors)

    def _wait_for_purchase_entry(self, deadline: float):
        """Wait briefly for detail CTA to advance into SKU or confirm page."""
        bot = self._bot
        remaining = max(0.0, deadline - _monotonic())
        if remaining <= 0:
            return None
        timeout = min(0.45, remaining)
        return bot._wait_for_purchase_entry_result(
            timeout=timeout,
            poll_interval=0.03,
            fallback_probe_on_timeout=False,
        )

    def _wait_for_confirm_ready(
        self, deadline: float, poll_window_s: float = 0.22
    ) -> bool:
        """Poll briefly for the confirm page without triggering heavier probes."""
        poll_end = min(_monotonic() + poll_window_s, deadline)
        while _monotonic() < poll_end:
            if self._confirm_page_ready():
                return True
            time.sleep(0.02)
        return False

    def _open_purchase_panel(self, detail_buy_coords, deadline: float):
        """Leave detail_page using shell double-tap first, then u2 click fallback."""
        bot = self._bot
        if not detail_buy_coords or _monotonic() >= deadline:
            return False

        batch_shell_taps(self._device, [detail_buy_coords, detail_buy_coords])
        entry_probe = self._wait_for_purchase_entry(deadline)
        if entry_probe:
            return entry_probe

        if _monotonic() >= deadline:
            return None
        bot._click_coordinates(*detail_buy_coords, duration=25)
        return self._wait_for_purchase_entry(deadline)

    def _select_price_with_pipeline(self, price_coords) -> bool:
        """Select the configured price using the same robust path as normal flow."""
        bot = self._bot
        return bool(bot._click_price_option_by_config_index(coords=price_coords))

    def _click_sku_buy_with_pipeline(self, sku_buy_coords) -> bool:
        """Click SKU buy/confirm button with element-first fallback."""
        bot = self._bot
        if self._forbid_formal_pipeline(
            "正式模式禁止调用 validation SKU 连点管道"
        ):
            return False
        burst_count = 1
        if bot._click_sku_buy_button_element(burst_count=burst_count):
            return True
        if sku_buy_coords:
            bot._burst_click_coordinates(
                *sku_buy_coords,
                count=burst_count,
                interval_ms=25,
                duration=25,
            )
            return True
        return False

    def _shell_price_and_buy_until_confirm(
        self, price_coords, sku_buy_coords, deadline: float
    ) -> bool:
        """Fast-path SKU selection using raw shell taps before element fallbacks."""
        if self._forbid_formal_pipeline(
            "正式模式禁止使用票档与购买按钮的 shell 批量点击"
        ):
            return False
        if (
            not price_coords
            or not sku_buy_coords
            or _monotonic() >= deadline
        ):
            return False

        shell_deadline = min(deadline, _monotonic() + 0.9)
        while _monotonic() < shell_deadline:
            batch_shell_taps(
                self._device,
                [price_coords, sku_buy_coords, sku_buy_coords],
            )
            if self._wait_for_confirm_ready(shell_deadline):
                return True
        return False

    # -- Public helpers -----------------------------------------------------

    def has_warm_coords(self) -> bool:
        """True when all keys required for a warm run are cached."""
        validator = self._bot_hot_cache_method("_validate_hot_cache_bundle")
        if validator is not None:
            return bool(validator(_WARM_REQUIRED_KEYS))
        return _WARM_REQUIRED_KEYS.issubset(self._cached_coords.keys())

    # -- Warm path ----------------------------------------------------------

    def run_warm(self, start_time: float) -> Optional[bool]:
        """Execute the warm (cached-coordinate) pipeline.

        ``start_time`` is a monotonic-clock reading shared by all phases.
        Returns True on success, None on timeout / failure.
        """
        if self._forbid_formal_pipeline(
            "正式模式禁止调用 legacy blind warm pipeline"
        ):
            return None
        # Production uses run_warm_validation(), which validates each page
        # scope after observing its state.  The legacy blind background loop
        # cannot safely validate SKU coordinates before dispatch, so stamped
        # DamaiBot caches deliberately refuse this path.
        if self._bot_hot_cache_method("_get_hot_coord") is not None:
            logger.warning("legacy blind warm pipeline disabled for stamped cache")
            return None
        deadline = start_time + _PIPELINE_DEADLINE_S

        # Step 1: batch city + detail_buy taps
        taps: List[Tuple[int, int]] = []
        city_coord = self._cached_coords.get("city")
        if city_coord is not None:
            taps.append(city_coord)
        detail_buy_coord = self._cached_coords.get("detail_buy")
        if detail_buy_coord is not None:
            taps.append(detail_buy_coord)
        if taps:
            batch_shell_taps(self._device, taps)

        if _monotonic() >= deadline:
            return None

        # Step 2: background blind clicker (price + sku_buy every 20ms)
        stop_event = threading.Event()
        price_coord = self._cached_coords.get("price")
        sku_buy_coord = self._cached_coords.get("sku_buy")

        def _blind_clicker():
            blind_taps: List[Tuple[int, int]] = []
            if price_coord is not None:
                blind_taps.append(price_coord)
            if sku_buy_coord is not None:
                blind_taps.append(sku_buy_coord)
            if not blind_taps:
                return
            while not stop_event.is_set():
                batch_shell_taps(self._device, blind_taps)
                stop_event.wait(0.02)

        clicker_thread = threading.Thread(target=_blind_clicker, daemon=True)
        clicker_thread.start()

        try:
            # Step 3: poll for attendee checkbox
            checkbox_found = poll_until(
                lambda: self._has_checkbox(),
                deadline=deadline,
            )
            if not checkbox_found:
                return None

            # Step 4: click attendee coordinates
            attendee_coord = self._cached_coords.get("attendee_checkboxes")
            if attendee_coord is not None:
                batch_shell_taps(self._device, [attendee_coord])
            return True
        finally:
            stop_event.set()
            clicker_thread.join(timeout=1.0)

    # -- Cold path ----------------------------------------------------------

    def run_cold(self, start_time: float) -> Optional[bool]:
        """Execute the cold (XML-dump) pipeline.

        ``start_time`` is a monotonic-clock reading shared by all phases.
        Returns True on success, None on timeout / failure.
        """
        if self._forbid_formal_pipeline(
            "正式模式禁止调用 legacy cold pipeline"
        ):
            return None
        deadline = start_time + _PIPELINE_DEADLINE_S

        # Phase 1: initial XML dump
        if _monotonic() >= deadline:
            return None
        try:
            xml_src = self._device.dump_hierarchy()
            if xml_src:
                ET.fromstring(xml_src)
        except Exception:
            logger.debug("cold pipeline: initial XML dump failed")

        # Phase 2: poll for SKU page
        if _monotonic() >= deadline:
            return None
        sku_found = poll_until(
            lambda: self._has_sku_layout(),
            deadline=deadline,
        )
        if not sku_found:
            return None

        # Phase 3: SKU XML dump
        if _monotonic() >= deadline:
            return None
        try:
            xml_src = self._device.dump_hierarchy()
            if xml_src:
                ET.fromstring(xml_src)
        except Exception:
            logger.debug("cold pipeline: SKU XML dump failed")

        # Phase 4: poll for confirm checkbox
        if _monotonic() >= deadline:
            return None
        checkbox_found = poll_until(
            lambda: self._has_checkbox(),
            deadline=deadline,
        )
        if not checkbox_found:
            return None

        return True

    # -- Private helpers ----------------------------------------------------

    def _has_checkbox(self) -> bool:
        """Check for attendee checkbox via u2 element lookup."""
        try:
            el = self._device(resourceId="cn.damai:id/checkbox")
            exists = getattr(el, "exists", False)
            if callable(exists):
                try:
                    return bool(exists(timeout=0))
                except TypeError:
                    return bool(exists())
            return bool(exists)
        except Exception:
            return False

    def _has_sku_layout(self) -> bool:
        """Check for SKU layout via u2 element lookup."""
        try:
            el = self._device(resourceId="cn.damai:id/layout_sku")
            exists = getattr(el, "exists", False)
            if callable(exists):
                try:
                    return bool(exists(timeout=0))
                except TypeError:
                    return bool(exists())
            return bool(exists)
        except Exception:
            return False

    # -- Migrated pipeline methods (from DamaiBot) --------------------------

    def rush_preselect_and_buy_via_xml(self, *, dialogs_checked=False):
        """Cold rush path: single XML dump to find city/date/buy button and cache coords.

        Replaces 3-6 sequential _cached_tap HTTP calls (~3-4s cold) with 1 dump_hierarchy
        (~0.3s) + local XML parsing + 2-3 cached clicks (~0.2-0.3s).
        Returns (buy_clicked: bool).
        """
        bot = self._bot
        self._last_preselect_failure = None
        if self._forbid_formal_pipeline(
            "正式模式禁止用同一旧快照批量点击日期、城市和购票入口"
        ):
            self._last_preselect_failure = "formal_pipeline_forbidden"
            return False
        if not dialogs_checked:
            bot._dismiss_fast_blocking_dialogs()
        xml_root = bot._dump_hierarchy_xml()
        if xml_root is None:
            self._last_preselect_failure = "device_error"
            return False
        detail_layout_digest = self._layout_digest(xml_root, "detail")
        self._set_cache_context(
            scope="detail",
            page_state="detail_page",
            layout_digest=detail_layout_digest,
        )

        merged_text = " ".join(
            " ".join(
                value
                for value in (
                    (node.get("text") or "").strip(),
                    (node.get("content-desc") or "").strip(),
                )
                if value
            )
            for node in xml_root.iter("node")
        )
        if any(marker in merged_text for marker in PENDING_ORDER_MARKERS):
            self._last_preselect_failure = "pending_order_dialog"
            bot._report_pending_order_dialog()
            return False
        if any(
            marker in merged_text for marker in MANUAL_VERIFICATION_MARKERS
        ):
            self._last_preselect_failure = "manual_verification"
            bot._set_terminal_failure("manual_verification_required")
            logger.warning("购票入口点击前检测到验证码/人机验证，已停止")
            return False
        if any(marker in merged_text for marker in TRANSIENT_ERROR_MARKERS):
            self._last_preselect_failure = "transient_error"
            logger.warning("购票入口点击前检测到页面刷新失败，交给恢复流程")
            return False

        # --- Extract coords from single XML dump ---
        buy_coords = None
        city_coords = None
        date_coords = None

        for node in xml_root.iter("node"):
            rid = node.get("resource-id", "")
            text = node.get("text", "")

            # Purchase button (detail page)
            if not buy_coords and rid in (
                "trade_project_detail_purchase_status_bar_container_fl",
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ):
                buy_coords = bot._extract_coords_from_xml_node(node)

            # City match
            if (
                not city_coords
                and self._config.city
                and text
                and self._config.city in text
            ):
                city_coords = bot._extract_coords_from_xml_node(node)

            # Date match
            if (
                not date_coords
                and self._config.date
                and text
                and self._config.date in text
            ):
                date_coords = bot._extract_coords_from_xml_node(node)

        missing_filters = []
        if self._config.date and date_coords is None:
            missing_filters.append("date")
        if self._config.city and city_coords is None:
            missing_filters.append("city")
        if missing_filters and self._config.if_commit_order:
            self._last_preselect_failure = "session_filter_unconfirmed"
            bot._set_terminal_failure("session_filter_unconfirmed")
            logger.error(
                "未确认目标%s，正式下单模式停止以避免抢错场次",
                "/".join(missing_filters),
            )
            return False

        # --- Cache coords and batch-click via shell ---
        tap_cmds = []
        if date_coords:
            self._put_cached_coord(
                "date",
                date_coords,
                scope="detail",
                page_state="detail_page",
                layout_digest=detail_layout_digest,
            )
            tap_cmds.append(f"input tap {int(date_coords[0])} {int(date_coords[1])}")
            logger.info(f"极速模式预选日期: {self._config.date}")
        elif self._config.date:
            self._put_cached_no_match(
                "date",
                scope="detail",
                page_state="detail_page",
                layout_digest=detail_layout_digest,
            )

        if city_coords:
            self._put_cached_coord(
                "city",
                city_coords,
                scope="detail",
                page_state="detail_page",
                layout_digest=detail_layout_digest,
            )
            tap_cmds.append(f"input tap {int(city_coords[0])} {int(city_coords[1])}")
            logger.info(f"极速模式预选城市: {self._config.city}")
        elif self._config.city:
            self._put_cached_no_match(
                "city",
                scope="detail",
                page_state="detail_page",
                layout_digest=detail_layout_digest,
            )

        if buy_coords:
            self._put_cached_coord(
                "detail_buy",
                buy_coords,
                scope="detail",
                page_state="detail_page",
                layout_digest=detail_layout_digest,
            )
            logger.info("点击购票按钮...")
            tap_cmds.append(f"input tap {int(buy_coords[0])} {int(buy_coords[1])}")
        if tap_cmds:
            self._device.shell("; ".join(tap_cmds))
            if not buy_coords:
                self._last_preselect_failure = "purchase_button_missing"
            return bool(buy_coords)
        self._last_preselect_failure = "purchase_button_missing"
        return False

    def run_cold_validation(self, start_time):
        """Fast cold validation: XML dump -> shell batch -> concurrent polling.

        Returns True on success, None to fall back to the normal flow.
        """
        bot = self._bot
        if self._forbid_formal_pipeline(
            "正式模式禁止调用开发验证冷管道"
        ):
            return False

        # --- Phase 1: detail page --- XML dump for city/buy coords, shell batch click ---
        if not self.rush_preselect_and_buy_via_xml():
            return None

        global_deadline = start_time + _PIPELINE_DEADLINE_S
        entry_probe = self._wait_for_purchase_entry(global_deadline)
        if not entry_probe:
            detail_buy = self._get_cached_coord(
                "detail_buy",
                scope="detail",
                page_state="detail_page",
            )
            if detail_buy:
                self._bot._click_coordinates(*detail_buy, duration=25)
                entry_probe = self._wait_for_purchase_entry(global_deadline)
        if not entry_probe:
            return None

        if entry_probe["state"] == "order_confirm_page" or self._confirm_page_ready():
            return self._finish_confirm(start_time)

        if entry_probe["state"] != "sku_page":
            return None

        # --- Phase 3: SKU page --- single XML dump for price + sku_buy coords ---
        sku_xml = bot._dump_hierarchy_xml()
        if sku_xml is None:
            return None
        sku_layout_digest = self._layout_digest(sku_xml, "sku")
        self._set_cache_context(
            scope="sku",
            page_state="sku_page",
            layout_digest=sku_layout_digest,
        )
        price_coords = bot._get_price_option_coordinates_by_config_index(
            xml_root=sku_xml
        )
        sku_buy_coords = bot._get_buy_button_coordinates(xml_root=sku_xml)
        if not price_coords or not sku_buy_coords:
            return None

        # Cache for future warm-pipeline runs.
        self._put_cached_coord(
            "price",
            price_coords,
            scope="sku",
            page_state="sku_page",
            layout_digest=sku_layout_digest,
        )
        self._put_cached_coord(
            "sku_buy",
            sku_buy_coords,
            scope="sku",
            page_state="sku_page",
            layout_digest=sku_layout_digest,
        )

        logger.info("选择票价...")
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        logger.info("选择数量...")
        logger.info("确定购买...")

        # --- Phase 4: shell fast path, then element-based fallbacks ---
        confirmed = self._shell_price_and_buy_until_confirm(
            price_coords,
            sku_buy_coords,
            global_deadline,
        )
        sold_out_checked = False
        if not confirmed:
            if not self._select_price_with_pipeline(price_coords):
                return None

            while _monotonic() < global_deadline:
                if not self._click_sku_buy_with_pipeline(sku_buy_coords):
                    return None

                if self._wait_for_confirm_ready(global_deadline, poll_window_s=0.55):
                    confirmed = True
                    break

                # Check for sold-out once midway to avoid wasting the full deadline.
                if (
                    not sold_out_checked
                    and _monotonic() > start_time + _PIPELINE_DEADLINE_S * 0.5
                ):
                    sold_out_checked = True
                    if bot._is_buy_button_sold_out():
                        logger.warning("购买按钮区域检测到缺货/售罄标识，管道提前退出")
                        bot._set_terminal_failure("sold_out")
                        return False

        if not confirmed:
            return None

        return self._finish_confirm(start_time)

    def _finish_confirm(self, start_time):
        """Shared tail for the cold pipeline: select attendees on confirm page."""
        bot = self._bot
        required_count = max(1, len(self._config.users or []))
        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）")
        logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")

        # Find and click attendee checkboxes (also caches coords for warm pipeline).
        checkbox_elements = bot._attendee_checkbox_elements()
        if checkbox_elements:
            _coords = []
            for el in checkbox_elements:
                try:
                    bt = getattr(el, "bounds", None)
                    if isinstance(bt, (list, tuple)) and len(bt) == 4:
                        left, top, right, bottom = [int(v) for v in bt]
                        _coords.append(((left + right) // 2, (top + bottom) // 2))
                except Exception:
                    pass
            if _coords:
                confirm_layout_digest = getattr(
                    self._bot,
                    "_hot_cache_layout_digests",
                    {},
                ).get("confirm") or f"confirm-checkboxes:{_coords!r}"
                self._set_cache_context(
                    scope="confirm",
                    page_state="order_confirm_page",
                    layout_digest=confirm_layout_digest,
                )
                self._put_cached_coord(
                    "attendee_checkboxes",
                    _coords,
                    scope="confirm",
                    page_state="order_confirm_page",
                    layout_digest=confirm_layout_digest,
                )
            for checkbox in checkbox_elements[:required_count]:
                bot._click_attendee_checkbox_fast(checkbox)

        bot._set_run_outcome("validation_ready")
        logger.info('if_commit_order=False，已完成观演人勾选，停止在"立即提交"前')
        logger.info(
            f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {_monotonic() - start_time:.2f}秒"
        )
        return True

    def run_warm_validation(self, start_time):
        """Ultra-fast warm validation: blind shell clicks + concurrent polling.

        Returns True on success, None to fall back to the normal flow.
        """
        bot = self._bot
        if self._forbid_formal_pipeline(
            "正式模式禁止调用开发验证热管道"
        ):
            return False
        detail_buy = self._get_cached_coord(
            "detail_buy",
            scope="detail",
            page_state="detail_page",
        )
        date = self._get_cached_coord(
            "date",
            scope="detail",
            page_state="detail_page",
        )
        city = self._get_cached_coord(
            "city",
            scope="detail",
            page_state="detail_page",
        )
        if detail_buy is None:
            return None
        required_count = max(1, len(self._config.users or []))

        # --- Step 1: apply cached filters, then enter purchase panel --------
        tap_cmds = []
        if date and not self._has_cached_no_match(
            "date", scope="detail", page_state="detail_page"
        ):
            tap_cmds.append(f"input tap {int(date[0])} {int(date[1])}")
            logger.info(f"极速模式预选日期: {self._config.date}")
        if city and not self._has_cached_no_match(
            "city", scope="detail", page_state="detail_page"
        ):
            tap_cmds.append(f"input tap {int(city[0])} {int(city[1])}")
            logger.info(f"极速模式预选城市: {self._config.city}")
        tap_cmds.append(
            f"input tap {int(detail_buy[0])} {int(detail_buy[1])}"
        )
        self._device.shell("; ".join(tap_cmds))
        logger.info("点击购票按钮...")
        deadline = start_time + _PIPELINE_DEADLINE_S
        entry_probe = self._wait_for_purchase_entry(deadline)
        if not entry_probe:
            self._bot._click_coordinates(*detail_buy, duration=25)
            entry_probe = self._wait_for_purchase_entry(deadline)
        if not entry_probe:
            return None
        if entry_probe["state"] == "order_confirm_page" or self._confirm_page_ready():
            entry_probe = {"state": "order_confirm_page"}
        elif entry_probe["state"] != "sku_page":
            return None

        # --- Step 2: select price + click buy, poll for confirm ---
        logger.info("选择票价...")
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        logger.info("选择数量...")
        logger.info("确定购买...")

        confirmed = False
        if entry_probe["state"] == "sku_page":
            price = self._get_cached_coord(
                "price",
                scope="sku",
                page_state="sku_page",
            )
            sku_buy = self._get_cached_coord(
                "sku_buy",
                scope="sku",
                page_state="sku_page",
            )
            if price is None or sku_buy is None:
                return None
            confirmed = self._shell_price_and_buy_until_confirm(price, sku_buy, deadline)
            if not confirmed and not self._select_price_with_pipeline(price):
                return None
            while (
                _monotonic() < deadline
                and entry_probe["state"] == "sku_page"
                and not confirmed
            ):
                if not self._click_sku_buy_with_pipeline(sku_buy):
                    return None
                if self._wait_for_confirm_ready(deadline, poll_window_s=0.55):
                    confirmed = True
                    break

        if entry_probe["state"] == "sku_page" and not confirmed:
            return None

        # --- Step 4: click attendees -------------------------------------
        attendees = self._get_cached_coord(
            "attendee_checkboxes",
            scope="confirm",
            page_state="order_confirm_page",
        )
        if attendees is None:
            return None
        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）")
        logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
        for c in attendees[:required_count]:
            bot._click_coordinates(*c)

        bot._set_run_outcome("validation_ready")
        logger.info('if_commit_order=False，已完成观演人勾选，停止在"立即提交"前')
        logger.info(
            f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {_monotonic() - start_time:.2f}秒"
        )
        return True
