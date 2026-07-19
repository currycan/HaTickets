"""Integration tests for mobile module workflow."""

import json
import time
from unittest.mock import Mock, patch

import pytest

from mobile.config import Config
from mobile.damai_app import DamaiBot
from mobile.damai_app.submit_journal import (
    bind_process_device_identity,
    reset_process_device_identity,
)


@pytest.fixture(autouse=True)
def _bind_test_physical_device_identity():
    """Give mocked formal workflows an explicit canonical device identity."""
    token = bind_process_device_identity(None, "TEST-PHYSICAL-DEVICE")
    try:
        yield
    finally:
        reset_process_device_identity(token)


def _make_mock_element(x=100, y=200, width=50, height=40):
    el = Mock()
    el.rect = {"x": x, "y": y, "width": width, "height": height}
    el.id = "fake-id"
    return el


def _make_mock_u2_device():
    """Create a mock u2 device with the attributes DamaiBot._setup_driver expects."""
    device = Mock()
    device.serial = "TESTSERIAL"
    device.app_current = Mock(return_value={"package": "cn.damai"})
    device.settings = {}
    device.shell = Mock(return_value="")
    device.dump_hierarchy = Mock(return_value="<hierarchy/>")

    def absent_selector(**_kwargs):
        selector = Mock()
        selector.exists = False
        selector.wait = Mock(return_value=False)
        return selector

    device.side_effect = absent_selector
    return device


