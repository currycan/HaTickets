# -*- coding: UTF-8 -*-
"""Unit tests for mobile/damai_app.py — DamaiBot class."""

from itertools import chain, repeat
import time as _time_module
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import Mock, patch, call, PropertyMock

from mobile.ui_primitives import ANDROID_UIAUTOMATOR
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from mobile.damai_app import DamaiBot, logger as damai_logger
from mobile.ui_primitives import logger as ui_primitives_logger
from mobile.config import Config
from mobile.item_resolver import DamaiItemDetail


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_time_side_effect(start=0.0, thereafter=1.5):
    """Return a time.time() side_effect function that never exhausts.

    First call returns *start*.  Every subsequent call returns *thereafter*.
    This works for loops where the deadline is computed as ``start + delta``
    and *thereafter* exceeds that deadline so the loop exits immediately.

    For code paths that cross **multiple** ``while time.time() < deadline``
    loops (e.g. ultra_fast_click → confirm-purchase retry), use a
    *thereafter* value large enough to exceed the sum of all timeouts.
    """
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        return start if calls["n"] == 1 else thereafter

    return _fake


def _make_time_monotonic(start=0.0, step=0.5):
    """Return a time.time() side_effect that increases by *step* each call.

    Useful when production code creates multiple sequential deadline loops
    — each call returns ``start + n * step`` so every loop eventually
    exceeds its deadline.
    """
    state = {"t": start - step}

    def _fake():
        state["t"] += step
        return state["t"]

    return _fake


def _make_mock_element(x=100, y=200, width=50, height=40):
    """Helper: create a mock element with a .rect property."""
    el = Mock()
    el.rect = {"x": x, "y": y, "width": width, "height": height}
    el.id = "fake-element-id"
    return el


def _make_item_detail():
    return DamaiItemDetail(
        item_id="1016133935724",
        item_name="【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站",
        item_name_display="北京·2026张杰未·LIVE—「开往1982」演唱会-北京站",
        city_name="北京市",
        venue_name="国家体育场-鸟巢",
        venue_city_name="北京市",
        show_time="2026.03.29-04.19",
        price_range="380-1680",
        raw_data={},
    )


@pytest.fixture(autouse=True)
def _enable_logger_propagation():
    """Enable propagation on the damai_app and ui_primitives loggers so caplog can capture messages."""
    damai_logger.propagate = True
    ui_primitives_logger.propagate = True
    yield
    damai_logger.propagate = False
    ui_primitives_logger.propagate = False


def _make_absent_selector():
    """Return a mock u2 selector that reports element not found."""
    sel = Mock()
    sel.exists = Mock(return_value=False)
    sel.wait = Mock(return_value=False)
    sel.count = 0
    return sel


@pytest.fixture
def bot():
    """Create a DamaiBot with fully mocked u2 driver and config."""
    mock_driver = Mock()
    mock_driver.settings = {}
    mock_driver.shell = Mock()
    mock_driver.app_current = Mock(return_value={"package": "cn.damai"})
    mock_driver.update_settings = Mock()
    mock_driver.execute_script = Mock()
    mock_driver.find_element = Mock()
    mock_driver.find_elements = Mock(return_value=[])
    mock_driver.quit = Mock()
    mock_driver.current_activity = "ProjectDetailActivity"
    # u2 device callable: d(text=...) returns selector reporting "not found"
    mock_driver.side_effect = lambda **kwargs: _make_absent_selector()
    mock_driver.xpath = Mock(return_value=Mock(all=Mock(return_value=[])))

    mock_config = Config(
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="test",
        users=["UserA", "UserB"],
        city="深圳",
        date="12.06",
        price="799元",
        price_index=1,
        if_commit_order=True,
        probe_only=False,
    )

    with (
        patch("mobile.damai_app.Config.load_config", return_value=mock_config),
        patch("uiautomator2.connect", return_value=mock_driver),
    ):
        bot = DamaiBot()
    return bot


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_init_accepts_injected_config(self):
        mock_driver = Mock()
        mock_driver.settings = {}
        mock_driver.app_current = Mock(return_value={"package": "cn.damai"})

        injected_config = Config(
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="张杰 演唱会",
            users=["UserA"],
            city="北京",
            date="04.06",
            price="1280元",
            price_index=6,
            if_commit_order=False,
            probe_only=True,
        )

        with (
            patch("uiautomator2.connect", return_value=mock_driver),
            patch("mobile.damai_app.Config.load_config") as load_config,
        ):
            bot = DamaiBot(config=injected_config)

        assert bot.config is injected_config
        load_config.assert_not_called()

    def test_init_loads_config_and_driver(self, bot):
        """Config is loaded and driver is created during __init__."""
        assert bot.config is not None
        assert bot.config.city == "深圳"
        assert bot.config.users == ["UserA", "UserB"]
        assert bot.driver is not None

    def test_setup_driver_connects_u2(self, bot):
        """_setup_driver connects via u2 and sets self.driver and self.d."""
        assert bot.driver is not None
        assert bot.d is not None
        assert bot.d is bot.driver


# ---------------------------------------------------------------------------
# u2 backend adapters
# ---------------------------------------------------------------------------


