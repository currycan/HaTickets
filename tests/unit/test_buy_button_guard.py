# -*- coding: UTF-8 -*-
"""Tests for the BuyButtonGuard safety module."""

import time
from unittest.mock import Mock, PropertyMock, patch

import pytest

from mobile.buy_button_guard import BuyButtonGuard, SAFE_TEXTS, BLOCKED_TEXTS


@pytest.fixture
def mock_device():
    """Create a mock uiautomator2 device."""
    return Mock()


@pytest.fixture
def guard(mock_device):
    """Create a BuyButtonGuard with a mock device."""
    return BuyButtonGuard(mock_device)


# ── is_safe_to_click ──


class TestIsSafeToClick:
    """Tests for is_safe_to_click method."""

    @pytest.mark.parametrize("text", ["立即购买", "立即购票", "立即抢票", "选座购买"])
    def test_safe_texts_return_true(self, guard, text):
        assert guard.is_safe_to_click(text) is True

    def test_all_safe_texts_accepted(self, guard):
        for text in SAFE_TEXTS:
            assert guard.is_safe_to_click(text) is True, f"Expected True for '{text}'"

    @pytest.mark.parametrize("text", ["预约抢票", "预约", "即将开抢", "待开售"])
    def test_blocked_texts_return_false(self, guard, text):
        assert guard.is_safe_to_click(text) is False

    def test_all_blocked_texts_rejected(self, guard):
        for text in BLOCKED_TEXTS:
            assert guard.is_safe_to_click(text) is False, f"Expected False for '{text}'"

    def test_empty_string_returns_false(self, guard):
        assert guard.is_safe_to_click("") is False

    def test_none_returns_false(self, guard):
        assert guard.is_safe_to_click(None) is False

    def test_unknown_text_returns_false(self, guard):
        assert guard.is_safe_to_click("提交抢票预约") is False

    def test_critical_reservation_blocked(self, guard):
        """The MOST important safety property: 预约抢票 must be blocked."""
        assert guard.is_safe_to_click("预约抢票") is False

    def test_critical_purchase_allowed(self, guard):
        """The MOST important safety property: 立即购票 must be allowed."""
        assert guard.is_safe_to_click("立即购票") is True
        assert guard.is_safe_to_click("立即抢票") is True


# ── get_current_text ──


class TestGetCurrentText:
    def test_returns_text_when_button_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "立即购买"
        mock_device.return_value = mock_el
        assert guard.get_current_text() == "立即购买"

    def test_returns_none_when_button_not_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = False
        mock_device.return_value = mock_el
        assert guard.get_current_text() is None

    def test_returns_none_when_exception(self, guard, mock_device):
        mock_device.side_effect = Exception("device error")
        assert guard.get_current_text() is None


# ── wait_until_safe ──


class TestWaitUntilSafe:
    def test_immediately_safe(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "立即购买"
        mock_device.return_value = mock_el

        # Provide enough time.time() values for the single check
        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=1.0, poll_ms=50) is True

    def test_transitions_from_blocked_to_safe(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        # 3 blocked polls, then safe
        mock_el.get_text.side_effect = ["预约抢票", "预约抢票", "预约抢票", "立即购买"]
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            # time() calls: deadline calc, then check after each poll
            mock_time.time.side_effect = [0.0, 0.1, 0.2, 0.3, 0.4]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is True

    def test_timeout_with_blocked_text(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = True
        mock_el.get_text.return_value = "预约抢票"
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            # First call sets deadline, subsequent calls exceed it
            mock_time.time.side_effect = [0.0, 11.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is False

    def test_timeout_button_not_found(self, guard, mock_device):
        mock_el = Mock()
        mock_el.exists = False
        mock_device.return_value = mock_el

        with patch("mobile.buy_button_guard.time") as mock_time:
            mock_time.time.side_effect = [0.0, 11.0]
            mock_time.sleep = Mock()
            assert guard.wait_until_safe(timeout_s=10.0, poll_ms=50) is False