class TestConfigToBotInit:
    def test_load_config_to_bot_init(self, tmp_path, monkeypatch):
        """Config.load_config → DamaiBot.__init__ → driver setup chain works."""
        monkeypatch.chdir(tmp_path)
        config_data = {
            "device_name": "Android",
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
        (tmp_path / "config.jsonc").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        mock_device = _make_mock_u2_device()

        with patch("uiautomator2.connect", return_value=mock_device):
            bot = DamaiBot()
            assert bot.config.city == "北京"
            assert bot.d is mock_device


class TestFullTicketGrabbingFlow:
    def test_all_phases_succeed(self):
        """Full 7-phase flow with mocked driver returns True."""
        mock_device = _make_mock_u2_device()
        mock_el = _make_mock_element()

        mock_config = Config(
            device_name="Android",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["A"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with \
            patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
            patch("uiautomator2.connect", return_value=mock_device), \
            patch("mobile.damai_app.time.sleep"):
            mock_device.find_element = Mock(return_value=mock_el)
            mock_device.find_elements = Mock(return_value=[mock_el])
            mock_device.execute_script = Mock()

            bot = DamaiBot()

            def formal_submit_boundary():
                proof = {
                    "resource_id": "cn.damai:id/btn_submit",
                    "package": "cn.damai",
                    "activity": "OrderConfirmActivity",
                    "layout_digest": "integration-confirm-layout",
                    "token": ("integration-submit-boundary",),
                    "fields": (("target_title", "test", ("test",)),),
                    "selected_names": ("A",),
                    "selected_row_count": 1,
                    "quantity": 1,
                    "observed_at": time.monotonic(),
                    "run_generation": bot._run_generation,
                }
                bot._last_formal_submit_boundary = proof
                return proof

            def verified_payment_result(timeout=5):
                context = bot._active_submit_verification_context(
                    observed_at=time.monotonic(),
                    timeout=timeout,
                )
                assert context is not None
                assert bot._remember_bound_submit_result(
                    "success",
                    context,
                    activity="integration.PaymentActivity",
                )
                return "success"

            with \
                patch.object(bot, "dismiss_startup_popups"), \
                patch.object(bot, "check_session_valid", return_value=True), \
                patch.multiple(
                    bot,
                    _current_page_matches_target=Mock(return_value=True),
                    _ensure_target_session=Mock(
                        side_effect=lambda probe: probe
                    ),
                    _reverify_formal_session_after_sku_recovery=Mock(
                        side_effect=lambda probe: probe
                    ),
                    _get_price_option_coordinates_by_config_index=Mock(
                        return_value=(300, 1200)
                    ),
                    _get_buy_button_coordinates=Mock(
                        return_value=(540, 2000)
                    ),
                    _ensure_formal_quantity_and_transition_snapshot=Mock(
                        return_value={"buy_coords": (540, 2000)}
                    ),
                    _formal_order_context_is_verified=Mock(return_value=True),
                    _formal_submit_boundary_snapshot=Mock(
                        side_effect=formal_submit_boundary
                    ),
                ), \
                patch.object(bot, "wait_for_sale_start"), \
                patch.object(bot, "select_performance_date"), \
                patch.object(
                    bot,
                    "_enter_purchase_flow_from_detail_page",
                    return_value={
                        "state": "sku_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": True,
                        "submit_button": False,
                    },
                ), \
                patch.object(bot, "_select_price_option", return_value=True), \
                patch.object(bot, "_has_element", return_value=False), \
                patch.object(bot, "ultra_fast_click", return_value=True), \
                patch.object(bot, "_wait_for_element", return_value=mock_el), \
                patch.object(bot, "_click_element_center"), \
                patch.object(bot, "_wait_for_submit_ready", return_value=True), \
                patch.object(
                    bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
                ), \
                patch.object(
                    bot,
                    "wait_for_page_state",
                    return_value={
                        "state": "order_confirm_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": False,
                        "submit_button": True,
                    },
                ), \
                patch.object(
                    bot,
                    "verify_order_result",
                    side_effect=verified_payment_result,
                ), \
                patch.object(
                    bot,
                    "probe_current_page",
                    return_value={
                        "state": "detail_page",
                        "purchase_button": True,
                        "price_container": True,
                        "quantity_picker": True,
                        "submit_button": True,
                    },
                ):
                result = bot.run_ticket_grabbing()
            assert result is True

    def test_flow_stops_before_submit_when_commit_disabled(self):
        """Commit-disabled mode reaches confirm page and exits before submit."""
        mock_device = _make_mock_u2_device()
        mock_el = _make_mock_element()

        mock_config = Config(
            device_name="Android",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["A"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=False,
            probe_only=False,
        )

        with \
            patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
            patch("uiautomator2.connect", return_value=mock_device), \
            patch("mobile.damai_app.time.sleep"):
            mock_device.find_element = Mock(return_value=mock_el)
            mock_device.find_elements = Mock(return_value=[mock_el])
            mock_device.execute_script = Mock()

            bot = DamaiBot()
            with \
                patch.object(bot, "dismiss_startup_popups"), \
                patch.object(bot, "check_session_valid", return_value=True), \
                patch.multiple(
                    bot,
                    _current_page_matches_target=Mock(return_value=True),
                    _ensure_target_session=Mock(
                        side_effect=lambda probe: probe
                    ),
                ), \
                patch.object(bot, "wait_for_sale_start"), \
                patch.object(bot, "select_performance_date"), \
                patch.object(
                    bot,
                    "_enter_purchase_flow_from_detail_page",
                    return_value={
                        "state": "sku_page",
                        "purchase_button": False,
                        "price_container": True,
                        "quantity_picker": True,
                        "submit_button": False,
                    },
                ), \
                patch.object(bot, "_select_price_option", return_value=True), \
                patch.object(bot, "_has_element", return_value=False), \
                patch.object(bot, "ultra_fast_click", return_value=True), \
                patch.object(
                    bot, "_ensure_attendees_selected_on_confirm_page", return_value=True
                ), \
                patch.object(
                    bot,
                    "probe_current_page",
                    return_value={
                        "state": "detail_page",
                        "purchase_button": True,
                        "price_container": True,
                        "quantity_picker": True,
                        "submit_button": True,
                    },
                ), \
                patch.object(
                    bot, "_wait_for_submit_ready", return_value=True
                ) as wait_submit_ready:
                result = bot.run_ticket_grabbing()

            assert result is True
            wait_submit_ready.assert_called_once()


class TestRetryWithDriverRecreation:
    def test_retry_recreates_driver(self):
        """run_with_retry calls quit + _setup_driver between attempts."""
        mock_device = _make_mock_u2_device()

        mock_config = Config(
            device_name="Android",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
            keyword="test",
            users=["A"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )

        with \
            patch("mobile.damai_app.Config.load_config", return_value=mock_config), \
            patch("uiautomator2.connect", return_value=mock_device), \
            patch("mobile.damai_app.time.sleep"):
            bot = DamaiBot()

            # Make run_ticket_grabbing always fail
            with \
                patch.object(bot, "run_ticket_grabbing", return_value=False), \
                patch.object(bot, "_fast_retry_from_current_state", return_value=False):
                with patch.object(bot, "_setup_driver") as mock_setup:
                    result = bot.run_with_retry(max_retries=3)

                    assert result is False
                    # quit called between retries (2 times for 3 attempts)
                    assert mock_device.quit.call_count == 2
                    assert mock_setup.call_count == 2