class TestU2BackendAdapters:
    def test_setup_driver_u2_calls_connect_and_app_start(self):
        mock_u2_driver = Mock()
        mock_u2_driver.settings = {}
        mock_u2_driver.app_start = Mock()
        mock_u2_driver.app_current = Mock(return_value={"package": "other.app"})

        cfg = Config(
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=False,
            probe_only=True,
        )

        with patch("uiautomator2.connect", return_value=mock_u2_driver) as connect:
            bot = DamaiBot(config=cfg)

        connect.assert_called_once_with(None)
        mock_u2_driver.app_start.assert_called_once_with(
            "cn.damai",
            activity=".launcher.splash.SplashMainActivity",
            stop=False,
        )
        assert bot.driver is mock_u2_driver
        assert bot.d is mock_u2_driver

    def test_click_coordinates_u2_uses_click(self):
        cfg = Config(
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=False,
            probe_only=True,
        )
        bot = DamaiBot(config=cfg, setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d

        bot._click_coordinates(10, 20, duration=30)
        bot.d.click.assert_called_once_with(10, 20)

    def test_has_element_u2_uses_selector_exists(self):
        cfg = Config(
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=False,
            probe_only=True,
        )
        bot = DamaiBot(config=cfg, setup_driver=False)
        selector = Mock()
        selector.exists = Mock(return_value=True)
        with patch.object(bot, "_find", return_value=selector):
            assert bot._has_element(By.ID, "cn.damai:id/checkbox") is True


# ---------------------------------------------------------------------------
# ultra_fast_click
# ---------------------------------------------------------------------------


class TestUltraFastClick:
    def test_ultra_fast_click_success(self, bot):
        """Element found, gesture click executed with center coords, returns True."""
        mock_el = _make_mock_element(x=100, y=200, width=50, height=40)

        with patch.object(bot, "_wait_for_element", return_value=mock_el):
            result = bot.ultra_fast_click(By.ID, "some_id")

        assert result is True

    def test_ultra_fast_click_timeout(self, bot):
        """_wait_for_element raises TimeoutException, returns False."""
        with patch.object(
            bot, "_wait_for_element", side_effect=TimeoutException("timeout")
        ):
            result = bot.ultra_fast_click(By.ID, "some_id")

        assert result is False


# ---------------------------------------------------------------------------
# _cached_tap
# ---------------------------------------------------------------------------


class TestCachedTap:
    def test_cache_hit_clicks_coordinates_and_returns_true(self, bot):
        """Warm path: cached (x, y) → single _click_coordinates call, True."""
        bot._cached_hot_path_coords["city"] = (300, 500)
        with (
            patch.object(bot, "_click_coordinates") as click_coords,
            patch.object(bot, "ultra_fast_click") as ufc,
        ):
            result = bot._cached_tap("city", By.ID, "some.id", timeout=0.3)
        assert result is True
        click_coords.assert_called_once_with(300, 500)
        ufc.assert_not_called()

    def test_cache_miss_u2_element_found_caches_and_clicks(self, bot):
        """u2 cold path: find element, extract bounds, cache coords, click."""
        mock_selector = Mock()
        mock_selector.wait.return_value = True
        mock_selector.info = {
            "bounds": {"left": 100, "top": 200, "right": 300, "bottom": 260}
        }
        with (
            patch.object(bot, "_appium_selector_to_u2", return_value=mock_selector),
            patch.object(bot, "_click_coordinates") as click_coords,
        ):
            result = bot._cached_tap(
                "city",
                ANDROID_UIAUTOMATOR,
                'new UiSelector().text("北京")',
                timeout=0.2,
            )
        assert result is True
        assert bot._cached_hot_path_coords["city"] == (200, 230)
        click_coords.assert_called_once_with(200, 230)

    def test_cache_miss_u2_element_not_found_returns_false(self, bot):
        """u2 cold path: element not found → returns False, no caching."""

        mock_selector = Mock()
        mock_selector.wait.return_value = False
        with patch.object(bot, "_appium_selector_to_u2", return_value=mock_selector):
            result = bot._cached_tap(
                "city", ANDROID_UIAUTOMATOR, 'new UiSelector().text("X")', timeout=0.1
            )
        assert result is False
        assert "city" not in bot._cached_hot_path_coords

    def test_cache_miss_u2_no_bounds_falls_back_to_element_center(self, bot):
        """u2: bounds missing → click via element center without caching."""

        mock_el = Mock()
        mock_selector = Mock()
        mock_selector.wait.return_value = True
        mock_selector.info = {"bounds": None}
        mock_selector.get.return_value = mock_el
        with (
            patch.object(bot, "_appium_selector_to_u2", return_value=mock_selector),
            patch.object(bot, "_click_element_center") as click_center,
        ):
            result = bot._cached_tap("k", By.ID, "id", timeout=0.1)
        assert result is True
        click_center.assert_called_once_with(mock_el, duration=50)
        assert "k" not in bot._cached_hot_path_coords

    def test_cache_miss_u2_exception_returns_false(self, bot):
        """u2: unexpected exception → returns False."""

        with patch.object(
            bot, "_appium_selector_to_u2", side_effect=RuntimeError("boom")
        ):
            result = bot._cached_tap("k", By.ID, "id", timeout=0.1)
        assert result is False

    def test_second_call_uses_cached_coords(self, bot):
        """After first successful u2 call, second call uses cached coords."""

        mock_selector = Mock()
        mock_selector.wait.return_value = True
        mock_selector.info = {
            "bounds": {"left": 50, "top": 100, "right": 150, "bottom": 140}
        }
        with (
            patch.object(bot, "_appium_selector_to_u2", return_value=mock_selector),
            patch.object(bot, "_click_coordinates") as click_coords,
        ):
            bot._cached_tap("btn", By.ID, "id", timeout=0.2)  # cold
            bot._cached_tap("btn", By.ID, "id", timeout=0.2)  # warm
        assert click_coords.call_count == 2
        mock_selector.wait.assert_called_once()  # only called on cold run


# ---------------------------------------------------------------------------
# batch_click
# ---------------------------------------------------------------------------


class TestBatchClick:
    def test_batch_click_all_success(self, bot):
        """ultra_fast_click called for each element pair."""
        elements = [("by1", "v1"), ("by2", "v2"), ("by3", "v3")]
        with (
            patch.object(bot, "ultra_fast_click", return_value=True) as ufc,
            patch("mobile.damai_app.time") as mock_time,
        ):
            bot.batch_click(elements, delay=0.1)

        assert ufc.call_count == 3
        ufc.assert_any_call("by1", "v1")
        ufc.assert_any_call("by2", "v2")
        ufc.assert_any_call("by3", "v3")

    def test_batch_click_some_fail(self, bot, caplog):
        """Failed clicks log a warning but processing continues."""
        elements = [("by1", "v1"), ("by2", "v2")]
        with (
            caplog.at_level("WARNING", logger="mobile.ui_primitives"),
            patch.object(bot, "ultra_fast_click", side_effect=[False, True]) as ufc,
            patch("mobile.ui_primitives.time"),
        ):
            bot.batch_click(elements, delay=0.1)

        assert ufc.call_count == 2
        assert "点击失败: v1" in caplog.text


# ---------------------------------------------------------------------------
# ultra_batch_click
# ---------------------------------------------------------------------------


class TestUltraBatchClick:
    def test_ultra_batch_click_collects_and_clicks(self, bot, caplog):
        """Coordinates collected for all elements, then clicked sequentially."""
        el1 = _make_mock_element(x=10, y=20, width=100, height=50)
        el2 = _make_mock_element(x=200, y=300, width=60, height=30)

        with (
            caplog.at_level("DEBUG", logger="mobile.ui_primitives"),
            patch.object(bot, "_wait_for_element", side_effect=[el1, el2]),
            patch("mobile.ui_primitives.time"),
        ):
            bot.ultra_batch_click([(By.ID, "v1"), (By.ID, "v2")], timeout=2)

        assert "成功找到 2 个用户" in caplog.text

    def test_ultra_batch_click_timeout_skips(self, bot, caplog):
        """Timed-out elements are skipped; found ones are still clicked."""
        el1 = _make_mock_element(x=10, y=20, width=100, height=50)

        with (
            caplog.at_level("DEBUG", logger="mobile.ui_primitives"),
            patch.object(
                bot, "_wait_for_element", side_effect=[el1, TimeoutException("timeout")]
            ),
            patch("mobile.ui_primitives.time"),
        ):
            bot.ultra_batch_click([(By.ID, "v1"), (By.ID, "v2")], timeout=2)

        assert "超时未找到用户: v2" in caplog.text
        assert "成功找到 1 个用户" in caplog.text


# ---------------------------------------------------------------------------
# smart_wait_and_click
# ---------------------------------------------------------------------------


class TestSmartWaitAndClick:
    def test_smart_wait_and_click_primary_success(self, bot):
        """Primary selector works on first try, returns True."""
        mock_el = _make_mock_element()
        with patch.object(bot, "_wait_for_element", return_value=mock_el):
            result = bot.smart_wait_and_click(By.ID, "some_id")

        assert result is True

    def test_smart_wait_and_click_backup_success(self, bot):
        """Primary fails (TimeoutException), backup selector works."""
        mock_el = _make_mock_element()
        with patch.object(
            bot,
            "_wait_for_element",
            side_effect=[
                TimeoutException("primary failed"),
                mock_el,
            ],
        ):
            result = bot.smart_wait_and_click(
                By.ID,
                "some_id",
                backup_selectors=[(By.ID, "backup_id")],
            )

        assert result is True

    def test_smart_wait_and_click_all_fail(self, bot):
        """All selectors (primary + backups) fail, returns False."""
        with patch.object(
            bot, "_wait_for_element", side_effect=TimeoutException("fail")
        ):
            result = bot.smart_wait_and_click(
                By.ID,
                "some_id",
                backup_selectors=[(By.ID, "v2"), (By.ID, "v3")],
            )

        assert result is False

    def test_smart_wait_and_click_no_backups(self, bot):
        """Only primary selector, fails, returns False."""
        with patch.object(
            bot, "_wait_for_element", side_effect=TimeoutException("fail")
        ):
            result = bot.smart_wait_and_click(By.ID, "some_id")

        assert result is False


# ---------------------------------------------------------------------------
# auto navigation
# ---------------------------------------------------------------------------


class TestAutoNavigation:
    def test_title_matches_target_with_keyword_tokens(self, bot):
        bot.config.keyword = "张杰 演唱会"

        assert (
            bot._title_matches_target(
                "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"
            )
            is True
        )

    def test_current_page_matches_target_uses_keyword_when_item_detail_missing(
        self, bot
    ):
        bot.item_detail = None
        bot.config.keyword = "余佳运 演唱会"

        with patch.object(
            bot,
            "_get_detail_title_text",
            return_value="【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站",
        ):
            assert bot._current_page_matches_target({"state": "sku_page"}) is False

    def test_exit_non_target_event_context_backs_out_until_search_page(self, bot):
        with (
            patch.object(
                bot, "_current_page_matches_target", side_effect=[False, False]
            ),
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                side_effect=[
                    {"state": "detail_page"},
                    {"state": "search_page"},
                ],
            ),
        ):
            result = bot._exit_non_target_event_context({"state": "sku_page"})

        assert result["state"] == "search_page"
        assert bot.d.press.call_count == 2

    def test_discover_target_event_exits_wrong_sku_page_before_search(self, bot):
        bot.config.keyword = "余佳运 演唱会"

        with (
            patch.object(
                bot, "_recover_to_navigation_start", return_value={"state": "sku_page"}
            ),
            patch.object(
                bot, "_current_page_matches_target", side_effect=[False, False]
            ),
            patch.object(
                bot,
                "_exit_non_target_event_context",
                return_value={"state": "search_page"},
            ) as exit_context,
            patch.object(
                bot, "_submit_search_keyword", return_value=True
            ) as submit_keyword,
            patch.object(
                bot,
                "_open_target_from_search_results",
                return_value={
                    "opened": True,
                    "search_results": [{"score": 80, "title": "余佳运演唱会"}],
                },
            ),
            patch.object(
                bot, "probe_current_page", return_value={"state": "detail_page"}
            ),
        ):
            result = bot.discover_target_event(
                ["余佳运 演唱会"], initial_probe={"state": "sku_page"}
            )

        assert result is not None
        exit_context.assert_called_once()
        submit_keyword.assert_called_once()

    def test_navigate_to_target_event_from_search_page(self, bot):
        with (
            patch.object(
                bot,
                "_recover_to_navigation_start",
                return_value={"state": "search_page"},
            ),
            patch.object(
                bot, "_submit_search_keyword", return_value=True
            ) as submit_keyword,
            patch.object(
                bot, "_open_target_from_search_results", return_value=True
            ) as open_target,
        ):
            result = bot.navigate_to_target_event({"state": "unknown"})

        assert result is True
        submit_keyword.assert_called_once()
        open_target.assert_called_once()

    def test_recover_to_navigation_start_handles_back_key_failure(self, bot):
        with (
            patch.object(bot, "_press_keycode_safe", return_value=False),
            patch.object(bot, "probe_current_page", return_value={"state": "unknown"}),
            patch.object(bot.d, "app_start") as app_start,
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot._recover_to_navigation_start({"state": "unknown"})

        app_start.assert_called_once_with(bot.config.app_package, stop=False)
        assert result["state"] == "unknown"

    def test_fast_retry_does_not_submit_when_commit_disabled(self, bot):
        bot.config.if_commit_order = False

        with (
            patch.object(
                bot, "probe_current_page", return_value={"state": "order_confirm_page"}
            ),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(
                bot, "smart_wait_for_element", return_value=True
            ) as wait_for_element,
            patch.object(bot, "smart_wait_and_click") as smart_click,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        wait_for_element.assert_called_once()
        smart_click.assert_not_called()

    def test_run_with_retry_stops_on_terminal_failure(self, bot):
        with (
            patch("mobile.damai_app.time.sleep"),
            patch.object(
                bot, "run_ticket_grabbing", side_effect=self._mark_terminal_failure(bot)
            ),
            patch.object(bot, "_fast_retry_from_current_state") as fast_retry,
            patch.object(bot, "_setup_driver") as setup_driver,
        ):
            result = bot.run_with_retry(max_retries=3)

        assert result is False
        fast_retry.assert_not_called()
        setup_driver.assert_not_called()

    @staticmethod
    def _mark_terminal_failure(bot):
        def runner(**kwargs):
            bot._terminal_failure_reason = "reservation_only"
            return False

        return runner


# ---------------------------------------------------------------------------
# run_ticket_grabbing
# ---------------------------------------------------------------------------


class TestRunTicketGrabbing:
    def test_run_ticket_grabbing_auto_navigates_from_homepage(self, bot):
        bot.config.probe_only = True

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(bot, "check_session_valid", return_value=True),
            patch.object(
                bot, "navigate_to_target_event", return_value=True
            ) as navigate,
            patch.object(
                bot,
                "probe_current_page",
                side_effect=[
                    {
                        "state": "homepage",
                        "purchase_button": False,
                        "price_container": False,
                        "quantity_picker": False,
                        "submit_button": False,
                        "reservation_mode": False,
                    },
                    {
                        "state": "detail_page",
                        "purchase_button": True,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": False,
                        "reservation_mode": False,
                    },
                ],
            ),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.3)
            result = bot.run_ticket_grabbing()

        assert result is True
        navigate.assert_called_once()

    def test_run_ticket_grabbing_returns_false_when_not_detail_page(self, bot):
        """Homepage or other non-detail states fail fast with a clear result."""
        bot.config.auto_navigate = False

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "homepage",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "smart_wait_and_click") as smart_click,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_probe_only_returns_true_when_detail_ready(self, bot):
        """probe_only stops before purchase when detail-page essentials are present."""
        bot.config.probe_only = True

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "smart_wait_and_click") as smart_click,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_logs_probe_mode_clearly(self, bot, caplog):
        """The first runtime log should clearly state this is only a probe."""
        bot.config.probe_only = True

        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.run_ticket_grabbing()

        assert result is True
        assert "开始执行安全探测" in caplog.text
        assert "不会点击“立即购票”" in caplog.text

    def test_run_ticket_grabbing_probe_only_returns_false_when_detail_incomplete(
        self, bot
    ):
        """probe_only reports failure when detail-page essentials are missing."""
        bot.config.probe_only = True

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": False,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_probe_only_returns_true_when_sku_page_ready(self, bot):
        """probe_only succeeds when the ticket sku page is already open."""
        bot.config.probe_only = True

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "sku_page",
                    "purchase_button": False,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "smart_wait_and_click") as smart_click,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_success(self, bot):
        """All phases succeed, returns True."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch.object(bot, "_submit_order_fast", return_value="success"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            # Provide enough time.time() values for the confirm-retry loop
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.5)
            # Mock find_element for price container + target_price
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []  # no quantity layout

            result = bot.run_ticket_grabbing()

        assert result is True

    def test_run_ticket_grabbing_rush_mode_uses_prefetched_buy_button_coordinates(
        self, bot
    ):
        bot.config.rush_mode = True
        bot.config.if_commit_order = False

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                    "price_coords": (240, 1560),
                    "buy_button_coords": (320, 1880),
                },
            ) as enter_purchase_flow,
            patch.object(
                bot, "_select_price_option", return_value=True
            ) as select_price,
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "_burst_click_coordinates") as burst_click_coords,
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.9)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        enter_purchase_flow.assert_called_once_with(prepared=False)
        select_price.assert_called_once_with(cached_coords=(240, 1560))
        burst_click_coords.assert_called_with(
            320, 1880, count=1, interval_ms=25, duration=25
        )

    def test_run_ticket_grabbing_stops_before_submit_when_commit_disabled(self, bot):
        """if_commit_order=False waits for confirm page but never clicks submit."""
        bot.config.if_commit_order = False

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(
                bot, "_wait_for_submit_ready", return_value=True
            ) as wait_submit_ready,
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click,
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.2)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        wait_submit_ready.assert_called_once()

    def test_run_ticket_grabbing_logs_validation_mode_clearly(self, bot, caplog):
        """Commit-disabled runs should be labeled as developer validation in logs."""
        bot.config.if_commit_order = False

        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.8)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert "开始执行开发验证" in caplog.text
        assert "开发调试路径" in caplog.text

    def test_run_ticket_grabbing_continues_from_sku_page_when_commit_disabled(
        self, bot
    ):
        """sku_page can continue directly to confirm page without returning to detail."""
        bot.config.if_commit_order = False

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "sku_page",
                    "purchase_button": False,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot, "_wait_for_submit_ready", return_value=True
            ) as wait_submit_ready,
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "smart_wait_and_click") as smart_click,
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.8)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()
        wait_submit_ready.assert_called_once()

    def test_run_ticket_grabbing_returns_false_for_reservation_sku_page(
        self, bot, caplog
    ):
        """Reservation-only sku pages stop safely before tapping the bottom action."""
        bot.config.if_commit_order = False

        with (
            caplog.at_level("WARNING", logger="mobile.damai_app"),
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                side_effect=[
                    {
                        "state": "sku_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": False,
                        "reservation_mode": True,
                    },
                    {
                        "state": "sku_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": False,
                        "reservation_mode": True,
                    },
                    {
                        "state": "sku_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": False,
                        "reservation_mode": True,
                    },
                ],
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "ultra_fast_click") as fast_click,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0

            result = bot.run_ticket_grabbing()

        assert result is False
        assert fast_click.call_count == 1
        fast_click.assert_called_once_with(
            ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("12.06")',
            timeout=1.0,
        )
        assert "抢票预约" in caplog.text

    def test_run_ticket_grabbing_returns_false_when_confirm_page_not_ready_and_commit_disabled(
        self, bot, caplog
    ):
        """Commit-disabled mode fails safely if the confirm page never becomes ready."""
        bot.config.if_commit_order = False

        with (
            caplog.at_level("WARNING", logger="mobile.damai_app"),
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=False),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click", return_value=0),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_monotonic(0.0, 2.0)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is False
        assert "未进入订单确认页" in caplog.text

    def test_run_ticket_grabbing_city_fail(self, bot):
        """City selection fails, returns False immediately."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "_select_city_from_detail_page", return_value=False),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_book_fail(self, bot):
        """Booking button fails, returns False."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot, "_enter_purchase_flow_from_detail_page", return_value=None
            ),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_exception_returns_false(self, bot):
        """Unexpected exception in flow returns False."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "smart_wait_and_click", side_effect=RuntimeError("boom")),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_submit_timeout_returns_false_and_marks_terminal_failure(
        self, bot
    ):
        """Submit timeout should fail closed to avoid false success and duplicate submit."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch.object(
                bot, "_submit_order_fast", return_value="timeout"
            ) as submit_fast,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.0)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is False
        assert bot._terminal_failure_reason == "submit_unverified"
        submit_fast.assert_called_once()

    def test_run_ticket_grabbing_existing_order_returns_success_with_pending_payment_outcome(
        self, bot
    ):
        """Existing unpaid order means submit flow already succeeded and only payment is pending."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch.object(bot, "_submit_order_fast", return_value="existing_order"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.0)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"
        assert bot._terminal_failure_reason is None

    def test_run_ticket_grabbing_returns_success_when_pending_order_dialog_detected_early(
        self, bot
    ):
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(bot, "check_session_valid", return_value=True),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "pending_order_dialog",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                    "reservation_mode": False,
                    "pending_order_dialog": True,
                },
            ),
        ):
            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"

    def test_run_ticket_grabbing_rush_mode_skips_detail_prepare_and_reprobe_when_no_sell_time(
        self, bot
    ):
        bot.config.rush_mode = True
        bot.config.sell_start_time = None
        bot.config.wait_cta_ready_timeout_ms = 60000

        detail_probe = {
            "state": "detail_page",
            "purchase_button": True,
            "price_container": True,
            "quantity_picker": False,
            "submit_button": False,
            "reservation_mode": False,
        }

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(bot, "check_session_valid", return_value=True),
            patch.object(
                bot, "probe_current_page", return_value=detail_probe
            ) as probe_page,
            patch.object(bot, "_prepare_detail_page_hot_path") as prepare_detail,
            patch.object(bot, "wait_for_sale_start"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_select_price_option", return_value=True),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "_submit_order_fast", return_value="success"),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.0)
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_submitted"
        prepare_detail.assert_not_called()
        assert probe_page.call_count == 1

    def test_run_ticket_grabbing_no_driver_quit_in_finally(self, bot):
        """Verify driver.quit is NOT called inside run_ticket_grabbing's finally block."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "smart_wait_and_click", return_value=False),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.return_value = 0.0
            bot.run_ticket_grabbing()

        bot.driver.quit.assert_not_called()

    def test_run_ticket_grabbing_skips_user_click_when_order_confirm_page_directly_opened(
        self, bot
    ):
        """Direct jump to order confirm page should skip manual user selection."""
        bot.config.if_commit_order = False

        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "sku_page",
                    "purchase_button": False,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch.object(bot, "ultra_fast_click", return_value=True),
            patch.object(bot, "ultra_batch_click") as batch_click,
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.8)
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        batch_click.assert_not_called()


class TestPageStateHelpers:
    def test_collect_search_results_reads_card_summary(self, bot):
        card = Mock()
        child_map = {
            "cn.damai:id/tv_project_name": [Mock(text="【北京】张杰演唱会")],
            "cn.damai:id/tv_project_venueName": [Mock(text="国家体育场-鸟巢")],
            "cn.damai:id/tv_project_city": [Mock(text="北京 | ")],
            "cn.damai:id/tv_project_time": [Mock(text="2026.03.29-04.19")],
            "cn.damai:id/bricks_dm_common_price_prefix": [Mock(text="¥")],
            "cn.damai:id/bricks_dm_common_price_des": [Mock(text="380")],
            "cn.damai:id/bricks_dm_common_price_suffix": [Mock(text="起")],
        }

        def fake_container_find(container, by, value):
            if container is bot.driver:
                return []
            return child_map.get(value, [])

        bot.config.keyword = "张杰 演唱会"

        with (
            patch.object(bot, "_find_all", return_value=[card]),
            patch.object(
                bot, "_container_find_elements", side_effect=fake_container_find
            ),
        ):
            results = bot.collect_search_results()

        assert results == [
            {
                "title": "【北京】张杰演唱会",
                "venue": "国家体育场-鸟巢",
                "city": "北京",
                "time": "2026.03.29-04.19",
                "price": "¥380起",
                "score": results[0]["score"],
            }
        ]
        assert results[0]["score"] >= 60

    def test_wait_for_purchase_entry_result_detects_sku_without_full_probe(self, bot):
        with (
            patch.object(bot, "_has_any_element", side_effect=[False, True]),
            patch.object(bot, "is_reservation_sku_mode", return_value=False),
        ):
            result = bot._wait_for_purchase_entry_result(timeout=0.2, poll_interval=0)

        assert result["state"] == "sku_page"
        assert result["reservation_mode"] is False

    def test_wait_for_purchase_entry_result_returns_none_without_fallback_probe(
        self, bot
    ):
        with (
            patch.object(bot, "_has_any_element", return_value=False),
            patch.object(bot, "probe_current_page") as probe,
        ):
            result = bot._wait_for_purchase_entry_result(
                timeout=0.01,
                poll_interval=0,
                fallback_probe_on_timeout=False,
            )

        assert result is None
        probe.assert_not_called()

    def test_wait_for_submit_ready_detects_submit_button(self, bot):
        with patch.object(bot, "_has_any_element", side_effect=[False, True]):
            assert bot._wait_for_submit_ready(timeout=0.2, poll_interval=0) is True

    def test_wait_for_submit_ready_times_out(self, bot):
        with patch.object(bot, "_has_any_element", return_value=False):
            assert bot._wait_for_submit_ready(timeout=0.01, poll_interval=0) is False

    def test_click_sku_buy_button_element_uses_element_click(self, bot):
        button = Mock()
        button.click = Mock()

        with patch.object(bot, "_find", return_value=button):
            assert bot._click_sku_buy_button_element() is True

        button.click.assert_called_once()

    def test_click_sku_buy_button_element_returns_false_when_missing(self, bot):
        with patch.object(bot, "_find", side_effect=RuntimeError("missing")):
            assert bot._click_sku_buy_button_element() is False

    def test_rush_mode_retries_sku_buy_with_element_click_fallback(self, bot):
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot.config.users = ["UserA"]

        initial_probe = {
            "state": "sku_page",
            "price_container": True,
            "reservation_mode": False,
            "price_coords": (300, 1200),
            "buy_button_coords": (540, 2100),
        }

        with (
            patch.object(bot, "_select_price_option", return_value=True),
            patch.object(bot, "_has_element", return_value=False),
            patch.object(
                bot, "_wait_for_submit_ready", side_effect=[False, True]
            ) as wait_ready,
            patch.object(
                bot, "_click_sku_buy_button_element", return_value=True
            ) as element_click,
            patch.object(bot, "_burst_click_coordinates") as burst_click,
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ),
            patch("mobile.damai_app.time.sleep"),
            patch("mobile.damai_app.time.time", side_effect=_make_time_monotonic()),
        ):
            assert bot.run_ticket_grabbing(initial_page_probe=initial_probe) is True

        assert wait_ready.call_count == 2
        burst_click.assert_called_once_with(
            540, 2100, count=1, interval_ms=25, duration=25
        )
        element_click.assert_called_once_with(burst_count=1)

    def test_ensure_attendees_selected_auto_selects_missing_checkbox(self, bot):
        checked_state = {"value": "false"}
        checkbox = Mock()
        checkbox.get_attribute.side_effect = lambda name: (
            checked_state["value"] if name == "checked" else ""
        )

        def _select_side_effect(_user_name):
            checked_state["value"] = "true"
            return True

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: 'textContains("实名观演人")' in value,
            ),
            patch.object(bot, "_attendee_checkbox_elements", return_value=[checkbox]),
            patch.object(
                bot, "_attendee_required_count_on_confirm_page", return_value=1
            ),
            patch.object(
                bot,
                "_select_attendee_checkbox_by_name",
                side_effect=_select_side_effect,
            ),
        ):
            assert bot._ensure_attendees_selected_on_confirm_page() is True

    def test_attendee_selected_count_falls_back_to_page_source(self, bot):
        checkbox = Mock(spec=[])
        type(checkbox).info = PropertyMock(side_effect=RuntimeError)
        bot.d.dump_hierarchy = Mock(
            return_value=(
                "<hierarchy>"
                '<node resource-id="cn.damai:id/checkbox" checked="true"/>'
                '<node resource-id="cn.damai:id/checkbox" checked="false"/>'
                "</hierarchy>"
            )
        )

        assert bot._attendee_selected_count([checkbox]) == 1

    def test_click_attendee_checkbox_falls_back_when_center_click_fails(self, bot):
        checkbox = Mock()
        checkbox.click = Mock()

        with (
            patch.object(
                bot, "_click_element_center", side_effect=Exception("center failed")
            ),
            patch.object(bot, "_burst_click_element_center", return_value=None),
            patch.object(bot, "_is_checkbox_selected", return_value=False),
            patch.object(bot, "_attendee_selected_count", side_effect=[0, 1]),
        ):
            assert bot._click_attendee_checkbox(checkbox) is True

        checkbox.click.assert_called_once()

    def test_select_attendee_checkbox_by_name_uses_contains_fallback_xpath(self, bot):
        checkbox = Mock()
        seen_xpaths = []

        def _find_all_side_effect(by, value):
            if by != By.XPATH:
                return []
            seen_xpaths.append(value)
            if "contains(normalize-space(@text)" in value:
                return [checkbox]
            return []

        with (
            patch.object(bot, "_find_all", side_effect=_find_all_side_effect),
            patch.object(bot, "_is_checkbox_selected", return_value=False),
            patch.object(
                bot, "_click_attendee_checkbox", return_value=True
            ) as click_checkbox,
        ):
            assert bot._select_attendee_checkbox_by_name("张志涛") is True

        assert any("contains(normalize-space(@text)" in xpath for xpath in seen_xpaths)
        click_checkbox.assert_called_once_with(checkbox)

    def test_ensure_attendees_selected_fails_when_section_visible_but_no_checkbox(
        self, bot
    ):
        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: 'textContains("实名观演人")' in value,
            ),
            patch.object(bot, "_attendee_checkbox_elements", return_value=[]),
        ):
            assert bot._ensure_attendees_selected_on_confirm_page() is False

    def test_ensure_attendees_polls_for_checkbox_in_rush_dev_mode(self, bot):
        """When rush_mode + dev validation, poll for checkbox elements instead of failing immediately."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot._cached_hot_path_coords.clear()

        checkbox1 = Mock()
        checkbox1.click = Mock()
        checkbox1.bounds = (100, 200, 150, 250)
        checkbox2 = Mock()
        checkbox2.click = Mock()
        checkbox2.bounds = (100, 300, 150, 350)
        call_count = {"n": 0}

        def _delayed_checkbox():
            call_count["n"] += 1
            if call_count["n"] < 3:
                return []
            return [checkbox1, checkbox2]

        with (
            patch.object(
                bot, "_attendee_checkbox_elements", side_effect=_delayed_checkbox
            ),
            patch.object(bot, "_attendee_selected_count", return_value=0),
            patch.object(bot, "_click_attendee_checkbox_fast", return_value=True),
        ):
            assert (
                bot._ensure_attendees_selected_on_confirm_page(
                    require_attendee_section=True
                )
                is True
            )
        assert call_count["n"] >= 3

    def test_get_buy_button_coordinates_returns_first_match_center(self, bot):
        element = _make_mock_element(x=20, y=40, width=100, height=60)
        with patch.object(bot, "_find_all", return_value=[element]):
            result = bot._get_buy_button_coordinates()

        assert result == (70, 70)

    def test_get_price_option_coordinates_by_config_index_returns_target_center(
        self, bot
    ):
        bot.config.price_index = 1
        # In u2 mode, this method uses _get_price_coords_from_xml.
        # Provide XML with two price cards where the second one is at known coords.
        xml = (
            '<hierarchy><node bounds="[0,0][1080,1920]">'
            '<node resource-id="cn.damai:id/project_detail_perform_price_flowlayout" bounds="[0,0][1080,400]">'
            '<node class="android.widget.FrameLayout" clickable="true" bounds="[10,20][110,100]" />'
            '<node class="android.widget.FrameLayout" clickable="true" bounds="[160,20][260,100]" />'
            "</node></node></hierarchy>"
        )
        bot.d.dump_hierarchy = Mock(return_value=xml)
        result = bot._get_price_option_coordinates_by_config_index()

        assert result == (210, 60)

    def test_get_visible_price_options_extracts_card_texts(self, bot):
        price_container = Mock()
        card_a = Mock()
        card_b = Mock()

        def fake_container_find(cont, by, value):
            if cont is price_container and value == "android.widget.FrameLayout":
                return [card_a, card_b]
            return []

        with (
            patch.object(bot, "_find", return_value=price_container),
            patch.object(
                bot, "_container_find_elements", side_effect=fake_container_find
            ),
            patch.object(bot, "_is_clickable", return_value=True),
            patch.object(
                bot,
                "_collect_descendant_texts",
                side_effect=lambda c, **kw: (
                    ["内场", "1280", "可预约"]
                    if c is card_a
                    else ["看台", "380", "无票"]
                ),
            ),
        ):
            options = bot.get_visible_price_options()

        assert options == [
            {
                "index": 0,
                "text": "内场1280元",
                "tag": "可预约",
                "raw_texts": ["内场", "1280", "可预约"],
                "source": "ui",
            },
            {
                "index": 1,
                "text": "看台380元",
                "tag": "无票",
                "raw_texts": ["看台", "380", "无票"],
                "source": "ui",
            },
        ]

    def test_purchase_bar_text_ready_distinguishes_reservation_from_purchase(self, bot):
        purchase_bar = Mock()
        with (
            patch.object(bot.driver, "find_element", return_value=purchase_bar),
            patch.object(bot, "_collect_descendant_texts", return_value=["立即购买"]),
        ):
            assert bot._purchase_bar_text_ready() is True

        with (
            patch.object(bot.driver, "find_element", return_value=purchase_bar),
            patch.object(bot, "_collect_descendant_texts", return_value=["抢票预约"]),
        ):
            assert bot._purchase_bar_text_ready() is False

    def test_normalize_ocr_price_text(self, bot):
        assert bot._normalize_ocr_price_text("38075 Fam ©") == "380元"
        assert bot._normalize_ocr_price_text("128076 gma G") == "1280元"
        assert bot._normalize_ocr_price_text("4807") == "480元"
        assert bot._normalize_ocr_price_text("13803") == "1380元"
        assert bot._normalize_ocr_price_text("noise") == ""

    def test_price_ocr_focus_rect_targets_left_price_area(self, bot):
        rect = {"x": 529, "y": 1684, "width": 496, "height": 185}

        assert bot._price_sel._price_ocr_focus_rect(rect) == {
            "x": 543,
            "y": 1698,
            "width": 173,
            "height": 111,
        }

    def test_choose_best_ocr_price_candidate_prefers_repeated_full_11(self, bot):
        candidates = [
            {"variant": "focus", "psm": "13", "price": "807元"},
            {"variant": "focus", "psm": "7", "price": "1180元"},
            {"variant": "focus", "psm": "11", "price": "1180元"},
            {"variant": "full", "psm": "11", "price": "1180元"},
        ]

        assert bot._price_sel._choose_best_ocr_price_candidate(candidates) == "1180元"

    def test_choose_best_ocr_price_candidate_prefers_focus_13_when_confirmed(self, bot):
        candidates = [
            {"variant": "focus", "psm": "13", "price": "1380元"},
            {"variant": "focus", "psm": "7", "price": "1580元"},
            {"variant": "full", "psm": "11", "price": "1380元"},
            {"variant": "full", "psm": "7", "price": "1580元"},
        ]

        assert bot._price_sel._choose_best_ocr_price_candidate(candidates) == "1380元"

    def test_ocr_price_text_from_card_prefers_focused_crop(self, bot):
        rect = {"x": 529, "y": 1684, "width": 496, "height": 185}

        def fake_run(cmd, check=False, capture_output=False, **kwargs):
            if cmd[0] == "magick":
                return Mock(stdout=b"", stderr=b"", returncode=0)
            if cmd[0] == "tesseract":
                return Mock(stdout=b"13803\n", stderr=b"", returncode=0)
            raise AssertionError(cmd)

        with (
            patch("mobile.price_selector._MAGICK_BIN", "magick"),
            patch("mobile.price_selector._TESSERACT_BIN", "tesseract"),
            patch("mobile.price_selector.subprocess.run", side_effect=fake_run) as run,
        ):
            result = bot._ocr_price_text_from_card("/tmp/sku.png", rect)

        assert result == "1380元"
        first_magick_cmd = run.call_args_list[0][0][0]
        assert "173x111+543+1698" in " ".join(first_magick_cmd)
        first_tesseract_cmd = run.call_args_list[1][0][0]
        assert "--psm" in first_tesseract_cmd
        assert first_tesseract_cmd[first_tesseract_cmd.index("--psm") + 1] == "13"

    def test_probe_current_page_detects_homepage(self, bot):
        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (
                    (by, value) == (By.ID, "cn.damai:id/homepage_header_search")
                ),
            ),
            patch.object(bot, "_get_current_activity", return_value=""),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "homepage"
            assert result["purchase_button"] is False

    def test_probe_current_page_detects_homepage_by_activity(self, bot):
        with (
            patch.object(bot, "_has_element", return_value=False),
            patch.object(
                bot, "_get_current_activity", return_value=".homepage.MainActivity"
            ),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "homepage"

    def test_probe_current_page_detects_search_activity(self, bot):
        with (
            patch.object(bot, "_has_element", return_value=False),
            patch.object(
                bot,
                "_get_current_activity",
                return_value="com.alibaba.pictures.bricks.search.v2.SearchActivity",
            ),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "search_page"
            assert result["purchase_button"] is False

    def test_probe_current_page_detects_detail_page_by_activity_and_summary_price(
        self, bot
    ):
        present = {
            (By.ID, "cn.damai:id/project_detail_price_layout"),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(
                bot,
                "_get_current_activity",
                return_value=".trade.newtradeorder.ui.projectdetail.ui.activity.ProjectDetailActivity",
            ),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "detail_page"
            assert result["purchase_button"] is False
            assert result["price_container"] is True

    def test_probe_current_page_detects_sku_page(self, bot):
        present = {
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(
                bot,
                "_get_current_activity",
                return_value=".commonbusiness.seatbiz.sku.qilin.ui.NcovSkuActivity",
            ),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "sku_page"
            assert result["price_container"] is True
            assert result["reservation_mode"] is False

    def test_probe_current_page_marks_reservation_mode_for_reservation_sku(self, bot):
        present = {
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看场次")'),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(
                bot,
                "_get_current_activity",
                return_value=".commonbusiness.seatbiz.sku.qilin.ui.NcovSkuActivity",
            ),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "sku_page"
            assert result["reservation_mode"] is True

    def test_probe_current_page_detects_pending_order_dialog(self, bot):
        present = {
            (By.ID, "cn.damai:id/damai_theme_dialog_confirm_btn"),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "_get_current_activity", return_value=""),
        ):
            result = bot._probe_current_page_element_based()

        assert result["state"] == "pending_order_dialog"
        assert result["pending_order_dialog"] is True

    def test_probe_current_page_detects_detail_page_controls(self, bot):
        present = {
            (
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ),
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "layout_num"),
            (By.ID, "cn.damai:id/checkbox"),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "_get_current_activity", return_value=""),
        ):
            result = bot._probe_current_page_element_based()

            assert result["state"] == "order_confirm_page"
            assert result["purchase_button"] is True
            assert result["price_container"] is True
            assert result["quantity_picker"] is True
            assert result["submit_button"] is True

    def test_dismiss_startup_popups_clicks_known_popups(self, bot):
        present = {
            (By.ID, "android:id/ok"),
            (By.ID, "cn.damai:id/id_boot_action_agree"),
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),
            (By.ID, "cn.damai:id/damai_theme_dialog_close_layout"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "ultra_fast_click", return_value=True) as fast_click,
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot.dismiss_startup_popups()

            assert result is True
            fast_click.assert_any_call(By.ID, "android:id/ok")
            fast_click.assert_any_call(By.ID, "cn.damai:id/id_boot_action_agree")
            fast_click.assert_any_call(
                By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"
            )
            fast_click.assert_any_call(
                By.ID, "cn.damai:id/damai_theme_dialog_close_layout"
            )
            fast_click.assert_any_call(
                ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'
            )
            fast_click.assert_any_call(
                ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'
            )

    def test_dismiss_fast_blocking_dialogs_handles_realname_tip(self, bot):
        present = {
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "ultra_fast_click", return_value=True) as fast_click,
            patch("mobile.damai_app.time.sleep"),
        ):
            assert bot._dismiss_fast_blocking_dialogs() is True

        fast_click.assert_any_call(
            By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn", timeout=0.15
        )
        fast_click.assert_any_call(
            ANDROID_UIAUTOMATOR,
            'new UiSelector().text("知道了")',
            timeout=0.15,
        )


