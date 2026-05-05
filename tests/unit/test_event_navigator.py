"""Unit tests for EventNavigator."""

from unittest.mock import MagicMock


import pytest

from mobile.event_navigator import (
    EventNavigator,
    SessionNotFoundError,
    _enumerate_sessions_from_xml,
    select_session,
)


class TestKeywordTokens:
    def _nav(self, keyword):
        config = MagicMock()
        config.keyword = keyword
        return EventNavigator(device=MagicMock(), config=config, probe=MagicMock())

    def test_splits_by_space(self):
        tokens = self._nav("张杰 演唱会")._keyword_tokens()
        assert tokens == ["张杰", "演唱会"]

    def test_splits_by_comma(self):
        tokens = self._nav("张杰,演唱会")._keyword_tokens()
        assert tokens == ["张杰", "演唱会"]

    def test_filters_short_tokens(self):
        tokens = self._nav("张杰 A 演唱会")._keyword_tokens()
        assert "A" not in tokens
        assert "张杰" in tokens

    def test_empty_keyword(self):
        assert self._nav("")._keyword_tokens() == []

    def test_none_keyword(self):
        assert self._nav(None)._keyword_tokens() == []

    def test_deduplicates(self):
        tokens = self._nav("张杰 张杰 演唱会")._keyword_tokens()
        assert tokens.count("张杰") == 1


class TestTitleMatchesTarget:
    def test_matches_item_detail_name(self):
        bot = MagicMock()
        bot.item_detail = MagicMock()
        bot.item_detail.item_name = "张杰未·LIVE巡回演唱会"
        bot.item_detail.item_name_display = "张杰未·LIVE"
        bot._keyword_tokens.return_value = []
        config = MagicMock()
        config.target_title = None
        config.keyword = None
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._title_matches_target("张杰未·LIVE巡回演唱会") is True

    def test_no_match_returns_false(self):
        bot = MagicMock()
        bot.item_detail = None
        bot._keyword_tokens.return_value = []
        config = MagicMock()
        config.target_title = "张杰"
        config.keyword = None
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._title_matches_target("周杰伦演唱会") is False

    def test_empty_title_returns_false(self):
        bot = MagicMock()
        bot.item_detail = None
        bot._keyword_tokens.return_value = []
        config = MagicMock()
        config.target_title = "张杰"
        config.keyword = None
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._title_matches_target("") is False

    def test_keyword_tokens_match(self):
        bot = MagicMock()
        bot.item_detail = None
        bot._keyword_tokens.return_value = ["张杰", "演唱会"]
        config = MagicMock()
        config.target_title = None
        config.keyword = "张杰 演唱会"
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._title_matches_target("张杰2026巡回演唱会北京站") is True


class TestCurrentPageMatchesTarget:
    def test_wrong_state_returns_false(self):
        bot = MagicMock()
        config = MagicMock()
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._current_page_matches_target({"state": "homepage"}) is False

    def test_no_target_info_returns_true(self):
        bot = MagicMock()
        bot.item_detail = None
        config = MagicMock()
        config.target_title = None
        config.keyword = None
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._current_page_matches_target({"state": "detail_page"}) is True

    def test_delegates_to_title_match(self):
        bot = MagicMock()
        bot.item_detail = MagicMock()
        bot._get_detail_title_text.return_value = "张杰演唱会"
        bot._title_matches_target.return_value = True
        config = MagicMock()
        config.target_title = "张杰"
        config.keyword = None
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)
        assert nav._current_page_matches_target({"state": "sku_page"}) is True


