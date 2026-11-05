"""Unit tests for EventNavigator."""

from unittest.mock import MagicMock, patch


import pytest

from mobile.event_navigator import (
    EventNavigator,
    HomeNotReadyError,
    SearchAmbiguousError,
    SearchEmptyError,
    SessionNotFoundError,
    _enumerate_sessions_from_xml,
    select_search_result,
    select_session,
    wait_for_home_ready,
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

    def test_reuses_observed_title_from_page_snapshot(self):
        bot = MagicMock()
        bot.item_detail = None
        bot._title_matches_target.return_value = True
        config = MagicMock()
        config.target_title = "张杰演唱会"
        config.target_venue = None
        config.keyword = None
        config.if_commit_order = False
        nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
        nav.set_bot(bot)

        assert nav._current_page_matches_target(
            {
                "state": "detail_page",
                "observed_title": "张杰演唱会",
                "observed_venue": "",
            }
        ) is True
        bot._get_detail_title_text.assert_not_called()


class TestNavigateToTarget:
    def test_already_on_detail_page_returns_true(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "detail_page"}
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        assert nav.navigate_to_target_event() is True

    def test_wrong_detail_page_delegates_to_bot_navigation(self):
        probe = MagicMock()
        current = {"state": "detail_page"}
        bot = MagicMock()
        bot._current_page_matches_target.return_value = False
        bot._navigate_to_target_impl.return_value = True
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=True), probe=probe
        )
        nav.set_bot(bot)

        assert nav.navigate_to_target_event(initial_probe=current) is True
        bot._navigate_to_target_impl.assert_called_once_with(initial_probe=current)

    def test_auto_navigate_disabled_returns_false(self):
        probe = MagicMock()
        probe.probe_current_page.return_value = {"state": "homepage"}
        nav = EventNavigator(
            device=MagicMock(), config=MagicMock(auto_navigate=False), probe=probe
        )
        assert nav.navigate_to_target_event() is False

    def test_probe_only_never_delegates_to_navigation(self):
        probe = MagicMock()
        bot = MagicMock()
        nav = EventNavigator(
            device=MagicMock(),
            config=MagicMock(auto_navigate=True, probe_only=True),
            probe=probe,
        )
        nav.set_bot(bot)

        assert nav.navigate_to_target_event({"state": "homepage"}) is False
        probe.probe_current_page.assert_not_called()
        bot._navigate_to_target_impl.assert_not_called()

    def test_probe_only_discovery_dispatches_no_navigation_actions(self):
        probe = MagicMock()
        bot = MagicMock()
        nav = EventNavigator(
            device=MagicMock(),
            config=MagicMock(auto_navigate=True, probe_only=True),
            probe=probe,
        )
        nav.set_bot(bot)

        assert nav.discover_target_event(["目标演出"]) is None
        bot.probe_current_page.assert_not_called()
        bot._recover_to_navigation_start.assert_not_called()
        bot._open_search_from_homepage.assert_not_called()
        bot._submit_search_keyword.assert_not_called()
        bot._open_target_from_search_results.assert_not_called()

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

    def test_strict_date_city_mismatch_never_falls_back(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)

        with pytest.raises(SessionNotFoundError, match="正式模式无法唯一确认"):
            select_session(
                driver,
                date="04.06",
                city="北京",
                fallback_index=0,
                strict=True,
            )

        driver.click.assert_not_called()

    def test_strict_mode_requires_configured_session_criteria(self):
        driver = self._make_driver(
            _hierarchy_xml(("04.06", "上海", "[0,0][540,200]"))
        )

        with pytest.raises(SessionNotFoundError, match="未配置 date/city"):
            select_session(driver, fallback_index=0, strict=True)

        driver.click.assert_not_called()

    def test_date_city_priority_over_fallback_index(self):
        xml = _hierarchy_xml(
            ("04.06", "上海", "[0,0][540,200]"),
            ("04.13", "北京", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.13", city="北京", fallback_index=0)
        assert idx == 1  # date+city wins, fallback_index ignored


# ---------------------------------------------------------------------------
# wait_for_home_ready (P2 #28)
# ---------------------------------------------------------------------------


class TestWaitForHomeReady:
    def test_returns_immediately_when_homepage(self):
        driver = MagicMock()
        probe = MagicMock()
        probe.classify.return_value = {"state": "homepage"}
        result = wait_for_home_ready(driver, probe, timeout=0.5, poll_interval=0.05)
        assert result["state"] == "homepage"
        probe.invalidate_cache.assert_called()

    def test_polls_until_homepage_appears(self):
        driver = MagicMock()
        probe = MagicMock()
        probe.classify.side_effect = [
            {"state": "unknown"},
            {"state": "unknown"},
            {"state": "homepage"},
        ]
        result = wait_for_home_ready(driver, probe, timeout=2.0, poll_interval=0.01)
        assert result["state"] == "homepage"
        assert probe.classify.call_count == 3

    def test_wall_clock_jump_does_not_expire_home_poll(self):
        driver = MagicMock()
        probe = MagicMock()
        probe.classify.side_effect = [
            {"state": "unknown"},
            {"state": "homepage"},
        ]
        wall_time = MagicMock()
        wall_time.time.side_effect = [0.0, 10**12]

        with patch(
            "mobile.page_helpers._monotonic",
            side_effect=[10.0, 10.0],
        ), patch(
            "mobile.page_helpers.time",
            wall_time,
        ):
            result = wait_for_home_ready(
                driver,
                probe,
                timeout=1.0,
                poll_interval=0.01,
            )

        assert result["state"] == "homepage"
        wall_time.time.assert_not_called()

    def test_home_page_probe_timeout(self, tmp_path):
        driver = MagicMock()
        driver.dump_hierarchy.return_value = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<hierarchy>"
            '<node text="登录" resource-id="cn.damai:id/login_btn"/>'
            "</hierarchy>"
        )
        probe = MagicMock()
        probe.classify.return_value = {"state": "unknown"}

        with pytest.raises(HomeNotReadyError, match="首页未就绪"):
            wait_for_home_ready(
                driver,
                probe,
                timeout=0.1,
                poll_interval=0.02,
                dump_dir=str(tmp_path),
            )

        dumps = list(tmp_path.glob("home_probe_*.xml"))
        assert len(dumps) == 1, f"expected single dump, got {dumps}"
        content = dumps[0].read_text(encoding="utf-8")
        assert "登录" in content
        assert "cn.damai:id/login_btn" in content


# ---------------------------------------------------------------------------
# select_search_result (P2 #23)
# ---------------------------------------------------------------------------


def _search_result_xml(*items):
    """Build a hierarchy with N ``ll_search_item`` cards.

    Each ``items`` entry is a tuple ``(title, bounds)``.
    """
    cards = ""
    for title, bounds in items:
        cards += (
            f'<node resource-id="cn.damai:id/ll_search_item" '
            f'clickable="true" bounds="{bounds}">'
            f'<node resource-id="cn.damai:id/tv_project_name" text="{title}"/>'
            f"</node>"
        )
    return f'<?xml version="1.0" encoding="UTF-8"?><hierarchy>{cards}</hierarchy>'


class TestSelectSearchResult:
    def _make_driver(self, xml):
        driver = MagicMock()
        driver.dump_hierarchy.return_value = xml
        return driver

    def test_search_result_zero_results_logs_keyword(self, tmp_path):
        driver = self._make_driver('<?xml version="1.0" encoding="UTF-8"?><hierarchy/>')
        with pytest.raises(SearchEmptyError) as exc_info:
            select_search_result(
                driver,
                keyword="周杰伦演唱会",
                timeout=0.1,
                poll_interval=0.02,
                dump_dir=str(tmp_path),
            )
        # keyword surfaced in exception message for debugging
        assert "周杰伦演唱会" in str(exc_info.value)
        assert "搜索结果为空" in str(exc_info.value)
        # dump persisted
        dumps = list(tmp_path.glob("search_probe_*.xml"))
        assert len(dumps) == 1

    def test_search_result_single_auto_select(self, tmp_path):
        xml = _search_result_xml(("周杰伦演唱会上海站", "[0,0][1080,400]"))
        driver = self._make_driver(xml)
        chosen = select_search_result(
            driver,
            keyword="周杰伦",
            timeout=0.5,
            poll_interval=0.02,
            dump_dir=str(tmp_path),
        )
        assert chosen["title"] == "周杰伦演唱会上海站"
        # Center of [0,0][1080,400] = (540, 200)
        driver.click.assert_called_once_with(540, 200)

    def test_wall_clock_jump_does_not_expire_search_poll(self, tmp_path):
        empty_xml = '<?xml version="1.0" encoding="UTF-8"?><hierarchy/>'
        result_xml = _search_result_xml(("目标演出", "[0,0][1080,400]"))
        driver = MagicMock()
        driver.dump_hierarchy.side_effect = [empty_xml, result_xml]
        wall_time = MagicMock()
        wall_time.time.side_effect = [10**12, 0.0]

        with patch(
            "mobile.page_helpers._monotonic",
            side_effect=[20.0, 20.0],
        ), patch(
            "mobile.page_helpers.time",
            wall_time,
        ):
            chosen = select_search_result(
                driver,
                keyword="目标",
                timeout=1.0,
                poll_interval=0.01,
                dump_dir=str(tmp_path),
            )

        assert chosen["title"] == "目标演出"
        wall_time.time.assert_not_called()

    def test_search_result_ambiguous_falls_back(self, tmp_path):
        xml = _search_result_xml(
            ("无关演出A", "[0,0][1080,200]"),
            ("无关演出B", "[0,200][1080,400]"),
        )
        driver = self._make_driver(xml)
        chosen = select_search_result(
            driver,
            keyword="周杰伦",
            target_title="周杰伦",
            timeout=0.2,
            poll_interval=0.02,
            dump_dir=str(tmp_path),
        )
        # Fuzzy match fails → fallback to first card (non-strict default)
        assert chosen["title"] == "无关演出A"
        assert chosen["index"] == 0
        # Center of [0,0][1080,200] = (540, 100)
        driver.click.assert_called_once_with(540, 100)

    def test_search_result_strict_ambiguous_raises(self, tmp_path):
        xml = _search_result_xml(
            ("无关A", "[0,0][1080,200]"),
            ("无关B", "[0,200][1080,400]"),
        )
        driver = self._make_driver(xml)
        with pytest.raises(SearchAmbiguousError, match="歧义"):
            select_search_result(
                driver,
                keyword="周杰伦",
                target_title="周杰伦",
                strict=True,
                timeout=0.2,
                poll_interval=0.02,
                dump_dir=str(tmp_path),
            )
        dumps = list(tmp_path.glob("search_probe_*.xml"))
        assert len(dumps) == 1

    def test_search_result_target_title_fuzzy_matches_second(self, tmp_path):
        xml = _search_result_xml(
            ("无关演出", "[0,0][1080,200]"),
            ("周杰伦2026演唱会", "[0,200][1080,400]"),
        )
        driver = self._make_driver(xml)
        chosen = select_search_result(
            driver,
            keyword="周杰伦",
            target_title="周杰伦",
            timeout=0.2,
            poll_interval=0.02,
            dump_dir=str(tmp_path),
        )
        assert chosen["title"] == "周杰伦2026演唱会"
        driver.click.assert_called_once_with(540, 300)


# ---------------------------------------------------------------------------
# select_session — qa W3-03 边界增补 (Task C1)
# ---------------------------------------------------------------------------


class TestSelectSessionBoundaryCases:
    """W3-03 qa-added 边界用例：补 fix-plan p1-25 未显式覆盖的路径。"""

    def _make_driver(self, xml):
        driver = MagicMock()
        driver.dump_hierarchy.return_value = xml
        return driver

    def test_select_session_returns_first_match_when_city_omitted(self):
        """提供 date 且 city 显式为 None：date 唯一命中应返回该卡片 idx，
        即使该卡片不是面板第 0 项。

        与 ``test_unique_date_match_clicks_card`` 区别：这里 city=None 显式传入，
        且匹配命中第 1 项（非 0 项），保证 ``date-only`` 单匹配路径独立可达。
        """
        xml = _hierarchy_xml(
            ("04.01", "北京", "[0,0][540,200]"),
            ("04.13", "上海", "[540,0][1080,200]"),
            ("04.27", "广州", "[0,200][540,400]"),
        )
        driver = self._make_driver(xml)
        idx = select_session(driver, date="04.13", city=None)
        assert idx == 1
        # Center of [540,0][1080,200] = (810, 100)
        driver.click.assert_called_once_with(810, 100)

    def test_select_session_falls_back_to_index_when_no_match(self):
        """提供 date + city 都未命中任何卡片，但同时提供了 fallback_index：
        应进入 fallback_index 路径选中对应卡片，而不是抛 SessionNotFoundError。

        与 ``test_fallback_index_when_no_date`` 区别：这里 date 是有提供的，
        但 cards 中根本没有该 date — 验证 date+city → date-only → fallback_index
        三段优先级链中第三段真的兜底。
        """
        xml = _hierarchy_xml(
            ("04.05", "北京", "[0,0][540,200]"),
            ("04.13", "上海", "[540,0][1080,200]"),
        )
        driver = self._make_driver(xml)
        # date "12.31" 未在卡片中；city "深圳" 也未匹配；fallback_index=1 兜底
        idx = select_session(driver, date="12.31", city="深圳", fallback_index=1)
        assert idx == 1
        # Center of [540,0][1080,200] = (810, 100)
        driver.click.assert_called_once_with(810, 100)


# ---------------------------------------------------------------------------
# issue #51+#50：标题模糊匹配 / 城市冲突 veto / 打分增强
# ---------------------------------------------------------------------------


class _NavBotShim:
    """把 bot 委托回环转发到 EventNavigator 本体的最小替身。

    与生产链路一致：navigator 内部通过 ``bot._keyword_tokens()`` /
    ``bot._title_matches_target()`` 回环调用（delegators 门面行为）。
    """

    def __init__(self, nav, detail_title=None, detail_venue=""):
        self._nav = nav
        self.item_detail = None
        self._detail_title = detail_title
        self._detail_venue = detail_venue
        self._verified_price_selection = None
        self.hot_cache_invalidations = []

    def _keyword_tokens(self):
        return self._nav._keyword_tokens()

    def _title_matches_target(self, title_text):
        return self._nav._title_matches_target(title_text)

    def _get_detail_title_text(self):
        return self._detail_title

    def _get_detail_venue_text(self):
        return self._detail_venue

    def _invalidate_all_hot_cache(self, reason=None):
        self.hot_cache_invalidations.append(reason)


def _make_fuzzy_nav(
    keyword=None,
    city=None,
    target_title=None,
    target_venue=None,
    detail_title=None,
    if_commit_order=False,
):
    config = MagicMock()
    config.keyword = keyword
    config.city = city
    config.target_title = target_title
    config.target_venue = target_venue
    config.if_commit_order = if_commit_order
    nav = EventNavigator(device=MagicMock(), config=config, probe=MagicMock())
    nav.set_bot(_NavBotShim(nav, detail_title=detail_title))
    return nav


class TestTitleMatchesTargetFuzzy:
    """issue #51 回归：词序/前缀/截断变体经模糊回退通过；无关演出仍拒绝。"""

    def _nav(self, city=None):
        return _make_fuzzy_nav(keyword="嘉年华2026周杰伦 演唱会", city=city)

    def test_title_matches_target_fuzzy_word_order(self):
        # 修复前 False：词序颠倒（相似度 0.870 >= 0.75）
        assert (
            self._nav()._title_matches_target("周杰伦嘉年华2026演唱会（北京站）")
            is True
        )

    def test_title_matches_target_official_word_order(self):
        # 修复前 False：官方全称词序变体（相似度 0.823）
        assert (
            self._nav()._title_matches_target(
                "2026周杰伦嘉年华世界巡回演唱会-北京站"
            )
            is True
        )

    def test_title_matches_target_truncated_ellipsis(self):
        # 修复前 False：UI 省略号截断（相似度 0.854）
        assert (
            self._nav()._title_matches_target("龙拳·北京 嘉年华2026周杰伦演唱…")
            is True
        )

    def test_title_matches_target_unrelated_false(self):
        # 防放松过度：无关演出（相似度 0.272）仍为 False
        assert (
            self._nav()._title_matches_target("张学友60+巡回演唱会北京站") is False
        )

    def test_title_matches_target_city_conflict_veto(self):
        # issue #50 反向风险收紧：目标广州、标题北京站——修复前全 token
        # 命中放行（True），修复后城市冲突 veto 直接拒绝
        nav = _make_fuzzy_nav(keyword="凤凰传奇 演唱会", city="广州")
        assert (
            nav._title_matches_target("凤凰传奇2026吉祥如意巡回演唱会——北京站")
            is False
        )

    def test_title_matches_target_target_city_in_title_not_vetoed(self):
        nav = _make_fuzzy_nav(keyword="凤凰传奇 演唱会", city="广州")
        assert (
            nav._title_matches_target("凤凰传奇2026吉祥如意巡回演唱会——广州站")
            is True
        )

    def test_title_matches_target_non_string_city_skips_veto(self):
        # MagicMock config.city（非 str）不触发 veto——既有 MagicMock 用例不受影响
        nav = _make_fuzzy_nav(keyword="张杰 演唱会", city=MagicMock())
        assert nav._title_matches_target("张杰2026巡回演唱会北京站") is True


class TestCurrentPageMatchesClickedTitle:
    """issue #51 回归：详情页短标题/截断用「刚点击的卡片标题」锚定校验。"""

    def test_current_page_matches_clicked_title_short_detail(self):
        nav = _make_fuzzy_nav(
            keyword="嘉年华2026周杰伦 演唱会",
            detail_title="龙拳·北京 嘉年华",
        )
        assert (
            nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="龙拳·北京 嘉年华2026周杰伦演唱会",
            )
            is True
        )

    def test_current_page_matches_clicked_title_fuzzy(self):
        # 详情页与卡片文案词序不同：靠 title_similarity >= 0.75 锚定通过
        nav = _make_fuzzy_nav(
            keyword="周杰伦 演唱会",
            detail_title="周杰伦嘉年华2026演唱会（北京站）",
        )
        assert (
            nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="嘉年华2026周杰伦演唱会",
            )
            is True
        )

    def test_current_page_empty_title_extended_poll_then_reads(self):
        # 2026-07-11 真机回归：标题异步慢渲染，延长轮询后读到 → 正常锚定通过
        nav = _make_fuzzy_nav(keyword="大鱼海棠 十周年", detail_title="")
        bot = nav._bot
        calls = {"n": 0}

        def fake_title():
            calls["n"] += 1
            return (
                "" if calls["n"] < 4 else "上海·2026《大鱼海棠·十周年重逢之夜》特别呈现"
            )

        bot._get_detail_title_text = fake_title
        with patch("mobile.event_navigator.time.sleep"):
            result = nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="上海•2026《大鱼海棠·十周年重逢之夜》特别呈现",
            )
        assert result is True
        assert calls["n"] == 4  # 首读空 + 轮询第 3 次读到

    def test_current_page_empty_title_venue_fallback(self):
        # 标题始终读空，但场馆可读且与 target_venue 一致 → venue 兜底通过
        nav = _make_fuzzy_nav(
            keyword="大鱼海棠 十周年", target_venue="虹口足球场", detail_title=""
        )
        nav._bot._detail_venue = "虹口足球场"
        with patch("mobile.event_navigator.time.sleep") as mock_sleep:
            result = nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="上海•2026《大鱼海棠·十周年重逢之夜》特别呈现",
            )
        assert result is True
        assert mock_sleep.call_count == 6  # 延长轮询全程走完

    def test_current_page_empty_title_unverified_accept(self):
        # 标题与场馆均不可读：「读不到」≠「不一致」，信任高分锚定卡片放行,
        # 非正式模式避免正确条目被加入 rejected_titles 黑名单造成死循环。
        nav = _make_fuzzy_nav(keyword="张杰 演唱会", detail_title="")
        calls = {"n": 0}

        def fake_title():
            calls["n"] += 1
            return ""

        nav._bot._get_detail_title_text = fake_title
        with patch("mobile.event_navigator.time.sleep"):
            result = nav._current_page_matches_target(
                {"state": "detail_page"}, clicked_title="张杰2026巡回演唱会"
            )
        assert result is True
        assert calls["n"] == 7  # 首读 + 6 次延长轮询全部读空

    def test_formal_commit_rejects_empty_title_and_venue(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            detail_title="",
            if_commit_order=True,
        )
        with patch("mobile.event_navigator.time.sleep"):
            result = nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="张杰2026巡回演唱会",
            )
        assert result is False

    def test_formal_commit_rejects_venue_only_identity(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_venue="国家体育场",
            detail_title="",
            if_commit_order=True,
        )
        nav._bot._detail_venue = "国家体育场-鸟巢"
        with patch("mobile.event_navigator.time.sleep"):
            result = nav._current_page_matches_target(
                {"state": "detail_page"},
                clicked_title="张杰2026巡回演唱会",
            )
        assert result is False

    def test_formal_commit_rejects_configured_venue_mismatch(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_title="张杰2026巡回演唱会",
            target_venue="国家体育场",
            detail_title="张杰2026巡回演唱会",
            if_commit_order=True,
        )
        nav._bot._detail_venue = "工人体育场"
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False

    def test_formal_commit_rejects_partial_venue_match(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_title="张杰2026巡回演唱会",
            target_venue="国家体育场",
            detail_title="张杰2026巡回演唱会",
            if_commit_order=True,
        )
        nav._bot._detail_venue = "国家体育场-鸟巢"

        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False
        assert nav._last_formal_event_identity is None

    def test_formal_commit_accepts_matching_title_and_venue(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_title="张杰2026巡回演唱会",
            target_venue="国家体育场-鸟巢",
            detail_title="张杰2026巡回演唱会",
            if_commit_order=True,
        )
        # Punctuation variants normalize to the same exact identity; the proof
        # retains the page strings observed in this snapshot.
        nav._bot._detail_venue = "国家体育场—鸟巢"
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is True
        assert nav._last_formal_event_identity == {
            "target_title": "张杰2026巡回演唱会",
            "observed_title": "张杰2026巡回演唱会",
            "observed_title_normalized": "张杰2026巡回演唱会",
            "target_venue": "国家体育场鸟巢",
            "observed_venue": "国家体育场—鸟巢",
            "observed_venue_normalized": "国家体育场鸟巢",
        }

    def test_formal_commit_rejects_original_show_vs_added_show(self):
        nav = _make_fuzzy_nav(
            keyword="周杰伦 演唱会",
            target_title="周杰伦2026嘉年华巡回演唱会上海站",
            detail_title="周杰伦2026嘉年华巡回演唱会上海站加场",
            if_commit_order=True,
        )

        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False
        assert nav._last_formal_event_identity is None

    def test_formal_commit_does_not_substitute_item_detail_title(self):
        nav = _make_fuzzy_nav(
            keyword="周杰伦 演唱会",
            target_title="周杰伦2026嘉年华巡回演唱会上海站",
            detail_title="周杰伦2026嘉年华巡回演唱会上海站加场",
            if_commit_order=True,
        )
        nav._bot.item_detail = MagicMock(
            item_name="周杰伦2026嘉年华巡回演唱会上海站加场",
            item_name_display="周杰伦上海加场",
        )

        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False
        assert nav._last_formal_event_identity is None

    def test_formal_failed_recheck_clears_prior_exact_identity(self):
        nav = _make_fuzzy_nav(
            keyword="周杰伦 演唱会",
            target_title="周杰伦2026嘉年华巡回演唱会上海站",
            detail_title="周杰伦2026嘉年华巡回演唱会上海站",
            if_commit_order=True,
        )
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is True
        assert nav._last_formal_event_identity is not None

        nav._bot._detail_title = "周杰伦2026嘉年华巡回演唱会上海站加场"
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False
        assert nav._last_formal_event_identity is None

    def test_formal_commit_rejects_same_artist_different_tour(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_title="张杰2026未·LIVE—开往1982演唱会",
            detail_title="张杰2026曜北斗巡回演唱会",
            if_commit_order=True,
        )
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False

    def test_formal_commit_rejects_conflicting_year(self):
        nav = _make_fuzzy_nav(
            keyword="张杰 演唱会",
            target_title="张杰2026未·LIVE巡回演唱会",
            detail_title="张杰2025未·LIVE巡回演唱会",
            if_commit_order=True,
        )
        assert nav._current_page_matches_target(
            {"state": "detail_page"}
        ) is False

    def test_current_page_no_clicked_title_keeps_old_path(self):
        # 向后兼容：不传 clicked_title 时走既有 keyword 校验路径
        nav = _make_fuzzy_nav(keyword="张杰 演唱会", detail_title="张杰2026巡回演唱会北京站")
        assert nav._current_page_matches_target({"state": "detail_page"}) is True

    def test_current_page_mismatch_returns_false(self):
        # 详情页标题与被点卡片、keyword 均不相干：锚定与回退双双失败 → False
        nav = _make_fuzzy_nav(keyword="张杰 演唱会", detail_title="开心麻花爆笑舞台剧")
        assert (
            nav._current_page_matches_target(
                {"state": "detail_page"}, clicked_title="张杰2026巡回演唱会北京站"
            )
            is False
        )