# ---------------------------------------------------------------------------
# run_with_retry
# ---------------------------------------------------------------------------


class TestRunWithRetry:
    def test_run_with_retry_success_first_attempt(self, bot):
        """Succeeds on first attempt, returns True immediately."""
        with (
            patch.object(bot, "run_ticket_grabbing", return_value=True),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=3)

        assert result is True

    def test_run_with_retry_success_second_attempt(self, bot):
        """Fails once, sets up driver again, succeeds second time."""
        with (
            patch.object(bot, "run_ticket_grabbing", side_effect=[False, True]),
            patch.object(bot, "_fast_retry_from_current_state", return_value=False),
            patch.object(bot, "_setup_driver") as mock_setup,
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=3)

        assert result is True
        mock_setup.assert_called_once()

    def test_run_with_retry_all_fail(self, bot):
        """All retries fail, returns False."""
        with (
            patch.object(bot, "run_ticket_grabbing", return_value=False),
            patch.object(bot, "_fast_retry_from_current_state", return_value=False),
            patch.object(bot, "_setup_driver"),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=3)

        assert result is False

    def test_run_with_retry_driver_quit_between_retries(self, bot):
        """Between retries, driver.quit and _setup_driver are called."""
        with (
            patch.object(bot, "run_ticket_grabbing", side_effect=[False, False, True]),
            patch.object(bot, "_fast_retry_from_current_state", return_value=False),
            patch.object(bot, "_setup_driver") as mock_setup,
            patch("mobile.damai_app.time"),
        ):
            bot.run_with_retry(max_retries=3)

        # quit called before each retry (2 failures, but last one succeeds so only 2 quit calls)
        assert bot.driver.quit.call_count == 2
        assert mock_setup.call_count == 2

    def test_run_with_retry_quit_exception_handled(self, bot):
        """driver.quit raises an exception, handled by except block."""
        bot.driver.quit.side_effect = Exception("quit failed")

        with (
            patch.object(bot, "run_ticket_grabbing", side_effect=[False, True]),
            patch.object(bot, "_setup_driver") as mock_setup,
            patch.object(bot, "_fast_retry_from_current_state", return_value=False),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=3)

        # Despite quit failure, retry continued and succeeded
        assert result is True

    def test_run_with_retry_uses_fast_retry(self, bot):
        """Verify fast retry is attempted before driver recreation."""
        with (
            patch.object(bot, "run_ticket_grabbing", side_effect=[False, False]),
            patch.object(
                bot, "_fast_retry_from_current_state", return_value=False
            ) as fast_retry,
            patch.object(bot, "_setup_driver"),
            patch("mobile.damai_app.time"),
        ):
            bot.run_with_retry(max_retries=2)

        # fast_retry called fast_retry_count times per failed attempt
        assert fast_retry.call_count == bot.config.fast_retry_count * 2

    def test_run_with_retry_first_fast_retry_has_no_extra_sleep(self, bot):
        """The first fast retry should execute immediately after a failed attempt."""
        with (
            patch.object(bot, "run_ticket_grabbing", return_value=False),
            patch.object(
                bot, "_fast_retry_from_current_state", side_effect=[False, True]
            ),
            patch.object(bot, "_setup_driver"),
            patch("mobile.damai_app.time.sleep") as mock_sleep,
        ):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        mock_sleep.assert_called_once_with(bot.config.fast_retry_interval_ms / 1000)

    def test_run_with_retry_manual_mode_skips_driver_recreation(self, bot):
        """Manual-start mode keeps the driver session instead of rebuilding it."""
        bot.config.auto_navigate = False

        with (
            patch.object(bot, "run_ticket_grabbing", side_effect=[False, False]),
            patch.object(bot, "_fast_retry_from_current_state", return_value=False),
            patch.object(bot, "_setup_driver") as mock_setup,
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=2)

        assert result is False
        bot.driver.quit.assert_not_called()
        mock_setup.assert_not_called()

    def test_run_with_retry_logs_probe_success_clearly(self, bot, caplog):
        """probe_only success should not be logged as ticket-purchase success."""
        bot.config.probe_only = True
        bot._last_run_outcome = "probe_ready"

        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "run_ticket_grabbing", return_value=True),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        assert "探测成功" in caplog.text
        assert "抢票成功！" not in caplog.text

    def test_run_with_retry_logs_validation_success_clearly(self, bot, caplog):
        """Developer validation success should mention no-submit explicitly."""
        bot.config.if_commit_order = False
        bot._last_run_outcome = "validation_ready"

        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "run_ticket_grabbing", return_value=True),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        assert "开发验证成功：已到订单确认页，未提交订单" in caplog.text

    def test_run_with_retry_logs_submit_success_when_order_submitted(self, bot, caplog):
        """Actual order submission keeps the purchase-success wording."""
        bot._last_run_outcome = "order_submitted"

        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "run_ticket_grabbing", return_value=True),
            patch("mobile.damai_app.time"),
        ):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        assert "抢票成功：已提交订单" in caplog.text


