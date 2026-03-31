# -*- coding: UTF-8 -*-
"""Unit tests for mobile/damai_app.py — DamaiBot class."""

from itertools import chain, repeat
import time as _time_module
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import Mock, patch, call, PropertyMock

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By

from mobile.damai_app import DamaiBot, logger as damai_logger
from mobile.config import Config
from mobile.item_resolver import DamaiItemDetail


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    """Enable propagation on the damai_app logger so caplog can capture messages."""
    damai_logger.propagate = True
    yield
    damai_logger.propagate = False


@pytest.fixture
def bot():
    """Create a DamaiBot with fully mocked Appium driver and config."""
    mock_driver = Mock()
    mock_driver.update_settings = Mock()
    mock_driver.execute_script = Mock()
    mock_driver.find_element = Mock()
    mock_driver.find_elements = Mock(return_value=[])
    mock_driver.quit = Mock()
    mock_driver.current_activity = "ProjectDetailActivity"

    mock_config = Config(
        server_url="http://127.0.0.1:4723",
        device_name="Android",
        udid=None,
        platform_version=None,
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

    with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
         patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
         patch("mobile.damai_app.AppiumOptions"):
        bot = DamaiBot()
    return bot


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_init_accepts_injected_config(self):
        mock_driver = Mock()
        mock_driver.update_settings = Mock()

        injected_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
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

        with patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.Config.load_config") as load_config:
            bot = DamaiBot(config=injected_config)

        assert bot.config is injected_config
        load_config.assert_not_called()

    def test_init_loads_config_and_driver(self, bot):
        """Config is loaded and driver is created during __init__."""
        assert bot.config is not None
        assert bot.config.city == "深圳"
        assert bot.config.users == ["UserA", "UserB"]
        assert bot.driver is not None

    def test_setup_driver_sets_wait(self, bot):
        """_setup_driver sets self.wait (WebDriverWait instance)."""
        assert bot.wait is not None
        # update_settings was called during setup
        bot.driver.update_settings.assert_called_once()

    def test_build_capabilities_uses_real_device_config(self):
        mock_driver = Mock()
        mock_driver.update_settings = Mock()

        mock_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Pixel 8",
            udid="R58M123456A",
            platform_version="14",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch.object(DamaiBot, "_list_connected_device_ids", return_value=["R58M123456A"]), \
             patch.object(DamaiBot, "_read_device_android_version", return_value="14"), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"):
            bot = DamaiBot()

        capabilities = bot._build_capabilities()
        assert capabilities["deviceName"] == "Pixel 8"
        assert capabilities["udid"] == "R58M123456A"
        assert capabilities["platformVersion"] == "14"
        assert capabilities["appPackage"] == "cn.damai"
        assert capabilities["appActivity"] == ".launcher.splash.SplashMainActivity"

    def test_init_resolves_item_url_and_fills_keyword(self):
        mock_driver = Mock()
        mock_driver.update_settings = Mock()

        mock_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword=None,
            item_url="https://m.damai.cn/shows/item.html?itemId=1016133935724",
            users=["UserA"],
            city="北京",
            date="04.06",
            price="380元",
            price_index=0,
            if_commit_order=False,
            probe_only=True,
        )
        item_detail = _make_item_detail()

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.DamaiItemResolver.fetch_item_detail", return_value=item_detail), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"):
            bot = DamaiBot()

        assert bot.item_detail == item_detail
        assert bot.config.item_id == "1016133935724"
        assert bot.config.keyword == item_detail.search_keyword

    def test_setup_driver_raises_clear_error_for_missing_udid(self):
        cfg = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid="emulator-5554",
            platform_version="15",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with patch.object(DamaiBot, "_list_connected_device_ids", return_value=["c6c4eb67"]), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.webdriver.Remote") as mock_remote:
            with pytest.raises(ValueError, match="udid=.*不在已连接设备列表"):
                DamaiBot(config=cfg)

        mock_remote.assert_not_called()

    def test_setup_driver_raises_clear_error_for_platform_version_mismatch(self):
        cfg = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid="c6c4eb67",
            platform_version="15",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["UserA"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with patch.object(DamaiBot, "_list_connected_device_ids", return_value=["c6c4eb67"]), \
             patch.object(DamaiBot, "_read_device_android_version", return_value="16"), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.webdriver.Remote") as mock_remote:
            with pytest.raises(ValueError, match="platform_version=.*不一致"):
                DamaiBot(config=cfg)

        mock_remote.assert_not_called()


# ---------------------------------------------------------------------------
# ultra_fast_click
# ---------------------------------------------------------------------------

class TestUltraFastClick:
    def test_ultra_fast_click_success(self, bot):
        """Element found, gesture click executed with center coords, returns True."""
        mock_el = _make_mock_element(x=100, y=200, width=50, height=40)

        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.return_value = mock_el
            result = bot.ultra_fast_click("by", "value")

        assert result is True
        bot.driver.execute_script.assert_called_once_with(
            "mobile: clickGesture",
            {"x": 125, "y": 220, "duration": 50},
        )

    def test_ultra_fast_click_timeout(self, bot):
        """WebDriverWait raises TimeoutException, returns False."""
        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.side_effect = TimeoutException("timeout")
            result = bot.ultra_fast_click("by", "value")

        assert result is False


# ---------------------------------------------------------------------------
# batch_click
# ---------------------------------------------------------------------------

class TestBatchClick:
    def test_batch_click_all_success(self, bot):
        """ultra_fast_click called for each element pair."""
        elements = [("by1", "v1"), ("by2", "v2"), ("by3", "v3")]
        with patch.object(bot, "ultra_fast_click", return_value=True) as ufc, \
             patch("mobile.damai_app.time") as mock_time:
            bot.batch_click(elements, delay=0.1)

        assert ufc.call_count == 3
        ufc.assert_any_call("by1", "v1")
        ufc.assert_any_call("by2", "v2")
        ufc.assert_any_call("by3", "v3")

    def test_batch_click_some_fail(self, bot, caplog):
        """Failed clicks log a warning but processing continues."""
        elements = [("by1", "v1"), ("by2", "v2")]
        with caplog.at_level("WARNING", logger="mobile.damai_app"), \
             patch.object(bot, "ultra_fast_click", side_effect=[False, True]) as ufc, \
             patch("mobile.damai_app.time"):
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

        with caplog.at_level("DEBUG", logger="mobile.damai_app"), \
             patch("mobile.damai_app.WebDriverWait") as MockWait, \
             patch("mobile.damai_app.time"):
            MockWait.return_value.until.side_effect = [el1, el2]
            bot.ultra_batch_click([("by1", "v1"), ("by2", "v2")], timeout=2)

        # Two clickGesture calls with correct center coordinates
        calls = bot.driver.execute_script.call_args_list
        assert len(calls) == 2
        assert calls[0] == call("mobile: clickGesture", {"x": 60, "y": 45, "duration": 30})
        assert calls[1] == call("mobile: clickGesture", {"x": 230, "y": 315, "duration": 30})

        assert "成功找到 2 个用户" in caplog.text

    def test_ultra_batch_click_timeout_skips(self, bot, caplog):
        """Timed-out elements are skipped; found ones are still clicked."""
        el1 = _make_mock_element(x=10, y=20, width=100, height=50)

        with caplog.at_level("DEBUG", logger="mobile.damai_app"), \
             patch("mobile.damai_app.WebDriverWait") as MockWait, \
             patch("mobile.damai_app.time"):
            MockWait.return_value.until.side_effect = [
                el1,
                TimeoutException("timeout"),
            ]
            bot.ultra_batch_click([("by1", "v1"), ("by2", "v2")], timeout=2)

        # Only 1 click executed (the successful one)
        assert bot.driver.execute_script.call_count == 1
        assert "超时未找到用户: v2" in caplog.text
        assert "成功找到 1 个用户" in caplog.text


# ---------------------------------------------------------------------------
# smart_wait_and_click
# ---------------------------------------------------------------------------

class TestSmartWaitAndClick:
    def test_smart_wait_and_click_primary_success(self, bot):
        """Primary selector works on first try, returns True."""
        mock_el = _make_mock_element()
        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.return_value = mock_el
            result = bot.smart_wait_and_click("by", "value")

        assert result is True
        bot.driver.execute_script.assert_called_once()

    def test_smart_wait_and_click_backup_success(self, bot):
        """Primary fails (TimeoutException), backup selector works."""
        mock_el = _make_mock_element()
        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.side_effect = [
                TimeoutException("primary failed"),
                mock_el,
            ]
            result = bot.smart_wait_and_click(
                "by", "value",
                backup_selectors=[("by2", "backup_value")],
            )

        assert result is True

    def test_smart_wait_and_click_all_fail(self, bot):
        """All selectors (primary + backups) fail, returns False."""
        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.side_effect = TimeoutException("fail")
            result = bot.smart_wait_and_click(
                "by", "value",
                backup_selectors=[("by2", "v2"), ("by3", "v3")],
            )

        assert result is False

    def test_smart_wait_and_click_no_backups(self, bot):
        """Only primary selector, fails, returns False."""
        with patch("mobile.damai_app.WebDriverWait") as MockWait:
            MockWait.return_value.until.side_effect = TimeoutException("fail")
            result = bot.smart_wait_and_click("by", "value")

        assert result is False


# ---------------------------------------------------------------------------
# auto navigation
# ---------------------------------------------------------------------------

class TestAutoNavigation:
    def test_title_matches_target_with_keyword_tokens(self, bot):
        bot.config.keyword = "张杰 演唱会"

        assert bot._title_matches_target("【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站") is True

    def test_current_page_matches_target_uses_keyword_when_item_detail_missing(self, bot):
        bot.item_detail = None
        bot.config.keyword = "余佳运 演唱会"

        with patch.object(bot, "_get_detail_title_text", return_value="【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"):
            assert bot._current_page_matches_target({"state": "sku_page"}) is False

    def test_exit_non_target_event_context_backs_out_until_search_page(self, bot):
        with patch.object(bot, "_current_page_matches_target", side_effect=[False, False]), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", side_effect=[
                 {"state": "detail_page"},
                 {"state": "search_page"},
             ]):
            result = bot._exit_non_target_event_context({"state": "sku_page"})

        assert result["state"] == "search_page"
        assert bot.driver.press_keycode.call_count == 2

    def test_discover_target_event_exits_wrong_sku_page_before_search(self, bot):
        bot.config.keyword = "余佳运 演唱会"

        with patch.object(bot, "_recover_to_navigation_start", return_value={"state": "sku_page"}), \
             patch.object(bot, "_current_page_matches_target", side_effect=[False, False]), \
             patch.object(bot, "_exit_non_target_event_context", return_value={"state": "search_page"}) as exit_context, \
             patch.object(bot, "_submit_search_keyword", return_value=True) as submit_keyword, \
             patch.object(bot, "collect_search_results", return_value=[{"score": 80, "title": "余佳运演唱会"}]), \
             patch.object(bot, "_open_target_from_search_results", return_value=True), \
             patch.object(bot, "probe_current_page", return_value={"state": "detail_page"}):
            result = bot.discover_target_event(["余佳运 演唱会"], initial_probe={"state": "sku_page"})

        assert result is not None
        exit_context.assert_called_once()
        submit_keyword.assert_called_once()

    def test_navigate_to_target_event_from_search_page(self, bot):
        with patch.object(bot, "_recover_to_navigation_start", return_value={"state": "search_page"}), \
             patch.object(bot, "_submit_search_keyword", return_value=True) as submit_keyword, \
             patch.object(bot, "_open_target_from_search_results", return_value=True) as open_target:
            result = bot.navigate_to_target_event({"state": "unknown"})

        assert result is True
        submit_keyword.assert_called_once()
        open_target.assert_called_once()

    def test_recover_to_navigation_start_handles_back_key_failure(self, bot):
        with patch.object(bot, "_press_keycode_safe", return_value=False), \
             patch.object(bot, "probe_current_page", return_value={"state": "unknown"}), \
             patch.object(bot.driver, "activate_app") as activate_app, \
             patch("mobile.damai_app.time.sleep"):
            result = bot._recover_to_navigation_start({"state": "unknown"})

        activate_app.assert_called_once_with(bot.config.app_package)
        assert result["state"] == "unknown"

    def test_fast_retry_does_not_submit_when_commit_disabled(self, bot):
        bot.config.if_commit_order = False

        with patch.object(bot, "probe_current_page", return_value={"state": "order_confirm_page"}), \
             patch.object(bot, "smart_wait_for_element", return_value=True) as wait_for_element, \
             patch.object(bot, "smart_wait_and_click") as smart_click:
            result = bot._fast_retry_from_current_state()

        assert result is True
        wait_for_element.assert_called_once()
        smart_click.assert_not_called()

    def test_run_with_retry_stops_on_terminal_failure(self, bot):
        with patch("mobile.damai_app.time.sleep"), \
             patch.object(bot, "run_ticket_grabbing", side_effect=self._mark_terminal_failure(bot)), \
             patch.object(bot, "_fast_retry_from_current_state") as fast_retry, \
             patch.object(bot, "_setup_driver") as setup_driver:
            result = bot.run_with_retry(max_retries=3)

        assert result is False
        fast_retry.assert_not_called()
        setup_driver.assert_not_called()

    @staticmethod
    def _mark_terminal_failure(bot):
        def runner():
            bot._terminal_failure_reason = "reservation_only"
            return False
        return runner