class TestScoreSearchResultEnhancements:
    """issue #50 回归：相似度加分 / 城市字段分 / 年份冲突罚分。"""

    def _nav(self):
        return _make_fuzzy_nav(
            keyword="凤凰传奇 演唱会", city="广州", target_venue=None
        )

    def test_score_search_result_similarity_bonus(self):
        # 修复前 40 分被拒（<60）；相似度 0.482 补分后过点击阈值
        score = self._nav()._score_search_result(
            "凤凰传奇「吉祥如意」2026巡演·广州站", "广州体育馆"
        )
        assert score >= 60

    def test_score_search_result_unrelated_stays_below(self):
        # 同城无关演出不得过阈值（相似度 0.314 无加分）
        score = self._nav()._score_search_result(
            "五月天2026巡回演唱会广州站", "广州体育馆"
        )
        assert score < 60

    def test_score_search_result_unrelated_with_city_field_stays_below(self):
        # 对抗审查修正 2 边界：城市字段分（+10）不得把同城无关演出推过阈值
        score = self._nav()._score_search_result(
            "五月天2026巡回演唱会广州站", "广州体育馆", "广州"
        )
        assert score < 60

    def test_score_search_result_city_field(self):
        # 城市字段加/罚分（+10 vs -80）：同一标题下差距至少 90
        nav = self._nav()
        title = "凤凰传奇「吉祥如意」2026巡演·广州站"
        score_match = nav._score_search_result(title, "体育馆", "广州")
        score_conflict = nav._score_search_result(title, "体育馆", "北京")
        assert score_match - score_conflict >= 90

    def test_score_search_result_city_field_none_backward_compatible(self):
        # 不传 city_text 与传 None 等价（旧调用方/delegators 兼容）
        nav = self._nav()
        title = "凤凰传奇「吉祥如意」2026巡演·广州站"
        assert nav._score_search_result(title, "体育馆") == nav._score_search_result(
            title, "体育馆", None
        )

    def test_score_search_result_year_conflict(self):
        # keyword 与标题都含 4 位年份且不同：至少低 60 分（防跨年巡演误配）
        nav = _make_fuzzy_nav(keyword="张杰2026演唱会", city=None)
        score_same_year = nav._score_search_result("张杰2026巡回演唱会", "")
        score_conflict = nav._score_search_result("张杰2025巡回演唱会", "")
        assert score_same_year - score_conflict >= 60