# ---------------------------------------------------------------------------
# wait_for_sale_start
# ---------------------------------------------------------------------------


class TestWaitForSaleStart:
    def test_wait_for_sale_start_no_config(self, bot):
        """sell_start_time=None, returns immediately without sleeping."""
        bot.config.sell_start_time = None
        bot.config.wait_cta_ready_timeout_ms = 0
        with patch("mobile.damai_app.time.sleep") as mock_sleep:
            bot.wait_for_sale_start()
        mock_sleep.assert_not_called()

    def test_wait_for_sale_start_already_passed(self, bot):
        """Time in past, returns immediately."""
        bot.config.sell_start_time = "2020-01-01T10:00:00+08:00"
        with patch("mobile.damai_app.time.sleep") as mock_sleep:
            bot.wait_for_sale_start()
        mock_sleep.assert_not_called()

    def test_wait_for_sale_start_waits_and_polls(self, bot):
        """Mock time so sale is in future, verify sleep called, then polling finds button."""
        _tz = timezone(timedelta(hours=8))
        # Sale starts 10 seconds from "now"
        future_time = datetime(2026, 6, 1, 20, 0, 10, tzinfo=_tz)
        bot.config.sell_start_time = future_time.isoformat()
        bot.config.countdown_lead_ms = 3000

        # Track datetime.now calls: first returns "now" (10s before sale),
        # then returns times during polling
        now_base = datetime(2026, 6, 1, 20, 0, 0, tzinfo=_tz)
        now_calls = [0]

        def mock_now(tz=None):
            now_calls[0] += 1
            if now_calls[0] <= 2:
                # Initial check + sleep calculation
                return now_base
            # During polling, return past the sale time
            return future_time + timedelta(seconds=1)

        with (
            patch("mobile.damai_app.datetime") as mock_dt,
            patch("mobile.damai_app.time.sleep") as mock_sleep,
            patch.object(bot, "_has_element", return_value=True),
        ):
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.now = mock_now
            bot.wait_for_sale_start()

        # Should have slept for the wait period (10s - 3s lead = 7s)
        assert mock_sleep.call_count >= 1
        # First sleep should be ~7 seconds
        first_sleep_arg = mock_sleep.call_args_list[0][0][0]
        assert 6.5 < first_sleep_arg < 7.5

    def test_wait_for_sale_start_skips_cta_wait_without_sell_start_time(self, bot):
        bot.config.sell_start_time = None
        bot.config.wait_cta_ready_timeout_ms = 5000

        with (
            patch("mobile.damai_app.logger") as mock_logger,
            patch("mobile.damai_app.time.sleep") as mock_sleep,
            patch.object(bot, "_is_sale_ready") as is_ready,
        ):
            bot.wait_for_sale_start()

        is_ready.assert_not_called()
        mock_sleep.assert_not_called()
        mock_logger.info.assert_any_call(
            "未配置 sell_start_time，已跳过 CTA 等待，直接开始执行"
        )

    def test_wait_for_sale_start_skips_cta_wait_timeout_branch_without_sell_start_time(
        self, bot
    ):
        bot.config.sell_start_time = None
        bot.config.wait_cta_ready_timeout_ms = 100

        with (
            patch("mobile.damai_app.logger"),
            patch("mobile.damai_app.time.sleep") as mock_sleep,
            patch.object(bot, "_is_sale_ready") as is_ready,
        ):
            bot.wait_for_sale_start()

        is_ready.assert_not_called()
        mock_sleep.assert_not_called()

    def test_prepare_detail_page_hot_path_preselects_date_and_city(self, bot):
        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "select_performance_date") as select_date,
            patch.object(
                bot, "_select_city_from_detail_page", return_value=True
            ) as select_city,
        ):
            result = bot._prepare_detail_page_hot_path()

        assert result is True
        select_date.assert_called_once()
        select_city.assert_called_once_with(timeout=0.6)

    def test_prepare_detail_page_hot_path_returns_false_outside_detail_page(self, bot):
        with (
            patch.object(bot, "probe_current_page", return_value={"state": "homepage"}),
            patch.object(bot, "select_performance_date") as select_date,
            patch.object(bot, "_select_city_from_detail_page") as select_city,
        ):
            result = bot._prepare_detail_page_hot_path()

        assert result is False
        select_date.assert_not_called()
        select_city.assert_not_called()


class TestDetailPagePurchaseEntry:
    def test_select_city_from_detail_page_uses_fallback_selectors(self, bot):
        with patch.object(
            bot, "smart_wait_and_click", return_value=True
        ) as smart_click:
            result = bot._select_city_from_detail_page(timeout=0.8)

        assert result is True
        smart_click.assert_called_once_with(
            ANDROID_UIAUTOMATOR,
            'new UiSelector().text("深圳")',
            [
                (ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("深圳")'),
                (By.XPATH, '//*[@text="深圳"]'),
            ],
            timeout=0.8,
        )

    def test_enter_purchase_flow_returns_none_when_city_selection_fails(self, bot):
        with (
            patch.object(bot, "select_performance_date") as select_date,
            patch.object(
                bot, "_select_city_from_detail_page", return_value=False
            ) as select_city,
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        assert result is None
        select_date.assert_called_once()
        select_city.assert_called_once_with(timeout=1.0)

    def test_enter_purchase_flow_rush_mode_continues_when_city_selection_misses(
        self, bot
    ):
        bot.config.rush_mode = True
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with (
            patch.object(bot, "_cached_tap", return_value=False),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ),
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        assert result == next_probe

    def test_enter_purchase_flow_uses_rush_mode_fast_path(self, bot):
        bot.config.rush_mode = True
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with (
            patch.object(bot, "_cached_tap", return_value=True) as cached_tap,
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ) as wait_result,
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result == next_probe
        cached_tap.assert_called()
        wait_result.assert_called_once_with(timeout=6.0, poll_interval=0.03)

    def test_enter_purchase_flow_falls_back_to_book_selectors(self, bot):
        next_probe = {"state": "order_confirm_page", "submit_button": True}

        with (
            patch.object(bot, "ultra_fast_click", return_value=False),
            patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click,
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ) as wait_result,
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result == next_probe
        smart_click.assert_called_once()
        wait_result.assert_called_once_with(timeout=5, poll_interval=0.08)

    def test_enter_purchase_flow_returns_none_when_all_clicks_fail(self, bot):
        with (
            patch.object(bot, "ultra_fast_click", return_value=False),
            patch.object(bot, "smart_wait_and_click", return_value=False),
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result is None


class TestSaleReadiness:
    def test_purchase_bar_text_ready_returns_false_when_bar_missing(self, bot):
        bot.driver.find_element.side_effect = Exception("missing")
        assert bot._purchase_bar_text_ready() is False

    def test_purchase_bar_text_ready_returns_false_when_descendants_are_empty(
        self, bot
    ):
        purchase_bar = Mock()
        with (
            patch.object(bot.driver, "find_element", return_value=purchase_bar),
            patch.object(bot, "_collect_descendant_texts", return_value=["", "   "]),
        ):
            assert bot._purchase_bar_text_ready() is False

    def test_is_sale_ready_detects_ready_selector(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: (
                value == 'new UiSelector().textContains("立即购买")'
            ),
        ):
            assert bot._is_sale_ready() is True

    def test_is_sale_ready_uses_sku_mode_to_block_reservation(self, bot):
        present = {(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")}

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "is_reservation_sku_mode", return_value=True),
        ):
            assert bot._is_sale_ready() is False

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "is_reservation_sku_mode", return_value=False),
        ):
            assert bot._is_sale_ready() is True

    def test_is_sale_ready_uses_purchase_bar_text_when_detail_cta_present(self, bot):
        present = {
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl")
        }

        with (
            patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: (by, value) in present,
            ),
            patch.object(bot, "_purchase_bar_text_ready", return_value=True),
        ):
            assert bot._is_sale_ready() is True

    def test_is_sale_ready_returns_false_without_any_signal(self, bot):
        with patch.object(bot, "_has_element", return_value=False):
            assert bot._is_sale_ready() is False


# ---------------------------------------------------------------------------
# _fast_retry_from_current_state
# ---------------------------------------------------------------------------


class TestFastRetry:
    def test_recover_to_detail_page_for_local_retry_backtracks(self, bot):
        """Local retry backs out until it finds a retryable event page."""
        unknown_probe = {
            "state": "unknown",
            "purchase_button": False,
            "price_container": False,
            "quantity_picker": False,
            "submit_button": False,
        }
        detail_probe = {
            "state": "detail_page",
            "purchase_button": True,
            "price_container": True,
            "quantity_picker": False,
            "submit_button": False,
        }
        # First call from dismiss_startup_popups fallback returns unknown;
        # second call (in back-loop with fast=True) returns detail_page.
        with (
            patch.object(
                bot, "probe_current_page", side_effect=[unknown_probe, detail_probe]
            ),
            patch.object(bot, "dismiss_startup_popups"),
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot._recover_to_detail_page_for_local_retry(
                initial_probe=unknown_probe
            )

        bot.d.press.assert_called_once_with("back")
        assert result["state"] == "detail_page"

    def test_fast_retry_from_detail_page(self, bot):
        """probe returns detail_page, re-runs full flow."""
        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        run_tg.assert_called_once()

    def test_fast_retry_from_order_confirm_page(self, bot):
        """probe returns order_confirm_page, re-attempts submit only."""
        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "order_confirm_page",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": True,
                },
            ),
            patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        smart_click.assert_called_once()

    def test_fast_retry_from_order_confirm_page_in_safe_mode_waits_for_submit_button(
        self, bot
    ):
        bot.config.if_commit_order = False

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "order_confirm_page",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": True,
                },
            ),
            patch.object(
                bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
            ) as ensure_attendees,
            patch.object(
                bot, "smart_wait_for_element", return_value=True
            ) as wait_element,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        ensure_attendees.assert_called_once()
        wait_element.assert_called_once()

    def test_fast_retry_returns_success_when_pending_order_dialog_detected(self, bot):
        with patch.object(
            bot,
            "probe_current_page",
            return_value={
                "state": "pending_order_dialog",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": False,
            },
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"

    def test_fast_retry_switches_to_auto_navigation_when_wrong_detail_page(self, bot):
        bot.item_detail = _make_item_detail()
        bot.config.auto_navigate = True

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "_current_page_matches_target", return_value=False),
            patch.object(
                bot, "navigate_to_target_event", return_value=True
            ) as navigate,
            patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        navigate.assert_called_once()
        run_tg.assert_called_once()

    def test_fast_retry_stops_in_manual_mode_when_wrong_detail_page(self, bot):
        bot.item_detail = _make_item_detail()
        bot.config.auto_navigate = False

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "_current_page_matches_target", return_value=False),
            patch.object(bot, "navigate_to_target_event") as navigate,
            patch.object(bot, "run_ticket_grabbing") as run_tg,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is False
        navigate.assert_not_called()
        run_tg.assert_not_called()

    def test_fast_retry_from_unknown_recovers_locally_then_reruns(self, bot):
        """Manual-start retry recovers locally before re-running the flow."""
        bot.config.auto_navigate = False

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "unknown",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(
                bot,
                "_recover_to_detail_page_for_local_retry",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ) as recover_local,
            patch.object(bot, "run_ticket_grabbing", return_value=False) as run_tg,
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot._fast_retry_from_current_state()

        recover_local.assert_called_once()
        run_tg.assert_called_once()
        assert result is False

    def test_fast_retry_from_unknown_returns_false_if_local_recovery_fails(self, bot):
        """Manual-start retry stops if it cannot recover to a detail/sku page."""
        bot.config.auto_navigate = False

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "unknown",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(
                bot,
                "_recover_to_detail_page_for_local_retry",
                return_value={
                    "state": "homepage",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ) as recover_local,
            patch.object(bot, "run_ticket_grabbing") as run_tg,
        ):
            result = bot._fast_retry_from_current_state()

        recover_local.assert_called_once()
        run_tg.assert_not_called()
        assert result is False

    def test_fast_retry_from_unknown_uses_auto_navigation_when_enabled(self, bot):
        bot.config.auto_navigate = True

        with (
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "unknown",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(
                bot, "navigate_to_target_event", return_value=True
            ) as navigate,
            patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg,
        ):
            result = bot._fast_retry_from_current_state()

        assert result is True
        navigate.assert_called_once()
        run_tg.assert_called_once()


