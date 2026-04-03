# -*- coding: UTF-8 -*-
"""Tests for mobile.fast_pipeline validation and warm pipeline methods."""

import time
from unittest.mock import Mock, patch

from mobile.fast_pipeline import (
    FastPipeline,
    _PIPELINE_DEADLINE_S,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_validation_pipeline():
    device = Mock()
    device.shell = Mock(return_value=("", ""))
    config = Mock()
    config.if_commit_order = False
    config.price_index = 5
    config.users = ["UserA"]
    config.city = "\u5317\u4eac"
    config.date = "04.18"

    fp = FastPipeline(device, config, probe=False, guard=Mock())
    bot = Mock()
    bot._wait_for_purchase_entry_result = Mock(return_value={"state": "sku_page"})
    bot._dump_hierarchy_xml = Mock(return_value=Mock())
    bot._get_price_option_coordinates_by_config_index = Mock(return_value=(300, 1200))
    bot._get_buy_button_coordinates = Mock(return_value=(540, 2100))
    bot._attendee_checkbox_elements = Mock(return_value=[])
    bot._set_run_outcome = Mock()
    fp.set_bot(bot)
    return fp, bot, device


def _make_warm_pipeline():
    device = Mock()
    device.shell = Mock()
    config = Mock()
    config.if_commit_order = False
    config.price_index = 3
    config.users = ["UserA"]
    config.city = "\u4e0a\u6d77"
    config.date = "05.01"

    fp = FastPipeline(device, config, probe=False, guard=Mock())
    fp._cached_coords = {
        "detail_buy": (540, 1700),
        "price": (200, 800),
        "sku_buy": (540, 1600),
        "attendee_checkboxes": [(100, 500), (100, 600)],
        "city": (300, 400),
        "date": (400, 300),
    }
    bot = Mock()
    bot._wait_for_purchase_entry_result = Mock(return_value={"state": "sku_page"})
    bot._click_coordinates = Mock()
    bot._click_price_option_by_config_index = Mock(return_value=True)
    bot._click_sku_buy_button_element = Mock(return_value=True)
    bot._set_run_outcome = Mock()
    fp.set_bot(bot)
    return fp, bot, device


# ---------------------------------------------------------------------------
# run_cold_validation additional branches
# ---------------------------------------------------------------------------


class TestRunColdValidationBranches:
    def test_returns_none_when_rush_preselect_fails(self):
        fp, bot, _device = _make_validation_pipeline()
        with patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=False):
            result = fp.run_cold_validation(start_time=time.time())
        assert result is None

    def test_returns_none_when_entry_probe_fails(self):
        fp, bot, _device = _make_validation_pipeline()
        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(fp, "_wait_for_purchase_entry", return_value=None):
            fp._cached_coords["detail_buy"] = (540, 1800)
            bot._click_coordinates = Mock()
            result = fp.run_cold_validation(start_time=time.time())
        # fmt: on
        assert result is None

    def test_goes_to_confirm_on_order_confirm_page(self):
        fp, bot, _device = _make_validation_pipeline()
        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(
                fp,
                "_wait_for_purchase_entry",
                return_value={"state": "order_confirm_page"},
            ), \
            patch.object(fp, "_finish_confirm", return_value=True) as finish_confirm:
            result = fp.run_cold_validation(start_time=time.time())
        # fmt: on
        assert result is True
        finish_confirm.assert_called_once()

    def test_returns_none_on_unknown_state(self):
        fp, bot, _device = _make_validation_pipeline()
        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "unknown_page"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False):
            result = fp.run_cold_validation(start_time=time.time())
        # fmt: on
        assert result is None

    def test_returns_none_when_no_price_coords(self):
        fp, bot, _device = _make_validation_pipeline()
        bot._get_price_option_coordinates_by_config_index.return_value = None
        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False):
            result = fp.run_cold_validation(start_time=time.time())
        # fmt: on
        assert result is None

    def test_element_fallback_when_shell_fails(self):
        """When shell fast path fails, falls back to element-based price selection."""
        fp, bot, _device = _make_validation_pipeline()
        bot._click_price_option_by_config_index = Mock(return_value=True)
        bot._click_sku_buy_button_element = Mock(return_value=True)

        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_wait_for_confirm_ready", return_value=True), \
            patch.object(fp, "_finish_confirm", return_value=True), \
            patch.object(fp, "_confirm_page_ready", return_value=False):
            result = fp.run_cold_validation(start_time=time.time())
        # fmt: on
        assert result is True

    def test_sold_out_detection(self):
        """When buy button is sold out midway, pipeline returns False."""
        fp, bot, _device = _make_validation_pipeline()
        bot._click_price_option_by_config_index = Mock(return_value=True)
        bot._click_sku_buy_button_element = Mock(return_value=True)
        bot._is_buy_button_sold_out = Mock(return_value=True)
        bot._set_terminal_failure = Mock()

        # fmt: off
        with \
            patch.object(fp, "rush_preselect_and_buy_via_xml", return_value=True), \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_wait_for_confirm_ready", return_value=False), \
            patch.object(fp, "_confirm_page_ready", return_value=False):
            # Use a start_time in the past so we're past the 50% mark
            result = fp.run_cold_validation(
                start_time=time.time() - _PIPELINE_DEADLINE_S * 0.6
            )
        # fmt: on
        assert result is False
        bot._set_terminal_failure.assert_called_once_with("sold_out")


