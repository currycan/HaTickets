"""Integration tests for mobile module workflow."""
import json
from unittest.mock import Mock, patch

import pytest
from selenium.common.exceptions import TimeoutException

from mobile.config import Config
from mobile.damai_app import DamaiBot


def _make_mock_element(x=100, y=200, width=50, height=40):
    el = Mock()
    el.rect = {"x": x, "y": y, "width": width, "height": height}
    el.id = "fake-id"
    return el


def _make_config(**overrides):
    defaults = dict(
        server_url="http://127.0.0.1:4723",
        device_name="Android",
        udid=None,
        platform_version="16",
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="test",
        users=["A"],
        city="深圳",
        date="12.06",
        price="799元",
        price_index=1,
        if_commit_order=True,
    )
    defaults.update(overrides)
    return Config(**defaults)


class TestConfigToBotInit:

    def test_load_config_to_bot_init(self, tmp_path, monkeypatch):
        """Config.load_config → DamaiBot.__init__ → driver setup chain works."""
        monkeypatch.chdir(tmp_path)
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Android",
            "platform_version": "16",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": True,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        mock_driver = Mock()
        mock_driver.update_settings = Mock()

        with patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.ClientConfig"), \
             patch("mobile.damai_app.RemoteConnection"):
            bot = DamaiBot()
            assert bot.config.city == "北京"
            assert bot.driver is mock_driver
            mock_driver.update_settings.assert_called_once()


class TestFullTicketGrabbingFlow:

    def _make_time_mock(self):
        m = Mock()
        m.time.side_effect = [0.0, 1.5, 3.0, 4.5]
        m.sleep = Mock()
        return m

    def test_all_phases_succeed(self):
        """Full flow with mocked driver returns True."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()
        mock_driver.current_activity = "ProjectDetailActivity"
        mock_el = _make_mock_element()

        mock_config = _make_config(if_commit_order=True)

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.ClientConfig"), \
             patch("mobile.damai_app.RemoteConnection"):
            mock_driver.find_element = Mock(return_value=mock_el)
            mock_driver.find_elements = Mock(return_value=[mock_el])
            mock_driver.execute_script = Mock()

            bot = DamaiBot()
            with patch.object(bot, "smart_wait_and_click", return_value=True), \
                 patch.object(bot, "_try_click_by_text_tokens", return_value=True), \
                 patch.object(bot, "_tap_from_dump", return_value=True), \
                 patch.object(bot, "_ensure_sku_panel", return_value=True), \
                 patch.object(bot, "_tap_right_bottom"), \
                 patch.object(bot, "_tap_text_from_dump", return_value=True), \
                 patch.object(bot, "_adb_screen_size", return_value=None), \
                 patch.object(bot, "_try_open_time_panel", return_value=True), \
                 patch.object(bot, "_try_select_date_by_index", return_value=True), \
                 patch.object(bot, "_try_select_any_price", return_value=True), \
                 patch.object(bot, "_swipe_up_small"), \
                 patch.object(bot, "_dump_page_source"), \
                 patch("mobile.damai_app.time", self._make_time_mock()):
                result = bot.run_ticket_grabbing()
            assert result is True

    def test_flow_stops_before_submit_when_commit_disabled(self):
        """Commit-disabled mode completes without submitting."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()

        mock_config = _make_config(if_commit_order=False)

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.ClientConfig"), \
             patch("mobile.damai_app.RemoteConnection"):
            mock_driver.find_elements = Mock(return_value=[])
            mock_driver.execute_script = Mock()

            bot = DamaiBot()
            with patch.object(bot, "smart_wait_and_click", return_value=True), \
                 patch.object(bot, "_try_click_by_text_tokens", return_value=True), \
                 patch.object(bot, "_tap_from_dump", return_value=True), \
                 patch.object(bot, "_ensure_sku_panel", return_value=True), \
                 patch.object(bot, "_tap_right_bottom"), \
                 patch.object(bot, "_tap_text_from_dump", return_value=True), \
                 patch.object(bot, "_adb_screen_size", return_value=None), \
                 patch.object(bot, "_try_open_time_panel", return_value=True), \
                 patch.object(bot, "_try_select_date_by_index", return_value=True), \
                 patch.object(bot, "_try_select_any_price", return_value=True), \
                 patch.object(bot, "_swipe_up_small"), \
                 patch.object(bot, "_dump_page_source"), \
                 patch("mobile.damai_app.time", self._make_time_mock()):
                result = bot.run_ticket_grabbing()

            assert result is True