class TestOpenTargetRejectedBlacklist:
    """issue #51：详情页校验失败的卡片进入黑名单，不再反复点击。"""

    def test_open_target_blacklists_mismatched_card(self):
        nav = _make_fuzzy_nav(keyword="张杰 演唱会", city=None)
        bot = nav._bot

        card_high, card_low = MagicMock(name="card_high"), MagicMock(name="card_low")
        texts = {
            (id(card_high), "cn.damai:id/tv_project_name"): "张杰2026巡回演唱会北京站",
            (id(card_low), "cn.damai:id/tv_project_name"): "张杰2026",
        }

        bot._find_all = MagicMock(return_value=[card_high, card_low])
        bot._safe_element_text = lambda container, by, value: texts.get(
            (id(container), value), ""
        )
        bot._score_search_result = (
            lambda title, venue, city_text=None: nav._score_search_result(
                title, venue, city_text
            )
        )
        bot._click_element_center = MagicMock()
        bot.wait_for_page_state = MagicMock(return_value={"state": "detail_page"})
        bot._current_page_matches_target = MagicMock(return_value=False)
        bot._press_keycode_safe = MagicMock(return_value=True)
        bot.dismiss_startup_popups = MagicMock()
        bot._scroll_search_results = MagicMock()
        bot._timed_step = MagicMock()
        bot._timed_step.return_value.__enter__ = MagicMock()
        bot._timed_step.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mobile.event_navigator.time.sleep"):
            result = nav._open_target_from_search_results(
                max_scrolls=1, return_details=True
            )

        assert result["opened"] is False
        # 高分卡首轮被点击并校验失败后进黑名单：第二轮不再点击（总共 1 次）
        bot._click_element_center.assert_called_once_with(card_high)
        # 点击后的详情页校验必须携带被点卡片标题（clicked_title 锚定）
        bot._current_page_matches_target.assert_called_once_with(
            {"state": "detail_page"}, clicked_title="张杰2026巡回演唱会北京站"
        )
        assert bot.hot_cache_invalidations == ["search_result_opened"]