# ---------------------------------------------------------------------------
# FastPipeline._finish_confirm
# ---------------------------------------------------------------------------


class TestFinishConfirm:
    def _make_pipeline_with_bot(self):
        device = Mock()
        config = Mock()
        config.users = ["UserA", "UserB"]
        config.if_commit_order = False

        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._set_run_outcome = Mock()
        fp.set_bot(bot)
        return fp, bot

    def test_with_checkbox_elements(self):
        fp, bot = self._make_pipeline_with_bot()
        cb1 = Mock()
        cb1.bounds = [10, 20, 30, 40]
        cb2 = Mock()
        cb2.bounds = [10, 60, 30, 80]
        bot._attendee_checkbox_elements.return_value = [cb1, cb2]
        bot._click_attendee_checkbox_fast = Mock()

        result = fp._finish_confirm(start_time=time.time())

        assert result is True
        assert bot._click_attendee_checkbox_fast.call_count == 2
        bot._set_run_outcome.assert_called_once_with("validation_ready")
        assert "attendee_checkboxes" in fp._cached_coords

    def test_with_no_checkbox_elements(self):
        fp, bot = self._make_pipeline_with_bot()
        bot._attendee_checkbox_elements.return_value = []

        result = fp._finish_confirm(start_time=time.time())

        assert result is True
        bot._set_run_outcome.assert_called_once_with("validation_ready")

    def test_with_invalid_bounds(self):
        fp, bot = self._make_pipeline_with_bot()
        cb = Mock()
        cb.bounds = None
        bot._attendee_checkbox_elements.return_value = [cb]
        bot._click_attendee_checkbox_fast = Mock()

        result = fp._finish_confirm(start_time=time.time())
        assert result is True

    def test_limits_clicks_to_required_count(self):
        fp, bot = self._make_pipeline_with_bot()
        fp._config.users = ["UserA"]
        cb1 = Mock()
        cb1.bounds = [10, 20, 30, 40]
        cb2 = Mock()
        cb2.bounds = [10, 60, 30, 80]
        bot._attendee_checkbox_elements.return_value = [cb1, cb2]
        bot._click_attendee_checkbox_fast = Mock()

        fp._finish_confirm(start_time=time.time())
        assert bot._click_attendee_checkbox_fast.call_count == 1

    def test_with_none_users(self):
        fp, bot = self._make_pipeline_with_bot()
        fp._config.users = None
        bot._attendee_checkbox_elements.return_value = []

        result = fp._finish_confirm(start_time=time.time())
        assert result is True


# ---------------------------------------------------------------------------
# FastPipeline.run_warm_validation
# ---------------------------------------------------------------------------


