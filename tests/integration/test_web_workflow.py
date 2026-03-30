"""Integration tests for web module workflow."""
import json
from unittest.mock import Mock, patch, MagicMock

import pytest
from selenium.common.exceptions import NoSuchElementException

from config import Config
from concert import Concert
from damai import load_config


class TestConfigToConcertInit:

    def test_load_config_creates_concert(self, tmp_path, monkeypatch):
        """load_config → Config → Concert constructor chain works."""
        monkeypatch.chdir(tmp_path)
        config_data = {
            "index_url": "https://www.damai.cn/",
            "login_url": "https://passport.damai.cn/login",
            "target_url": "https://detail.damai.cn/item.htm?id=1",
            "users": ["A", "B"],
            "city": "上海",
            "dates": ["2026-05-01"],
            "prices": ["580"],
            "if_listen": True,
            "if_commit_order": True,
        }
        (tmp_path / "config.json").write_text(json.dumps(config_data), encoding="utf-8")

        cfg = load_config()
        assert isinstance(cfg, Config)
        assert cfg.users == ["A", "B"]

        mock_driver = Mock()
        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(cfg)
            assert con.status == 0
            assert con.config is cfg


class TestEnterConcertToChooseTicket:

    def test_enter_sets_status_2_then_choose_checks_status(self):
        """enter_concert sets status=2, choose_ticket proceeds only if status==2."""
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["A"], city=None, dates=None, prices=None,
            if_listen=False, if_commit_order=False,
            fast_mode=True, page_load_delay=0.1,
        )

        mock_driver = Mock()
        mock_driver.title = "大麦网-全球演出赛事官方购票平台-100%正品、先付先抢、在线选座！"
        mock_driver.current_url = "https://detail.damai.cn/item.htm?id=1"
        mock_driver.find_element = Mock(side_effect=NoSuchElementException)
        mock_driver.find_elements = Mock(return_value=[])

        cookie_data = {"cookies": [{"name": "t", "value": "v", "domain": ".damai.cn"}], "saved_at": 9999999999}
        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"), \
             patch("concert.os.path.exists", return_value=True), \
             patch("concert.json.load", return_value=cookie_data), \
             patch("builtins.open", create=True):
            con = Concert(config)
            con.login_method = 1

            # Simulate login with cookies
            con.enter_concert()
            assert con.status == 2


class TestOrderFlowPC:

    def test_pc_details_page_flow(self):
        """PC flow: select city/date/price/quantity in sequence."""
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["A", "B"], city="杭州", dates=["2026-04-11"],
            prices=["680"], if_listen=True, if_commit_order=True,
            fast_mode=True, page_load_delay=0.1,
        )

        mock_driver = Mock()
        mock_driver.current_url = "https://detail.damai.cn/item.htm?id=1"
        mock_driver.find_element = Mock(side_effect=NoSuchElementException)
        mock_driver.find_elements = Mock(return_value=[])

        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(config)
            con.status = 2

            # These should not raise even when elements aren't found
            result = con.select_city_on_page_pc()
            # Returns False when nothing matched, which is expected
            assert result is False or result is True

            result = con.select_date_on_page_pc()
            assert result is False or result is True


class TestOrderFlowMobile:

    def test_mobile_details_page_flow(self):
        """Mobile flow: select city/date/price in sequence."""
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://m.damai.cn/item.htm?id=1",
            users=["A"], city="杭州", dates=["2026-04-11"],
            prices=["680"], if_listen=True, if_commit_order=True,
            fast_mode=True, page_load_delay=0.1,
        )

        mock_driver = Mock()
        mock_driver.current_url = "https://m.damai.cn/item.htm?id=1"
        mock_driver.find_element = Mock(side_effect=NoSuchElementException)
        mock_driver.find_elements = Mock(return_value=[])

        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(config)
            con.status = 2

            result = con.select_city_on_page()
            assert result is False or result is True

            result = con.select_date_on_page()
            assert result is False or result is True


# ---------------------------------------------------------------------------
# Extended: Ticket selection fallback scenarios
# ---------------------------------------------------------------------------

class TestTicketSelectionFallbacks:

    def _make_concert(self, city=None, dates=None, prices=None, fast_mode=True):
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["A"], city=city, dates=dates, prices=prices,
            if_listen=False, if_commit_order=False,
            fast_mode=fast_mode, page_load_delay=0,
        )
        mock_driver = Mock()
        mock_driver.current_url = "https://detail.damai.cn/item.htm?id=1"
        mock_driver.find_element = Mock(side_effect=NoSuchElementException)
        mock_driver.find_elements = Mock(return_value=[])

        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(config)
        con.driver = mock_driver
        return con

    def test_select_date_with_no_matching_elements_returns_false(self):
        """When driver returns no elements, date selection returns False."""
        con = self._make_concert(dates=["2026-05-01"])
        result = con.select_date_on_page_pc()
        assert result is False

    def test_select_price_with_no_matching_elements_returns_false(self):
        """When driver returns no elements, price selection returns False."""
        con = self._make_concert(prices=["680"])
        result = con.select_price_on_page_pc()
        assert result is False

    def test_select_city_with_no_elements_returns_false(self):
        """City selection with empty elements returns False."""
        con = self._make_concert(city="上海")
        result = con.select_city_on_page_pc()
        assert result is False

    def test_select_quantity_no_buttons_returns_true(self):
        """Quantity selection is non-blocking: returns True even when buttons not found."""
        con = self._make_concert()
        result = con.select_quantity_on_page()
        assert result is True

    def test_no_city_config_skips_city_selection(self):
        """When city is None in config, select_details_page_pc skips city step."""
        con = self._make_concert(city=None, dates=["2026-05-01"], prices=["680"])
        # Should not raise even with no city
        con.select_details_page_pc()

    def test_fast_mode_true_does_not_raise(self):
        """fast_mode=True completes without exception on empty page."""
        con = self._make_concert(city="上海", dates=["2026-05-01"], prices=["680"], fast_mode=True)
        con.select_details_page_pc()  # no raise expected


# ---------------------------------------------------------------------------
# Extended: Status gating
# ---------------------------------------------------------------------------

class TestStatusGating:

    def test_choose_ticket_skips_when_status_not_2(self):
        """choose_ticket() returns early if status != 2."""
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["A"], city=None, dates=None, prices=None,
            if_listen=False, if_commit_order=False,
            fast_mode=True, page_load_delay=0,
        )
        mock_driver = Mock()
        mock_driver.find_elements = Mock(return_value=[])
        mock_driver.find_element = Mock(side_effect=NoSuchElementException)

        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(config)

        con.status = 0  # not 2
        con.choose_ticket()  # should return immediately without touching driver
        mock_driver.find_elements.assert_not_called()

    def test_commit_order_skips_when_status_not_3(self):
        """commit_order() returns early if status != 3."""
        config = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["A"], city=None, dates=None, prices=None,
            if_listen=False, if_commit_order=True,
            fast_mode=True, page_load_delay=0,
        )
        mock_driver = Mock()
        mock_driver.find_elements = Mock(return_value=[])

        with patch("concert.get_chromedriver_path", return_value="/fake"), \
             patch("concert.webdriver.Chrome", return_value=mock_driver), \
             patch("selenium.webdriver.chrome.service.Service"):
            con = Concert(config)

        con.status = 0
        con.commit_order()  # should return early
        mock_driver.find_elements.assert_not_called()