# ---------------------------------------------------------------------------
# run_ticket_grabbing
# ---------------------------------------------------------------------------

class TestRunTicketGrabbing:
    def test_run_ticket_grabbing_auto_navigates_from_homepage(self, bot):
        bot.config.probe_only = True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "navigate_to_target_event", return_value=True) as navigate, \
             patch.object(bot, "probe_current_page", side_effect=[
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
             ]), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.3]
            result = bot.run_ticket_grabbing()

        assert result is True
        navigate.assert_called_once()

    def test_run_ticket_grabbing_returns_false_when_not_detail_page(self, bot):
        """Homepage or other non-detail states fail fast with a clear result."""
        bot.config.auto_navigate = False

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "homepage",
                 "purchase_button": False,
                 "price_container": False,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "smart_wait_and_click") as smart_click, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_probe_only_returns_true_when_detail_ready(self, bot):
        """probe_only stops before purchase when detail-page essentials are present."""
        bot.config.probe_only = True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "smart_wait_and_click") as smart_click, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_logs_probe_mode_clearly(self, bot, caplog):
        """The first runtime log should clearly state this is only a probe."""
        bot.config.probe_only = True

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.run_ticket_grabbing()

        assert result is True
        assert "开始执行安全探测" in caplog.text
        assert "不会点击“立即购票”" in caplog.text

    def test_run_ticket_grabbing_probe_only_returns_false_when_detail_incomplete(self, bot):
        """probe_only reports failure when detail-page essentials are missing."""
        bot.config.probe_only = True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": False,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_probe_only_returns_true_when_sku_page_ready(self, bot):
        """probe_only succeeds when the ticket sku page is already open."""
        bot.config.probe_only = True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "sku_page",
                 "purchase_button": False,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "smart_wait_and_click") as smart_click, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()

    def test_run_ticket_grabbing_success(self, bot):
        """All phases succeed, returns True."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="success"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.5]
            # Mock find_element for price container + target_price
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []  # no quantity layout

            result = bot.run_ticket_grabbing()

        assert result is True

    def test_run_ticket_grabbing_rush_mode_uses_prefetched_buy_button_coordinates(self, bot):
        bot.config.rush_mode = True
        bot.config.if_commit_order = False

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
                 "price_coords": (240, 1560),
                 "buy_button_coords": (320, 1880),
             }) as enter_purchase_flow, \
             patch.object(bot, "_select_price_option", return_value=True) as select_price, \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "_burst_click_coordinates") as burst_click_coords, \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.9]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        enter_purchase_flow.assert_called_once_with(prepared=False)
        select_price.assert_called_once_with(cached_coords=(240, 1560))
        burst_click_coords.assert_called_once_with(320, 1880, count=1, interval_ms=25, duration=25)

    def test_run_ticket_grabbing_stops_before_submit_when_commit_disabled(self, bot):
        """if_commit_order=False waits for confirm page but never clicks submit."""
        bot.config.if_commit_order = False

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True) as wait_submit_ready, \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click, \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.2]
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

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.8]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert "开始执行开发验证" in caplog.text
        assert "开发调试路径" in caplog.text

    def test_run_ticket_grabbing_continues_from_sku_page_when_commit_disabled(self, bot):
        """sku_page can continue directly to confirm page without returning to detail."""
        bot.config.if_commit_order = False

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "sku_page",
                 "purchase_button": False,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True) as wait_submit_ready, \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "smart_wait_and_click") as smart_click, \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.8]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        smart_click.assert_not_called()
        wait_submit_ready.assert_called_once()

    def test_run_ticket_grabbing_returns_false_for_reservation_sku_page(self, bot, caplog):
        """Reservation-only sku pages stop safely before tapping the bottom action."""
        bot.config.if_commit_order = False

        with caplog.at_level("WARNING", logger="mobile.damai_app"), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", side_effect=[
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
             ]), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "ultra_fast_click") as fast_click, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0

            result = bot.run_ticket_grabbing()

        assert result is False
        assert fast_click.call_count == 1
        fast_click.assert_called_once_with(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("12.06")',
            timeout=1.0,
        )
        assert "抢票预约" in caplog.text

    def test_run_ticket_grabbing_returns_false_when_confirm_page_not_ready_and_commit_disabled(self, bot, caplog):
        """Commit-disabled mode fails safely if the confirm page never becomes ready."""
        bot.config.if_commit_order = False

        with caplog.at_level("WARNING", logger="mobile.damai_app"), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=False), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click", return_value=0), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
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
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_select_city_from_detail_page", return_value=False), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_book_fail(self, bot):
        """Booking button fails, returns False."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value=None), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_price_exception_tries_backup(self, bot):
        """Text match fails, index find_element raises, backup via wait.until succeeds."""
        mock_price_container = Mock()
        mock_target = _make_mock_element()
        mock_price_container.find_element.return_value = mock_target

        def ultra_fast_click_side_effect(by, value, timeout=1.5):
            # Fail the text-based price match, succeed for everything else
            if 'textContains("799元")' in str(value):
                return False
            return True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "check_session_valid", return_value=True), \
             patch.object(bot, "select_performance_date"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", side_effect=ultra_fast_click_side_effect), \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="success"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 2.0]
            # find_element raises for price container, triggering wait.until backup
            bot.driver.find_element.side_effect = NoSuchElementException("not found")
            bot.wait.until = Mock(return_value=mock_price_container)
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        # Backup path used wait.until
        bot.wait.until.assert_called_once()

    def test_run_ticket_grabbing_exception_returns_false(self, bot):
        """Unexpected exception in flow returns False."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "smart_wait_and_click", side_effect=RuntimeError("boom")), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_submit_timeout_returns_false_and_marks_terminal_failure(self, bot):
        """Submit timeout should fail closed to avoid false success and duplicate submit."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="timeout") as submit_fast, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.0]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is False
        assert bot._terminal_failure_reason == "submit_unverified"
        submit_fast.assert_called_once()

    def test_run_ticket_grabbing_existing_order_returns_success_with_pending_payment_outcome(self, bot):
        """Existing unpaid order means submit flow already succeeded and only payment is pending."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="existing_order"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.0]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"
        assert bot._terminal_failure_reason is None

    def test_run_ticket_grabbing_returns_success_when_pending_order_dialog_detected_early(self, bot):
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "check_session_valid", return_value=True), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "pending_order_dialog",
                 "purchase_button": False,
                 "price_container": False,
                 "quantity_picker": False,
                 "submit_button": False,
                 "reservation_mode": False,
                 "pending_order_dialog": True,
             }):
            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"

    def test_run_ticket_grabbing_rush_mode_skips_detail_prepare_and_reprobe_when_no_sell_time(self, bot):
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

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "check_session_valid", return_value=True), \
             patch.object(bot, "probe_current_page", return_value=detail_probe) as probe_page, \
             patch.object(bot, "_prepare_detail_page_hot_path") as prepare_detail, \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_select_price_option", return_value=True), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "_submit_order_fast", return_value="success"), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.0]
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert bot._last_run_outcome == "order_submitted"
        prepare_detail.assert_not_called()
        assert probe_page.call_count == 1

    def test_run_ticket_grabbing_no_driver_quit_in_finally(self, bot):
        """Verify driver.quit is NOT called inside run_ticket_grabbing's finally block."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "smart_wait_and_click", return_value=False), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            bot.run_ticket_grabbing()

        bot.driver.quit.assert_not_called()

    def test_run_ticket_grabbing_skips_user_click_when_order_confirm_page_directly_opened(self, bot):
        """Direct jump to order confirm page should skip manual user selection."""
        bot.config.if_commit_order = False

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "sku_page",
                 "purchase_button": False,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click") as batch_click, \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.8]
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
        card.find_elements.side_effect = lambda by=None, value=None: {
            (By.ID, "cn.damai:id/tv_project_name"): [Mock(text="【北京】张杰演唱会")],
            (By.ID, "cn.damai:id/tv_project_venueName"): [Mock(text="国家体育场-鸟巢")],
            (By.ID, "cn.damai:id/tv_project_city"): [Mock(text="北京 | ")],
            (By.ID, "cn.damai:id/tv_project_time"): [Mock(text="2026.03.29-04.19")],
            (By.ID, "cn.damai:id/bricks_dm_common_price_prefix"): [Mock(text="¥")],
            (By.ID, "cn.damai:id/bricks_dm_common_price_des"): [Mock(text="380")],
            (By.ID, "cn.damai:id/bricks_dm_common_price_suffix"): [Mock(text="起")],
        }.get((by, value), [])
        bot.driver.find_elements.return_value = [card]
        bot.config.keyword = "张杰 演唱会"

        results = bot.collect_search_results()

        assert results == [{
            "title": "【北京】张杰演唱会",
            "venue": "国家体育场-鸟巢",
            "city": "北京",
            "time": "2026.03.29-04.19",
            "price": "¥380起",
            "score": results[0]["score"],
        }]
        assert results[0]["score"] >= 60

    def test_wait_for_purchase_entry_result_detects_sku_without_full_probe(self, bot):
        with patch.object(bot, "_has_any_element", side_effect=[False, True]), \
             patch.object(bot, "is_reservation_sku_mode", return_value=False):
            result = bot._wait_for_purchase_entry_result(timeout=0.2, poll_interval=0)

        assert result["state"] == "sku_page"
        assert result["reservation_mode"] is False

    def test_wait_for_submit_ready_detects_submit_button(self, bot):
        with patch.object(bot, "_has_any_element", side_effect=[False, True]):
            assert bot._wait_for_submit_ready(timeout=0.2, poll_interval=0) is True

    def test_wait_for_submit_ready_times_out(self, bot):
        with patch.object(bot, "_has_any_element", return_value=False):
            assert bot._wait_for_submit_ready(timeout=0.01, poll_interval=0) is False

    def test_ensure_attendees_selected_auto_selects_missing_checkbox(self, bot):
        checked_state = {"value": "false"}
        checkbox = Mock()
        checkbox.get_attribute.side_effect = lambda name: checked_state["value"] if name == "checked" else ""

        def _select_side_effect(_user_name):
            checked_state["value"] = "true"
            return True

        with patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: 'textContains("实名观演人")' in value
             ), \
             patch.object(bot, "_attendee_checkbox_elements", return_value=[checkbox]), \
             patch.object(bot, "_attendee_required_count_on_confirm_page", return_value=1), \
             patch.object(bot, "_select_attendee_checkbox_by_name", side_effect=_select_side_effect):
            assert bot._ensure_attendees_selected_on_confirm_page() is True

    def test_attendee_selected_count_falls_back_to_page_source(self, bot):
        checkbox = Mock()
        checkbox.get_attribute.side_effect = lambda name: "false" if name == "checked" else ""
        bot.driver.page_source = (
            '<node resource-id="cn.damai:id/checkbox" checked="true"/>'
            '<node resource-id="cn.damai:id/checkbox" checked="false"/>'
        )

        assert bot._attendee_selected_count([checkbox]) == 1

    def test_click_attendee_checkbox_falls_back_when_center_click_fails(self, bot):
        checkbox = Mock()
        checkbox.click = Mock()

        with patch.object(bot, "_click_element_center", side_effect=Exception("center failed")), \
             patch.object(bot, "_burst_click_element_center", return_value=None), \
             patch.object(bot, "_is_checkbox_selected", return_value=False), \
             patch.object(bot, "_attendee_selected_count", side_effect=[0, 1]):
            assert bot._click_attendee_checkbox(checkbox) is True

        checkbox.click.assert_called_once()

    def test_select_attendee_checkbox_by_name_uses_contains_fallback_xpath(self, bot):
        checkbox = Mock()
        seen_xpaths = []

        def _find_elements_side_effect(by=None, value=None):
            if by != By.XPATH:
                return []
            seen_xpaths.append(value)
            if "contains(normalize-space(@text)" in value:
                return [checkbox]
            return []

        with patch.object(bot.driver, "find_elements", side_effect=_find_elements_side_effect), \
             patch.object(bot, "_is_checkbox_selected", return_value=False), \
             patch.object(bot, "_click_attendee_checkbox", return_value=True) as click_checkbox:
            assert bot._select_attendee_checkbox_by_name("张志涛") is True

        assert any("contains(normalize-space(@text)" in xpath for xpath in seen_xpaths)
        click_checkbox.assert_called_once_with(checkbox)

    def test_ensure_attendees_selected_fails_when_section_visible_but_no_checkbox(self, bot):
        with patch.object(
                bot,
                "_has_element",
                side_effect=lambda by, value: 'textContains("实名观演人")' in value
             ), \
             patch.object(bot, "_attendee_checkbox_elements", return_value=[]):
            assert bot._ensure_attendees_selected_on_confirm_page() is False

    def test_get_buy_button_coordinates_returns_first_match_center(self, bot):
        element = _make_mock_element(x=20, y=40, width=100, height=60)
        bot.driver.find_elements.return_value = [element]

        result = bot._get_buy_button_coordinates()

        assert result == (70, 70)

    def test_get_price_option_coordinates_by_config_index_returns_target_center(self, bot):
        bot.config.price_index = 1
        card0 = _make_mock_element(x=10, y=20, width=100, height=80)
        card1 = _make_mock_element(x=160, y=20, width=100, height=80)
        container = Mock()
        container.find_element.return_value = card1
        container.find_elements.return_value = [card0, card1]
        card0.get_attribute.return_value = "true"
        card1.get_attribute.return_value = "true"

        with patch.object(bot.driver, "find_element", return_value=container):
            result = bot._get_price_option_coordinates_by_config_index()

        assert result == (210, 60)

    def test_get_visible_price_options_extracts_card_texts(self, bot):
        price_container = Mock()
        card_a = Mock()
        card_b = Mock()
        price_container.find_elements.side_effect = lambda by=None, value=None: (
            [card_a, card_b] if (by, value) == (By.CLASS_NAME, "android.widget.FrameLayout") else []
        )
        bot.driver.find_element.return_value = price_container
        card_a.get_attribute.side_effect = lambda name: "true" if name == "clickable" else ""
        card_b.get_attribute.side_effect = lambda name: "true" if name == "clickable" else ""
        card_a.find_elements.return_value = [Mock(text="内场"), Mock(text="1280"), Mock(text="可预约")]
        card_b.find_elements.return_value = [Mock(text="看台"), Mock(text="380"), Mock(text="无票")]

        options = bot.get_visible_price_options()

        assert options == [
            {"index": 0, "text": "内场1280元", "tag": "可预约", "raw_texts": ["内场", "1280", "可预约"], "source": "ui"},
            {"index": 1, "text": "看台380元", "tag": "无票", "raw_texts": ["看台", "380", "无票"], "source": "ui"},
        ]

    def test_purchase_bar_text_ready_distinguishes_reservation_from_purchase(self, bot):
        purchase_bar = Mock()
        with patch.object(bot.driver, "find_element", return_value=purchase_bar), \
             patch.object(bot, "_collect_descendant_texts", return_value=["立即购买"]):
            assert bot._purchase_bar_text_ready() is True

        with patch.object(bot.driver, "find_element", return_value=purchase_bar), \
             patch.object(bot, "_collect_descendant_texts", return_value=["抢票预约"]):
            assert bot._purchase_bar_text_ready() is False

    def test_normalize_ocr_price_text(self, bot):
        assert bot._normalize_ocr_price_text("38075 Fam ©") == "380元"
        assert bot._normalize_ocr_price_text("128076 gma G") == "1280元"
        assert bot._normalize_ocr_price_text("noise") == ""

    def test_probe_current_page_detects_homepage(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: (by, value) == (By.ID, "cn.damai:id/homepage_header_search"),
        ), patch.object(bot, "_get_current_activity", return_value=""):
            result = bot.probe_current_page()

            assert result["state"] == "homepage"
            assert result["purchase_button"] is False

    def test_probe_current_page_detects_homepage_by_activity(self, bot):
        with patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "_get_current_activity", return_value=".homepage.MainActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "homepage"

    def test_probe_current_page_detects_search_activity(self, bot):
        with patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "_get_current_activity", return_value="com.alibaba.pictures.bricks.search.v2.SearchActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "search_page"
            assert result["purchase_button"] is False

    def test_probe_current_page_detects_detail_page_by_activity_and_summary_price(self, bot):
        present = {
            (By.ID, "cn.damai:id/project_detail_price_layout"),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_get_current_activity", return_value=".trade.newtradeorder.ui.projectdetail.ui.activity.ProjectDetailActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "detail_page"
            assert result["purchase_button"] is False
            assert result["price_container"] is True

    def test_probe_current_page_detects_sku_page(self, bot):
        present = {
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_get_current_activity", return_value=".commonbusiness.seatbiz.sku.qilin.ui.NcovSkuActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "sku_page"
            assert result["price_container"] is True
            assert result["reservation_mode"] is False

    def test_probe_current_page_marks_reservation_mode_for_reservation_sku(self, bot):
        present = {
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看场次")'),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_get_current_activity", return_value=".commonbusiness.seatbiz.sku.qilin.ui.NcovSkuActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "sku_page"
            assert result["reservation_mode"] is True

    def test_probe_current_page_detects_pending_order_dialog(self, bot):
        present = {
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("未支付订单")'),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_get_current_activity", return_value=""):
            result = bot.probe_current_page()

        assert result["state"] == "pending_order_dialog"
        assert result["pending_order_dialog"] is True

    def test_probe_current_page_detects_detail_page_controls(self, bot):
        present = {
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "layout_num"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_get_current_activity", return_value=""):
            result = bot.probe_current_page()

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
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "ultra_fast_click", return_value=True) as fast_click, \
             patch("mobile.damai_app.time.sleep"):
            result = bot.dismiss_startup_popups()

            assert result is True
            fast_click.assert_any_call(By.ID, "android:id/ok")
            fast_click.assert_any_call(By.ID, "cn.damai:id/id_boot_action_agree")
            fast_click.assert_any_call(By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn")
            fast_click.assert_any_call(By.ID, "cn.damai:id/damai_theme_dialog_close_layout")
            fast_click.assert_any_call(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")')
            fast_click.assert_any_call(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")')


# ---------------------------------------------------------------------------
# run_with_retry
# ---------------------------------------------------------------------------

class TestRunWithRetry:
    def test_run_with_retry_success_first_attempt(self, bot):
        """Succeeds on first attempt, returns True immediately."""
        with patch.object(bot, "run_ticket_grabbing", return_value=True), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is True

    def test_run_with_retry_success_second_attempt(self, bot):
        """Fails once, sets up driver again, succeeds second time."""
        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, True]), \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False), \
             patch.object(bot, "_setup_driver") as mock_setup, \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is True
        mock_setup.assert_called_once()

    def test_run_with_retry_all_fail(self, bot):
        """All retries fail, returns False."""
        with patch.object(bot, "run_ticket_grabbing", return_value=False), \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False), \
             patch.object(bot, "_setup_driver"), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is False

    def test_run_with_retry_driver_quit_between_retries(self, bot):
        """Between retries, driver.quit and _setup_driver are called."""
        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, False, True]), \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False), \
             patch.object(bot, "_setup_driver") as mock_setup, \
             patch("mobile.damai_app.time"):
            bot.run_with_retry(max_retries=3)

        # quit called before each retry (2 failures, but last one succeeds so only 2 quit calls)
        assert bot.driver.quit.call_count == 2
        assert mock_setup.call_count == 2

    def test_run_with_retry_quit_exception_handled(self, bot):
        """driver.quit raises an exception, handled by except block."""
        bot.driver.quit.side_effect = Exception("quit failed")

        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, True]), \
             patch.object(bot, "_setup_driver") as mock_setup, \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        # Despite quit failure, retry continued and succeeded
        assert result is True

    def test_run_with_retry_uses_fast_retry(self, bot):
        """Verify fast retry is attempted before driver recreation."""
        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, False]), \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False) as fast_retry, \
             patch.object(bot, "_setup_driver"), \
             patch("mobile.damai_app.time"):
            bot.run_with_retry(max_retries=2)

        # fast_retry called fast_retry_count times per failed attempt
        assert fast_retry.call_count == bot.config.fast_retry_count * 2

    def test_run_with_retry_first_fast_retry_has_no_extra_sleep(self, bot):
        """The first fast retry should execute immediately after a failed attempt."""
        with patch.object(bot, "run_ticket_grabbing", return_value=False), \
             patch.object(bot, "_fast_retry_from_current_state", side_effect=[False, True]), \
             patch.object(bot, "_setup_driver"), \
             patch("mobile.damai_app.time.sleep") as mock_sleep:
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        mock_sleep.assert_called_once_with(bot.config.fast_retry_interval_ms / 1000)

    def test_run_with_retry_manual_mode_skips_driver_recreation(self, bot):
        """Manual-start mode keeps the driver session instead of rebuilding it."""
        bot.config.auto_navigate = False

        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, False]), \
             patch.object(bot, "_fast_retry_from_current_state", return_value=False), \
             patch.object(bot, "_setup_driver") as mock_setup, \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=2)

        assert result is False
        bot.driver.quit.assert_not_called()
        mock_setup.assert_not_called()

    def test_run_with_retry_logs_probe_success_clearly(self, bot, caplog):
        """probe_only success should not be logged as ticket-purchase success."""
        bot.config.probe_only = True
        bot._last_run_outcome = "probe_ready"

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "run_ticket_grabbing", return_value=True), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        assert "探测成功" in caplog.text
        assert "抢票成功！" not in caplog.text

    def test_run_with_retry_logs_validation_success_clearly(self, bot, caplog):
        """Developer validation success should mention no-submit explicitly."""
        bot.config.if_commit_order = False
        bot._last_run_outcome = "validation_ready"

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "run_ticket_grabbing", return_value=True), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=1)

        assert result is True
        assert "开发验证成功：已到订单确认页，未提交订单" in caplog.text

    def test_run_with_retry_logs_submit_success_when_order_submitted(self, bot, caplog):
        """Actual order submission keeps the purchase-success wording."""
        bot._last_run_outcome = "order_submitted"

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "run_ticket_grabbing", return_value=True), \
             patch("mobile.damai_app.time"):
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

        with patch("mobile.damai_app.datetime") as mock_dt, \
             patch("mobile.damai_app.time.sleep") as mock_sleep, \
             patch.object(bot, "_has_element", return_value=True):
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

        with patch("mobile.damai_app.logger") as mock_logger, \
             patch("mobile.damai_app.time.sleep") as mock_sleep, \
             patch.object(bot, "_is_sale_ready") as is_ready:
            bot.wait_for_sale_start()

        is_ready.assert_not_called()
        mock_sleep.assert_not_called()
        mock_logger.info.assert_any_call("未配置 sell_start_time，已跳过 CTA 等待，直接开始执行")

    def test_wait_for_sale_start_skips_cta_wait_timeout_branch_without_sell_start_time(self, bot):
        bot.config.sell_start_time = None
        bot.config.wait_cta_ready_timeout_ms = 100

        with patch("mobile.damai_app.logger"), \
             patch("mobile.damai_app.time.sleep") as mock_sleep, \
             patch.object(bot, "_is_sale_ready") as is_ready:
            bot.wait_for_sale_start()

        is_ready.assert_not_called()
        mock_sleep.assert_not_called()

    def test_prepare_detail_page_hot_path_preselects_date_and_city(self, bot):
        with patch.object(bot, "probe_current_page", return_value={
                "state": "detail_page",
                "purchase_button": True,
                "price_container": True,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "_select_city_from_detail_page", return_value=True) as select_city:
            result = bot._prepare_detail_page_hot_path()

        assert result is True
        select_date.assert_called_once()
        select_city.assert_called_once_with(timeout=0.6)

    def test_prepare_detail_page_hot_path_returns_false_outside_detail_page(self, bot):
        with patch.object(bot, "probe_current_page", return_value={"state": "homepage"}), \
             patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "_select_city_from_detail_page") as select_city:
            result = bot._prepare_detail_page_hot_path()

        assert result is False
        select_date.assert_not_called()
        select_city.assert_not_called()