# ---------------------------------------------------------------------------
# verify_order_result
# ---------------------------------------------------------------------------


class TestVerifyOrderResult:
    def test_verify_order_success_payment_activity(self, bot):
        """Activity contains 'Pay', returns 'success'."""
        with (
            patch.object(
                bot,
                "_get_current_activity",
                return_value="com.alipay.android.app.PayActivity",
            ),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.verify_order_result(timeout=5)

        assert result == "success"

    def test_verify_order_success_payment_text(self, bot):
        """Payment-specific UI text returns 'success'."""

        def has_element_side_effect(by, value):
            return "立即支付" in value

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.verify_order_result(timeout=5)

        assert result == "success"

    def test_verify_order_generic_payment_text_does_not_count_as_success(self, bot):
        """Generic '支付' text should not be treated as a successful submit signal."""
        time_values = chain([0.0, 0.2, 0.5, 0.8, 1.1], repeat(1.1))

        def has_element_side_effect(by, value):
            # Simulate a page containing generic "支付" wording but no payment CTA.
            return 'textContains("支付")' in value and "未支付" not in value

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time.time", side_effect=time_values),
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot.verify_order_result(timeout=1)

        assert result == "timeout"

    def test_verify_order_payment_cta_on_confirm_page_does_not_count_as_success(
        self, bot
    ):
        """Even if payment CTA text appears, still being on confirm page should not be success."""
        time_values = chain([0.0, 0.2, 0.5, 0.8, 1.1], repeat(1.1))

        def has_element_side_effect(by, value):
            if 'textContains("未支付")' in value:
                return False
            if (
                'textContains("已售罄")' in value
                or 'textContains("库存不足")' in value
                or 'textContains("暂时无票")' in value
            ):
                return False
            if 'textContains("滑块")' in value or 'textContains("验证")' in value:
                return False
            if 'textContains("立即支付")' in value:
                return True
            if 'text("立即提交")' in value:
                return True
            if 'textContains("确认购买")' in value:
                return True
            return False

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time.time", side_effect=time_values),
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot.verify_order_result(timeout=1)

        assert result == "timeout"

    def test_verify_order_sold_out(self, bot):
        """Element contains '已售罄', returns 'sold_out'."""

        def has_element_side_effect(by, value):
            return "已售罄" in value

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.verify_order_result(timeout=5)

        assert result == "sold_out"

    def test_verify_order_timeout(self, bot):
        """No indicators found, returns 'timeout'."""
        call_count = [0]

        def mock_time_func():
            call_count[0] += 1
            # Return increasing time so we exceed timeout quickly
            return call_count[0] * 3.0

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", return_value=False),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time = mock_time_func
            mock_time.sleep = Mock()
            result = bot.verify_order_result(timeout=5)

        assert result == "timeout"

    def test_verify_order_captcha(self, bot):
        """Element contains '验证', returns 'captcha'."""

        def has_element_side_effect(by, value):
            # Skip 支付 and 已售罄/库存不足/暂时无票, match 验证
            if "支付" in value:
                return False
            if "已售罄" in value or "库存不足" in value or "暂时无票" in value:
                return False
            if "验证" in value:
                return True
            return False

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.verify_order_result(timeout=5)

        assert result == "captcha"

    def test_verify_order_existing_order(self, bot):
        """Element contains '未支付', returns 'existing_order'."""

        def has_element_side_effect(by, value):
            if "支付" in value and "未" not in value:
                return False
            if "已售罄" in value or "库存不足" in value or "暂时无票" in value:
                return False
            if "滑块" in value or "验证" in value:
                return False
            if "未支付" in value:
                return True
            return False

        with (
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 0.1)
            result = bot.verify_order_result(timeout=5)

        assert result == "existing_order"


# ---------------------------------------------------------------------------
# select_performance_date
# ---------------------------------------------------------------------------


class TestSelectPerformanceDate:
    def test_select_performance_date_found(self, bot, caplog):
        """Date text found and clicked successfully."""
        with (
            caplog.at_level("INFO", logger="mobile.damai_app"),
            patch.object(bot, "ultra_fast_click", return_value=True) as ufc,
        ):
            bot.select_performance_date()

        ufc.assert_called_once_with(
            ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("12.06")',
            timeout=1.0,
        )
        assert "选择场次日期: 12.06" in caplog.text

    def test_select_performance_date_not_found(self, bot, caplog):
        """Date not found, continues gracefully without error."""
        with (
            caplog.at_level("DEBUG", logger="mobile.damai_app"),
            patch.object(bot, "ultra_fast_click", return_value=False) as ufc,
        ):
            bot.select_performance_date()

        ufc.assert_called_once()
        assert "未找到日期" in caplog.text

    def test_select_performance_date_no_date_configured(self, bot):
        """No date in config, returns immediately without clicking."""
        bot.config.date = ""
        with patch.object(bot, "ultra_fast_click") as ufc:
            bot.select_performance_date()

        ufc.assert_not_called()


# ---------------------------------------------------------------------------
# check_session_valid
# ---------------------------------------------------------------------------


class TestCheckSessionValid:
    def test_check_session_valid_logged_in(self, bot):
        """No login indicators, returns True."""
        with (
            patch.object(
                bot, "_get_current_activity", return_value="ProjectDetailActivity"
            ),
            patch.object(bot, "_has_element", return_value=False),
        ):
            result = bot.check_session_valid()

        assert result is True

    def test_check_session_valid_login_activity(self, bot, caplog):
        """LoginActivity detected, returns False."""
        with (
            caplog.at_level("ERROR", logger="mobile.damai_app"),
            patch.object(
                bot,
                "_get_current_activity",
                return_value="com.taobao.login.LoginActivity",
            ),
        ):
            result = bot.check_session_valid()

        assert result is False
        assert "登录已过期" in caplog.text

    def test_check_session_valid_sign_activity(self, bot, caplog):
        """SignActivity detected, returns False."""
        with (
            caplog.at_level("ERROR", logger="mobile.damai_app"),
            patch.object(
                bot, "_get_current_activity", return_value="com.taobao.SignActivity"
            ),
        ):
            result = bot.check_session_valid()

        assert result is False
        assert "登录已过期" in caplog.text

    def test_check_session_valid_login_prompt(self, bot, caplog):
        """'请先登录' text detected on page, returns False."""

        def has_element_side_effect(by, value):
            return "请先登录" in value

        with (
            caplog.at_level("ERROR", logger="mobile.damai_app"),
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
        ):
            result = bot.check_session_valid()

        assert result is False
        assert "登录提示" in caplog.text


class TestSkuInspectionHelpers:
    def test_dismiss_startup_popups_returns_false_when_nothing_is_clickable(self, bot):
        with (
            patch.object(bot, "_has_element", return_value=False),
            patch.object(bot, "ultra_fast_click") as fast_click,
        ):
            assert bot.dismiss_startup_popups() is False

        fast_click.assert_not_called()

    def test_is_reservation_sku_mode_detects_indicator(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: (
                value == 'new UiSelector().text("预约想看场次")'
            ),
        ):
            assert bot.is_reservation_sku_mode() is True

    def test_get_visible_date_options_deduplicates_blank_values(self, bot):
        element_a = Mock(text="04.04")
        element_b = Mock(text="04.04")
        element_c = Mock(text="  ")
        element_d = Mock(text="04.05")
        with patch.object(
            bot, "_find_all", return_value=[element_a, element_b, element_c, element_d]
        ):
            assert bot.get_visible_date_options() == ["04.04", "04.05"]

    def test_get_visible_price_options_returns_empty_when_container_missing(self, bot):
        bot.driver.find_element.side_effect = Exception("missing")
        assert bot.get_visible_price_options() == []

    def test_get_visible_price_options_returns_empty_when_cards_are_not_a_sequence(
        self, bot
    ):
        price_container = Mock()
        price_container.find_elements.side_effect = lambda by=None, value=None: Mock()
        bot.driver.find_element.return_value = price_container

        assert bot.get_visible_price_options() == []

    def test_get_detail_venue_text_uses_second_resource_id(self, bot):
        with patch.object(
            bot, "_safe_element_text", side_effect=["", "浦发银行东方体育中心"]
        ):
            assert bot._get_detail_venue_text() == "浦发银行东方体育中心"

    def test_ensure_sku_page_for_inspection_returns_existing_sku_page(self, bot):
        page_probe = {"state": "sku_page", "reservation_mode": False}
        assert bot.ensure_sku_page_for_inspection(page_probe) == page_probe

    def test_ensure_sku_page_for_inspection_returns_non_detail_probe_as_is(self, bot):
        page_probe = {"state": "homepage"}
        assert bot.ensure_sku_page_for_inspection(page_probe) == page_probe

    def test_ensure_sku_page_for_inspection_enters_sku_from_detail_page(self, bot):
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with (
            patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click,
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ) as wait_entry,
        ):
            result = bot.ensure_sku_page_for_inspection({"state": "detail_page"})

        assert result == next_probe
        assert smart_click.call_count == 1
        wait_entry.assert_called_once_with(timeout=5, poll_interval=0.04)

    def test_ensure_sku_page_for_inspection_returns_probe_when_click_fails(self, bot):
        with (
            patch.object(bot, "smart_wait_and_click", return_value=False),
            patch.object(
                bot, "probe_current_page", return_value={"state": "detail_page"}
            ) as probe,
        ):
            result = bot.ensure_sku_page_for_inspection({"state": "detail_page"})

        assert result == {"state": "detail_page"}
        probe.assert_called_once()

    def test_inspect_current_target_event_collects_dates_and_prices(self, bot):
        sku_probe = {"state": "sku_page", "reservation_mode": True}
        prices = [{"index": 5, "text": "看台 899元", "tag": "可选", "source": "ui"}]

        with (
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "_dump_hierarchy_xml", return_value=None),
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=sku_probe
            ),
            patch.object(
                bot, "_get_detail_title_text", side_effect=["", "马思唯上海站"]
            ),
            patch.object(
                bot,
                "_get_detail_venue_text",
                side_effect=["", "上海市 · 浦发银行东方体育中心"],
            ),
            patch.object(bot, "get_visible_date_options", return_value=["04.04"]),
            patch.object(bot, "get_visible_price_options", return_value=prices),
        ):
            summary = bot.inspect_current_target_event({"state": "detail_page"})

        assert summary == {
            "state": "sku_page",
            "title": "马思唯上海站",
            "venue": "上海市 · 浦发银行东方体育中心",
            "dates": ["04.04"],
            "price_options": prices,
            "reservation_mode": True,
        }

    def test_inspect_current_target_event_skips_price_reads_outside_sku_page(self, bot):
        with (
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "_dump_hierarchy_xml", return_value=None),
            patch.object(
                bot,
                "_wait_for_purchase_entry_result",
                return_value={"state": "detail_page"},
            ),
            patch.object(bot, "_get_detail_title_text", return_value="马思唯上海站"),
            patch.object(
                bot, "_get_detail_venue_text", return_value="浦发银行东方体育中心"
            ),
            patch.object(bot, "get_visible_date_options") as get_dates,
            patch.object(bot, "get_visible_price_options") as get_prices,
        ):
            summary = bot.inspect_current_target_event({"state": "detail_page"})

        assert summary["state"] == "detail_page"
        assert summary["dates"] == []
        assert summary["price_options"] == []
        get_dates.assert_not_called()
        get_prices.assert_not_called()

    def test_check_session_valid_register_prompt(self, bot, caplog):
        """'登录/注册' text detected on page, returns False."""

        def has_element_side_effect(by, value):
            return "登录/注册" in value

        with (
            caplog.at_level("ERROR", logger="mobile.damai_app"),
            patch.object(bot, "_get_current_activity", return_value="SomeActivity"),
            patch.object(bot, "_has_element", side_effect=has_element_side_effect),
        ):
            result = bot.check_session_valid()

        assert result is False
        assert "登录提示" in caplog.text


# ---------------------------------------------------------------------------
# XML-based hierarchy helpers (u2 fast path)
# ---------------------------------------------------------------------------


def _make_u2_bot():
    """Create a DamaiBot with u2 backend and no real driver setup."""
    cfg = Config(
        server_url=None,
        device_name="Android",
        udid=None,
        platform_version=None,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="test",
        users=["UserA"],
        city="深圳",
        date="04.06",
        price="1680元",
        price_index=9,
        if_commit_order=False,
        probe_only=True,
        driver_backend="u2",
    )
    bot = DamaiBot(config=cfg, setup_driver=False)
    bot.d = Mock()
    bot.driver = bot.d
    return bot


_SIMPLE_HIERARCHY = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" bounds="[0,0][1080,2340]">
    <node index="0" text="张杰未·LIVE演唱会" resource-id="cn.damai:id/title_tv" class="android.widget.TextView" bounds="[0,100][1080,200]" />
    <node index="1" text="北京工人体育场" resource-id="cn.damai:id/venue_name_0" class="android.widget.TextView" bounds="[0,200][1080,260]" />
    <node index="2" text="04.06" resource-id="cn.damai:id/tv_date" class="android.widget.TextView" bounds="[0,300][200,360]" />
    <node index="3" text="04.07" resource-id="cn.damai:id/tv_date" class="android.widget.TextView" bounds="[200,300][400,360]" />
    <node index="4" text="" resource-id="cn.damai:id/project_detail_perform_price_flowlayout" class="android.widget.LinearLayout" bounds="[0,400][1080,900]">
      <node index="0" text="" resource-id="" class="android.widget.FrameLayout" clickable="true" bounds="[0,400][350,600]">
        <node index="0" text="缺货登记" resource-id="" class="android.widget.TextView" bounds="[10,450][340,550]" />
      </node>
      <node index="1" text="" resource-id="" class="android.widget.FrameLayout" clickable="true" bounds="[360,400][710,600]">
        <node index="0" text="" resource-id="" class="android.widget.TextView" bounds="[370,450][700,550]" />
      </node>
    </node>
  </node>
