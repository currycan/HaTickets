"""Unit tests for PriceSelector."""
from unittest.mock import MagicMock
import pytest
from mobile.price_selector import PriceSelector


class TestSelectByIndex:
    def test_clicks_correct_coordinates_via_bot(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = (100, 200)
        d = MagicMock()
        config = MagicMock()
        config.price_index = 2
        selector = PriceSelector(device=d, config=config, probe=MagicMock())
        selector.set_bot(bot)
        result = selector.select_by_index()
        assert result is True
        d.click.assert_called_once_with(100, 200)

    def test_returns_false_when_bot_returns_none(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = None
        selector = PriceSelector(device=MagicMock(), config=MagicMock(price_index=99), probe=MagicMock())
        selector.set_bot(bot)
        assert selector.select_by_index() is False

    def test_returns_false_when_no_bot(self):
        selector = PriceSelector(device=MagicMock(), config=MagicMock(price_index=0), probe=MagicMock())
        assert selector.select_by_index() is False


class TestGetBuyButtonCoords:
    def test_delegates_to_bot(self):
        bot = MagicMock()
        bot._get_buy_button_coordinates.return_value = (300, 400)
        selector = PriceSelector(device=MagicMock(), config=MagicMock(), probe=MagicMock())
        selector.set_bot(bot)
        assert selector.get_buy_button_coords() == (300, 400)

    def test_returns_none_when_no_bot(self):
        selector = PriceSelector(device=MagicMock(), config=MagicMock(), probe=MagicMock())
        assert selector.get_buy_button_coords() is None
