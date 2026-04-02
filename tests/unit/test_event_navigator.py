"""Unit tests for EventNavigator."""
from unittest.mock import MagicMock
import pytest
from mobile.event_navigator import EventNavigator


class TestNavigateToTarget:
    def test_already_on_detail_page_returns_true(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "detail_page"}
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        assert nav.navigate_to_target_event() is True

    def test_auto_navigate_disabled_returns_false(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=False), probe=probe)
        assert nav.navigate_to_target_event() is False

    def test_delegates_to_bot(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = True
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        bot._navigate_to_target_impl.assert_called_once()
        assert result is True

    def test_delegates_to_bot_returns_false_on_failure(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = False
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        assert result is False

    def test_delegates_to_bot_catches_exception(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.side_effect = RuntimeError("device disconnected")
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        assert result is False

    def test_no_bot_returns_false(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        assert nav.navigate_to_target_event() is False

    def test_passes_initial_probe_to_bot(self):
        probe = MagicMock()
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = True
        nav = EventNavigator(device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe)
        nav.set_bot(bot)
        initial = {"state": "search_page"}
        nav.navigate_to_target_event(initial_probe=initial)
        bot._navigate_to_target_impl.assert_called_once_with(initial_probe=initial)