class TestRunWarmValidation:
    def test_success_via_shell(self):
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=True):
            result = fp.run_warm_validation(start_time=time.time())
        # fmt: on

        assert result is True
        bot._set_run_outcome.assert_called_once_with("validation_ready")
        bot._click_coordinates.assert_called()

    def test_returns_none_when_entry_probe_fails(self):
        fp, bot, device = _make_warm_pipeline()

        with patch.object(fp, "_wait_for_purchase_entry", return_value=None):
            bot._click_coordinates = Mock()
            result = fp.run_warm_validation(start_time=time.time())

        assert result is None

    def test_returns_none_on_unknown_state(self):
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "unknown"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False):
            result = fp.run_warm_validation(start_time=time.time())
        # fmt: on

        assert result is None

    def test_skips_to_confirm_on_order_confirm_page(self):
        fp, bot, device = _make_warm_pipeline()

        with patch.object(
            fp, "_wait_for_purchase_entry", return_value={"state": "order_confirm_page"}
        ):
            result = fp.run_warm_validation(start_time=time.time())

        assert result is True
        bot._set_run_outcome.assert_called_once_with("validation_ready")

    def test_element_fallback_on_shell_failure(self):
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_select_price_with_pipeline", return_value=True), \
            patch.object(fp, "_click_sku_buy_with_pipeline", return_value=True), \
            patch.object(fp, "_wait_for_confirm_ready", return_value=True):
            result = fp.run_warm_validation(start_time=time.time())
        # fmt: on

        assert result is True

    def test_returns_none_when_price_select_fails(self):
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_select_price_with_pipeline", return_value=False):
            result = fp.run_warm_validation(start_time=time.time())
        # fmt: on

        assert result is None

    def test_returns_none_when_sku_buy_fails(self):
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_select_price_with_pipeline", return_value=True), \
            patch.object(fp, "_click_sku_buy_with_pipeline", return_value=False):
            result = fp.run_warm_validation(start_time=time.time())
        # fmt: on

        assert result is None

    def test_uses_date_and_city_coords(self):
        fp, bot, device = _make_warm_pipeline()

        with patch.object(
            fp, "_wait_for_purchase_entry", return_value={"state": "order_confirm_page"}
        ):
            fp.run_warm_validation(start_time=time.time())

        first_shell_cmd = device.shell.call_args_list[0][0][0]
        assert "input tap 400 300" in first_shell_cmd
        assert "input tap 300 400" in first_shell_cmd

    def test_skips_date_city_when_in_no_match(self):
        fp, bot, device = _make_warm_pipeline()
        fp._cached_no_match = {"date", "city"}

        with patch.object(
            fp, "_wait_for_purchase_entry", return_value={"state": "order_confirm_page"}
        ):
            fp.run_warm_validation(start_time=time.time())

        first_shell_cmd = device.shell.call_args_list[0][0][0]
        assert "input tap 400 300" not in first_shell_cmd
        assert "input tap 300 400" not in first_shell_cmd

    def test_returns_none_when_confirm_never_reached(self):
        """When on sku_page and confirm is never reached, returns None."""
        fp, bot, device = _make_warm_pipeline()

        # fmt: off
        with \
            patch.object(
                fp, "_wait_for_purchase_entry", return_value={"state": "sku_page"}
            ), \
            patch.object(fp, "_confirm_page_ready", return_value=False), \
            patch.object(fp, "_shell_price_and_buy_until_confirm", return_value=False), \
            patch.object(fp, "_select_price_with_pipeline", return_value=True), \
            patch.object(fp, "_click_sku_buy_with_pipeline", return_value=True), \
            patch.object(fp, "_wait_for_confirm_ready", return_value=False):
            result = fp.run_warm_validation(
                start_time=time.time() - _PIPELINE_DEADLINE_S
            )
        # fmt: on

        assert result is None


# ---------------------------------------------------------------------------
# FastPipeline helper method edge cases
# ---------------------------------------------------------------------------


class TestPipelineHelperEdgeCases:
    def test_wait_for_purchase_entry_returns_none_when_deadline_passed(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        fp.set_bot(bot)

        result = fp._wait_for_purchase_entry(deadline=time.time() - 1.0)
        assert result is None
        bot._wait_for_purchase_entry_result.assert_not_called()

    def test_confirm_page_ready_on_checkbox_id(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._has_element = Mock(return_value=True)
        bot._attendee_checkbox_elements = Mock(return_value=[])
        fp.set_bot(bot)

        assert fp._confirm_page_ready() is True

    def test_confirm_page_ready_on_attendee_elements(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._has_element = Mock(side_effect=[False, False, False, False])
        bot._attendee_checkbox_elements = Mock(return_value=[Mock()])
        fp.set_bot(bot)

        assert fp._confirm_page_ready() is True

    def test_confirm_page_ready_false_when_nothing_found(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._has_element = Mock(return_value=False)
        bot._attendee_checkbox_elements = Mock(return_value=[])
        fp.set_bot(bot)

        assert fp._confirm_page_ready() is False

    def test_open_purchase_panel_returns_false_when_no_coords(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        fp.set_bot(bot)

        result = fp._open_purchase_panel(None, time.time() + 5.0)
        assert result is False

    def test_open_purchase_panel_returns_false_when_deadline_passed(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        fp.set_bot(bot)

        result = fp._open_purchase_panel((540, 1700), time.time() - 1.0)
        assert result is False

    def test_open_purchase_panel_returns_entry_on_shell_success(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._wait_for_purchase_entry_result = Mock(return_value={"state": "sku_page"})
        fp.set_bot(bot)

        result = fp._open_purchase_panel((540, 1700), time.time() + 5.0)
        assert result == {"state": "sku_page"}

    def test_open_purchase_panel_u2_click_fallback(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._wait_for_purchase_entry_result = Mock(
            side_effect=[None, {"state": "sku_page"}]
        )
        bot._click_coordinates = Mock()
        fp.set_bot(bot)

        result = fp._open_purchase_panel((540, 1700), time.time() + 5.0)
        assert result == {"state": "sku_page"}
        bot._click_coordinates.assert_called_once_with(540, 1700, duration=25)

    def test_select_price_delegates_to_bot(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._click_price_option_by_config_index = Mock(return_value=True)
        fp.set_bot(bot)

        result = fp._select_price_with_pipeline((200, 800))
        assert result is True
        bot._click_price_option_by_config_index.assert_called_once_with(
            coords=(200, 800)
        )

    def test_click_sku_buy_element_succeeds(self):
        device = Mock()
        config = Mock()
        config.if_commit_order = True
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._click_sku_buy_button_element = Mock(return_value=True)
        fp.set_bot(bot)

        result = fp._click_sku_buy_with_pipeline((540, 1600))
        assert result is True
        bot._click_sku_buy_button_element.assert_called_once_with(burst_count=2)

    def test_click_sku_buy_falls_back_to_coordinates(self):
        device = Mock()
        config = Mock()
        config.if_commit_order = False
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._click_sku_buy_button_element = Mock(return_value=False)
        bot._burst_click_coordinates = Mock()
        fp.set_bot(bot)

        result = fp._click_sku_buy_with_pipeline((540, 1600))
        assert result is True
        bot._burst_click_coordinates.assert_called_once_with(
            540,
            1600,
            count=1,
            interval_ms=25,
            duration=25,
        )

    def test_click_sku_buy_returns_false_when_both_fail(self):
        device = Mock()
        config = Mock()
        config.if_commit_order = False
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._click_sku_buy_button_element = Mock(return_value=False)
        fp.set_bot(bot)

        result = fp._click_sku_buy_with_pipeline(None)
        assert result is False

    def test_shell_price_and_buy_returns_false_no_price(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp._shell_price_and_buy_until_confirm(
            None, (540, 1600), time.time() + 5.0
        )
        assert result is False

    def test_shell_price_and_buy_returns_false_no_sku(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp._shell_price_and_buy_until_confirm(
            (200, 800), None, time.time() + 5.0
        )
        assert result is False

    def test_shell_price_and_buy_returns_false_deadline_passed(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp._shell_price_and_buy_until_confirm(
            (200, 800), (540, 1600), time.time() - 1.0
        )
        assert result is False


# ---------------------------------------------------------------------------
# run_cold additional branches
# ---------------------------------------------------------------------------


class TestRunColdAdditional:
    def test_success_path(self):
        device = Mock()
        device.dump_hierarchy.return_value = "<node />"
        mock_el = Mock()
        mock_el.exists = True
        device.return_value = mock_el

        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp.run_cold(time.time())
        assert result is True

    def test_xml_dump_exception(self):
        device = Mock()
        device.dump_hierarchy.side_effect = Exception("connection lost")
        mock_el = Mock()
        mock_el.exists = False
        device.return_value = mock_el

        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp.run_cold(time.time() - _PIPELINE_DEADLINE_S + 0.1)
        assert result is None

    def test_returns_none_on_immediate_deadline(self):
        device = Mock()
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())

        result = fp.run_cold(time.time() - _PIPELINE_DEADLINE_S - 1.0)
        assert result is None

    def test_has_checkbox_returns_false_on_exception(self):
        device = Mock()
        device.side_effect = Exception("device error")
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        assert fp._has_checkbox() is False

    def test_has_sku_layout_returns_false_on_exception(self):
        device = Mock()
        device.side_effect = Exception("device error")
        config = Mock()
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        assert fp._has_sku_layout() is False


# ---------------------------------------------------------------------------
# rush_preselect_and_buy_via_xml
# ---------------------------------------------------------------------------


class TestRushPreselectAndBuyViaXml:
    def _make_pipeline_with_xml(self, xml_str, city=None, date=None):
        import xml.etree.ElementTree as ET

        device = Mock()
        device.shell = Mock()
        config = Mock()
        config.city = city
        config.date = date
        fp = FastPipeline(device, config, probe=False, guard=Mock())
        bot = Mock()
        bot._dismiss_fast_blocking_dialogs = Mock()
        xml_root = ET.fromstring(xml_str) if xml_str else None
        bot._dump_hierarchy_xml = Mock(return_value=xml_root)
        bot._extract_coords_from_xml_node = Mock(return_value=(540, 1700))
        fp.set_bot(bot)
        return fp, bot, device

    def test_returns_false_when_xml_is_none(self):
        fp, bot, device = self._make_pipeline_with_xml(None)
        bot._dump_hierarchy_xml.return_value = None
        result = fp.rush_preselect_and_buy_via_xml()
        assert result is False

    def test_returns_true_when_buy_button_found(self):
        xml = '<node resource-id="trade_project_detail_purchase_status_bar_container_fl" bounds="[0,0][1080,1920]" text="" />'
        fp, bot, device = self._make_pipeline_with_xml(xml)
        result = fp.rush_preselect_and_buy_via_xml()
        assert result is True
        assert "detail_buy" in fp._cached_coords

    def test_caches_city_coords(self):
        xml = '<root><node resource-id="" text="\u4e0a\u6d77" bounds="[0,0][100,100]" /><node resource-id="trade_project_detail_purchase_status_bar_container_fl" text="" bounds="[0,0][1080,1920]" /></root>'
        fp, bot, device = self._make_pipeline_with_xml(xml, city="\u4e0a\u6d77")
        fp.rush_preselect_and_buy_via_xml()
        assert "city" in fp._cached_coords

    def test_caches_date_coords(self):
        xml = '<root><node resource-id="" text="05.01" bounds="[0,0][100,100]" /><node resource-id="trade_project_detail_purchase_status_bar_container_fl" text="" bounds="[0,0][1080,1920]" /></root>'
        fp, bot, device = self._make_pipeline_with_xml(xml, date="05.01")
        fp.rush_preselect_and_buy_via_xml()
        assert "date" in fp._cached_coords

    def test_adds_to_no_match_when_city_not_found(self):
        xml = '<node resource-id="trade_project_detail_purchase_status_bar_container_fl" text="" bounds="[0,0][1080,1920]" />'
        fp, bot, device = self._make_pipeline_with_xml(xml, city="\u6df1\u5733")
        fp.rush_preselect_and_buy_via_xml()
        assert "city" in fp._cached_no_match

    def test_adds_to_no_match_when_date_not_found(self):
        xml = '<node resource-id="trade_project_detail_purchase_status_bar_container_fl" text="" bounds="[0,0][1080,1920]" />'
        fp, bot, device = self._make_pipeline_with_xml(xml, date="12.25")
        fp.rush_preselect_and_buy_via_xml()
        assert "date" in fp._cached_no_match

    def test_returns_false_when_no_buy_button(self):
        xml = (
            '<node resource-id="some_other_id" text="random" bounds="[0,0][100,100]" />'
        )
        fp, bot, device = self._make_pipeline_with_xml(xml)
        result = fp.rush_preselect_and_buy_via_xml()
        assert result is False
