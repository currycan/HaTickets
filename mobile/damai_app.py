# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError as e:
    raise SystemExit(
        "依赖缺失：selenium 未安装。\n"
        "→ 请在项目根目录运行：poetry install\n"
        "→ 然后通过 mobile/scripts/start_ticket_grabbing.sh 启动而非直接运行 .py"
    ) from e

try:
    from mobile.config import Config
except ImportError:
    from config import Config

try:
    from mobile.item_resolver import (
        DamaiItemResolver,
        DamaiItemResolveError,
        city_keyword,
        normalize_text,
    )
except ImportError:
    from item_resolver import (
        normalize_text,
    )

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

try:
    from mobile.ui_primitives import UIPrimitives, ANDROID_UIAUTOMATOR
except ImportError:
    from ui_primitives import UIPrimitives, ANDROID_UIAUTOMATOR

try:
    from mobile.buy_button_guard import BuyButtonGuard
    from mobile.page_probe import PageProbe
    from mobile.fast_pipeline import FastPipeline, poll_until, batch_shell_taps
    from mobile.recovery import RecoveryHelper
    from mobile.event_navigator import EventNavigator
    from mobile.price_selector import PriceSelector
    from mobile.attendee_selector import AttendeeSelector
except ImportError:
    from buy_button_guard import BuyButtonGuard
    from page_probe import PageProbe
    from fast_pipeline import FastPipeline
    from recovery import RecoveryHelper
    from event_navigator import EventNavigator
    from price_selector import PriceSelector
    from attendee_selector import AttendeeSelector

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
# 开售后大麦详情页 / 票务面板上"可点购票"的安全文案集合（issue #29）。
# 任意一个文案出现即视为开售已开放购买入口；不在此列的文案（如"预约抢票""即将开抢"）
# 一律视为未开售，避免误点预约入口。
SALE_READY_TEXTS: tuple[str, ...] = (
    "立即购票",
    "立即预定",
    "立即预订",  # 大麦 2026-04 后新增（issue #29）
    "立即抢票",
    "Book Now",  # 国际化场景兜底
)
# UiSelector textMatches 用：基于 SALE_READY_TEXTS 自动生成的正则联合
# （新增/修改 SALE_READY_TEXTS 时此处自动同步，避免文案分散）
_SALE_READY_TEXT_REGEX_OR = "|".join(f".*{t}.*" for t in SALE_READY_TEXTS)
_CTA_READY_KEYWORDS = (
    *SALE_READY_TEXTS,
    "立即购买",
    "选座购买",
    "购买",
    "抢票",
    "预定",
    "提交订单",
    "去结算",
    "确定",
)
_CTA_BLOCKED_KEYWORDS = (
    "预约",
    "预售",
    "即将开抢",
    "待开售",
    "未开售",
    "倒计时",
    "无票",
    "售罄",
    "缺货",
)
_MANUAL_STEP_BASELINES = {
    "搜索页输入并提交关键词": 6.0,
    "搜索结果扫描并打开目标": 12.0,
}