class TestNavigateToTarget:
    def test_already_on_detail_page_returns_true(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "detail_page"}
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        assert nav.navigate_to_target_event() is True

    def test_auto_navigate_disabled_returns_false(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=False), probe=probe
        )
        assert nav.navigate_to_target_event() is False

    def test_delegates_to_bot(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = True
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        bot._navigate_to_target_impl.assert_called_once()
        assert result is True

    def test_delegates_to_bot_returns_false_on_failure(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = False
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        assert result is False

    def test_delegates_to_bot_catches_exception(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        bot = MagicMock()
        bot._navigate_to_target_impl.side_effect = RuntimeError("device disconnected")
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        nav.set_bot(bot)
        result = nav.navigate_to_target_event()
        assert result is False

    def test_no_bot_returns_false(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        assert nav.navigate_to_target_event() is False

    def test_passes_initial_probe_to_bot(self):
        probe = MagicMock()
        bot = MagicMock()
        bot._navigate_to_target_impl.return_value = True
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        nav.set_bot(bot)
        initial = {"state": "search_page"}
        nav.navigate_to_target_event(initial_probe=initial)
        bot._navigate_to_target_impl.assert_called_once_with(initial_probe=initial)


# ---------------------------------------------------------------------------
# select_session (P1 #25)
# ---------------------------------------------------------------------------


def _hierarchy_xml(*sessions):
    """Build a minimal hierarchy XML containing N session cards.

    Each ``sessions`` entry is a tuple ``(date, city, bounds)``.
    """
    cards_xml = ""
    for date_text, city_text, bounds in sessions:
        cards_xml += f'''
            <node clickable="true" bounds="{bounds}">
              <node resource-id="cn.damai:id/tv_date" text="{date_text}" bounds="{bounds}"/>
              <node resource-id="cn.damai:id/tv_venue" text="{city_text}" bounds="{bounds}"/>
            </node>'''
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <hierarchy>
      <node resource-id="cn.damai:id/sku_panel_dates" bounds="[0,0][1080,400]">
        {cards_xml}
      </node>
    </hierarchy>"""


class TestEnumerateSessionsFromXml:
    def test_returns_empty_when_panel_missing(self):
        xml = "<hierarchy><node/></hierarchy>"
        assert _enumerate_sessions_from_xml(xml) == []

    def test_handles_malformed_xml(self):
        assert _enumerate_sessions_from_xml("<not closed") == []

    def test_collects_each_card_once(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        cards = _enumerate_sessions_from_xml(xml)
        assert len(cards) == 2
        assert cards[0]["date"] == "04.06"
        assert "上海" in cards[0]["text"]
        assert cards[1]["date"] == "04.13"


class TestSelectSession:
    def _make_driver(self, xml):
        driver = MagicMock()
        driver.dump_hierarchy.return_value = xml
        return driver

    def test_raises_when_panel_missing(self):
        driver = self._make_driver("<hierarchy></hierarchy>")
        with pytest.raises(SessionNotFoundError, match="未发现可选场次"):
            select_session(driver, date="04.06")

    def test_unique_date_match_clicks_card(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.06")
        assert idx == 0
        # Center of [0,0][540,200] = (270, 100)
        driver.click.assert_called_once_with(270, 100)

    def test_date_normalisation_handles_chinese_input(self):
        xml = _hierarchy_xml(
            ("04月06日", "上海", "[0,0][540,200]"),
        )
        driver = self._make_driver(xml)
        # User passed normalised "04.06" → matches "04月06日" via normalize_date
        assert select_session(driver, date="04.06") == 0

    def test_date_plus_city_disambiguates_duplicates(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.06", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.06", city="北京")
        assert idx == 1
        driver.click.assert_called_once_with(810, 100)

    def test_date_alone_ambiguous_raises(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.06", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        with pytest.raises(SessionNotFoundError, match="命中 2 条"):
            select_session(driver, date="04.06")

    def test_fallback_index_when_no_date(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, fallback_index=1)
        assert idx == 1

    def test_fallback_index_out_of_range_raises(self):
        xml = _hierarchy_xml(("04.06", "上海", "[0,0][540,200]"))
        driver = self._make_driver(xml)
        with pytest.raises(SessionNotFoundError, match="越界"):
            select_session(driver, fallback_index=5)

    def test_no_hints_raises(self):
        xml = _hierarchy_xml(("04.06", "上海", "[0,0][540,200]"))
        driver = self._make_driver(xml)
        with pytest.raises(SessionNotFoundError, match="未提供"):
            select_session(driver)

    def test_dump_hierarchy_failure_propagates_as_session_error(self):
        driver = MagicMock()
        driver.dump_hierarchy.side_effect = RuntimeError("device offline")
        with pytest.raises(SessionNotFoundError, match="dump_hierarchy 失败"):
            select_session(driver, date="04.06")

    def test_click_failure_wraps_as_session_error(self):
        xml = _hierarchy_xml(("04.06", "上海", "[0,0][540,200]"))
        driver = self._make_driver(xml)
        driver.click.side_effect = RuntimeError("ADB closed")
        with pytest.raises(SessionNotFoundError, match="点击场次卡片失败"):
            select_session(driver, date="04.06")

    def test_date_city_no_match_falls_through_to_date_only(self):
        """When date+city specified but city absent, fall back to date-only.

        This guards against users who specify a city that the venue copy
        does not include verbatim (e.g. "上海" vs "上海徐汇")."""
        xml = _hierarchy_xml(("04.06", "上海体育馆", "[0,0][540,200]"))
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.06", city="北京")
        assert idx == 0  # date-only single match wins after city miss

    def test_date_city_priority_over_fallback_index(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.13", city="北京", fallback_index=0)
        assert idx == 1  # date+city wins, fallback_index ignored