</hierarchy>"""

import xml.etree.ElementTree as ET


class TestXmlHierarchyHelpers:
    def test_xml_find_text_by_resource_id_returns_matching_text(self):
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        assert (
            DamaiBot._xml_find_text_by_resource_id(root, "cn.damai:id/title_tv")
            == "张杰未·LIVE演唱会"
        )

    def test_xml_find_text_by_resource_id_returns_empty_when_missing(self):
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        assert (
            DamaiBot._xml_find_text_by_resource_id(root, "cn.damai:id/nonexistent")
            == ""
        )

    def test_xml_find_text_by_resource_id_returns_empty_for_none_root(self):
        assert (
            DamaiBot._xml_find_text_by_resource_id(None, "cn.damai:id/title_tv") == ""
        )

    def test_get_detail_title_text_uses_xml_root_when_u2(self):
        bot = _make_u2_bot()
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        assert bot._get_detail_title_text(xml_root=root) == "张杰未·LIVE演唱会"

    def test_get_detail_title_text_falls_back_without_xml_root(self, bot):
        with patch.object(bot, "_safe_element_text", return_value="演唱会标题"):
            assert bot._get_detail_title_text() == "演唱会标题"

    def test_get_detail_venue_text_uses_xml_root_when_u2(self):
        bot = _make_u2_bot()
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        assert bot._get_detail_venue_text(xml_root=root) == "北京工人体育场"

    def test_get_detail_venue_text_falls_back_without_xml_root(self, bot):
        with patch.object(
            bot, "_safe_element_text", side_effect=["", "浦发银行东方体育中心"]
        ):
            assert bot._get_detail_venue_text() == "浦发银行东方体育中心"

    def test_get_visible_date_options_uses_xml_root_when_u2(self):
        bot = _make_u2_bot()
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        assert bot.get_visible_date_options(xml_root=root) == ["04.06", "04.07"]

    def test_get_visible_date_options_deduplicates_with_xml_root(self):
        xml = """<hierarchy><node>
          <node text="04.06" resource-id="cn.damai:id/tv_date" bounds="[0,0][100,50]"/>
          <node text="04.06" resource-id="cn.damai:id/tv_date" bounds="[100,0][200,50]"/>
        </node></hierarchy>"""
        bot = _make_u2_bot()
        root = ET.fromstring(xml)
        assert bot.get_visible_date_options(xml_root=root) == ["04.06"]

    def test_get_visible_price_options_from_xml_returns_ui_text(self):
        # Card 0 has text "缺货登记"; Card 1 has no text → filtered out.
        bot = _make_u2_bot()
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        options = bot._get_visible_price_options_from_xml(root, allow_ocr=False)
        assert len(options) == 1
        assert options[0]["index"] == 0
        assert options[0]["text"] == "缺货登记"
        assert options[0]["source"] == "ui"

    def test_get_visible_price_options_from_xml_skips_cards_with_no_text_or_tag(self):
        xml = """<hierarchy><node>
          <node resource-id="cn.damai:id/project_detail_perform_price_flowlayout"
                class="android.widget.LinearLayout" bounds="[0,0][1080,600]">
            <node class="android.widget.FrameLayout" clickable="true" bounds="[0,0][350,200]">
            </node>
          </node>
        </node></hierarchy>"""
        bot = _make_u2_bot()
        root = ET.fromstring(xml)
        options = bot._get_visible_price_options_from_xml(root, allow_ocr=False)
        assert options == []

    def test_get_visible_price_options_from_xml_returns_empty_when_container_missing(
        self,
    ):
        xml = """<hierarchy><node><node text="other" bounds="[0,0][100,100]"/></node></hierarchy>"""
        bot = _make_u2_bot()
        root = ET.fromstring(xml)
        assert bot._get_visible_price_options_from_xml(root, allow_ocr=False) == []

    def test_get_visible_price_options_dispatches_to_xml_path_for_u2(self):
        bot = _make_u2_bot()
        root = ET.fromstring(_SIMPLE_HIERARCHY)
        with patch.object(
            bot._price_sel, "_get_visible_price_options_from_xml", return_value=[]
        ) as xml_fn:
            bot.get_visible_price_options(xml_root=root)
        xml_fn.assert_called_once_with(root, allow_ocr=True)

    def test_dump_hierarchy_xml_returns_parsed_root(self):
        bot = _make_u2_bot()
        bot.d.dump_hierarchy = Mock(return_value="<hierarchy><node/></hierarchy>")
        root = bot._dump_hierarchy_xml()
        assert root is not None
        bot.d.dump_hierarchy.assert_called_once()

    def test_dump_hierarchy_xml_returns_none_on_error(self):
        bot = _make_u2_bot()
        bot.d.dump_hierarchy = Mock(side_effect=Exception("adb error"))
        assert bot._dump_hierarchy_xml() is None

    def test_inspect_current_target_event_dumps_hierarchy_once_for_u2(self):
        bot = _make_u2_bot()
        bot.d.dump_hierarchy = Mock(return_value="<hierarchy><node/></hierarchy>")
        sku_probe = {"state": "sku_page", "reservation_mode": False}

        with (
            patch.object(bot, "probe_current_page", return_value=sku_probe),
            patch.object(bot, "ensure_sku_page_for_inspection", return_value=sku_probe),
            patch.object(bot, "_get_detail_title_text", return_value="演唱会"),
            patch.object(bot, "_get_detail_venue_text", return_value="场馆"),
            patch.object(bot, "get_visible_date_options", return_value=["04.06"]),
            patch.object(bot, "get_visible_price_options", return_value=[]),
        ):
            bot.inspect_current_target_event()

        # Hierarchy should only be dumped once (no re-dump since page didn't navigate).
        bot.d.dump_hierarchy.assert_called_once()


# ---------------------------------------------------------------------------
# Price selection (text match + index fallback)
# ---------------------------------------------------------------------------


class TestPriceSelection:
    def test_select_price_option_fast_rush_mode_trusts_index_without_visible_scan(
        self, bot
    ):
        bot.config.rush_mode = True
        bot.config.price_index = 5

        with (
            patch.object(
                bot, "_click_price_option_by_config_index", return_value=True
            ) as click_index,
            patch.object(bot, "get_visible_price_options") as get_visible,
        ):
            result = bot._select_price_option_fast()

        assert result is True
        click_index.assert_called_once_with(burst=True, coords=None)
        get_visible.assert_not_called()

    def test_select_price_option_fast_rush_mode_uses_cached_coordinates(self, bot):
        bot.config.rush_mode = True

        with (
            patch.object(
                bot, "_click_price_option_by_config_index", return_value=True
            ) as click_index,
            patch.object(bot, "get_visible_price_options") as get_visible,
        ):
            result = bot._select_price_option_fast(cached_coords=(240, 1560))

        assert result is True
        click_index.assert_called_once_with(burst=True, coords=(240, 1560))
        get_visible.assert_not_called()

    def test_select_price_option_fast_uses_config_index_without_ocr(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with (
            patch.object(
                bot,
                "get_visible_price_options",
                return_value=[
                    {"index": 5, "text": "", "tag": "", "source": "ui"},
                ],
            ) as get_visible,
            patch.object(
                bot, "_click_visible_price_option", return_value=True
            ) as click_visible,
        ):
            result = bot._select_price_option_fast()

        assert result is True
        get_visible.assert_called_once_with(allow_ocr=False)
        click_visible.assert_called_once_with(5)

    def test_click_price_option_by_config_index_bursts_clicks_in_rush_mode(self, bot):
        with (
            patch.object(
                bot,
                "_get_price_option_coordinates_by_config_index",
                return_value=(260, 1540),
            ),
            patch.object(bot, "_burst_click_coordinates") as burst_click,
        ):
            result = bot._click_price_option_by_config_index(burst=True)

        assert result is True
        burst_click.assert_called_once_with(
            260, 1540, count=2, interval_ms=25, duration=25
        )

    def test_select_price_option_fast_uses_config_index_when_ui_tree_is_empty(
        self, bot
    ):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with (
            patch.object(bot, "get_visible_price_options", return_value=[]),
            patch.object(
                bot, "_click_price_option_by_config_index", return_value=True
            ) as click_index,
            patch.object(bot, "ultra_fast_click", return_value=False),
        ):
            result = bot._select_price_option_fast()

        assert result is True
        click_index.assert_called_once_with()

    def test_select_price_option_prefers_visible_exact_match(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with (
            patch.object(
                bot,
                "get_visible_price_options",
                return_value=[
                    {"index": 0, "text": "看台699元", "tag": "", "source": "ocr"},
                    {"index": 5, "text": "看台899元", "tag": "", "source": "ocr"},
                ],
            ),
            patch.object(
                bot, "_click_visible_price_option", return_value=True
            ) as click_visible,
            patch.object(bot, "ultra_fast_click") as fast_click,
        ):
            result = bot._select_price_option()

        assert result is True
        click_visible.assert_called_once_with(5)
        fast_click.assert_not_called()

    def test_select_price_option_returns_false_when_target_unavailable(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with (
            patch.object(
                bot,
                "get_visible_price_options",
                return_value=[
                    {
                        "index": 5,
                        "text": "看台899元",
                        "tag": "缺货登记",
                        "source": "ocr",
                    },
                ],
            ),
            patch.object(bot, "_click_visible_price_option") as click_visible,
            patch.object(bot, "ultra_fast_click") as fast_click,
        ):
            result = bot._select_price_option()

        assert result is False
        click_visible.assert_not_called()
        fast_click.assert_not_called()

    def test_submit_order_fast_retries_until_success(self, bot):
        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]

        with (
            patch.object(bot, "ultra_fast_click", side_effect=[True, True]),
            patch.object(bot, "smart_wait_and_click", return_value=False),
            patch.object(
                bot, "verify_order_result", side_effect=["timeout", "success"]
            ) as verify_result,
        ):
            result = bot._submit_order_fast(submit_selectors)

        assert result == "success"
        assert verify_result.call_args_list == [call(timeout=1.2), call(timeout=1.2)]

    def test_submit_order_fast_runs_followup_verify_when_submit_disappears(self, bot):
        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]

        with (
            patch.object(bot, "ultra_fast_click", side_effect=[True, False, False]),
            patch.object(bot, "smart_wait_and_click", return_value=False),
            patch.object(
                bot, "verify_order_result", side_effect=["timeout", "existing_order"]
            ) as verify_result,
        ):
            result = bot._submit_order_fast(submit_selectors)

        assert result == "existing_order"
        assert verify_result.call_args_list == [call(timeout=1.2), call(timeout=2)]

    def test_price_selection_text_match_success(self, bot):
        """Text-based price match works, index fallback not used."""
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(bot, "check_session_valid", return_value=True),
            patch.object(
                bot,
                "probe_current_page",
                return_value={
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
            ),
            patch.object(bot, "wait_for_sale_start"),
            patch.object(bot, "select_performance_date"),
            patch.object(
                bot,
                "_enter_purchase_flow_from_detail_page",
                return_value={
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": False,
                },
            ),
            patch.object(bot, "_wait_for_submit_ready", return_value=True),
            patch.object(bot, "smart_wait_and_click", return_value=True),
            patch.object(bot, "ultra_fast_click", return_value=True) as ufc,
            patch.object(bot, "ultra_batch_click"),
            patch.object(bot, "_submit_order_fast", return_value="success"),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time.side_effect = _make_time_side_effect(0.0, 1.5)
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        # ultra_fast_click should have been called with the price text selector
        price_call_found = any(
            'textContains("799元")' in str(c) for c in ufc.call_args_list
        )
        assert price_call_found, (
            f"Expected price text selector call, got: {ufc.call_args_list}"
        )

    def test_price_selection_falls_back_to_index(self, bot, caplog):
        """Text match fails, index-based fallback used."""
        call_count = [0]

        def ultra_fast_click_side_effect(by, value, timeout=1.5):
            call_count[0] += 1
            # First call with textContains (price) returns False to trigger fallback
            if 'textContains("799元")' in str(value):
                return False
            return True

        import logging as _logging

        _price_logger = _logging.getLogger("mobile.price_selector")
        _price_logger.propagate = True
        try:
            with (
                caplog.at_level("INFO"),
                patch.object(bot, "dismiss_startup_popups"),
                patch.object(bot, "check_session_valid", return_value=True),
                patch.object(
                    bot,
                    "probe_current_page",
                    return_value={
                        "state": "detail_page",
                        "purchase_button": True,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": False,
                    },
                ),
                patch.object(bot, "wait_for_sale_start"),
                patch.object(bot, "select_performance_date"),
                patch.object(
                    bot,
                    "_enter_purchase_flow_from_detail_page",
                    return_value={
                        "state": "sku_page",
                        "price_container": True,
                        "reservation_mode": False,
                    },
                ),
                patch.object(bot, "_wait_for_submit_ready", return_value=True),
                patch.object(bot, "smart_wait_and_click", return_value=True),
                patch.object(
                    bot, "ultra_fast_click", side_effect=ultra_fast_click_side_effect
                ),
                patch.object(bot, "ultra_batch_click"),
                patch.object(bot, "_submit_order_fast", return_value="success"),
                patch("mobile.damai_app.time") as mock_time,
            ):
                mock_time.time.side_effect = _make_time_side_effect(0.0, 1.5)
                # Mock price container for index-based fallback
                mock_target = _make_mock_element()
                with (
                    patch.object(
                        bot._price_sel, "_click_price_card_element", return_value=False
                    ),
                    patch.object(bot, "_find", return_value=Mock()),
                    patch.object(
                        bot,
                        "_container_find_elements",
                        return_value=[mock_target, mock_target],
                    ),
                    patch.object(bot, "_is_clickable", return_value=True),
                    patch.object(bot, "_click_element_center"),
                    patch.object(
                        bot,
                        "_ensure_attendees_selected_on_confirm_page",
                        return_value=True,
                    ),
                ):
                    result = bot.run_ticket_grabbing()
        finally:
            _price_logger.propagate = False

        assert result is True
        assert "通过配置索引直接选择票价" in caplog.text


# ---------------------------------------------------------------------------
# Utility method coverage: _safe_element_text / _safe_element_texts
# ---------------------------------------------------------------------------


class TestSafeElementText:
    def test_returns_first_nonempty_text(self, bot):
        el1 = Mock()
        el1.text = "  "  # whitespace only
        el2 = Mock()
        el2.text = "580元"
        container = Mock()
        with patch.object(bot, "_container_find_elements", return_value=[el1, el2]):
            result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == "580元"

    def test_returns_empty_when_all_empty(self, bot):
        el = Mock()
        el.text = "  "
        container = Mock()
        with patch.object(bot, "_container_find_elements", return_value=[el]):
            result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""

    def test_returns_empty_on_exception(self, bot):
        container = Mock()
        with patch.object(
            bot, "_container_find_elements", side_effect=Exception("driver error")
        ):
            result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""

    def test_returns_empty_when_no_elements(self, bot):
        container = Mock()
        with patch.object(bot, "_container_find_elements", return_value=[]):
            result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""


class TestSafeElementTexts:
    def test_returns_unique_nonempty_texts(self, bot):
        el1 = Mock()
        el1.text = "580元"
        el2 = Mock()
        el2.text = "580元"  # duplicate
        el3 = Mock()
        el3.text = "1280元"
        container = Mock()
        with patch.object(
            bot, "_container_find_elements", return_value=[el1, el2, el3]
        ):
            result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == ["580元", "1280元"]

    def test_returns_empty_list_on_exception(self, bot):
        container = Mock()
        with patch.object(
            bot, "_container_find_elements", side_effect=Exception("driver error")
        ):
            result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == []

    def test_filters_empty_texts(self, bot):
        el1 = Mock()
        el1.text = ""
        el2 = Mock()
        el2.text = "380元"
        container = Mock()
        with patch.object(bot, "_container_find_elements", return_value=[el1, el2]):
            result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == ["380元"]


# ---------------------------------------------------------------------------
# _collect_descendant_texts
# ---------------------------------------------------------------------------


class TestCollectDescendantTexts:
    def test_returns_unique_texts(self, bot):
        """u2 path: parses XML hierarchy for descendant texts."""
        container = Mock()
        container.info = {
            "bounds": {"left": 0, "top": 0, "right": 1080, "bottom": 1920}
        }
        xml = (
            '<hierarchy><node bounds="[0,0][1080,1920]">'
            '<node bounds="[10,10][200,50]" text="580元" />'
            '<node bounds="[10,60][200,100]" text="580元" />'
            '<node bounds="[10,110][200,150]" text="可预约" />'
            "</node></hierarchy>"
        )
        bot.d.dump_hierarchy = Mock(return_value=xml)
        result = bot._collect_descendant_texts(container)
        assert result == ["580元", "可预约"]

    def test_returns_empty_on_info_exception(self, bot):
        """u2 path: exception during info access returns empty list."""
        container = Mock()
        type(container).info = PropertyMock(side_effect=Exception("error"))
        result = bot._collect_descendant_texts(container)
        assert result == []

    def test_handles_element_text_exception(self, bot):
        """u2 path: nodes with empty text are skipped."""
        container = Mock()
        container.info = {
            "bounds": {"left": 0, "top": 0, "right": 1080, "bottom": 1920}
        }
        xml = (
            '<hierarchy><node bounds="[0,0][1080,1920]">'
            '<node bounds="[10,10][200,50]" text="" />'
            '<node bounds="[10,60][200,100]" text="正常文本" />'
            "</node></hierarchy>"
        )
        bot.d.dump_hierarchy = Mock(return_value=xml)
        result = bot._collect_descendant_texts(container)
        assert result == ["正常文本"]


# ---------------------------------------------------------------------------
# _has_element exception path / _get_current_activity exception path
# ---------------------------------------------------------------------------


class TestHasElementExceptionPath:
    def test_has_element_returns_false_on_exception(self, bot):
        with patch.object(bot, "_find", side_effect=Exception("driver error")):
            result = bot._has_element(By.ID, "some_id")
        assert result is False

    def test_has_any_element_returns_false_when_all_miss(self, bot):
        mock_selector = Mock()
        mock_selector.exists = Mock(return_value=False)
        with patch.object(bot, "_find", return_value=mock_selector):
            result = bot._has_any_element([(By.ID, "id1"), (By.ID, "id2")])
        assert result is False


class TestGetCurrentActivityExceptionPath:
    def test_returns_empty_string_on_exception(self, bot):
        type(bot.driver).current_activity = PropertyMock(side_effect=Exception("error"))
        result = bot._get_current_activity()
        assert result == ""
        # cleanup
        type(bot.driver).current_activity = PropertyMock(return_value="SomeActivity")


# ---------------------------------------------------------------------------
# _click_element_center / _burst_click_element_center / _burst_click_coordinates
# ---------------------------------------------------------------------------


class TestClickHelpers:
    def test_click_element_center_calls_u2_click(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        bot._click_element_center(el)
        bot.d.click.assert_called_with(125, 220)

    def test_burst_click_element_center_calls_multiple_times(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        with patch("mobile.ui_primitives.time.sleep") as mock_sleep:
            bot._burst_click_element_center(el, count=3, interval_ms=10)
        assert bot.d.click.call_count >= 3
        assert mock_sleep.call_count == 2  # sleeps between calls

    def test_burst_click_element_center_no_sleep_when_zero_interval(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        bot.d.click.reset_mock()
        with patch("mobile.ui_primitives.time.sleep") as mock_sleep:
            bot._burst_click_element_center(el, count=2, interval_ms=0)
        assert mock_sleep.call_count == 0

    def test_burst_click_coordinates_calls_u2_click(self, bot):
        bot.d.click.reset_mock()
        with patch("mobile.ui_primitives.time.sleep"):
            bot._burst_click_coordinates(100, 200, count=2, interval_ms=10)
        assert bot.d.click.call_count == 2

    def test_burst_click_coordinates_no_sleep_for_single(self, bot):
        bot.d.click.reset_mock()
        with patch("mobile.ui_primitives.time.sleep") as mock_sleep:
            bot._burst_click_coordinates(100, 200, count=1, interval_ms=10)
        assert mock_sleep.call_count == 0


# ---------------------------------------------------------------------------
# smart_wait_for_element — backup selectors and not-found path
# ---------------------------------------------------------------------------


class TestSmartWaitForElement:
    def test_returns_true_on_primary_found(self, bot):
        with patch.object(bot, "_wait_for_element", return_value=Mock()):
            result = bot.smart_wait_for_element(By.ID, "some_id")
        assert result is True

    def test_returns_false_when_all_timeout(self, bot):
        with patch.object(bot, "_wait_for_element", side_effect=TimeoutException()):
            result = bot.smart_wait_for_element(
                By.ID,
                "primary_id",
                backup_selectors=[(By.ID, "backup_id")],
            )
        assert result is False

    def test_uses_backup_when_primary_fails(self, bot):
        call_count = [0]

        def wait_side_effect(by, value, timeout=1.5):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TimeoutException()
            return Mock()

        with patch.object(bot, "_wait_for_element", side_effect=wait_side_effect):
            result = bot.smart_wait_for_element(
                By.ID,
                "primary_id",
                backup_selectors=[(By.ID, "backup_id")],
            )
        assert result is True


# ---------------------------------------------------------------------------
# wait_for_page_state — timeout path
# ---------------------------------------------------------------------------


class TestWaitForPageState:
    def test_returns_last_probe_on_timeout(self, bot):
        with (
            patch.object(
                bot, "probe_current_page", return_value={"state": "unknown_state"}
            ),
            patch("mobile.damai_app.time.time", side_effect=[0.0, 10.0, 20.0]),
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot.wait_for_page_state({"order_confirm_page"}, timeout=5)
        assert result["state"] == "unknown_state"

    def test_returns_immediately_on_matching_state(self, bot):
        with (
            patch.object(
                bot, "probe_current_page", return_value={"state": "detail_page"}
            ),
            patch("mobile.damai_app.time.time", side_effect=[0.0, 1.0]),
            patch("mobile.damai_app.time.sleep"),
        ):
            result = bot.wait_for_page_state({"detail_page"})
        assert result["state"] == "detail_page"


# ---------------------------------------------------------------------------
# Warm Validation Pipeline
# ---------------------------------------------------------------------------


class TestWarmValidationPipeline:
    """Tests for _has_warm_pipeline_coords and _run_warm_validation_pipeline."""

    def _populate_coords(self, bot):
        """Fill all coords required by the warm pipeline."""
        bot._cached_hot_path_coords.update(
            {
                "detail_buy": (540, 1800),
                "price": (300, 1200),
                "sku_buy": (540, 2100),
                "attendee_checkboxes": [(100, 900)],
                "city": (200, 600),
            }
        )

    def test_has_warm_pipeline_coords_all_present(self, bot):
        self._populate_coords(bot)
        assert bot._has_warm_pipeline_coords() is True

    def test_has_warm_pipeline_coords_missing_price(self, bot):
        self._populate_coords(bot)
        del bot._cached_hot_path_coords["price"]
        assert bot._has_warm_pipeline_coords() is False

    def test_has_warm_pipeline_coords_missing_attendee(self, bot):
        self._populate_coords(bot)
        del bot._cached_hot_path_coords["attendee_checkboxes"]
        assert bot._has_warm_pipeline_coords() is False

    def test_pipeline_success_with_city(self, bot):
        """Warm pipeline batches filters/detail taps and can confirm via shell fast path."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot.config.city = "北京"
        bot.config.users = ["UserA"]

        self._populate_coords(bot)

        mock_d = Mock()
        mock_d.shell = Mock(return_value=("", ""))
        bot.d = mock_d
        bot.driver = mock_d
        if hasattr(bot, "_pipeline"):
            bot._pipeline._device = mock_d

        with (
            patch.object(
                bot,
                "_wait_for_purchase_entry_result",
                return_value={"state": "sku_page"},
            ),
            patch.object(bot, "_click_price_option_by_config_index") as price_click,
            patch.object(bot, "_click_sku_buy_button_element") as buy_click,
            patch.object(bot, "_click_coordinates") as click_coords,
            patch.object(
                bot._pipeline, "_confirm_page_ready", side_effect=[False, True]
            ),
        ):
            result = bot._run_warm_validation_pipeline(start_time=_time_module.time())

        assert result is True
        assert mock_d.shell.call_count >= 2
        first_shell = mock_d.shell.call_args_list[0][0][0]
        assert "input tap 200 600" in first_shell  # city
        assert first_shell.count("input tap 540 1800") == 2
        second_shell = mock_d.shell.call_args_list[1][0][0]
        assert "input tap 300 1200" in second_shell
        assert second_shell.count("input tap 540 2100") == 2
        detail_calls = [
            c for c in click_coords.call_args_list if c[0][:2] == (540, 1800)
        ]
        assert len(detail_calls) == 0
        buy_click.assert_not_called()
        price_click.assert_not_called()
        # Attendee also clicked via _click_coordinates
        attendee_calls = [c for c in click_coords.call_args_list if c[0] == (100, 900)]
        assert len(attendee_calls) == 1

    def test_pipeline_success_without_city(self, bot):
        """Pipeline skips city when city in no_match."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot.config.city = "北京"
        bot.config.users = ["UserA"]

        self._populate_coords(bot)
        bot._cached_hot_path_no_match.add("city")

        mock_d = Mock()
        mock_d.shell = Mock(return_value=("", ""))
        bot.d = mock_d
        bot.driver = mock_d
        if hasattr(bot, "_pipeline"):
            bot._pipeline._device = mock_d

        with (
            patch.object(
                bot,
                "_wait_for_purchase_entry_result",
                return_value={"state": "sku_page"},
            ),
            patch.object(bot, "_click_price_option_by_config_index") as price_click,
            patch.object(bot, "_click_sku_buy_button_element") as buy_click,
            patch.object(bot, "_click_coordinates"),
            patch.object(
                bot._pipeline, "_confirm_page_ready", side_effect=[False, True]
            ),
        ):
            result = bot._run_warm_validation_pipeline(start_time=_time_module.time())

        assert result is True
        assert mock_d.shell.call_count >= 2
        first_shell = mock_d.shell.call_args_list[0][0][0]
        assert "input tap 540 1800" in first_shell
        assert "input tap 200 600" not in first_shell
        buy_click.assert_not_called()
        price_click.assert_not_called()

    def test_pipeline_returns_none_on_timeout(self, bot):
        """Pipeline returns None if confirm page never detected."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot.config.users = ["UserA"]

        self._populate_coords(bot)

        mock_d = Mock()
        mock_d.shell = Mock(return_value=("", ""))
        bot.d = mock_d
        bot.driver = mock_d
        if hasattr(bot, "_pipeline"):
            bot._pipeline._device = mock_d

        with (
            patch.object(
                bot,
                "_wait_for_purchase_entry_result",
                return_value={"state": "sku_page"},
            ),
            patch.object(bot, "_click_price_option_by_config_index", return_value=True),
            patch.object(bot, "_click_sku_buy_button_element", return_value=True),
            patch.object(bot._pipeline, "_confirm_page_ready", side_effect=False),
            patch("mobile.damai_app.time") as mock_time,
        ):
            mock_time.time = Mock(side_effect=[100.0, 100.0, 109.0])
            mock_time.sleep = Mock()
            result = bot._run_warm_validation_pipeline(start_time=100.0)

        assert result is None

    def test_pipeline_detail_and_buy_fallbacks_use_u2_when_shell_fast_path_misses(
        self, bot
    ):
        """Warm pipeline falls back to u2 detail/buy clicks when shell taps do not advance."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        bot.config.users = ["UserA"]

        self._populate_coords(bot)

        mock_d = Mock()
        mock_d.shell = Mock(return_value=("", ""))
        bot.d = mock_d
        bot.driver = mock_d
        if hasattr(bot, "_pipeline"):
            bot._pipeline._device = mock_d

        with (
            patch.object(
                bot,
                "_wait_for_purchase_entry_result",
                side_effect=[None, {"state": "sku_page"}],
            ),
            patch.object(
                bot._pipeline, "_shell_price_and_buy_until_confirm", return_value=False
            ),
            patch.object(bot, "_click_price_option_by_config_index", return_value=True),
            patch.object(
                bot, "_click_sku_buy_button_element", return_value=True
            ) as buy_click,
            patch.object(bot, "_click_coordinates") as click_coords,
            patch.object(
                bot._pipeline, "_confirm_page_ready", side_effect=[False, True]
            ),
        ):
            result = bot._run_warm_validation_pipeline(start_time=_time_module.time())

        assert result is True
        detail_calls = [
            c for c in click_coords.call_args_list if c[0][:2] == (540, 1800)
        ]
        assert len(detail_calls) == 1, (
            f"Expected detail CTA u2 fallback, got: {click_coords.call_args_list}"
        )
        buy_click.assert_called_once_with(burst_count=1)

    def test_pipeline_hooks_into_run_ticket_grabbing(self, bot):
        """run_ticket_grabbing uses pipeline on warm validation retry with cached coords."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        self._populate_coords(bot)
        initial_probe = {"state": "detail_page", "purchase_button": True}

        with patch.object(
            bot, "_run_warm_validation_pipeline", return_value=True
        ) as pipeline:
            result = bot.run_ticket_grabbing(initial_page_probe=initial_probe)

        assert result is True
        pipeline.assert_called_once()

    def test_pipeline_fallback_on_missing_coords(self, bot):
        """run_ticket_grabbing falls through to normal flow when coords not cached."""
        bot.config.rush_mode = True
        bot.config.if_commit_order = False
        # Don't populate coords
        initial_probe = {"state": "detail_page", "purchase_button": True}

        with (
            patch.object(bot, "_run_warm_validation_pipeline") as pipeline,
            patch.object(
                bot, "_enter_purchase_flow_from_detail_page", return_value=None
            ),
        ):
            result = bot.run_ticket_grabbing(initial_page_probe=initial_probe)

        pipeline.assert_not_called()  # _has_warm_pipeline_coords returned False


