# -*- coding: UTF-8 -*-
"""Unit tests for mobile/damai_app.py — DamaiBot class."""

import time as _time_module

import pytest
from unittest.mock import Mock, patch, call, PropertyMock

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By

from mobile.damai_app import DamaiBot
from mobile.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mock_element(x=100, y=200, width=50, height=40):
    """Helper: create a mock element with a .rect property."""
    el = Mock()
    el.rect = {"x": x, "y": y, "width": width, "height": height}
    el.id = "fake-element-id"
    return el


@pytest.fixture
def bot():
    """Create a DamaiBot with fully mocked Appium driver and config."""
    mock_driver = Mock()
    mock_driver.update_settings = Mock()
    mock_driver.execute_script = Mock()
    mock_driver.find_element = Mock()
    mock_driver.find_elements = Mock(return_value=[])
    mock_driver.quit = Mock()

    mock_config = Config(
        server_url="http://127.0.0.1:4723",
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

    def test_batch_click_some_fail(self, bot, capsys):
        """Failed clicks print a message but processing continues."""
        elements = [("by1", "v1"), ("by2", "v2")]
        with patch.object(bot, "ultra_fast_click", side_effect=[False, True]) as ufc, \
             patch("mobile.damai_app.time"):
            bot.batch_click(elements, delay=0.1)

        assert ufc.call_count == 2
        captured = capsys.readouterr()
        assert "点击失败: v1" in captured.out


# ---------------------------------------------------------------------------
# ultra_batch_click
# ---------------------------------------------------------------------------

class TestUltraBatchClick:
    def test_ultra_batch_click_collects_and_clicks(self, bot, capsys):
        """Coordinates collected for all elements, then clicked sequentially."""
        el1 = _make_mock_element(x=10, y=20, width=100, height=50)
        el2 = _make_mock_element(x=200, y=300, width=60, height=30)

        with patch("mobile.damai_app.WebDriverWait") as MockWait, \
             patch("mobile.damai_app.time"):
            MockWait.return_value.until.side_effect = [el1, el2]
            bot.ultra_batch_click([("by1", "v1"), ("by2", "v2")], timeout=2)

        # Two clickGesture calls with correct center coordinates
        calls = bot.driver.execute_script.call_args_list
        assert len(calls) == 2
        assert calls[0] == call("mobile: clickGesture", {"x": 60, "y": 45, "duration": 30})
        assert calls[1] == call("mobile: clickGesture", {"x": 230, "y": 315, "duration": 30})

        captured = capsys.readouterr()
        assert "成功找到 2 个用户" in captured.out

    def test_ultra_batch_click_timeout_skips(self, bot, capsys):
        """Timed-out elements are skipped; found ones are still clicked."""
        el1 = _make_mock_element(x=10, y=20, width=100, height=50)

        with patch("mobile.damai_app.WebDriverWait") as MockWait, \
             patch("mobile.damai_app.time"):
            MockWait.return_value.until.side_effect = [
                el1,
                TimeoutException("timeout"),
            ]
            bot.ultra_batch_click([("by1", "v1"), ("by2", "v2")], timeout=2)

        # Only 1 click executed (the successful one)
        assert bot.driver.execute_script.call_count == 1
        captured = capsys.readouterr()
        assert "超时未找到用户: v2" in captured.out
        assert "成功找到 1 个用户" in captured.out


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
# run_ticket_grabbing
# ---------------------------------------------------------------------------

class TestRunTicketGrabbing:
    def test_run_ticket_grabbing_returns_false_when_not_detail_page(self, bot):
        """Homepage or other non-detail states fail fast with a clear result."""
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
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
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
             patch.object(bot, "smart_wait_and_click", return_value=False), \
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
             patch.object(bot, "smart_wait_and_click", side_effect=[True, False]), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            # Mock price container
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_price_exception_tries_backup(self, bot):
        """First price attempt raises, backup via wait.until succeeds."""
        mock_price_container = Mock()
        mock_target = _make_mock_element()
        mock_price_container.find_element.return_value = mock_target

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "smart_wait_and_click", return_value=True), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 2.0]
            # First find_element raises, triggering backup path
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
             patch.object(bot, "smart_wait_and_click", side_effect=RuntimeError("boom")), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            result = bot.run_ticket_grabbing()

        assert result is False

    def test_run_ticket_grabbing_submit_warns_on_failure(self, bot, capsys):
        """Submit button fails but function still returns True (warning printed)."""
        call_count = [0]

        def smart_click_side_effect(*args, **kwargs):
            call_count[0] += 1
            # 1st call = city, 2nd = book button, 3rd = submit
            if call_count[0] == 3:
                return False  # submit fails
            return True

        with patch.object(bot, "dismiss_startup_popups"), \
             patch.object(bot, "probe_current_page", return_value={
                 "state": "detail_page",
                 "purchase_button": True,
                 "price_container": True,
                 "quantity_picker": False,
                 "submit_button": False,
             }), \
             patch.object(bot, "smart_wait_and_click", side_effect=smart_click_side_effect), \
             patch.object(bot, "ultra_fast_click", return_value=True), \
             patch.object(bot, "ultra_batch_click"), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.side_effect = [0.0, 1.0]
            mock_price_container = Mock()
            mock_target = _make_mock_element()
            mock_price_container.find_element.return_value = mock_target
            bot.driver.find_element.return_value = mock_price_container
            bot.driver.find_elements.return_value = []

            result = bot.run_ticket_grabbing()

        assert result is True
        captured = capsys.readouterr()
        assert "提交订单按钮未找到" in captured.out

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
             patch.object(bot, "smart_wait_and_click", return_value=False), \
             patch("mobile.damai_app.time") as mock_time:
            mock_time.time.return_value = 0.0
            bot.run_ticket_grabbing()

        bot.driver.quit.assert_not_called()