class TestDetailPagePurchaseEntry:
    def test_select_city_from_detail_page_uses_fallback_selectors(self, bot):
        with patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click:
            result = bot._select_city_from_detail_page(timeout=0.8)

        assert result is True
        smart_click.assert_called_once_with(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("深圳")',
            [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("深圳")'),
                (By.XPATH, '//*[@text="深圳"]'),
            ],
            timeout=0.8,
        )

    def test_enter_purchase_flow_returns_none_when_city_selection_fails(self, bot):
        with patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "_select_city_from_detail_page", return_value=False) as select_city:
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        assert result is None
        select_date.assert_called_once()
        select_city.assert_called_once_with(timeout=1.0)

    def test_enter_purchase_flow_rush_mode_continues_when_city_selection_misses(self, bot):
        bot.config.rush_mode = True
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "_select_city_from_detail_page", return_value=False) as select_city, \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "_wait_for_purchase_entry_result", return_value=next_probe):
            result = bot._enter_purchase_flow_from_detail_page(prepared=False)

        assert result == next_probe
        select_date.assert_called_once_with(timeout=0.35)
        select_city.assert_called_once_with(timeout=0.35)

    def test_enter_purchase_flow_uses_rush_mode_fast_path(self, bot):
        bot.config.rush_mode = True
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with patch.object(bot, "ultra_fast_click", return_value=True) as fast_click, \
             patch.object(bot, "_wait_for_purchase_entry_result", return_value=next_probe) as wait_result:
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result == next_probe
        fast_click.assert_called_once_with(
            By.ID,
            "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            timeout=0.25,
        )
        wait_result.assert_called_once_with(timeout=0.7, poll_interval=0.03)

    def test_enter_purchase_flow_falls_back_to_book_selectors(self, bot):
        next_probe = {"state": "order_confirm_page", "submit_button": True}

        with patch.object(bot, "ultra_fast_click", return_value=False), \
             patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click, \
             patch.object(bot, "_wait_for_purchase_entry_result", return_value=next_probe) as wait_result:
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result == next_probe
        smart_click.assert_called_once()
        wait_result.assert_called_once_with(timeout=5, poll_interval=0.08)

    def test_enter_purchase_flow_returns_none_when_all_clicks_fail(self, bot):
        with patch.object(bot, "ultra_fast_click", return_value=False), \
             patch.object(bot, "smart_wait_and_click", return_value=False):
            result = bot._enter_purchase_flow_from_detail_page(prepared=True)

        assert result is None


class TestSaleReadiness:
    def test_purchase_bar_text_ready_returns_false_when_bar_missing(self, bot):
        bot.driver.find_element.side_effect = Exception("missing")
        assert bot._purchase_bar_text_ready() is False

    def test_purchase_bar_text_ready_returns_false_when_descendants_are_empty(self, bot):
        purchase_bar = Mock()
        with patch.object(bot.driver, "find_element", return_value=purchase_bar), \
             patch.object(bot, "_collect_descendant_texts", return_value=["", "   "]):
            assert bot._purchase_bar_text_ready() is False

    def test_is_sale_ready_detects_ready_selector(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: value == 'new UiSelector().textContains("立即购买")',
        ):
            assert bot._is_sale_ready() is True

    def test_is_sale_ready_uses_sku_mode_to_block_reservation(self, bot):
        present = {(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")}

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "is_reservation_sku_mode", return_value=True):
            assert bot._is_sale_ready() is False

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "is_reservation_sku_mode", return_value=False):
            assert bot._is_sale_ready() is True

    def test_is_sale_ready_uses_purchase_bar_text_when_detail_cta_present(self, bot):
        present = {(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl")}

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "_purchase_bar_text_ready", return_value=True):
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
        with patch.object(bot, "probe_current_page", side_effect=[
                {
                    "state": "unknown",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                },
                {
                    "state": "detail_page",
                    "purchase_button": True,
                    "price_container": True,
                    "quantity_picker": False,
                    "submit_button": False,
                },
             ]), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch("mobile.damai_app.time.sleep"):
            result = bot._recover_to_detail_page_for_local_retry(
                initial_probe={
                    "state": "unknown",
                    "purchase_button": False,
                    "price_container": False,
                    "quantity_picker": False,
                    "submit_button": False,
                }
            )

        bot.driver.press_keycode.assert_called_once_with(4)
        assert result["state"] == "detail_page"

    def test_fast_retry_from_detail_page(self, bot):
        """probe returns detail_page, re-runs full flow."""
        with patch.object(bot, "probe_current_page", return_value={
                "state": "detail_page",
                "purchase_button": True,
                "price_container": True,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg:
            result = bot._fast_retry_from_current_state()

        assert result is True
        run_tg.assert_called_once()

    def test_fast_retry_from_order_confirm_page(self, bot):
        """probe returns order_confirm_page, re-attempts submit only."""
        with patch.object(bot, "probe_current_page", return_value={
                "state": "order_confirm_page",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": True,
             }), \
             patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click:
            result = bot._fast_retry_from_current_state()

        assert result is True
        smart_click.assert_called_once()

    def test_fast_retry_from_order_confirm_page_in_safe_mode_waits_for_submit_button(self, bot):
        bot.config.if_commit_order = False

        with patch.object(bot, "probe_current_page", return_value={
                "state": "order_confirm_page",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": True,
             }), \
             patch.object(bot, "_ensure_attendees_selected_on_confirm_page", return_value=True) as ensure_attendees, \
             patch.object(bot, "smart_wait_for_element", return_value=True) as wait_element:
            result = bot._fast_retry_from_current_state()

        assert result is True
        ensure_attendees.assert_called_once()
        wait_element.assert_called_once()

    def test_fast_retry_returns_success_when_pending_order_dialog_detected(self, bot):
        with patch.object(bot, "probe_current_page", return_value={
                "state": "pending_order_dialog",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": False,
             }):
            result = bot._fast_retry_from_current_state()

        assert result is True
        assert bot._last_run_outcome == "order_pending_payment"

    def test_fast_retry_switches_to_auto_navigation_when_wrong_detail_page(self, bot):
        bot.item_detail = _make_item_detail()
        bot.config.auto_navigate = True

        with patch.object(bot, "probe_current_page", return_value={
                "state": "detail_page",
                "purchase_button": True,
                "price_container": True,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "_current_page_matches_target", return_value=False), \
             patch.object(bot, "navigate_to_target_event", return_value=True) as navigate, \
             patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg:
            result = bot._fast_retry_from_current_state()

        assert result is True
        navigate.assert_called_once()
        run_tg.assert_called_once()

    def test_fast_retry_stops_in_manual_mode_when_wrong_detail_page(self, bot):
        bot.item_detail = _make_item_detail()
        bot.config.auto_navigate = False

        with patch.object(bot, "probe_current_page", return_value={
                "state": "detail_page",
                "purchase_button": True,
                "price_container": True,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "_current_page_matches_target", return_value=False), \
             patch.object(bot, "navigate_to_target_event") as navigate, \
             patch.object(bot, "run_ticket_grabbing") as run_tg:
            result = bot._fast_retry_from_current_state()

        assert result is False
        navigate.assert_not_called()
        run_tg.assert_not_called()

    def test_fast_retry_from_unknown_recovers_locally_then_reruns(self, bot):
        """Manual-start retry recovers locally before re-running the flow."""
        bot.config.auto_navigate = False

        with patch.object(bot, "probe_current_page", return_value={
                "state": "unknown",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "_recover_to_detail_page_for_local_retry", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }) as recover_local, \
             patch.object(bot, "run_ticket_grabbing", return_value=False) as run_tg, \
             patch("mobile.damai_app.time.sleep"):
            result = bot._fast_retry_from_current_state()

        recover_local.assert_called_once()
        run_tg.assert_called_once()
        assert result is False

    def test_fast_retry_from_unknown_returns_false_if_local_recovery_fails(self, bot):
        """Manual-start retry stops if it cannot recover to a detail/sku page."""
        bot.config.auto_navigate = False

        with patch.object(bot, "probe_current_page", return_value={
                "state": "unknown",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "_recover_to_detail_page_for_local_retry", return_value={
                 "state": "homepage",
                 "purchase_button": False,
                 "price_container": False,
                 "quantity_picker": False,
                 "submit_button": False,
             }) as recover_local, \
             patch.object(bot, "run_ticket_grabbing") as run_tg:
            result = bot._fast_retry_from_current_state()

        recover_local.assert_called_once()
        run_tg.assert_not_called()
        assert result is False

    def test_fast_retry_from_unknown_uses_auto_navigation_when_enabled(self, bot):
        bot.config.auto_navigate = True

        with patch.object(bot, "probe_current_page", return_value={
                "state": "unknown",
                "purchase_button": False,
                "price_container": False,
                "quantity_picker": False,
                "submit_button": False,
             }), \
             patch.object(bot, "navigate_to_target_event", return_value=True) as navigate, \
             patch.object(bot, "run_ticket_grabbing", return_value=True) as run_tg:
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
        with patch.object(bot, "_get_current_activity", return_value="com.alipay.android.app.PayActivity"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.verify_order_result(timeout=5)

        assert result == "success"

    def test_verify_order_success_payment_text(self, bot):
        """Payment-specific UI text returns 'success'."""
        def has_element_side_effect(by, value):
            return '立即支付' in value

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.verify_order_result(timeout=5)

        assert result == "success"

    def test_verify_order_generic_payment_text_does_not_count_as_success(self, bot):
        """Generic '支付' text should not be treated as a successful submit signal."""
        time_values = chain([0.0, 0.2, 0.5, 0.8, 1.1], repeat(1.1))

        def has_element_side_effect(by, value):
            # Simulate a page containing generic "支付" wording but no payment CTA.
            return 'textContains("支付")' in value and '未支付' not in value

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time.time", side_effect=time_values), \
             patch("mobile.damai_app.time.sleep"):
            result = bot.verify_order_result(timeout=1)

        assert result == "timeout"

    def test_verify_order_payment_cta_on_confirm_page_does_not_count_as_success(self, bot):
        """Even if payment CTA text appears, still being on confirm page should not be success."""
        time_values = chain([0.0, 0.2, 0.5, 0.8, 1.1], repeat(1.1))

        def has_element_side_effect(by, value):
            if 'textContains("未支付")' in value:
                return False
            if 'textContains("已售罄")' in value or 'textContains("库存不足")' in value or 'textContains("暂时无票")' in value:
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

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time.time", side_effect=time_values), \
             patch("mobile.damai_app.time.sleep"):
            result = bot.verify_order_result(timeout=1)

        assert result == "timeout"

    def test_verify_order_sold_out(self, bot):
        """Element contains '已售罄', returns 'sold_out'."""
        def has_element_side_effect(by, value):
            return '已售罄' in value

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.verify_order_result(timeout=5)

        assert result == "sold_out"

    def test_verify_order_timeout(self, bot):
        """No indicators found, returns 'timeout'."""
        call_count = [0]

        def mock_time_func():
            call_count[0] += 1
            # Return increasing time so we exceed timeout quickly
            return call_count[0] * 3.0

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", return_value=False), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time = mock_time_func
            mock_time.sleep = Mock()
            result = bot.verify_order_result(timeout=5)

        assert result == "timeout"

    def test_verify_order_captcha(self, bot):
        """Element contains '验证', returns 'captcha'."""
        def has_element_side_effect(by, value):
            # Skip 支付 and 已售罄/库存不足/暂时无票, match 验证
            if '支付' in value:
                return False
            if '已售罄' in value or '库存不足' in value or '暂时无票' in value:
                return False
            if '验证' in value:
                return True
            return False

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.verify_order_result(timeout=5)

        assert result == "captcha"

    def test_verify_order_existing_order(self, bot):
        """Element contains '未支付', returns 'existing_order'."""
        def has_element_side_effect(by, value):
            if '支付' in value and '未' not in value:
                return False
            if '已售罄' in value or '库存不足' in value or '暂时无票' in value:
                return False
            if '滑块' in value or '验证' in value:
                return False
            if '未支付' in value:
                return True
            return False

        with patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.1]
            result = bot.verify_order_result(timeout=5)

        assert result == "existing_order"


# ---------------------------------------------------------------------------
# select_performance_date
# ---------------------------------------------------------------------------

class TestSelectPerformanceDate:
    def test_select_performance_date_found(self, bot, caplog):
        """Date text found and clicked successfully."""
        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "ultra_fast_click", return_value=True) as ufc:
            bot.select_performance_date()

        ufc.assert_called_once_with(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("12.06")',
            timeout=1.0,
        )
        assert "选择场次日期: 12.06" in caplog.text

    def test_select_performance_date_not_found(self, bot, caplog):
        """Date not found, continues gracefully without error."""
        with caplog.at_level("DEBUG", logger="mobile.damai_app"), \
             patch.object(bot, "ultra_fast_click", return_value=False) as ufc:
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
        with patch.object(bot, "_get_current_activity", return_value="ProjectDetailActivity"), \
             patch.object(bot, "_has_element", return_value=False):
            result = bot.check_session_valid()

        assert result is True

    def test_check_session_valid_login_activity(self, bot, caplog):
        """LoginActivity detected, returns False."""
        with caplog.at_level("ERROR", logger="mobile.damai_app"), \
             patch.object(bot, "_get_current_activity", return_value="com.taobao.login.LoginActivity"):
            result = bot.check_session_valid()

        assert result is False
        assert "登录已过期" in caplog.text

    def test_check_session_valid_sign_activity(self, bot, caplog):
        """SignActivity detected, returns False."""
        with caplog.at_level("ERROR", logger="mobile.damai_app"), \
             patch.object(bot, "_get_current_activity", return_value="com.taobao.SignActivity"):
            result = bot.check_session_valid()

        assert result is False
        assert "登录已过期" in caplog.text

    def test_check_session_valid_login_prompt(self, bot, caplog):
        """'请先登录' text detected on page, returns False."""
        def has_element_side_effect(by, value):
            return '请先登录' in value

        with caplog.at_level("ERROR", logger="mobile.damai_app"), \
             patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect):
            result = bot.check_session_valid()

        assert result is False
        assert "登录提示" in caplog.text


class TestSkuInspectionHelpers:
    def test_dismiss_startup_popups_returns_false_when_nothing_is_clickable(self, bot):
        with patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "ultra_fast_click") as fast_click:
            assert bot.dismiss_startup_popups() is False

        fast_click.assert_not_called()

    def test_is_reservation_sku_mode_detects_indicator(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: value == 'new UiSelector().text("预约想看场次")',
        ):
            assert bot.is_reservation_sku_mode() is True

    def test_get_visible_date_options_deduplicates_blank_values(self, bot):
        element_a = Mock(text="04.04")
        element_b = Mock(text="04.04")
        element_c = Mock(text="  ")
        element_d = Mock(text="04.05")
        bot.driver.find_elements.return_value = [element_a, element_b, element_c, element_d]

        assert bot.get_visible_date_options() == ["04.04", "04.05"]

    def test_get_visible_price_options_returns_empty_when_container_missing(self, bot):
        bot.driver.find_element.side_effect = Exception("missing")
        assert bot.get_visible_price_options() == []

    def test_get_visible_price_options_returns_empty_when_cards_are_not_a_sequence(self, bot):
        price_container = Mock()
        price_container.find_elements.side_effect = lambda by=None, value=None: Mock()
        bot.driver.find_element.return_value = price_container

        assert bot.get_visible_price_options() == []

    def test_get_detail_venue_text_uses_second_resource_id(self, bot):
        with patch.object(bot, "_safe_element_text", side_effect=["", "浦发银行东方体育中心"]):
            assert bot._get_detail_venue_text() == "浦发银行东方体育中心"

    def test_ensure_sku_page_for_inspection_returns_existing_sku_page(self, bot):
        page_probe = {"state": "sku_page", "reservation_mode": False}
        assert bot.ensure_sku_page_for_inspection(page_probe) == page_probe

    def test_ensure_sku_page_for_inspection_returns_non_detail_probe_as_is(self, bot):
        page_probe = {"state": "homepage"}
        assert bot.ensure_sku_page_for_inspection(page_probe) == page_probe

    def test_ensure_sku_page_for_inspection_enters_sku_from_detail_page(self, bot):
        bot.config.date = "04.04"
        bot.config.city = "上海"
        next_probe = {"state": "sku_page", "reservation_mode": False}

        with patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "smart_wait_and_click", return_value=True) as smart_click, \
             patch.object(bot, "wait_for_page_state", return_value=next_probe) as wait_state:
            result = bot.ensure_sku_page_for_inspection({"state": "detail_page"})

        assert result == next_probe
        select_date.assert_called_once()
        assert smart_click.call_count == 2
        wait_state.assert_called_once_with({"sku_page", "order_confirm_page"}, timeout=5)

    def test_ensure_sku_page_for_inspection_returns_probe_when_click_fails(self, bot):
        with patch.object(bot, "select_performance_date") as select_date, \
             patch.object(bot, "smart_wait_and_click", return_value=False), \
             patch.object(bot, "probe_current_page", return_value={"state": "detail_page"}) as probe:
            result = bot.ensure_sku_page_for_inspection({"state": "detail_page"})

        assert result == {"state": "detail_page"}
        select_date.assert_called_once()
        probe.assert_called_once()

    def test_inspect_current_target_event_collects_dates_and_prices(self, bot):
        sku_probe = {"state": "sku_page", "reservation_mode": True}
        prices = [{"index": 5, "text": "看台 899元", "tag": "可选", "source": "ui"}]

        with patch.object(bot, "_get_detail_title_text", side_effect=["", "马思唯上海站"]), \
             patch.object(bot, "_get_detail_venue_text", side_effect=["", "上海市 · 浦发银行东方体育中心"]), \
             patch.object(bot, "ensure_sku_page_for_inspection", return_value=sku_probe), \
             patch.object(bot, "get_visible_date_options", return_value=["04.04"]), \
             patch.object(bot, "get_visible_price_options", return_value=prices):
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
        with patch.object(bot, "_get_detail_title_text", return_value="马思唯上海站"), \
             patch.object(bot, "_get_detail_venue_text", return_value="浦发银行东方体育中心"), \
             patch.object(bot, "ensure_sku_page_for_inspection", return_value={"state": "detail_page"}), \
             patch.object(bot, "get_visible_date_options") as get_dates, \
             patch.object(bot, "get_visible_price_options") as get_prices:
            summary = bot.inspect_current_target_event({"state": "detail_page"})

        assert summary["state"] == "detail_page"
        assert summary["dates"] == []
        assert summary["price_options"] == []
        get_dates.assert_not_called()
        get_prices.assert_not_called()

    def test_check_session_valid_register_prompt(self, bot, caplog):
        """'登录/注册' text detected on page, returns False."""
        def has_element_side_effect(by, value):
            return '登录/注册' in value

        with caplog.at_level("ERROR", logger="mobile.damai_app"), \
             patch.object(bot, "_get_current_activity", return_value="SomeActivity"), \
             patch.object(bot, "_has_element", side_effect=has_element_side_effect):
            result = bot.check_session_valid()

        assert result is False
        assert "登录提示" in caplog.text


# ---------------------------------------------------------------------------
# Price selection (text match + index fallback)
# ---------------------------------------------------------------------------

class TestPriceSelection:
    def test_select_price_option_fast_rush_mode_trusts_index_without_visible_scan(self, bot):
        bot.config.rush_mode = True
        bot.config.price_index = 5

        with patch.object(bot, "_click_price_option_by_config_index", return_value=True) as click_index, \
             patch.object(bot, "get_visible_price_options") as get_visible:
            result = bot._select_price_option_fast()

        assert result is True
        click_index.assert_called_once_with(burst=True, coords=None)
        get_visible.assert_not_called()

    def test_select_price_option_fast_rush_mode_uses_cached_coordinates(self, bot):
        bot.config.rush_mode = True

        with patch.object(bot, "_click_price_option_by_config_index", return_value=True) as click_index, \
             patch.object(bot, "get_visible_price_options") as get_visible:
            result = bot._select_price_option_fast(cached_coords=(240, 1560))

        assert result is True
        click_index.assert_called_once_with(burst=True, coords=(240, 1560))
        get_visible.assert_not_called()

    def test_select_price_option_fast_uses_config_index_without_ocr(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with patch.object(bot, "get_visible_price_options", return_value=[
            {"index": 5, "text": "", "tag": "", "source": "ui"},
        ]) as get_visible, \
             patch.object(bot, "_click_visible_price_option", return_value=True) as click_visible:
            result = bot._select_price_option_fast()

        assert result is True
        get_visible.assert_called_once_with(allow_ocr=False)
        click_visible.assert_called_once_with(5)

    def test_click_price_option_by_config_index_bursts_clicks_in_rush_mode(self, bot):
        with patch.object(bot, "_get_price_option_coordinates_by_config_index", return_value=(260, 1540)), \
             patch.object(bot, "_burst_click_coordinates") as burst_click:
            result = bot._click_price_option_by_config_index(burst=True)

        assert result is True
        burst_click.assert_called_once_with(260, 1540, count=2, interval_ms=25, duration=25)

    def test_select_price_option_fast_uses_config_index_when_ui_tree_is_empty(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with patch.object(bot, "get_visible_price_options", return_value=[]), \
             patch.object(bot, "_click_price_option_by_config_index", return_value=True) as click_index, \
             patch.object(bot, "ultra_fast_click", return_value=False):
            result = bot._select_price_option_fast()

        assert result is True
        click_index.assert_called_once_with()

    def test_select_price_option_prefers_visible_exact_match(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with patch.object(bot, "get_visible_price_options", return_value=[
            {"index": 0, "text": "看台699元", "tag": "", "source": "ocr"},
            {"index": 5, "text": "看台899元", "tag": "", "source": "ocr"},
        ]), \
             patch.object(bot, "_click_visible_price_option", return_value=True) as click_visible, \
             patch.object(bot, "ultra_fast_click") as fast_click:
            result = bot._select_price_option()

        assert result is True
        click_visible.assert_called_once_with(5)
        fast_click.assert_not_called()

    def test_select_price_option_returns_false_when_target_unavailable(self, bot):
        bot.config.price = "899元"
        bot.config.price_index = 5

        with patch.object(bot, "get_visible_price_options", return_value=[
            {"index": 5, "text": "看台899元", "tag": "缺货登记", "source": "ocr"},
        ]), \
             patch.object(bot, "_click_visible_price_option") as click_visible, \
             patch.object(bot, "ultra_fast_click") as fast_click:
            result = bot._select_price_option()

        assert result is False
        click_visible.assert_not_called()
        fast_click.assert_not_called()

    def test_submit_order_fast_retries_until_success(self, bot):
        submit_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]')
        ]

        with patch.object(bot, "ultra_fast_click", side_effect=[True, True]), \
             patch.object(bot, "smart_wait_and_click", return_value=False), \
             patch.object(bot, "verify_order_result", side_effect=["timeout", "success"]) as verify_result:
            result = bot._submit_order_fast(submit_selectors)

        assert result == "success"
        assert verify_result.call_args_list == [call(timeout=1.2), call(timeout=1.2)]

    def test_submit_order_fast_runs_followup_verify_when_submit_disappears(self, bot):
        submit_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]')
        ]

        with patch.object(bot, "ultra_fast_click", side_effect=[True, False, False]), \
             patch.object(bot, "smart_wait_and_click", return_value=False), \
             patch.object(bot, "verify_order_result", side_effect=["timeout", "existing_order"]) as verify_result:
            result = bot._submit_order_fast(submit_selectors)

        assert result == "existing_order"
        assert verify_result.call_args_list == [call(timeout=1.2), call(timeout=2)]

    def test_price_selection_text_match_success(self, bot):
        """Text-based price match works, index fallback not used."""
        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "check_session_valid", return_value=True), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "select_performance_date"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True) as ufc, \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="success"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.5]
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        # ultra_fast_click should have been called with the price text selector
        price_call_found = any(
            'textContains("799元")' in str(c)
            for c in ufc.call_args_list
        )
        assert price_call_found, f"Expected price text selector call, got: {ufc.call_args_list}"

    def test_price_selection_falls_back_to_index(self, bot, caplog):
        """Text match fails, index-based fallback used."""
        call_count = [0]

        def ultra_fast_click_side_effect(by, value, timeout=1.5):
            call_count[0] += 1
            # First call with textContains (price) returns False to trigger fallback
            if 'textContains("799元")' in str(value):
                return False
            return True

        with caplog.at_level("INFO", logger="mobile.damai_app"), \
             patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "check_session_valid", return_value=True), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "wait_for_sale_start"), \
             patch.object(bot, "select_performance_date"), \
             patch.object(bot, "_enter_purchase_flow_from_detail_page", return_value={
                 "state": "sku_page",
                 "price_container": True,
                 "reservation_mode": False,
             }), \
             patch.object(bot, "_wait_for_submit_ready", return_value=True), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", side_effect=ultra_fast_click_side_effect), \
             patch.object(bot, "ultra_batch_click"), \
             patch.object(bot, "_submit_order_fast", return_value="success"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.5]
            # Mock price container for index-based fallback
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        assert "通过配置索引直接选择票价" in caplog.text


# ---------------------------------------------------------------------------
# Utility method coverage: _safe_element_text / _safe_element_texts
# ---------------------------------------------------------------------------

class TestSafeElementText:
    def test_returns_first_nonempty_text(self, bot):
        container = Mock()
        el1 = Mock()
        el1.text = "  "  # whitespace only
        el2 = Mock()
        el2.text = "580元"
        container.find_elements.return_value = [el1, el2]
        result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == "580元"

    def test_returns_empty_when_all_empty(self, bot):
        container = Mock()
        el = Mock()
        el.text = "  "
        container.find_elements.return_value = [el]
        result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""

    def test_returns_empty_on_exception(self, bot):
        container = Mock()
        container.find_elements.side_effect = Exception("driver error")
        result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""

    def test_returns_empty_when_no_elements(self, bot):
        container = Mock()
        container.find_elements.return_value = []
        result = bot._safe_element_text(container, By.CLASS_NAME, "tv_price")
        assert result == ""


class TestSafeElementTexts:
    def test_returns_unique_nonempty_texts(self, bot):
        container = Mock()
        el1 = Mock()
        el1.text = "580元"
        el2 = Mock()
        el2.text = "580元"  # duplicate
        el3 = Mock()
        el3.text = "1280元"
        container.find_elements.return_value = [el1, el2, el3]
        result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == ["580元", "1280元"]

    def test_returns_empty_list_on_exception(self, bot):
        container = Mock()
        container.find_elements.side_effect = Exception("driver error")
        result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == []

    def test_filters_empty_texts(self, bot):
        container = Mock()
        el1 = Mock()
        el1.text = ""
        el2 = Mock()
        el2.text = "380元"
        container.find_elements.return_value = [el1, el2]
        result = bot._safe_element_texts(container, By.CLASS_NAME, "tv_price")
        assert result == ["380元"]


# ---------------------------------------------------------------------------
# _collect_descendant_texts
# ---------------------------------------------------------------------------

class TestCollectDescendantTexts:
    def test_returns_unique_texts(self, bot):
        container = Mock()
        el1 = Mock()
        el1.text = "580元"
        el2 = Mock()
        el2.text = "580元"  # duplicate
        el3 = Mock()
        el3.text = "可预约"
        container.find_elements.return_value = [el1, el2, el3]
        result = bot._collect_descendant_texts(container)
        assert result == ["580元", "可预约"]

    def test_returns_empty_on_find_elements_exception(self, bot):
        container = Mock()
        container.find_elements.side_effect = Exception("error")
        result = bot._collect_descendant_texts(container)
        assert result == []

    def test_handles_element_text_exception(self, bot):
        container = Mock()
        el1 = Mock()
        type(el1).text = PropertyMock(side_effect=Exception("stale element"))
        el2 = Mock()
        el2.text = "正常文本"
        container.find_elements.return_value = [el1, el2]
        result = bot._collect_descendant_texts(container)
        assert result == ["正常文本"]


# ---------------------------------------------------------------------------
# _has_element exception path / _get_current_activity exception path
# ---------------------------------------------------------------------------

class TestHasElementExceptionPath:
    def test_has_element_returns_false_on_exception(self, bot):
        bot.driver.find_elements.side_effect = Exception("driver error")
        result = bot._has_element(By.ID, "some_id")
        assert result is False
        bot.driver.find_elements.side_effect = None  # reset

    def test_has_any_element_returns_false_when_all_miss(self, bot):
        bot.driver.find_elements.return_value = []
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
    def test_click_element_center_calls_script(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        bot._click_element_center(el)
        bot.driver.execute_script.assert_called_with(
            "mobile: clickGesture",
            {"x": 125, "y": 220, "duration": 50},
        )

    def test_burst_click_element_center_calls_multiple_times(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        with patch("mobile.damai_app.time.sleep") as mock_sleep:
            bot._burst_click_element_center(el, count=3, interval_ms=10)
        assert bot.driver.execute_script.call_count >= 3
        assert mock_sleep.call_count == 2  # sleeps between calls

    def test_burst_click_element_center_no_sleep_when_zero_interval(self, bot):
        el = _make_mock_element(x=100, y=200, width=50, height=40)
        bot.driver.execute_script.reset_mock()
        with patch("mobile.damai_app.time.sleep") as mock_sleep:
            bot._burst_click_element_center(el, count=2, interval_ms=0)
        assert mock_sleep.call_count == 0

    def test_burst_click_coordinates_calls_script(self, bot):
        bot.driver.execute_script.reset_mock()
        with patch("mobile.damai_app.time.sleep"):
            bot._burst_click_coordinates(100, 200, count=2, interval_ms=10)
        assert bot.driver.execute_script.call_count == 2

    def test_burst_click_coordinates_no_sleep_for_single(self, bot):
        bot.driver.execute_script.reset_mock()
        with patch("mobile.damai_app.time.sleep") as mock_sleep:
            bot._burst_click_coordinates(100, 200, count=1, interval_ms=10)
        assert mock_sleep.call_count == 0


# ---------------------------------------------------------------------------
# smart_wait_for_element — backup selectors and not-found path
# ---------------------------------------------------------------------------

class TestSmartWaitForElement:
    def test_returns_true_on_primary_found(self, bot):
        with patch("mobile.damai_app.WebDriverWait") as mock_wdw:
            mock_wdw.return_value.until.return_value = Mock()
            result = bot.smart_wait_for_element(By.ID, "some_id")
        assert result is True

    def test_returns_false_when_all_timeout(self, bot):
        with patch("mobile.damai_app.WebDriverWait") as mock_wdw:
            mock_wdw.return_value.until.side_effect = TimeoutException()
            result = bot.smart_wait_for_element(
                By.ID, "primary_id",
                backup_selectors=[(By.ID, "backup_id")],
            )
        assert result is False

    def test_uses_backup_when_primary_fails(self, bot):
        call_count = [0]
        def until_side_effect(condition):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TimeoutException()
            return Mock()
        with patch("mobile.damai_app.WebDriverWait") as mock_wdw:
            mock_wdw.return_value.until.side_effect = until_side_effect
            result = bot.smart_wait_for_element(
                By.ID, "primary_id",
                backup_selectors=[(By.ID, "backup_id")],
            )
        assert result is True


# ---------------------------------------------------------------------------
# wait_for_page_state — timeout path
# ---------------------------------------------------------------------------

class TestWaitForPageState:
    def test_returns_last_probe_on_timeout(self, bot):
        with patch.object(bot, "probe_current_page",
                          return_value={"state": "unknown_state"}), \
             patch("mobile.damai_app.time.time", side_effect=[0.0, 10.0, 20.0]), \
             patch("mobile.damai_app.time.sleep"):
            result = bot.wait_for_page_state({"order_confirm_page"}, timeout=5)
        assert result["state"] == "unknown_state"

    def test_returns_immediately_on_matching_state(self, bot):
        with patch.object(bot, "probe_current_page",
                          return_value={"state": "detail_page"}), \
             patch("mobile.damai_app.time.time", side_effect=[0.0, 1.0]), \
             patch("mobile.damai_app.time.sleep"):
            result = bot.wait_for_page_state({"detail_page"})
        assert result["state"] == "detail_page"


# ---------------------------------------------------------------------------
# _prepare_runtime_config — error handling and city mismatch branches
# ---------------------------------------------------------------------------

class TestPrepareRuntimeConfig:
    def _make_config_with_item_url(self, keyword="张杰 演唱会", city="北京"):
        return Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword=keyword,
            users=["UserA"],
            city=city,
            date="04.06",
            price="580元",
            price_index=0,
            if_commit_order=False,
            probe_only=True,
            item_url="https://m.damai.cn/damai/detail/item.html?itemId=1016133935724",
            item_id=None,
        )

    def test_resolve_error_with_keyword_logs_warning_and_continues(self, caplog):
        """If DamaiItemResolveError and keyword exists, log warning and return."""
        from mobile.item_resolver import DamaiItemResolveError
        cfg = self._make_config_with_item_url(keyword="张杰 演唱会")
        with patch("mobile.damai_app.DamaiItemResolver") as mock_resolver_cls, \
             caplog.at_level("WARNING", logger="mobile.damai_app"):
            mock_resolver_cls.return_value.fetch_item_detail.side_effect = DamaiItemResolveError("网络错误")
            bot = DamaiBot(config=cfg, setup_driver=False)
        assert bot.item_detail is None
        assert "继续使用现有 keyword" in caplog.text

    def test_resolve_error_without_keyword_raises(self):
        """If DamaiItemResolveError and no keyword (None), re-raise."""
        from mobile.item_resolver import DamaiItemResolveError
        cfg = self._make_config_with_item_url(keyword="张杰 演唱会")
        # Directly set keyword to None after construction to bypass validation
        cfg.keyword = None
        with patch("mobile.damai_app.DamaiItemResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.fetch_item_detail.side_effect = DamaiItemResolveError("网络错误")
            with pytest.raises(DamaiItemResolveError):
                DamaiBot(config=cfg, setup_driver=False)

    def test_city_mismatch_raises_value_error(self):
        """If fetched city doesn't match config city, raise ValueError."""
        item_detail = _make_item_detail()  # city_name="北京市"
        cfg = self._make_config_with_item_url(city="上海")  # mismatch!
        with patch("mobile.damai_app.DamaiItemResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.fetch_item_detail.return_value = item_detail
            with pytest.raises(ValueError, match="不一致"):
                DamaiBot(config=cfg, setup_driver=False)

    def test_keyword_auto_populated_from_item_detail(self):
        """When keyword is None and item resolves OK, keyword is set from item_detail."""
        item_detail_with_keyword = DamaiItemDetail(
            item_id="1016133935724",
            item_name="张杰演唱会",
            item_name_display="张杰演唱会",
            city_name="北京市",
            venue_name="鸟巢",
            venue_city_name="北京市",
            show_time="2026.04.06",
            price_range="580-1280",
            raw_data={},
        )
        cfg = self._make_config_with_item_url(city="北京", keyword="张杰 演唱会")
        # Directly set keyword to None after construction
        cfg.keyword = None
        with patch("mobile.damai_app.DamaiItemResolver") as mock_resolver_cls:
            mock_resolver_cls.return_value.fetch_item_detail.return_value = item_detail_with_keyword
            bot = DamaiBot(config=cfg, setup_driver=False)
        assert bot.item_detail is not None
        assert bot.config.keyword is not None  # auto-populated