# ---------------------------------------------------------------------------
# Fast Back to Detail Page
# ---------------------------------------------------------------------------


class TestRecoverToDetailPage:
    """Tests for _recover_to_detail_page_for_local_retry."""

    def test_recover_uses_fast_probe_in_back_loop(self, bot):
        """Back-navigation loop should use probe_current_page(fast=True)."""
        initial_probe = {"state": "order_confirm_page"}
        detail_result = {
            "state": "detail_page",
            "purchase_button": True,
            "price_container": False,
            "quantity_picker": False,
            "submit_button": False,
            "reservation_mode": False,
            "pending_order_dialog": False,
        }
        # First probe_current_page call (after dismiss_startup_popups) returns order_confirm;
        # second call (in back-loop with fast=True) returns detail_page.
        with (
            patch.object(bot, "dismiss_startup_popups"),
            patch.object(
                bot,
                "probe_current_page",
                side_effect=[{"state": "order_confirm_page"}, detail_result],
            ) as probe_mock,
            patch.object(bot, "_press_keycode_safe", return_value=True),
        ):
            result = bot._recover_to_detail_page_for_local_retry(initial_probe)
        assert result["state"] == "detail_page"
        # Second call should be fast=True (back-loop)
        assert probe_mock.call_count == 2
        assert probe_mock.call_args_list[1] == call(fast=True)


# ---------------------------------------------------------------------------
# Cold Rush XML Dump Optimization
# ---------------------------------------------------------------------------


class TestRushPreSelectViaXml:
    """Tests for _extract_coords_from_xml_node and _rush_preselect_and_buy_via_xml."""

    DETAIL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node resource-id="cn.damai:id/title_tv" text="张杰演唱会" bounds="[0,200][1080,260]"/>
  <node resource-id="" text="北京" bounds="[100,500][200,550]"/>
  <node resource-id="" text="04.18" bounds="[300,500][400,550]"/>
  <node resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        text="" bounds="[0,1800][1080,1920]"/>