class DamaiBot(UIPrimitives):
    def __init__(self, config=None, setup_driver=True):
        self.config = config or Config.load_config()
        self.item_detail = None
        self.driver = None
        self.d = None
        self.wait = None
        self._terminal_failure_reason = None
        self._last_run_outcome = None
        self._last_discovery_step_timings = []
        # Cache of {key: (x, y)} coordinates for hot-path elements.
        # Populated on the first run and reused on warm retries (1 HTTP call vs 4+).
        self._cached_hot_path_coords: dict = {}
        # Keys of preselect steps that failed on a previous run and should be skipped.
        self._cached_hot_path_no_match: set = set()
        self._prepare_runtime_config()
        if setup_driver:
            self._setup_driver()

        # Sub-modules that work with any backend (Appium or u2)
        _device = self.d or self.driver
        self._attendee_sel = AttendeeSelector(_device, self.config)
        self._attendee_sel.set_bot(self)
        self._price_sel = PriceSelector(_device, self.config, probe=None)
        self._price_sel.set_bot(self)
        self._navigator = EventNavigator(_device, self.config, probe=None)
        self._navigator.set_bot(self)

        # Sub-modules for u2-optimized operations
        if self.d is not None:
            self._page_probe = PageProbe(self.d, self.config)
            self._page_probe.set_bot(self)
            self._guard = BuyButtonGuard(self.d)
            self._pipeline = FastPipeline(
                self.d, self.config, self._page_probe, self._guard
            )
            self._pipeline.set_bot(self)
            # Share coordinate cache with pipeline
            self._pipeline._cached_coords = self._cached_hot_path_coords
            self._pipeline._cached_no_match = self._cached_hot_path_no_match
            # Update sub-modules with real probe now that it exists
            self._navigator._probe = self._page_probe
            self._recovery = RecoveryHelper(self.d, self._page_probe, self._navigator)
            self._price_sel._probe = self._page_probe

    def _ensure_pipeline(self):
        """Lazily create the FastPipeline if not yet initialised (e.g. in tests)."""
        if hasattr(self, "_pipeline"):
            return
        device = self.d or self.driver
        probe = getattr(self, "_page_probe", None)
        guard = getattr(self, "_guard", None)
        self._pipeline = FastPipeline(device, self.config, probe, guard)
        self._pipeline.set_bot(self)
        self._pipeline._cached_coords = self._cached_hot_path_coords
        self._pipeline._cached_no_match = self._cached_hot_path_no_match

    _SOLD_OUT_RE = re.compile(r"缺货|售罄|无票")

    def _is_buy_button_sold_out(self):
        """Check if the buy button itself shows sold-out text.

        Only inspects the btn_buy_view element, not the whole page, to avoid
        false positives from other price tiers showing '缺货登记'.
        """
        xml_root = self._dump_hierarchy_xml()
        if xml_root is None:
            return False
        for node in xml_root.iter("node"):
            rid = node.get("resource-id", "")
            if rid in ("btn_buy_view", "cn.damai:id/btn_buy_view"):
                text = node.get("text", "")
                desc = node.get("content-desc", "")
                if self._SOLD_OUT_RE.search(text) or self._SOLD_OUT_RE.search(desc):
                    return True
                return False
        return False

    def _set_terminal_failure(self, reason):
        """Mark the current failure as non-retriable."""
        self._terminal_failure_reason = reason

    def _set_run_outcome(self, outcome):
        """Record the terminal outcome for the latest run attempt."""
        self._last_run_outcome = outcome

    def _execution_mode_key(self):
        """Return the current execution mode key."""
        if self.config.probe_only:
            return "probe"
        if not self.config.if_commit_order:
            return "validation"
        return "submit"

    def _execution_mode_label(self):
        """Return a short user-facing label for the current execution mode."""
        labels = {
            "probe": "安全探测",
            "validation": "开发验证",
            "submit": "正式抢票",
        }
        return labels[self._execution_mode_key()]

    def _execution_mode_description(self):
        """Return a user-facing description for the current execution mode."""
        descriptions = {
            "probe": "只检查目标演出页，不会点击“立即购票”",
            "validation": "会继续进入确认页并勾选观演人，但不会点击“立即提交”；这是开发调试路径",
            "submit": "会尝试提交订单",
        }
        return descriptions[self._execution_mode_key()]

    def _log_execution_mode(self):
        """Emit a clear log that tells the user what this run will actually do."""
        logger.info(
            f"开始执行{self._execution_mode_label()}：{self._execution_mode_description()}"
        )

    def _log_success_outcome(self, retry_prefix=""):
        """Emit a success log message that matches the actual run outcome."""
        prefix = f"{retry_prefix}" if retry_prefix else ""
        outcome_messages = {
            "probe_ready": "探测成功：已到目标演出页，购票控件已就绪",
            "validation_ready": "开发验证成功：已到订单确认页，未提交订单",
            "order_submitted": "抢票成功：已提交订单",
            "order_pending_payment": "抢票成功：检测到未支付订单，请立即前往支付完成下单",
            "order_flow_completed": "抢票流程完成：已执行提交，等待后续结果确认",
        }
        logger.info(
            f"{prefix}{outcome_messages.get(self._last_run_outcome, '本轮执行成功')}"
        )

    @contextmanager
    def _timed_step(self, step_name, manual_baseline_seconds=None):
        """Record and log per-step latency for discovery hot path."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            faster_than_manual = (
                True
                if manual_baseline_seconds is None
                else elapsed <= manual_baseline_seconds
            )
            self._last_discovery_step_timings.append(
                {
                    "step": step_name,
                    "seconds": round(elapsed, 3),
                    "manual_baseline_seconds": manual_baseline_seconds,
                    "faster_than_manual": faster_than_manual,
                }
            )
            if manual_baseline_seconds is None:
                logger.info(f"步骤耗时[{step_name}] {elapsed:.2f}s")
            else:
                relation = "快于手动基线" if faster_than_manual else "慢于手动基线"
                log_fn = logger.info if faster_than_manual else logger.warning
                log_fn(
                    f"步骤耗时[{step_name}] {elapsed:.2f}s（手动基线 {manual_baseline_seconds:.2f}s，{relation}）"
                )

    def _prepare_runtime_config(self):
        """Pre-flight config checks before creating the driver session."""
        pass

    def _setup_driver(self):
        """初始化 uiautomator2 直连驱动。"""
        import uiautomator2 as u2

        serial = getattr(self.config, "serial", None) or None
        self.d = u2.connect(serial)
        try:
            self.d.settings["wait_timeout"] = 0
            self.d.settings["operation_delay"] = (0, 0)
        except Exception:
            # 测试桩或精简驱动对象可能不支持 dict-style settings
            pass
        should_start_app = True
        try:
            current_app = self.d.app_current()
            if (
                isinstance(current_app, dict)
                and current_app.get("package") == self.config.app_package
            ):
                should_start_app = False
        except Exception:
            should_start_app = True

        if should_start_app:
            self.d.app_start(
                self.config.app_package,
                activity=self.config.app_activity,
                stop=False,
            )
        self.driver = self.d
        self.wait = None

    # Core element operations, click operations, element inspection, and selector
    # utilities are inherited from UIPrimitives (mobile/ui_primitives.py).

    def wait_for_page_state(self, expected_states, timeout=5, poll_interval=0.2):
        """轮询等待页面进入指定状态，返回最后一次探测结果。"""
        deadline = time.time() + timeout
        last_probe = None

        while time.time() < deadline:
            last_probe = self.probe_current_page(fast=True)
            if last_probe["state"] in expected_states:
                return last_probe
            time.sleep(poll_interval)

        return last_probe if last_probe is not None else self.probe_current_page()

    def _wait_for_purchase_entry_result(
        self, timeout=1.2, poll_interval=0.04, fallback_probe_on_timeout=True
    ):
        """Wait for the detail-page CTA to open either sku or confirm page."""
        if self.config.rush_mode:
            # 极速模式：每次轮询只用 1 个 ID 选择器（~60ms/轮），替代 4 个选择器（~240ms/轮）。
            # 先检查 sku（更常见的转换目标），再检查确认页 checkbox。
            skip_reservation_check = not self.config.if_commit_order
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._has_element(By.ID, "cn.damai:id/layout_sku"):
                    return {
                        "state": "sku_page",
                        "price_container": True,
                        "reservation_mode": False
                        if skip_reservation_check
                        else self.is_reservation_sku_mode(),
                    }
                if self._has_element(By.ID, "cn.damai:id/checkbox"):
                    return {"state": "order_confirm_page", "submit_button": True}
                time.sleep(poll_interval)
            if fallback_probe_on_timeout:
                return self.probe_current_page()
            return None

        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]
        sku_selectors = [
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
            (By.ID, "cn.damai:id/sku_contanier"),
            (By.ID, "cn.damai:id/layout_price"),
            (By.ID, "cn.damai:id/tv_price_name"),
        ]

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._has_any_element(submit_selectors):
                return {"state": "order_confirm_page", "submit_button": True}
            if self._has_any_element(sku_selectors):
                return {
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": self.is_reservation_sku_mode(),
                }
            time.sleep(poll_interval)

        if fallback_probe_on_timeout:
            return self.probe_current_page()
        return None

    def _wait_for_submit_ready(self, timeout=1.6, poll_interval=0.04):
        """Wait until the confirm-page submit button appears."""
        if self.config.rush_mode:
            # 极速模式：单 ID 选择器轮询（~60ms/轮 vs 3选择器 ~180ms/轮）。
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._has_element(By.ID, "cn.damai:id/checkbox"):
                    return True
                time.sleep(poll_interval)
            return False

        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._has_any_element(submit_selectors):
                return True
            time.sleep(poll_interval)

        return False

    def _click_sku_buy_button_element(self, burst_count=1):
        """Fallback to an element click for Damai's custom SKU buy button."""
        try:
            buy_button = self._find(By.ID, "cn.damai:id/btn_buy_view")
        except Exception:
            return False

        click_action = getattr(buy_button, "click", None)
        if not callable(click_action):
            return False

        for attempt in range(max(1, int(burst_count))):
            try:
                click_action()
            except Exception:
                if attempt == 0:
                    return False
            if attempt < burst_count - 1:
                time.sleep(0.03)
        return True

    def _attendee_required_count_on_confirm_page(self):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_required_count_on_confirm_page()
        logger.warning("AttendeeSelector 未初始化")
        return max(1, len(self.config.users or []))

    def _attendee_checkbox_elements(self):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_checkbox_elements()
        return []

    @staticmethod
    def _is_checkbox_selected(checkbox):
        return DamaiBot._is_checked(checkbox)

    def _attendee_selected_count(
        self, checkbox_elements=None, use_source_fallback=True
    ):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_selected_count(
                checkbox_elements, use_source_fallback
            )
        return 0

    def _click_attendee_checkbox(self, checkbox):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._click_attendee_checkbox(checkbox)
        return False

    def _click_attendee_checkbox_fast(self, checkbox):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._click_attendee_checkbox_fast(checkbox)
        return False

    def _select_attendee_checkbox_by_name(self, user_name):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._select_attendee_checkbox_by_name(user_name)
        return False

    def _ensure_attendees_selected_on_confirm_page(
        self, require_attendee_section=False
    ):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._ensure_attendees_selected_on_confirm_page(
                require_attendee_section
            )
        logger.warning("AttendeeSelector 未初始化")
        return False

    def _get_buy_button_coordinates(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_buy_button_coordinates(xml_root)
        return None

    def _get_price_option_coordinates_by_config_index(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_price_option_coordinates_by_config_index(
                xml_root
            )
        return None

    def _build_compound_price_text(self, container):
        if hasattr(self, "_price_sel"):
            return self._price_sel._build_compound_price_text(container)
        return ""

    def _price_option_text_from_descendants(self, texts):
        if hasattr(self, "_price_sel"):
            return self._price_sel._price_option_text_from_descendants(texts)
        return ""

    def _normalize_ocr_price_text(self, ocr_output):
        if hasattr(self, "_price_sel"):
            return self._price_sel._normalize_ocr_price_text(ocr_output)
        return ""

    def _ocr_price_text_from_card(self, screenshot_path, rect):
        if hasattr(self, "_price_sel"):
            return self._price_sel._ocr_price_text_from_card(screenshot_path, rect)
        return ""

    def _extract_price_digits(self, text):
        if hasattr(self, "_price_sel"):
            return self._price_sel._extract_price_digits(text)
        return None

    def _price_text_matches_target(self, text):
        if hasattr(self, "_price_sel"):
            return self._price_sel._price_text_matches_target(text)
        return False

    def _is_price_option_available(self, option):
        if hasattr(self, "_price_sel"):
            return self._price_sel._is_price_option_available(option)
        return True

    def _click_visible_price_option(self, card_index):
        if hasattr(self, "_price_sel"):
            return self._price_sel._click_visible_price_option(card_index)
        return False

    def _click_price_option_by_config_index(self, burst=False, coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._click_price_option_by_config_index(burst, coords)
        return False

    def _select_price_option_fast(self, cached_coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._select_price_option_fast(cached_coords)
        return None

    def _select_price_option(self, cached_coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._select_price_option(cached_coords)
        return False

    def _keyword_tokens(self):
        if hasattr(self, "_navigator"):
            return self._navigator._keyword_tokens()
        return []

    def _get_detail_title_text(self, xml_root=None):
        """Read title text from detail/sku pages."""
        if xml_root is not None and self._using_u2():
            title = self._xml_find_text_by_resource_id(xml_root, "cn.damai:id/title_tv")
            if title:
                return title
            parts = [
                self._xml_find_text_by_resource_id(xml_root, rid)
                for rid in (
                    "cn.damai:id/project_title_tv1",
                    "cn.damai:id/project_title_tv2",
                )
            ]
            return "".join(p.strip() for p in parts if p).strip()

        title = ""
        try:
            title = self._safe_element_text(self.driver, By.ID, "cn.damai:id/title_tv")
        except Exception:
            title = ""

        if title:
            return title

        title_parts = []
        for resource_id in (
            "cn.damai:id/project_title_tv1",
            "cn.damai:id/project_title_tv2",
        ):
            part = self._safe_element_text(self.driver, By.ID, resource_id)
            if part:
                title_parts.append(part.strip())

        return "".join(title_parts).strip()

    def _title_matches_target(self, title_text):
        if hasattr(self, "_navigator"):
            return self._navigator._title_matches_target(title_text)
        return False

    def _current_page_matches_target(self, page_probe):
        if hasattr(self, "_navigator"):
            return self._navigator._current_page_matches_target(page_probe)
        return False

    def _exit_non_target_event_context(
        self, page_probe, max_back_steps=4, back_delay=0.5
    ):
        """Back out from a non-target detail/sku page until search/homepage is reachable."""
        current_probe = page_probe

        for _ in range(max_back_steps):
            if current_probe["state"] not in {"detail_page", "sku_page"}:
                return current_probe
            if self._current_page_matches_target(current_probe):
                return current_probe

            if not self._press_keycode_safe(4, context="退出非目标演出页"):
                break
            time.sleep(back_delay)
            self.dismiss_startup_popups()
            current_probe = self.probe_current_page()

        return current_probe

    def _recover_to_navigation_start(self, page_probe, max_back_steps=3):
        """Recover to a navigable page such as homepage or search page."""
        navigable_states = {"homepage", "search_page", "detail_page", "sku_page"}
        current_probe = page_probe
        if current_probe["state"] in navigable_states:
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="恢复导航起点"):
                break
            time.sleep(0.4)
            current_probe = self.probe_current_page()
            if current_probe["state"] in navigable_states:
                return current_probe

        try:
            if not self._using_u2():
                self.driver.activate_app(self.config.app_package)
            else:
                self.d.app_start(self.config.app_package, stop=False)
            time.sleep(1)
        except Exception:
            pass

        return self.probe_current_page()

    def _recover_to_detail_page_for_local_retry(
        self, initial_probe=None, max_back_steps=8, back_delay=0.15
    ):
        """Recover locally to the current event detail/sku page without rebuilding the Appium session."""
        # Delegate to RecoveryHelper if available
        if hasattr(self, "_recovery") and initial_probe is None:
            result = self._recovery.recover_to_detail_page()
            if result["state"] in {"detail_page", "sku_page"}:
                return result
            # Fall through to existing logic if recovery failed

        # Original logic below (unchanged)
        current_probe = initial_probe or self.probe_current_page(fast=True)
        retryable_states = {"detail_page", "sku_page"}

        if current_probe["state"] in retryable_states and (
            not self.item_detail or self._current_page_matches_target(current_probe)
        ):
            return current_probe

        self.dismiss_startup_popups()
        current_probe = self.probe_current_page()
        if current_probe["state"] in retryable_states and (
            not self.item_detail or self._current_page_matches_target(current_probe)
        ):
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="本地快速回退"):
                break
            time.sleep(back_delay)
            # Use lightweight probe during back-navigation (skip popup
            # dismissal and full probe — saves ~2s per step).
            current_probe = self.probe_current_page(fast=True)
            if current_probe["state"] in retryable_states and (
                not self.item_detail or self._current_page_matches_target(current_probe)
            ):
                return current_probe

        # If we ended up on homepage, try forward navigation
        if current_probe["state"] in {"homepage"}:
            logger.info("回退到首页，尝试正向导航回详情页")
            self.navigate_to_target_event()
            current_probe = self.probe_current_page()

        return current_probe

    def _open_search_from_homepage(self):
        if hasattr(self, "_navigator"):
            return self._navigator._open_search_from_homepage()
        return False

    def _submit_search_keyword(self):
        if hasattr(self, "_navigator"):
            return self._navigator._submit_search_keyword()
        return False

    def _score_search_result(self, title_text, venue_text):
        if hasattr(self, "_navigator"):
            return self._navigator._score_search_result(title_text, venue_text)
        return -1

    def _scroll_search_results(self):
        if hasattr(self, "_navigator"):
            return self._navigator._scroll_search_results()

    def _open_target_from_search_results(
        self, max_scrolls=2, max_results=5, return_details=False
    ):
        if hasattr(self, "_navigator"):
            return self._navigator._open_target_from_search_results(
                max_scrolls, max_results, return_details
            )
        return {"opened": False, "search_results": []} if return_details else False

    def collect_search_results(self, max_scrolls=0, max_results=5):
        if hasattr(self, "_navigator"):
            return self._navigator.collect_search_results(max_scrolls, max_results)
        return []

    def navigate_to_target_event(self, initial_probe=None):
        """Navigate to the target event. Delegates to EventNavigator."""
        if hasattr(self, "_navigator") and self._navigator is not None:
            return self._navigator.navigate_to_target_event(initial_probe=initial_probe)
        return self._navigate_to_target_impl(initial_probe=initial_probe)

    def _navigate_to_target_impl(self, initial_probe=None):
        if hasattr(self, "_navigator"):
            return self._navigator._navigate_to_target_impl(initial_probe)
        return False

    def discover_target_event(
        self, keyword_candidates, initial_probe=None, search_scrolls=1, result_limit=5
    ):
        if hasattr(self, "_navigator"):
            return self._navigator.discover_target_event(
                keyword_candidates, initial_probe, search_scrolls, result_limit
            )
        return None

    def select_performance_date(self, timeout=1.0):
        """选择演出场次日期"""
        if not self.config.date:
            return

        date_selector = f'new UiSelector().textContains("{self.config.date}")'
        if self.ultra_fast_click(ANDROID_UIAUTOMATOR, date_selector, timeout=timeout):
            logger.info(f"选择场次日期: {self.config.date}")
        else:
            logger.debug(f"未找到日期 '{self.config.date}'，使用默认场次")

    def _select_city_from_detail_page(self, timeout=1.0):
        """Select the configured city on the detail page."""
        city_selectors = [
            (ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
            (
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{self.config.city}")',
            ),
            (By.XPATH, f'//*[@text="{self.config.city}"]'),
        ]
        return self.smart_wait_and_click(
            *city_selectors[0], city_selectors[1:], timeout=timeout
        )

    def _prepare_detail_page_hot_path(self):
        """Preselect detail-page filters before the sale opens so launch-time work is minimized."""
        page_probe = self.probe_current_page()
        if page_probe["state"] != "detail_page":
            return False

        prepared = False
        if self.config.date:
            self.select_performance_date()
            prepared = True

        if self.config.city and self._select_city_from_detail_page(timeout=0.6):
            logger.info(f"已预选城市: {self.config.city}")
            prepared = True

        return prepared

    def _rush_preselect_and_buy_via_xml(self):
        self._ensure_pipeline()
        return self._pipeline.rush_preselect_and_buy_via_xml()

    def _enter_purchase_flow_from_detail_page(self, prepared=False):
        """Open the purchase panel from the detail page with a low-latency hot path."""
        if self.config.rush_mode:
            self._dismiss_fast_blocking_dialogs()
        if not prepared:
            if self.config.rush_mode:
                # 极速模式冷路径：单次 XML dump 提取所有坐标（~0.3s），替代多次 _cached_tap（~3-4s）。
                # 热路径（有缓存）用 _cached_tap 直接点击缓存坐标（1次 HTTP/元素）。
                if self._using_u2() and not self._cached_hot_path_coords.get(
                    "detail_buy"
                ):
                    # Cold path: single XML dump for all detail page elements.
                    if self._rush_preselect_and_buy_via_xml():
                        next_probe = self._wait_for_purchase_entry_result(
                            timeout=6.0, poll_interval=0.03
                        )
                        if next_probe["state"] in {"sku_page", "order_confirm_page"}:
                            return next_probe
                else:
                    # Warm path: cached coords for date/city/buy.
                    if (
                        self.config.date
                        and "date" not in self._cached_hot_path_no_match
                    ):
                        _date_found = self._cached_tap(
                            "date",
                            ANDROID_UIAUTOMATOR,
                            f'new UiSelector().textContains("{self.config.date}")',
                            timeout=0.1,
                        )
                        if _date_found:
                            logger.info(f"极速模式预选日期: {self.config.date}")
                        elif "date" not in self._cached_hot_path_coords:
                            self._cached_hot_path_no_match.add("date")
                    if (
                        self.config.city
                        and "city" not in self._cached_hot_path_no_match
                    ):
                        _city_found = self._cached_tap(
                            "city",
                            ANDROID_UIAUTOMATOR,
                            f'new UiSelector().text("{self.config.city}")',
                            timeout=0.2,
                        )
                        if not _city_found:
                            _city_found = self._cached_tap(
                                "city",
                                ANDROID_UIAUTOMATOR,
                                f'new UiSelector().textContains("{self.config.city}")',
                                timeout=0.15,
                            )
                        if _city_found:
                            logger.info(f"极速模式预选城市: {self.config.city}")
                        elif "city" not in self._cached_hot_path_coords:
                            self._cached_hot_path_no_match.add("city")
                            logger.debug("极速模式未命中城市选择，继续抢占购票入口")
            else:
                self.select_performance_date()
                logger.info("选择城市...")
                if not self._select_city_from_detail_page(timeout=1.0):
                    logger.warning("城市选择失败")
                    return None

        if not self._cached_hot_path_coords.get("detail_buy"):
            logger.info("点击购票按钮...")
        if self.config.rush_mode:
            # 极速模式：_cached_tap 冷路径查找并缓存购票按钮坐标，热路径直接点击（1次HTTP）。
            # 点击一次后等足够长时间，避免重复点击重置 sku_page 加载。
            _buy_clicked = self._cached_tap(
                "detail_buy",
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                timeout=0.2,
            )
            if not _buy_clicked:
                # 文案集合源 SALE_READY_TEXTS（issue #29）+ 旧文案兜底
                _buy_clicked = self._cached_tap(
                    "detail_buy",
                    ANDROID_UIAUTOMATOR,
                    f'new UiSelector().textMatches("{_SALE_READY_TEXT_REGEX_OR}|.*购票.*|.*抢票.*|.*购买.*")',
                    timeout=0.25,
                )
            if _buy_clicked:
                next_probe = self._wait_for_purchase_entry_result(
                    timeout=6.0, poll_interval=0.03
                )
                if next_probe["state"] in {"sku_page", "order_confirm_page"}:
                    return next_probe

        # 文案集合源 SALE_READY_TEXTS（issue #29）+ 旧"预约/购买"兜底
        book_selectors = [
            (
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ),
            (
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textMatches("{_SALE_READY_TEXT_REGEX_OR}|.*预约.*|.*购买.*")',
            ),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
        ]
        if not self.smart_wait_and_click(
            *book_selectors[0], book_selectors[1:], timeout=0.8
        ):
            logger.warning("购票按钮点击失败")
            return None
        return self._wait_for_purchase_entry_result(timeout=5, poll_interval=0.08)

    def check_session_valid(self):
        """检查大麦 App 登录状态是否有效"""
        activity = self._get_current_activity()
        if "LoginActivity" in activity or "SignActivity" in activity:
            logger.error("检测到登录页面，大麦 App 登录已过期，请重新登录")
            return False

        login_prompt_selectors = [
            'new UiSelector().textContains("请先登录")',
            'new UiSelector().textContains("登录/注册")',
        ]
        for selector in login_prompt_selectors:
            if self._has_element(ANDROID_UIAUTOMATOR, selector):
                logger.error("检测到登录提示，请重新登录大麦 App")
                return False

        return True

    def _purchase_bar_text_ready(self):
        """Inspect the detail-page CTA text and decide whether sale has opened."""
        try:
            purchase_bar = self._find(
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            )
        except Exception:
            return False

        texts = [
            text.strip()
            for text in self._collect_descendant_texts(purchase_bar)
            if text.strip()
        ]
        merged = normalize_text("".join(texts))
        if not merged:
            return False
        if any(normalize_text(keyword) in merged for keyword in _CTA_BLOCKED_KEYWORDS):
            return False
        return any(normalize_text(keyword) in merged for keyword in _CTA_READY_KEYWORDS)

    def _is_sale_ready(self):
        """Check whether the current UI state is actionable for purchase.

        Sale-readiness is detected via :data:`SALE_READY_TEXTS` (开售文案) plus
        a small set of post-confirm CTAs ("立即购买" / "选座购买" / "提交订单" 等)
        that may appear once the user has already entered the SKU/order page.
        """
        ready_texts = (
            *SALE_READY_TEXTS,
            "立即购买",
            "选座购买",
            "立即提交",
            "提交订单",
        )
        for text in ready_texts:
            if self._has_element(
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{text}")',
            ):
                self._last_sale_ready_text = text
                return True

        if self._has_element(
            By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
        ):
            return not self.is_reservation_sku_mode()

        if self._has_element(
            By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        ):
            return self._purchase_bar_text_ready()

        return False

    def wait_for_sale_start(self):
        """等待开售时间，在开售前 countdown_lead_ms 毫秒开始轮询。"""
        if self.config.sell_start_time is None:
            if self.config.wait_cta_ready_timeout_ms > 0:
                logger.info("未配置 sell_start_time，已跳过 CTA 等待，直接开始执行")
            return

        _tz_shanghai = timezone(timedelta(hours=8))
        sell_time = datetime.fromisoformat(self.config.sell_start_time)
        # Ensure timezone-aware
        if sell_time.tzinfo is None:
            sell_time = sell_time.replace(tzinfo=_tz_shanghai)

        now = datetime.now(tz=_tz_shanghai)
        if now >= sell_time:
            logger.info("开售时间已过，跳过等待")
            return

        lead_delta = timedelta(milliseconds=self.config.countdown_lead_ms)
        poll_start = sell_time - lead_delta
        sleep_seconds = (poll_start - now).total_seconds()

        if sleep_seconds > 0:
            logger.info(
                f"等待开售，将在 {self.config.sell_start_time} 前 "
                f"{self.config.countdown_lead_ms}ms 开始轮询"
            )
            time.sleep(sleep_seconds)

        # Use BuyButtonGuard for precise button-text monitoring
        if hasattr(self, "_guard") and self._guard.wait_until_safe(
            timeout_s=8.0, poll_ms=50
        ):
            logger.info("BuyButtonGuard 检测到可购买按钮")
            return

        # Tight polling loop with multiple purchase signals until the page becomes actionable.
        deadline = sell_time + timedelta(seconds=8)
        polls = 0
        while datetime.now(tz=_tz_shanghai) < deadline:
            polls += 1
            if self._is_sale_ready():
                cta_text = getattr(self, "_last_sale_ready_text", None) or "?"
                logger.info(f"CTA_MATCH: text={cta_text!r} polls={polls} (开售已开始)")
                return
            time.sleep(0.08)

        logger.warning(f"等待开售超时（轮询 {polls} 次），继续执行")

    def verify_order_result(self, timeout=5):
        """验证订单提交结果"""
        start = time.time()
        payment_text_selectors = [
            'new UiSelector().textContains("立即支付")',
            'new UiSelector().textContains("去支付")',
            'new UiSelector().textContains("确认支付")',
            'new UiSelector().textContains("支付剩余时间")',
            'new UiSelector().textContains("收银台")',
        ]

        while time.time() - start < timeout:
            activity = self._get_current_activity()

            # Success: payment page
            if any(kw in activity for kw in ("Pay", "Cashier", "AlipayClient")):
                logger.info("订单提交成功，已跳转支付页面")
                return "success"

            # Check page text for various outcomes
            if self._has_element(
                ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("未支付")'
            ):
                logger.warning("已有未支付订单")
                return "existing_order"
            if (
                self._has_element(
                    ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("已售罄")'
                )
                or self._has_element(
                    ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("库存不足")'
                )
                or self._has_element(
                    ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("暂时无票")'
                )
            ):
                logger.warning("票已售罄")
                return "sold_out"
            if self._has_element(
                ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("滑块")'
            ) or self._has_element(
                ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("验证")'
            ):
                logger.warning("触发验证码")
                return "captcha"
            if any(
                self._has_element(ANDROID_UIAUTOMATOR, selector)
                for selector in payment_text_selectors
            ):
                submit_still_visible = self._has_element(
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().text("立即提交")',
                )
                confirm_title_visible = self._has_element(
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textContains("确认购买")',
                )
                if submit_still_visible or confirm_title_visible:
                    logger.warning(
                        "检测到支付相关文本，但仍在确认购买页，暂不判定提交成功"
                    )
                else:
                    logger.info("订单提交成功，检测到支付页关键控件")
                    return "success"

            time.sleep(0.3)

        logger.warning("订单验证超时")
        return "timeout"

    def _submit_order_fast(self, submit_selectors):
        """Attempt submit quickly and retry within the confirm page before falling back."""
        attempt_count = 3
        has_submitted_once = False
        for attempt in range(attempt_count):
            submit_success = False
            if self.ultra_fast_click(*submit_selectors[0], timeout=0.35):
                submit_success = True
            elif self.ultra_fast_click(*submit_selectors[1], timeout=0.35):
                submit_success = True
            elif self.smart_wait_and_click(
                *submit_selectors[0], submit_selectors[1:], timeout=0.6
            ):
                submit_success = True

            if not submit_success:
                logger.warning("提交订单按钮未找到，请手动确认订单状态")
                if has_submitted_once:
                    followup_result = self.verify_order_result(timeout=2)
                    if followup_result != "timeout":
                        return followup_result
                return "timeout"

            has_submitted_once = True
            verify_timeout = 1.2 if attempt < attempt_count - 1 else 3
            result = self.verify_order_result(timeout=verify_timeout)
            if result != "timeout":
                return result
            logger.warning(
                f"提交后暂未确认结果，快速重试提交 {attempt + 2}/{attempt_count}"
            )

        return "timeout"

    def _fast_retry_from_current_state(self):
        """根据当前页面状态进行快速重试。"""
        page_probe = self.probe_current_page()
        state = page_probe["state"]

        if state in ("detail_page", "sku_page"):
            if self.item_detail and not self._current_page_matches_target(page_probe):
                if not self.config.auto_navigate:
                    logger.warning(
                        "当前详情页不是目标演出，手动起跑模式下停止本地快速重试"
                    )
                    return False
                logger.info("当前详情页不是目标演出，转为自动导航")
                return (
                    self.navigate_to_target_event(page_probe)
                    and self.run_ticket_grabbing()
                )
            return self.run_ticket_grabbing()
        elif state == "order_confirm_page":
            if not self.config.if_commit_order:
                if not self._ensure_attendees_selected_on_confirm_page():
                    self._set_terminal_failure("attendee_unselected")
                    logger.error("开发验证模式下观演人未选择完整，已停止")
                    return False
                submit_selectors = [
                    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                    (
                        ANDROID_UIAUTOMATOR,
                        'new UiSelector().textMatches(".*提交.*|.*确认.*")',
                    ),
                    (By.XPATH, '//*[contains(@text,"提交")]'),
                ]
                return self.smart_wait_for_element(
                    *submit_selectors[0], submit_selectors[1:]
                )
            if not self._ensure_attendees_selected_on_confirm_page():
                self._set_terminal_failure("attendee_unselected")
                return False
            submit_selectors = [
                (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textMatches(".*提交.*|.*确认.*")',
                ),
                (By.XPATH, '//*[contains(@text,"提交")]'),
            ]
            return self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:])
        elif state == "pending_order_dialog":
            self._set_run_outcome("order_pending_payment")
            logger.info(
                "检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付"
            )
            return True
        else:
            if self.config.auto_navigate:
                return (
                    self.navigate_to_target_event(page_probe)
                    and self.run_ticket_grabbing()
                )
            recovered_probe = self._recover_to_detail_page_for_local_retry(page_probe)
            if recovered_probe["state"] not in {"detail_page", "sku_page"}:
                logger.warning(
                    f"本地快速回退后仍未回到演出页，当前状态: {recovered_probe['state']}"
                )
                return False
            return self.run_ticket_grabbing()

    def dismiss_startup_popups(self):
        """处理首启的一次性系统/应用弹窗。"""
        dismissed = False

        popup_clicks = [
            (By.ID, "android:id/ok"),  # Android 全屏提示
            (By.ID, "cn.damai:id/id_boot_action_agree"),  # 大麦隐私协议
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),  # 开启消息通知
            (
                By.ID,
                "cn.damai:id/damai_theme_dialog_close_layout",
            ),  # 新版升级提示关闭按钮
            (By.ID, "cn.damai:id/damai_theme_dialog_close_btn"),  # 新版升级提示关闭图标
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().text("Cancel")',
            ),  # Add to home screen
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
        ]

        for by, value in popup_clicks:
            if self._has_element(by, value):
                if self.ultra_fast_click(by, value):
                    dismissed = True
                    time.sleep(0.3)

        return dismissed

    def _dismiss_fast_blocking_dialogs(self):
        """Dismiss lightweight blocking dialogs without the full startup scan."""
        dismissed = False
        dialog_clicks = [
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),
            (By.ID, "cn.damai:id/damai_theme_dialog_close_btn"),
            (By.ID, "cn.damai:id/damai_theme_dialog_close_layout"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
        ]

        for by, value in dialog_clicks:
            if not self._has_element(by, value):
                continue
            if self.ultra_fast_click(by, value, timeout=0.15):
                dismissed = True
                time.sleep(0.05)

        return dismissed

    def is_reservation_sku_mode(self):
        """识别当前 SKU 页是否仍处于抢票预约流，而非正式下单流。"""
        reservation_indicators = [
            (By.ID, "cn.damai:id/btn_cancel_reservation"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看场次")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看票档")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("提交抢票预约")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("已预约")'),
        ]

        return any(self._has_element(by, value) for by, value in reservation_indicators)

    def get_visible_date_options(self, xml_root=None):
        """Return visible date options on the current page."""
        if xml_root is not None and self._using_u2():
            dates = []
            seen = set()
            for node in xml_root.iter("node"):
                if node.get("resource-id") == "cn.damai:id/tv_date":
                    text = (node.get("text") or "").strip()
                    if text and text not in seen:
                        dates.append(text)
                        seen.add(text)
            return dates

        dates = []
        seen = set()
        for element in self._find_all(By.ID, "cn.damai:id/tv_date"):
            text = self._read_element_text(element).strip()
            if not text or text in seen:
                continue
            dates.append(text)
            seen.add(text)
        return dates

    def get_visible_price_options(self, allow_ocr=True, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel.get_visible_price_options(
                allow_ocr=allow_ocr, xml_root=xml_root
            )
        return []

    def _get_visible_price_options_from_xml(self, xml_root, allow_ocr=True):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_visible_price_options_from_xml(
                xml_root, allow_ocr=allow_ocr
            )
        return []

    def _get_detail_venue_text(self, xml_root=None):
        """Read venue text from the detail page if present."""
        if xml_root is not None and self._using_u2():
            for resource_id in (
                "cn.damai:id/venue_name_0",
                "cn.damai:id/tv_project_venueName",
            ):
                value = self._xml_find_text_by_resource_id(xml_root, resource_id)
                if value:
                    return value.strip()
            return ""

        for resource_id in (
            "cn.damai:id/venue_name_0",
            "cn.damai:id/tv_project_venueName",
        ):
            value = self._safe_element_text(self.driver, By.ID, resource_id)
            if value:
                return value.strip()
        return ""

    def ensure_sku_page_for_inspection(self, page_probe=None):
        """Safely enter the sku page so prompt-based flows can inspect dates and prices."""
        page_probe = page_probe or self.probe_current_page()
        if page_probe["state"] == "sku_page":
            return page_probe

        if page_probe["state"] != "detail_page":
            return page_probe

        book_selectors = [
            (
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ),
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")',
            ),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
        ]
        if not self.smart_wait_and_click(
            *book_selectors[0], book_selectors[1:], timeout=0.5
        ):
            return self.probe_current_page()

        return self._wait_for_purchase_entry_result(timeout=5, poll_interval=0.04)

    def inspect_current_target_event(self, page_probe=None):
        """Summarize the currently opened event for prompt-based confirmation."""
        page_probe = page_probe or self.probe_current_page()

        xml_root = None
        sku_probe = page_probe

        if page_probe["state"] == "detail_page":
            # Click buy immediately so sku_page starts loading before we do anything else.
            book_selectors = [
                (
                    By.ID,
                    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                ),
                (
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")',
                ),
                (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
            ]
            clicked = self.smart_wait_and_click(
                *book_selectors[0], book_selectors[1:], timeout=0.5
            )
            # Dump detail_page hierarchy while sku_page loads (~1.5s parallel time).
            xml_root = self._dump_hierarchy_xml()
            if clicked:
                sku_probe = self._wait_for_purchase_entry_result(
                    timeout=4.0, poll_interval=0.04
                )
            else:
                sku_probe = self.probe_current_page()
        elif page_probe["state"] != "sku_page":
            sku_probe = self.ensure_sku_page_for_inspection(page_probe)

        summary = {
            "state": sku_probe["state"],
            "title": self._get_detail_title_text(xml_root=xml_root),
            "venue": self._get_detail_venue_text(xml_root=xml_root),
            "dates": [],
            "price_options": [],
            "reservation_mode": sku_probe.get("reservation_mode", False),
        }

        if sku_probe["state"] == "sku_page":
            # Re-dump for sku_page content (different screen from detail_page).
            xml_root = self._dump_hierarchy_xml()
            if not summary["title"]:
                summary["title"] = self._get_detail_title_text(xml_root=xml_root)
            if not summary["venue"]:
                summary["venue"] = self._get_detail_venue_text(xml_root=xml_root)
            summary["reservation_mode"] = sku_probe.get("reservation_mode", False)
            summary["dates"] = self.get_visible_date_options(xml_root=xml_root)
            summary["price_options"] = self.get_visible_price_options(xml_root=xml_root)

        return summary

    def probe_current_page(self, fast=False):
        """探测当前页面状态和关键控件可见性。"""
        # Delegate to PageProbe when available (u2 backend)
        if hasattr(self, "_page_probe"):
            result = self._page_probe.probe_current_page(fast=fast)
            if result["state"] != "unknown" or fast:
                logger.info(f"当前页面状态: {result['state']}")
                return result

        # Fallback: element-based probe using _has_element
        return self._probe_current_page_element_based()

    def _probe_current_page_element_based(self):
        """Full probe using _has_element calls (fallback when PageProbe unavailable)."""
        state = "unknown"
        current_activity = self._get_current_activity()
        purchase_button = self._has_element(
            By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        )
        detail_price_summary = self._has_element(
            By.ID, "cn.damai:id/project_detail_price_layout"
        )
        sku_price_container = (
            self._has_element(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
            or self._has_element(By.ID, "cn.damai:id/layout_price")
            or self._has_element(By.ID, "cn.damai:id/tv_price_name")
        )
        quantity_picker = self._has_element(By.ID, "layout_num")
        submit_button = self._has_element(By.ID, "cn.damai:id/checkbox")
        pending_order_dialog = self._has_element(
            By.ID, "cn.damai:id/damai_theme_dialog_confirm_btn"
        )
        reservation_mode = False

        if self._has_element(By.ID, "cn.damai:id/id_boot_action_agree"):
            state = "consent_dialog"
        elif pending_order_dialog:
            state = "pending_order_dialog"
        elif (
            "MainActivity" in current_activity
            or self._has_element(By.ID, "cn.damai:id/homepage_header_search")
            or self._has_element(
                By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"
            )
        ):
            state = "homepage"
        elif "SearchActivity" in current_activity or self._has_element(
            By.ID, "cn.damai:id/header_search_v2_input"
        ):
            state = "search_page"
        elif submit_button:
            state = "order_confirm_page"
        elif (
            "NcovSkuActivity" in current_activity
            or self._has_element(By.ID, "cn.damai:id/layout_sku")
            or self._has_element(By.ID, "cn.damai:id/sku_contanier")
        ):
            state = "sku_page"
        elif (
            "ProjectDetailActivity" in current_activity
            or purchase_button
            or detail_price_summary
            or self._has_element(By.ID, "cn.damai:id/title_tv")
        ):
            state = "detail_page"

        if state == "sku_page":
            reservation_mode = self.is_reservation_sku_mode()

        result = {
            "state": state,
            "purchase_button": purchase_button,
            "price_container": sku_price_container or detail_price_summary,
            "quantity_picker": quantity_picker,
            "submit_button": submit_button,
            "reservation_mode": reservation_mode,
            "pending_order_dialog": pending_order_dialog,
        }

        logger.info(f"当前页面状态: {result['state']}")
        if current_activity:
            logger.debug(f"当前 Activity: {current_activity}")
        logger.debug(
            "探测结果: "
            f"purchase_button={result['purchase_button']}, "
            f"price_container={result['price_container']}, "
            f"quantity_picker={result['quantity_picker']}, "
            f"submit_button={result['submit_button']}, "
            f"reservation_mode={result['reservation_mode']}"
        )

        return result

    def _has_warm_pipeline_coords(self):
        """Check if all coordinates required for the blind pipeline are cached."""
        if hasattr(self, "_pipeline"):
            return self._pipeline.has_warm_coords()
        c = self._cached_hot_path_coords
        return all(
            [
                c.get("detail_buy"),
                c.get("price"),
                c.get("sku_buy"),
                c.get("attendee_checkboxes"),
            ]
        )

    def _run_cold_validation_pipeline(self, start_time):
        self._ensure_pipeline()
        return self._pipeline.run_cold_validation(start_time)

    def _cold_pipeline_finish_confirm(self, start_time):
        self._ensure_pipeline()
        return self._pipeline._finish_confirm(start_time)

    def _run_warm_validation_pipeline(self, start_time):
        self._ensure_pipeline()
        return self._pipeline.run_warm_validation(start_time)

    def run_ticket_grabbing(self, initial_page_probe=None):
        """执行抢票主流程"""
        try:
            start_time = time.time()
            self._terminal_failure_reason = None
            self._last_run_outcome = None
            self._log_execution_mode()
            page_probe = initial_page_probe or self.probe_current_page(fast=True)
            fast_validation_hot_path = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and initial_page_probe is not None
                and page_probe["state"] in {"detail_page", "sku_page"}
            )
            if fast_validation_hot_path:
                logger.info(
                    "开发验证极速路径：跳过启动弹窗与登录探测，直接执行抢票热路径"
                )
                if page_probe["state"] == "detail_page":
                    if self._has_warm_pipeline_coords():
                        # Warm pipeline: blind shell clicks + concurrent polling.
                        pipeline_result = self._run_warm_validation_pipeline(start_time)
                    elif self._using_u2():
                        # Cold pipeline: XML dump → shell batch → concurrent polling.
                        pipeline_result = self._run_cold_validation_pipeline(start_time)
                    else:
                        pipeline_result = None
                    if pipeline_result is True:
                        return True
                    if pipeline_result is False:
                        return False
                    # pipeline_result is None → fall through to normal flow.
                    # Refresh page_probe since the pipeline may have advanced
                    # the app state (e.g. from detail_page to sku_page).
                    page_probe = self.probe_current_page(fast=True)
            else:
                self.dismiss_startup_popups()
                if not self.check_session_valid():
                    self._set_terminal_failure("session_invalid")
                    return False

            if page_probe["state"] == "pending_order_dialog":
                self._set_run_outcome("order_pending_payment")
                logger.info(
                    "检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付"
                )
                return True

            if page_probe["state"] not in {"detail_page", "sku_page"} or (
                self.item_detail and not self._current_page_matches_target(page_probe)
            ):
                if self.config.auto_navigate:
                    logger.info("当前不在目标演出页，尝试自动导航")
                    if not self.navigate_to_target_event(page_probe):
                        return False
                    page_probe = self.probe_current_page()
                    if page_probe["state"] == "pending_order_dialog":
                        self._set_run_outcome("order_pending_payment")
                        logger.info(
                            "检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付"
                        )
                        return True
                else:
                    logger.warning("当前不在演出详情页，请先手动打开目标演出详情页")
                    return False

            if self.config.probe_only:
                detail_ready = (
                    page_probe["state"] == "detail_page"
                    and page_probe["purchase_button"]
                    and page_probe["price_container"]
                )
                sku_ready = (
                    page_probe["state"] == "sku_page" and page_probe["price_container"]
                )

                if detail_ready or sku_ready:
                    self._set_run_outcome("probe_ready")
                    logger.info(
                        "probe_only 模式: 详情页关键控件已就绪，停止在购票点击前"
                    )
                    end_time = time.time()
                    logger.info(f"探测完成，耗时: {end_time - start_time:.2f}秒")
                    return True

                logger.warning("probe_only 模式: 详情页关键控件未就绪")
                return False

            prepared_detail_page = False
            should_prepare_detail_page = page_probe["state"] == "detail_page" and (
                self.config.sell_start_time is not None
                or (
                    self.config.wait_cta_ready_timeout_ms > 0
                    and not self.config.rush_mode
                )
            )
            if should_prepare_detail_page:
                prepared_detail_page = self._prepare_detail_page_hot_path()
                page_probe = self.probe_current_page()

            # Wait for sale start if configured
            self.wait_for_sale_start()
            # 极速模式 + 未配置开售时间时，wait_for_sale_start 为即时返回，无需再次探测页面状态。
            if self.config.sell_start_time is not None or not self.config.rush_mode:
                page_probe = self.probe_current_page()

            if page_probe["state"] == "detail_page":
                page_probe = self._enter_purchase_flow_from_detail_page(
                    prepared=prepared_detail_page
                )
                if page_probe is None:
                    return False
            else:
                logger.info("当前已在票档选择页，跳过城市和预约按钮步骤")
                # 新版 SKU 页会先展示日期卡片，需在此再次选择场次后才会展开票档列表。
                if self.config.rush_mode and not self.config.if_commit_order:
                    logger.info("开发验证极速路径：已在票档页，跳过场次切换")
                else:
                    self.select_performance_date(
                        timeout=0.35 if self.config.rush_mode else 1.0
                    )
                if self.config.rush_mode:
                    # 极速模式下避免一次完整重探测，减少热路径阻塞。
                    page_probe = dict(page_probe)
                    page_probe.setdefault("state", "sku_page")
                    if "reservation_mode" not in page_probe:
                        page_probe["reservation_mode"] = self.is_reservation_sku_mode()
                else:
                    page_probe = self.probe_current_page()

            if page_probe["state"] == "sku_page" and page_probe.get("reservation_mode"):
                logger.warning(
                    "检测到当前页面仍是“预售/抢票预约”流程，继续点击底部按钮只会提交预约，不会进入订单确认页"
                )
                self._set_terminal_failure("reservation_only")
                return False

            price_coords = (
                page_probe.get("price_coords") if self.config.rush_mode else None
            )
            buy_button_coords = (
                page_probe.get("buy_button_coords") if self.config.rush_mode else None
            )
            # 热路径优先从 bot 级缓存读取坐标，避免重复 XML dump（热重试节省 ~0.5s）。
            if self.config.rush_mode:
                if price_coords is None:
                    price_coords = self._cached_hot_path_coords.get("price")
                if buy_button_coords is None:
                    buy_button_coords = self._cached_hot_path_coords.get("sku_buy")
            if self.config.rush_mode and page_probe["state"] == "sku_page":
                if price_coords is None or buy_button_coords is None:
                    # Single hierarchy dump shared by both coordinate captures (~0.5s vs 4s+).
                    _sku_xml = self._dump_hierarchy_xml()
                    if price_coords is None:
                        price_coords = (
                            self._get_price_option_coordinates_by_config_index(
                                xml_root=_sku_xml
                            )
                        )
                    if buy_button_coords is None:
                        buy_button_coords = self._get_buy_button_coordinates(
                            xml_root=_sku_xml
                        )
            # 更新 bot 级缓存供后续热重试使用。
            if self.config.rush_mode:
                if price_coords is not None:
                    self._cached_hot_path_coords["price"] = price_coords
                if buy_button_coords is not None:
                    self._cached_hot_path_coords["sku_buy"] = buy_button_coords

            # 3. 票价选择 - 优化查找逻辑
            skip_price_selection = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and self._has_element(By.ID, "layout_num")
            )
            if skip_price_selection:
                logger.info("开发验证极速路径：检测到已处于可调数量状态，跳过票档点击")
            else:
                logger.info("选择票价...")
                if not self._select_price_option(cached_coords=price_coords):
                    return False

            # 4. 数量选择
            logger.info("选择数量...")
            if len(self.config.users) > 1 and self._has_element(By.ID, "layout_num"):
                clicks_needed = len(self.config.users) - 1
                if clicks_needed > 0:
                    try:
                        plus_button = self._find(By.ID, "img_jia")
                        for i in range(clicks_needed):
                            rect = self._element_rect(plus_button)
                            x = rect["x"] + rect["width"] // 2
                            y = rect["y"] + rect["height"] // 2
                            self._click_coordinates(x, y, duration=50)
                            time.sleep(0.02)
                    except Exception as e:
                        logger.error(f"快速点击加号失败: {e}")

            # if self.driver.find_elements(by=By.ID, value='layout_num') and self.config.users is not None:
            #     for i in range(len(self.config.users) - 1):
            #         self.driver.find_element(by=By.ID, value='img_jia').click()

            # 5. 确定购买 — brief wait for price selection to register.
            # Damai App ignores confirm clicks until btn_buy_view becomes clickable (price > 0).
            time.sleep(0.5)
            logger.info("确定购买...")
            submit_ready = False
            confirm_deadline = time.time() + (4.0 if self.config.rush_mode else 1.8)
            confirm_attempt = 0
            while time.time() < confirm_deadline and not submit_ready:
                confirm_attempt += 1
                if self.config.rush_mode and buy_button_coords:
                    if confirm_attempt == 1:
                        burst_count = 1 if not self.config.if_commit_order else 2
                        self._burst_click_coordinates(
                            *buy_button_coords,
                            count=burst_count,
                            interval_ms=25,
                            duration=25,
                        )
                    else:
                        burst_count = 1 if not self.config.if_commit_order else 2
                        if not self._click_sku_buy_button_element(
                            burst_count=burst_count
                        ):
                            self._burst_click_coordinates(
                                *buy_button_coords,
                                count=burst_count,
                                interval_ms=25,
                                duration=25,
                            )
                elif self.config.rush_mode:
                    # Element click may not work on Damai's custom btn_buy_view —
                    # use coordinate click from XML bounds as primary method.
                    _buy_coords = self._get_buy_button_coordinates()
                    if _buy_coords:
                        burst_count = 1 if not self.config.if_commit_order else 2
                        self._burst_click_coordinates(
                            *_buy_coords, count=burst_count, interval_ms=25, duration=25
                        )
                        buy_button_coords = _buy_coords  # cache for next retry
                    else:
                        burst_count = 1 if not self.config.if_commit_order else 2
                        if not self._click_sku_buy_button_element(
                            burst_count=burst_count
                        ):
                            try:
                                buy_button = self._find(
                                    By.ID, "cn.damai:id/btn_buy_view"
                                )
                                self._burst_click_element_center(
                                    buy_button,
                                    count=burst_count,
                                    interval_ms=25,
                                    duration=25,
                                )
                            except Exception:
                                self.ultra_fast_click(By.ID, "cn.damai:id/btn_buy_view")
                else:
                    if not self.ultra_fast_click(By.ID, "cn.damai:id/btn_buy_view"):
                        self.ultra_fast_click(
                            ANDROID_UIAUTOMATOR,
                            'new UiSelector().textMatches(".*确定.*|.*购买.*")',
                        )
                # Short wait then check if confirm page appeared
                remaining = confirm_deadline - time.time()
                check_timeout = min(1.0, max(0.1, remaining))
                submit_ready = self._wait_for_submit_ready(
                    timeout=check_timeout,
                    poll_interval=0.03 if self.config.rush_mode else 0.05,
                )
                if not submit_ready and confirm_attempt < 5:
                    logger.debug(f"确定按钮第 {confirm_attempt} 次点击未生效，重试...")
                # Every 3rd attempt, check if buy button shows sold-out text.
                if not submit_ready and confirm_attempt % 3 == 0:
                    if self._is_buy_button_sold_out():
                        logger.warning(
                            "购买按钮区域检测到缺货/售罄标识，该票档当前不可购买"
                        )
                        self._set_terminal_failure("sold_out")
                        return False
            if not submit_ready:
                if self.config.rush_mode and not self.config.if_commit_order:
                    logger.info(
                        "开发验证极速路径：确认页未完全就绪，跳过预选用户兜底，直接校验观演人区域"
                    )
                else:
                    # 6. 批量选择用户
                    logger.info("选择用户...")
                    user_clicks = [
                        (ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")')
                        for user in self.config.users
                    ]
                    user_timeout = 0.35 if self.config.rush_mode else 1.0
                    clicked_users = self.ultra_batch_click(
                        user_clicks, timeout=user_timeout
                    )
                    if clicked_users:
                        submit_ready = self._wait_for_submit_ready(
                            timeout=0.9 if self.config.rush_mode else 1.5,
                            poll_interval=0.03 if self.config.rush_mode else 0.05,
                        )

            if not submit_ready and not (
                self.config.rush_mode and not self.config.if_commit_order
            ):
                logger.warning("未进入订单确认页，请检查票档可用性或观演人配置")
                return False

            submit_selectors = [
                (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textMatches(".*提交.*|.*确认.*")',
                ),
                (By.XPATH, '//*[contains(@text,"提交")]'),
            ]
            if not self._ensure_attendees_selected_on_confirm_page(
                require_attendee_section=self.config.rush_mode
                and not self.config.if_commit_order
            ):
                self._set_terminal_failure("attendee_unselected")
                logger.error("订单提交前观演人未选择完整，已停止自动提交")
                return False

            if not self.config.if_commit_order:
                self._set_run_outcome("validation_ready")
                end_time = time.time()
                logger.info(
                    "if_commit_order=False，已完成观演人勾选，停止在“立即提交”前"
                )
                logger.info(
                    f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {end_time - start_time:.2f}秒"
                )
                return True

            # 7. 提交订单
            logger.info("提交订单...")
            result = self._submit_order_fast(submit_selectors)
            if result == "success":
                self._set_run_outcome("order_submitted")
                end_time = time.time()
                logger.info(f"抢票成功！耗时: {end_time - start_time:.2f}秒")
                return True
            if result == "existing_order":
                self._set_run_outcome("order_pending_payment")
                end_time = time.time()
                logger.info(
                    f"检测到未支付订单（已占单待支付），请立即前往订单页支付。耗时: {end_time - start_time:.2f}秒"
                )
                return True
            elif result in ("sold_out", "captcha"):
                return False
            # timeout/unknown — fail closed to avoid false positives and duplicate submissions
            self._set_terminal_failure("submit_unverified")
            end_time = time.time()
            logger.error(
                f"提交后未能确认成功状态（result={result}），"
                f"为避免重复下单已停止自动重试，请手动检查订单列表。耗时: {end_time - start_time:.2f}秒"
            )
            return False

        except Exception as e:
            logger.error(f"抢票过程发生错误: {e}")
            return False
        finally:
            time.sleep(0.05)

    def run_with_retry(self, max_retries=3, initial_page_probe=None):
        """带重试机制的抢票"""
        for attempt in range(max_retries):
            logger.info(f"第 {attempt + 1} 次尝试（{self._execution_mode_label()}）...")
            # Pass initial_page_probe only on the first attempt; retries must re-probe.
            probe = initial_page_probe if attempt == 0 else None
            if self.run_ticket_grabbing(initial_page_probe=probe):
                self._log_success_outcome()
                return True

            if self._terminal_failure_reason:
                logger.error(
                    f"检测到不可重试失败，停止后续重试: {self._terminal_failure_reason}"
                )
                break

            # Fast retry within same session
            for fast_attempt in range(self.config.fast_retry_count):
                logger.info(
                    f"快速重试 {fast_attempt + 1}/{self.config.fast_retry_count}"
                    f"（{self._execution_mode_label()}）..."
                )
                if fast_attempt > 0 and self.config.fast_retry_interval_ms > 0:
                    time.sleep(self.config.fast_retry_interval_ms / 1000)
                if self._fast_retry_from_current_state():
                    self._log_success_outcome("快速重试成功：")
                    return True
                if self._terminal_failure_reason:
                    logger.error(
                        f"快速重试遇到不可重试失败，停止后续重试: {self._terminal_failure_reason}"
                    )
                    break

            if self._terminal_failure_reason:
                break

            # Full driver recreation
            logger.warning(f"第 {attempt + 1} 次尝试及快速重试均失败")
            if attempt < max_retries - 1:
                if not self.config.auto_navigate:
                    logger.info("手动起跑模式，保留当前会话并继续本地重试")
                    continue
                logger.info("重建驱动后重试...")
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self._setup_driver()

        logger.error("所有尝试均失败")
        return False


# 使用示例
if __name__ == "__main__":
    bot = None
    try:
        bot = DamaiBot()
        bot.run_with_retry(max_retries=3)
    except (ValueError, RuntimeError) as exc:
        logger.error(str(exc))
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass
