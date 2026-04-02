# -*- coding: UTF-8 -*-
"""Unit tests for mobile/recovery.py — RecoveryHelper class."""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from mobile.recovery import RecoveryHelper, _MAX_BACK_STEPS, _BACK_DELAY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(page: str) -> dict:
    """Build a minimal probe result dict."""
    return {"page": page}


def _build_helper(probe_states):
    """Create a RecoveryHelper with mocked dependencies.

    Args:
        probe_states: A list of dicts that ``probe.probe_current_page`` will
            return on successive calls.

    Returns:
        Tuple of (helper, device_mock, probe_mock, navigator_mock).
    """
    device = MagicMock()
    probe = MagicMock()
    navigator = MagicMock()

    probe.probe_current_page.side_effect = list(probe_states)

    return RecoveryHelper(device, probe, navigator), device, probe, navigator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecoveryHelper:
    """Tests for RecoveryHelper.recover_to_detail_page."""

    @patch("mobile.recovery.time")
    def test_already_on_detail_page(self, mock_time):
        """If already on detail_page, return immediately — no back pressed."""
        helper, device, probe, navigator = _build_helper([
            _make_state("detail_page"),
        ])

        result = helper.recover_to_detail_page()

        assert result["page"] == "detail_page"
        device.press.assert_not_called()
        navigator.navigate_to_target_event.assert_not_called()
        # Only one probe call (the initial check).
        assert probe.probe_current_page.call_count == 1

    @patch("mobile.recovery.time")
    def test_already_on_sku_page(self, mock_time):
        """sku_page is also a valid target — return immediately."""
        helper, device, probe, navigator = _build_helper([
            _make_state("sku_page"),
        ])

        result = helper.recover_to_detail_page()

        assert result["page"] == "sku_page"
        device.press.assert_not_called()

    @patch("mobile.recovery.time")
    def test_one_back_reaches_detail(self, mock_time):
        """One back press reaches detail_page."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),   # initial check
            _make_state("detail_page"),  # after 1st back
        ])

        result = helper.recover_to_detail_page()

        assert result["page"] == "detail_page"
        device.press.assert_called_once_with("back")
        probe.invalidate_cache.assert_called_once()
        navigator.navigate_to_target_event.assert_not_called()

    @patch("mobile.recovery.time")
    def test_deep_back_five_steps(self, mock_time):
        """Five back presses before reaching detail_page."""
        helper, device, probe, navigator = _build_helper([
            _make_state("some_page"),    # initial
            _make_state("some_page"),    # back 1
            _make_state("some_page"),    # back 2
            _make_state("some_page"),    # back 3
            _make_state("some_page"),    # back 4
            _make_state("detail_page"),  # back 5
        ])

        result = helper.recover_to_detail_page()

        assert result["page"] == "detail_page"
        assert device.press.call_count == 5
        assert probe.invalidate_cache.call_count == 5
        navigator.navigate_to_target_event.assert_not_called()

    @patch("mobile.recovery.time")
    def test_homepage_triggers_forward_navigation(self, mock_time):
        """Hitting homepage during back loop triggers forward navigation."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),   # initial
            _make_state("some_page"),    # back 1
            _make_state("homepage"),     # back 2 → break to forward nav
            _make_state("detail_page"),  # after forward navigation probe
        ])

        result = helper.recover_to_detail_page()

        assert result["page"] == "detail_page"
        assert device.press.call_count == 2
        navigator.navigate_to_target_event.assert_called_once()

    @patch("mobile.recovery.time")
    def test_all_failed_returns_last_state(self, mock_time):
        """When all strategies fail, return last known state without crashing."""
        # Initial check + 8 backs (all unknown) + forward nav probe (still unknown)
        states = (
            [_make_state("unknown")]      # initial
            + [_make_state("unknown")] * _MAX_BACK_STEPS  # 8 backs
            + [_make_state("unknown")]    # after forward navigation
        )
        helper, device, probe, navigator = _build_helper(states)

        result = helper.recover_to_detail_page()

        assert result["page"] == "unknown"
        assert device.press.call_count == _MAX_BACK_STEPS
        navigator.navigate_to_target_event.assert_called_once()

    @patch("mobile.recovery.time")
    def test_back_delay_applied(self, mock_time):
        """Verify time.sleep is called with the correct delay after each back."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),
            _make_state("some_page"),
            _make_state("detail_page"),
        ])

        helper.recover_to_detail_page()

        assert mock_time.sleep.call_count == 2
        mock_time.sleep.assert_called_with(_BACK_DELAY)