</hierarchy>"""

    DETAIL_XML_NO_CITY = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        text="" bounds="[0,1800][1080,1920]"/>
</hierarchy>"""

    def test_extract_coords_from_xml_node(self, bot):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self.DETAIL_XML)
        buy_node = root.find(
            './/*[@resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"]'
        )
        coords = bot._extract_coords_from_xml_node(buy_node)
        assert coords == (540, 1860)

    def test_extract_coords_no_bounds(self, bot):
        import xml.etree.ElementTree as ET

        node = ET.fromstring('<node resource-id="x" text="y"/>')
        assert bot._extract_coords_from_xml_node(node) is None

    def test_rush_preselect_finds_city_date_buy(self, bot):
        """Single XML dump extracts city, date, and buy button coords, batch-clicked via shell."""
        import xml.etree.ElementTree as ET

        bot.config.city = "北京"
        bot.config.date = "04.18"
        bot.config.rush_mode = True

        mock_d = Mock()
        bot.d = mock_d
        bot.driver = mock_d
        if hasattr(bot, "_pipeline"):
            bot._pipeline._device = mock_d
        with (
            patch.object(bot, "_dismiss_fast_blocking_dialogs", return_value=False),
            patch.object(
                bot, "_dump_hierarchy_xml", return_value=ET.fromstring(self.DETAIL_XML)
            ),
        ):
            result = bot._rush_preselect_and_buy_via_xml()

        assert result is True
        assert bot._cached_hot_path_coords["city"] == (150, 525)
        assert bot._cached_hot_path_coords["date"] == (350, 525)
        assert bot._cached_hot_path_coords["detail_buy"] == (540, 1860)
        # Date/city/detail taps are batched; detail buy is fired twice for stability.
        mock_d.shell.assert_called_once()
        shell_cmd = mock_d.shell.call_args[0][0]
        assert "input tap 350 525" in shell_cmd  # date
        assert "input tap 150 525" in shell_cmd  # city
        assert shell_cmd.count("input tap 540 1860") == 2

    def test_rush_preselect_no_city_adds_no_match(self, bot):
        """City not found → added to _cached_hot_path_no_match."""
        import xml.etree.ElementTree as ET

        bot.config.city = "上海"
        bot.config.date = None
        bot.config.rush_mode = True

        mock_d = Mock()
        bot.d = mock_d
        with (
            patch.object(bot, "_dismiss_fast_blocking_dialogs", return_value=False),
            patch.object(
                bot,
                "_dump_hierarchy_xml",
                return_value=ET.fromstring(self.DETAIL_XML_NO_CITY),
            ),
        ):
            result = bot._rush_preselect_and_buy_via_xml()

        assert result is True
        assert "city" in bot._cached_hot_path_no_match
        assert bot._cached_hot_path_coords["detail_buy"] == (540, 1860)

    def test_rush_preselect_no_buy_returns_false(self, bot):
        """Buy button not found → returns False."""
        import xml.etree.ElementTree as ET

        bot.config.city = "北京"
        bot.config.rush_mode = True
        no_buy_xml = (
            '<hierarchy><node text="北京" bounds="[100,500][200,550]"/></hierarchy>'
        )

        mock_d = Mock()
        bot.d = mock_d
        with (
            patch.object(bot, "_dismiss_fast_blocking_dialogs", return_value=False),
            patch.object(
                bot, "_dump_hierarchy_xml", return_value=ET.fromstring(no_buy_xml)
            ),
        ):
            result = bot._rush_preselect_and_buy_via_xml()

        assert result is False

    def test_rush_preselect_xml_dump_fails(self, bot):
        """dump_hierarchy returns None → returns False."""

        bot.config.rush_mode = True

        with patch.object(bot, "_dump_hierarchy_xml", return_value=None):
            result = bot._rush_preselect_and_buy_via_xml()

        assert result is False

    def test_enter_purchase_flow_uses_xml_on_cold_u2(self, bot):
        """Cold u2 rush mode uses XML dump instead of multiple _cached_tap calls."""

        bot.config.rush_mode = True
        bot.config.city = "北京"

        next_probe = {
            "state": "sku_page",
            "price_container": True,
            "reservation_mode": False,
        }
        with (
            patch.object(
                bot, "_rush_preselect_and_buy_via_xml", return_value=True
            ) as xml_method,
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ),
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        assert result == next_probe
        xml_method.assert_called_once()

    def test_enter_purchase_flow_warm_uses_cached_tap(self, bot):
        """Warm path (detail_buy already cached) uses _cached_tap, not XML dump."""
        bot.config.rush_mode = True
        bot._cached_hot_path_coords["detail_buy"] = (540, 1860)

        next_probe = {
            "state": "sku_page",
            "price_container": True,
            "reservation_mode": False,
        }
        with (
            patch.object(bot, "_rush_preselect_and_buy_via_xml") as xml_method,
            patch.object(bot, "_cached_tap", return_value=True),
            patch.object(
                bot, "_wait_for_purchase_entry_result", return_value=next_probe
            ),
        ):
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        xml_method.assert_not_called()  # warm path, skip XML dump


# ---------------------------------------------------------------------------
# Coverage gap: element property helpers (_element_rect, _is_clickable,
#                                          _is_checked)
# ---------------------------------------------------------------------------


class TestElementPropertyHelpers:
    """Cover element attribute extraction (lines 492-554)."""

    # -- _element_rect --

    def test_element_rect_from_dict(self, bot):
        el = Mock()
        el.rect = {"x": 10, "y": 20, "width": 100, "height": 50}
        assert bot._element_rect(el) == {"x": 10, "y": 20, "width": 100, "height": 50}

    def test_element_rect_from_tuple(self, bot):
        el = Mock()
        el.rect = (10, 20, 100, 50)
        assert bot._element_rect(el) == {"x": 10, "y": 20, "width": 100, "height": 50}

    def test_element_rect_from_bounds_tuple(self, bot):
        el = Mock(spec=[])  # no .rect
        el.bounds = (0, 100, 200, 300)
        assert bot._element_rect(el) == {"x": 0, "y": 100, "width": 200, "height": 200}

    def test_element_rect_from_bounds_exception_falls_to_info(self, bot):
        el = Mock(spec=[])  # no .rect
        type(el).bounds = PropertyMock(side_effect=RuntimeError)
        el.info = {"bounds": {"left": 5, "top": 10, "right": 105, "bottom": 60}}
        assert bot._element_rect(el) == {"x": 5, "y": 10, "width": 100, "height": 50}

    def test_element_rect_from_info_dict(self, bot):
        el = Mock(spec=[])  # no .rect, no .bounds
        el.info = {"bounds": {"left": 0, "top": 0, "right": 50, "bottom": 80}}
        assert bot._element_rect(el) == {"x": 0, "y": 0, "width": 50, "height": 80}

    def test_element_rect_info_empty_bounds(self, bot):
        el = Mock(spec=[])
        el.info = {"bounds": {}}
        assert bot._element_rect(el) == {"x": 0, "y": 0, "width": 0, "height": 0}

    # -- _is_clickable --

    def test_is_clickable_via_get_attribute_true(self):
        el = Mock()
        el.get_attribute = Mock(return_value="true")
        assert DamaiBot._is_clickable(el) is True

    def test_is_clickable_via_get_attribute_false(self):
        el = Mock()
        el.get_attribute = Mock(return_value="false")
        assert DamaiBot._is_clickable(el) is False

    def test_is_clickable_get_attribute_raises(self):
        el = Mock()
        el.get_attribute = Mock(side_effect=RuntimeError)
        assert DamaiBot._is_clickable(el) is False

    def test_is_clickable_via_info(self):
        el = Mock(spec=[])  # no get_attribute
        el.info = {"clickable": True}
        assert DamaiBot._is_clickable(el) is True

    def test_is_clickable_info_raises(self):
        el = Mock(spec=[])
        type(el).info = PropertyMock(side_effect=RuntimeError)
        assert DamaiBot._is_clickable(el) is False

    # -- _is_checked --

    def test_is_checked_via_get_attribute_true(self):
        el = Mock()
        el.get_attribute = Mock(return_value="true")
        assert DamaiBot._is_checked(el) is True

    def test_is_checked_via_get_attribute_false(self):
        el = Mock()
        el.get_attribute = Mock(return_value="false")
        assert DamaiBot._is_checked(el) is False

    def test_is_checked_get_attribute_raises(self):
        el = Mock()
        el.get_attribute = Mock(side_effect=RuntimeError)
        assert DamaiBot._is_checked(el) is False

    def test_is_checked_via_info(self):
        el = Mock(spec=[])
        el.info = {"checked": True}
        assert DamaiBot._is_checked(el) is True

    def test_is_checked_info_raises(self):
        el = Mock(spec=[])
        type(el).info = PropertyMock(side_effect=RuntimeError)
        assert DamaiBot._is_checked(el) is False


# ---------------------------------------------------------------------------
# Coverage gap: _container_find_elements (lines 556-618)
# ---------------------------------------------------------------------------


class TestContainerFindElements:
    """Cover container-scoped element lookups."""

    def test_delegates_to_find_all_when_container_is_driver(self, bot):
        """When container is self.driver, delegates to _find_all."""
        fake_results = [Mock()]
        with patch.object(bot, "_find_all", return_value=fake_results):
            result = bot._container_find_elements(bot.driver, By.ID, "some_id")
        assert result == fake_results

    def test_u2_container_by_id_via_elem_iter(self, bot):
        """u2 backend: container.elem.iter() filters by resource-id."""
        node_match = Mock()
        node_match.get = Mock(
            side_effect=lambda k: "target_id" if k == "resource-id" else None
        )
        node_miss = Mock()
        node_miss.get = Mock(
            side_effect=lambda k: "other_id" if k == "resource-id" else None
        )

        container = Mock()
        container.elem = Mock()
        container.elem.iter = Mock(return_value=[node_match, node_miss])

        result = bot._container_find_elements(container, By.ID, "target_id")
        assert result == [node_match]

    def test_u2_container_by_class_via_elem_iter(self, bot):
        """u2 backend: container.elem.iter() filters by class name."""
        node = Mock()
        node.get = Mock(
            side_effect=lambda k: "android.widget.TextView" if k == "class" else None
        )

        container = Mock()
        container.elem = Mock()
        container.elem.iter = Mock(return_value=[node])

        result = bot._container_find_elements(
            container, By.CLASS_NAME, "android.widget.TextView"
        )
        assert result == [node]

    def test_u2_container_by_id_child_iteration(self, bot):
        """u2 backend: falls back to child() iteration when no elem."""
        child0 = Mock()
        child0.exists = Mock(return_value=True)
        child0.info = {"resourceId": "target_id"}

        child1 = Mock()
        child1.exists = Mock(return_value=False)

        container = Mock(spec=["child"])
        container.child = Mock(side_effect=[child0, child1])

        with patch.object(bot, "_selector_exists", side_effect=[True, False]):
            result = bot._container_find_elements(container, By.ID, "target_id")
        assert result == [child0]

    def test_u2_container_unknown_by_returns_empty(self, bot):
        """u2 backend: unknown 'by' strategy returns empty list."""
        container = Mock(spec=[])
        result = bot._container_find_elements(container, "unknown_by", "val")
        assert result == []

    def test_u2_container_elem_iter_exception(self, bot):
        """u2 backend: elem.iter() raises → falls to child iteration."""
        container = Mock(spec=["child", "elem"])
        container.elem = Mock()
        container.elem.iter = Mock(side_effect=RuntimeError("broken"))
        # child iteration — no children exist
        child_none = Mock()
        child_none.exists = Mock(return_value=False)
        container.child = Mock(return_value=child_none)

        with patch.object(bot, "_selector_exists", return_value=False):
            result = bot._container_find_elements(container, By.ID, "some_id")
        assert result == []


# ---------------------------------------------------------------------------
# Coverage gap: _selector_exists / _wait_for_element (lines 457-490)
# ---------------------------------------------------------------------------


class TestSelectorExistsAndWait:
    """Cover element existence checking and wait helpers."""

    def test_selector_exists_callable_with_timeout(self):
        sel = Mock()
        sel.exists = Mock(return_value=True)
        assert DamaiBot._selector_exists(sel) is True
        sel.exists.assert_called_with(timeout=0)

    def test_selector_exists_callable_fallback_no_timeout(self):
        """exists(timeout=0) raises TypeError → falls back to exists()."""
        sel = Mock()
        sel.exists = Mock(side_effect=[TypeError, True])
        assert DamaiBot._selector_exists(sel) is True

    def test_selector_exists_callable_exception(self):
        """exists() raises non-TypeError → returns False."""
        sel = Mock()
        sel.exists = Mock(side_effect=RuntimeError)
        assert DamaiBot._selector_exists(sel) is False

    def test_selector_exists_bool_true(self):
        sel = Mock(spec=[])
        sel.exists = True
        assert DamaiBot._selector_exists(sel) is True

    def test_selector_exists_bool_false(self):
        sel = Mock(spec=[])
        sel.exists = False
        assert DamaiBot._selector_exists(sel) is False

    def test_selector_exists_via_wait(self):
        """No exists attr → falls to wait(timeout=0)."""
        sel = Mock(spec=["wait"])
        sel.wait = Mock(return_value=True)
        assert DamaiBot._selector_exists(sel) is True

    def test_selector_exists_wait_exception(self):
        sel = Mock(spec=["wait"])
        sel.wait = Mock(side_effect=RuntimeError)
        assert DamaiBot._selector_exists(sel) is False

    def test_selector_exists_nothing_returns_false(self):
        """No exists, no wait → False."""
        sel = Mock(spec=[])
        assert DamaiBot._selector_exists(sel) is False


# ---------------------------------------------------------------------------
# SALE_READY_TEXTS — issue #29 「立即预订」文案兼容性
# ---------------------------------------------------------------------------


class TestSaleReadyTexts:
    """Verify wait_for_sale_start / _is_sale_ready handle every SALE_READY_TEXTS variant.

    Damai shipped a UI string change ("立即预定" → "立即预订") in 2026-04 that broke
    the previous hard-coded list. These tests pin every supported variant so a
    future copy change cannot regress silently (issue #29).
    """

    @pytest.mark.parametrize(
        "variant",
        [
            "立即购票",
            "立即预定",
            "立即预订",
            "立即抢票",
            "Book Now",
        ],
    )
    def test_book_now_text_variants_recognized(self, bot, variant):
        """Each SALE_READY_TEXTS member must make _is_sale_ready return True."""
        from mobile.damai_app import SALE_READY_TEXTS

        assert variant in SALE_READY_TEXTS, (
            f"{variant!r} dropped from SALE_READY_TEXTS — issue #29 regression"
        )

        def has_element(by, value):
            return f'textContains("{variant}")' in str(value)

        with patch.object(bot, "_has_element", side_effect=has_element):
            assert bot._is_sale_ready() is True
            assert getattr(bot, "_last_sale_ready_text", None) == variant

    def test_wait_for_sale_start_polls_with_book_now(self, bot, monkeypatch):
        """When the page surfaces 「立即预订」, wait_for_sale_start returns within 1s."""
        _tz = timezone(timedelta(hours=8))
        # Sale starts 1s in the future so we enter the polling branch (not the
        # "开售时间已过，跳过等待" early-return branch). countdown_lead_ms=0 keeps the
        # pre-roll sleep at zero.
        sell_time = datetime(2026, 6, 1, 20, 0, 1, tzinfo=_tz)
        now_base = datetime(2026, 6, 1, 20, 0, 0, tzinfo=_tz)
        bot.config.sell_start_time = sell_time.isoformat()
        bot.config.countdown_lead_ms = 0

        # Stage datetime.now so we (a) pass the "已过" check, (b) deterministically
        # enter the polling loop with deadline still in the future.
        now_calls = [0]

        def mock_now(tz=None):
            now_calls[0] += 1
            if now_calls[0] == 1:
                # First call: "is sale already over?" → we are 1s before sell_time
                return now_base
            if now_calls[0] == 2:
                # Second call: compute pre-roll sleep_seconds (sell_time - lead - now)
                return now_base
            # During polling: stay before deadline so the loop can run at least once
            return now_base

        # BuyButtonGuard NOT used in this test path
        bot._guard = Mock()
        bot._guard.wait_until_safe = Mock(return_value=False)

        # _has_element returns True only for textContains("立即预订")
        def has_element(by, value):
            return 'textContains("立即预订")' in str(value)

        # Skip real sleeps to keep the test fast
        sleep_calls = []
        monkeypatch.setattr(
            "mobile.damai_app.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        wall_start = _time_module.perf_counter()
        with (
            patch("mobile.damai_app.datetime") as mock_dt,
            patch.object(bot, "_has_element", side_effect=has_element),
        ):
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.now = mock_now
            bot.wait_for_sale_start()
        wall_elapsed = _time_module.perf_counter() - wall_start

        assert wall_elapsed < 1.0, (
            f"wait_for_sale_start should detect 立即预订 quickly, took {wall_elapsed:.3f}s"
        )
        assert getattr(bot, "_last_sale_ready_text", None) == "立即预订"
