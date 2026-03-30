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


class TestConfigToBotInit:

    def test_load_config_to_bot_init(self, tmp_path, monkeypatch):
        """Config.load_config → DamaiBot.__init__ → driver setup chain works."""
        monkeypatch.chdir(tmp_path)
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Android",
            "udid": None,
            "platform_version": None,
            "app_package": "cn.damai",
            "app_activity": ".launcher.splash.SplashMainActivity",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": True,
            "probe_only": False,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        mock_driver = Mock()
        mock_driver.update_settings = Mock()

        with patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"):
            bot = DamaiBot()
            assert bot.config.city == "北京"
            assert bot.driver is mock_driver
            mock_driver.update_settings.assert_called_once()


class TestFullTicketGrabbingFlow:

    def test_all_phases_succeed(self):
        """Full 7-phase flow with mocked driver returns True."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()
        mock_driver.current_activity = "ProjectDetailActivity"
        mock_el = _make_mock_element()

        mock_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test", users=["A"], city="深圳",
            date="12.06", price="799元", price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.WebDriverWait") as mock_wait, \
             patch("mobile.damai_app.time.sleep"):
            mock_wait.return_value.until = Mock(return_value=mock_el)
            mock_driver.find_element = Mock(return_value=mock_el)
            mock_driver.find_elements = Mock(return_value=[mock_el])
            mock_driver.execute_script = Mock()

            bot = DamaiBot()
            with patch.object(bot, "dismiss_startup_popups"), \
                 patch.object(bot, "check_session_valid", return_value=True), \
                 patch.object(bot, "wait_for_sale_start"), \
                 patch.object(bot, "select_performance_date"), \
                 patch.object(bot, "wait_for_page_state", return_value={
                     "state": "order_confirm_page",
                     "purchase_button": False,
                     "price_container": True,
                     "quantity_picker": False,
                     "submit_button": True,
                 }), \
                 patch.object(bot, "verify_order_result", return_value="success"), \
                 patch.object(bot, "probe_current_page", return_value={
                     "state": "detail_page",
                     "purchase_button": True,
                     "price_container": True,
                     "quantity_picker": True,
                     "submit_button": True,
                 }):
                result = bot.run_ticket_grabbing()
            assert result is True

    def test_flow_stops_before_submit_when_commit_disabled(self):
        """Commit-disabled mode reaches confirm page and exits before submit."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()
        mock_driver.current_activity = "ProjectDetailActivity"
        mock_el = _make_mock_element()

        mock_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test", users=["A"], city="深圳",
            date="12.06", price="799元", price_index=1,
            if_commit_order=False,
            probe_only=False,
        )

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.WebDriverWait") as mock_wait, \
             patch("mobile.damai_app.time.sleep"):
            mock_wait.return_value.until = Mock(return_value=mock_el)
            mock_driver.find_element = Mock(return_value=mock_el)
            mock_driver.find_elements = Mock(return_value=[mock_el])
            mock_driver.execute_script = Mock()

            bot = DamaiBot()
            with patch.object(bot, "dismiss_startup_popups"), \
                 patch.object(bot, "check_session_valid", return_value=True), \
                 patch.object(bot, "wait_for_sale_start"), \
                 patch.object(bot, "select_performance_date"), \
                 patch.object(bot, "probe_current_page", return_value={
                     "state": "detail_page",
                     "purchase_button": True,
                     "price_container": True,
                     "quantity_picker": True,
                     "submit_button": True,
                 }), \
                 patch.object(bot, "_wait_for_submit_ready", return_value=True) as wait_submit_ready:
                result = bot.run_ticket_grabbing()

            assert result is True
            wait_submit_ready.assert_called_once()


class TestRetryWithDriverRecreation:

    def test_retry_recreates_driver(self):
        """run_with_retry calls quit + _setup_driver between attempts."""
        mock_driver = Mock()
        mock_driver.update_settings = Mock()
        mock_driver.quit = Mock()

        mock_config = Config(
            server_url="http://127.0.0.1:4723",
            device_name="Android",
            udid=None,
            platform_version=None,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test", users=["A"], city="深圳",
            date="12.06", price="799元", price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
             patch("mobile.damai_app.webdriver.Remote", return_value=mock_driver), \
             patch("mobile.damai_app.AppiumOptions"), \
             patch("mobile.damai_app.time.sleep"):
            bot = DamaiBot()

            # Make run_ticket_grabbing always fail
            with patch.object(bot, "run_ticket_grabbing", return_value=False), \
                 patch.object(bot, "_fast_retry_from_current_state", return_value=False):
                with patch.object(bot, "_setup_driver") as mock_setup:
                    result = bot.run_with_retry(max_retries=3)

                    assert result is False
                    # quit called between retries (2 times for 3 attempts)
                    assert mock_driver.quit.call_count == 2
                    assert mock_setup.call_count == 2