class TestPageStateHelpers:
    def test_probe_current_page_detects_homepage(self, bot):
        with patch.object(
            bot,
            "_has_element",
            side_effect=lambda by, value: (by, value) == (By.ID, "cn.damai:id/homepage_header_search"),
        ), patch.object(bot, "_get_current_activity", return_value=""):
            result = bot.probe_current_page()

            assert result["state"] == "homepage"
            assert result["purchase_button"] is False

    def test_probe_current_page_detects_search_activity(self, bot):
        with patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "_get_current_activity", return_value="com.alibaba.pictures.bricks.search.v2.SearchActivity"):
            result = bot.probe_current_page()

            assert result["state"] == "search_page"
            assert result["purchase_button"] is False

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
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'),
        }

        with patch.object(bot, "_has_element", side_effect=lambda by, value: (by, value) in present), \
             patch.object(bot, "ultra_fast_click", return_value=True) as fast_click, \
             patch("mobile.damai_app.time.sleep"):
            result = bot.dismiss_startup_popups()

            assert result is True
            fast_click.assert_any_call(By.ID, "android:id/ok")
            fast_click.assert_any_call(By.ID, "cn.damai:id/id_boot_action_agree")
            fast_click.assert_any_call(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")')


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
             patch.object(bot, "_setup_driver") as mock_setup, \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is True
        mock_setup.assert_called_once()

    def test_run_with_retry_all_fail(self, bot):
        """All retries fail, returns False."""
        with patch.object(bot, "run_ticket_grabbing", return_value=False), \
             patch.object(bot, "_setup_driver"), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is False

    def test_run_with_retry_driver_quit_between_retries(self, bot):
        """Between retries, driver.quit and _setup_driver are called."""
        with patch.object(bot, "run_ticket_grabbing", side_effect=[False, False, True]), \
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
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        # Despite quit failure, retry continued and succeeded
        assert result is True
        mock_setup.assert_called_once()