class TestRetryWithDriverRecreation:

    def test_retry_recreates_driver(self):
        """run_with_retry calls quit + _setup_driver between attempts."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()

        mock_config = _make_config(if_commit_order=True)

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.ClientConfig"), \
             patch("mobile.damai_app.RemoteConnection"), \
             patch("mobile.damai_app.time"):
            bot = DamaiBot()

            with patch.object(bot, "run_ticket_grabbing", return_value=False):
                with patch.object(bot, "_setup_driver") as mock_setup:
                    result = bot.run_with_retry(max_retries=3)

                    assert result is False
                    # quit called between retries (2 times for 3 attempts)
                    assert mock_driver.quit.call_count == 2
                    assert mock_setup.call_count == 2


# ---------------------------------------------------------------------------
# Extended: DamaiBot helper methods
# ---------------------------------------------------------------------------

def _make_bot(config_overrides=None):
    mock_driver = Mock()
    mock_driver.update_settings = Mock()
    mock_driver.quit = Mock()

    cfg = _make_config(**(config_overrides or {}))

    with patch("mobile.damai_app.Config.load_config", return_value=cfg), \
         patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
         patch("mobile.damai_app.AppiumOptions"), \
         patch("mobile.damai_app.ClientConfig"), \
         patch("mobile.damai_app.RemoteConnection"):
        bot = DamaiBot()

    bot.driver = mock_driver
    return bot, mock_driver


class TestBotHelperMethods:

    def test_ultra_fast_click_returns_true_on_success(self):
        """ultra_fast_click returns True when element found and clicked."""
        bot, mock_driver = _make_bot()
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.webdriver.support import expected_conditions as EC
        el = _make_mock_element()

        with patch("mobile.damai_app.WebDriverWait") as mock_wait_cls:
            mock_wait = Mock()
            mock_wait.until = Mock(return_value=el)
            mock_wait_cls.return_value = mock_wait
            with patch.object(bot, "_click_element", return_value=True):
                result = bot.ultra_fast_click(AppiumBy.ID, "some.id", timeout=0.5)

        assert result is True

    def test_ultra_fast_click_returns_false_on_timeout(self):
        """ultra_fast_click returns False when element not found (TimeoutException)."""
        bot, mock_driver = _make_bot()
        from selenium.common.exceptions import TimeoutException
        from appium.webdriver.common.appiumby import AppiumBy

        with patch("mobile.damai_app.WebDriverWait") as mock_wait_cls:
            mock_wait = Mock()
            mock_wait.until = Mock(side_effect=TimeoutException())
            mock_wait_cls.return_value = mock_wait
            result = bot.ultra_fast_click(AppiumBy.ID, "nonexistent", timeout=0.1)

        assert result is False

    def test_batch_click_calls_ultra_fast_click(self):
        """batch_click calls ultra_fast_click for each (by, value) pair."""
        bot, mock_driver = _make_bot()
        from appium.webdriver.common.appiumby import AppiumBy

        elements_info = [
            (AppiumBy.ID, "btn1"),
            (AppiumBy.ID, "btn2"),
        ]

        with patch.object(bot, "ultra_fast_click", return_value=True) as mock_click, \
             patch("mobile.damai_app.time"):
            bot.batch_click(elements_info, delay=0)

        assert mock_click.call_count == len(elements_info)

    def test_run_with_retry_succeeds_first_attempt(self):
        """run_with_retry returns True immediately when first attempt succeeds."""
        bot, mock_driver = _make_bot()

        with patch.object(bot, "run_ticket_grabbing", return_value=True):
            result = bot.run_with_retry(max_retries=3)

        assert result is True
        mock_driver.quit.assert_not_called()

    def test_run_with_retry_retries_on_false(self):
        """run_with_retry retries when run_ticket_grabbing returns False."""
        bot, mock_driver = _make_bot()

        call_count = {"n": 0}
        def side_effect():
            call_count["n"] += 1
            return call_count["n"] >= 3  # succeed on 3rd attempt

        with patch.object(bot, "run_ticket_grabbing", side_effect=side_effect), \
             patch.object(bot, "_setup_driver"), \
             patch("mobile.damai_app.time"):
            result = bot.run_with_retry(max_retries=3)

        assert result is True
        assert call_count["n"] == 3


class TestBotConfigPropagation:

    def test_fast_mode_config_accessible(self):
        """Bot exposes fast_mode from config."""
        bot, _ = _make_bot({"fast_mode": True})
        assert bot.config.fast_mode is True

    def test_commit_order_disabled_config_accessible(self):
        """Bot exposes if_commit_order from config."""
        bot, _ = _make_bot({"if_commit_order": False})
        assert bot.config.if_commit_order is False

    def test_users_list_accessible(self):
        """Bot config has users list."""
        bot, _ = _make_bot({"users": ["Alice", "Bob"]})
        assert bot.config.users == ["Alice", "Bob"]
