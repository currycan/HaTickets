# -*- coding: UTF-8 -*-
"""Focused adapter tests for Appium/u2 compatibility helpers in DamaiBot."""

import pytest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from mobile.config import Config
from mobile.damai_app import DamaiBot
from mobile.hot_path_benchmark import _fast_check_detail_page


def _u2_config():
    return Config(
        server_url=None,
        device_name="Android",
        udid=None,
        platform_version=None,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="test",
        users=["UserA"],
        city="深圳",
        date="12.06",
        price="799元",
        price_index=1,
        if_commit_order=False,
        probe_only=True,
        driver_backend="u2",
    )


def _appium_config():
    return Config(
        server_url="http://127.0.0.1:4723",
        device_name="Android",
        udid=None,
        platform_version=None,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="test",
        users=["UserA"],
        city="深圳",
        date="12.06",
        price="799元",
        price_index=1,
        if_commit_order=False,
        probe_only=True,
        driver_backend="appium",
    )


class TestU2SetupAndCoreAdapters:
    def test_setup_u2_driver_sets_driver_and_settings(self):
        mock_d = Mock()
        mock_d.settings = {}
        mock_d.app_start = Mock()
        with patch("uiautomator2.connect", return_value=mock_d) as connect:
            bot = DamaiBot(config=_u2_config())

        connect.assert_called_once_with(None)
        assert bot.driver is mock_d
        assert bot.d is mock_d
        assert bot.wait is None
        assert mock_d.settings["wait_timeout"] == 0
        assert mock_d.settings["operation_delay"] == (0, 0)

    def test_setup_u2_driver_tolerates_non_mapping_settings(self):
        class _DummySettings:
            pass

        mock_d = Mock()
        mock_d.settings = _DummySettings()
        mock_d.app_start = Mock()
        with patch("uiautomator2.connect", return_value=mock_d):
            bot = DamaiBot(config=_u2_config())

        assert bot.driver is mock_d
        mock_d.app_start.assert_called_once()

    def test_find_all_u2_id_uses_xpath_matches(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        expected = [Mock(), Mock(), Mock()]
        bot.d.xpath.return_value.all.return_value = expected

        result = bot._find_all(By.ID, "cn.damai:id/checkbox")
        assert result == expected

    def test_find_all_appium_non_iterable_returns_empty(self):
        bot = DamaiBot(config=_appium_config(), setup_driver=False)
        bot.driver = Mock()
        bot.driver.find_elements.return_value = Mock()
        assert bot._find_all(By.ID, "cn.damai:id/checkbox") == []

    def test_parse_uiselector_supports_index_and_clickable(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock(return_value="selector")
        bot.driver = bot.d

        selector = bot._appium_selector_to_u2(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().className("android.widget.FrameLayout").index(2).clickable(true)',
        )
        assert selector == "selector"
        bot.d.assert_called_once_with(className="android.widget.FrameLayout", clickable=True, instance=2)

    def test_parse_uiselector_invalid_raises(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        with pytest.raises(ValueError, match="无法解析 UiSelector"):
            bot._parse_uiselector("new UiSelector()")

    def test_selector_exists_wait_fallback(self):
        selector = Mock()
        selector.exists = None
        selector.wait = Mock(return_value=True)
        assert DamaiBot._selector_exists(selector) is True

    def test_wait_for_element_u2_success(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        selector = Mock()
        selector.wait.return_value = True
        selector.get.return_value = "element"
        with patch.object(bot, "_find", return_value=selector):
            assert bot._wait_for_element(By.ID, "foo", timeout=0.1) == "element"

    def test_wait_for_element_u2_timeout(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        selector = Mock()
        selector.wait.return_value = False
        with patch.object(bot, "_find", return_value=selector):
            with pytest.raises(TimeoutException):
                bot._wait_for_element(By.ID, "foo", timeout=0.1)

    def test_element_rect_from_bounds_tuple(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        element = type("ElementStub", (), {})()
        element.bounds = (10, 20, 110, 70)
        element.info = {}
        rect = bot._element_rect(element)
        assert rect == {"x": 10, "y": 20, "width": 100, "height": 50}

    def test_element_rect_from_tuple_rect(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        element = type("ElementStub", (), {})()
        element.rect = (10, 20, 300, 180)
        element.info = {}
        rect = bot._element_rect(element)
        assert rect == {"x": 10, "y": 20, "width": 300, "height": 180}

    def test_read_element_text_supports_xml_attrib_text(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        element = type("XmlNode", (), {"attrib": {"text": "张杰演唱会"}})()
        assert bot._read_element_text(element) == "张杰演唱会"

    def test_click_coordinates_u2_long_click(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        bot._click_coordinates(10, 20, duration=120)
        bot.d.long_click.assert_called_once()

    def test_press_keycode_safe_u2(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        assert bot._press_keycode_safe(66) is True
        bot.d.press.assert_called_once_with("enter")

    def test_container_find_elements_u2_child_scan(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)

        def _child(resourceId=None, className=None, instance=0):
            child = Mock()
            child.exists = Mock(return_value=instance == 0)
            child.info = {"resourceId": resourceId, "className": className}
            return child

        container = Mock()
        container.child.side_effect = _child
        results = bot._container_find_elements(container, By.ID, "cn.damai:id/title_tv")
        assert len(results) == 1

    def test_container_find_elements_u2_devicexml_node(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        container = Mock()
        container.elem = ET.fromstring(
            "<node resource-id='cn.damai:id/ll_search_item' class='android.widget.LinearLayout'>"
            "<node resource-id='cn.damai:id/tv_project_name' class='android.widget.TextView' text='张杰'/>"
            "</node>"
        )
        assert len(bot._container_find_elements(container, By.ID, "cn.damai:id/tv_project_name")) == 1
        assert len(bot._container_find_elements(container, By.CLASS_NAME, "android.widget.TextView")) == 1

    def test_collect_descendant_texts_u2_filters_by_bounds(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        bot.d.dump_hierarchy.return_value = (
            '<hierarchy>'
            '<node bounds="[0,0][100,100]" text="容器"/>'
            '<node bounds="[10,10][80,80]" text="内部文本"/>'
            '<node bounds="[150,150][220,220]" text="外部文本"/>'
            "</hierarchy>"
        )
        container = Mock()
        container.info = {"bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100}}
        texts = bot._collect_descendant_texts(container)
        assert "内部文本" in texts
        assert "外部文本" not in texts

    def test_scroll_search_results_u2_uses_swipe(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        bot._scroll_search_results()
        bot.d.swipe.assert_called_once()

    def test_recover_to_navigation_start_u2_uses_app_start(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        bot._press_keycode_safe = Mock(return_value=False)
        bot.probe_current_page = Mock(return_value={"state": "unknown"})
        with patch("mobile.damai_app.time.sleep"):
            result = bot._recover_to_navigation_start({"state": "unknown"})
        assert result["state"] == "unknown"
        bot.d.app_start.assert_called_once_with(bot.config.app_package, stop=False)


def test_fast_check_detail_page_ignores_non_iterable_find_result():
    bot = Mock()
    bot._find_all.return_value = Mock()
    assert _fast_check_detail_page(bot) is None


class TestU2SearchHotPath:
    def test_timed_step_records_manual_comparison(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        with patch("mobile.damai_app.time.perf_counter", side_effect=[1.0, 2.0]):
            with bot._timed_step("测试步骤", manual_baseline_seconds=2.0):
                pass
        timing = bot._last_discovery_step_timings[-1]
        assert timing["step"] == "测试步骤"
        assert timing["faster_than_manual"] is True

    def test_setup_u2_driver_skips_app_start_when_app_already_running(self):
        mock_d = Mock()
        mock_d.settings = {}
        mock_d.app_current.return_value = {"package": "cn.damai"}
        mock_d.app_start = Mock()
        with patch("uiautomator2.connect", return_value=mock_d):
            bot = DamaiBot(config=_u2_config())
        assert bot.driver is mock_d
        mock_d.app_start.assert_not_called()

    def test_find_all_u2_fallback_mismatch_returns_empty(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        bot.d = Mock()
        bot.driver = bot.d
        bot.d.xpath.side_effect = RuntimeError("xpath unavailable")

        selector = Mock()
        selector.exists = Mock(return_value=True)
        selector.info = {"resourceId": "other:id/value"}
        bot.d.return_value = selector

        assert bot._find_all(By.ID, "cn.damai:id/target") == []

    def test_submit_search_keyword_u2_success(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        search_input = Mock()
        search_input.text = ""
        search_input.set_text = Mock()
        with patch.object(bot, "_wait_for_element", return_value=search_input), \
             patch.object(bot, "_click_element_center"), \
             patch.object(bot, "_read_element_text", side_effect=["", ""]), \
             patch.object(bot, "_press_keycode_safe", return_value=True), \
             patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "_find_all", return_value=[Mock()]), \
             patch("mobile.damai_app.time.sleep"):
            assert bot._submit_search_keyword() is True

        search_input.set_text.assert_called_once_with(bot.config.keyword)

    def test_submit_search_keyword_u2_timeout(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        search_input = Mock()
        search_input.text = ""
        search_input.set_text = Mock()
        with patch.object(bot, "_wait_for_element", return_value=search_input), \
             patch.object(bot, "_click_element_center"), \
             patch.object(bot, "_read_element_text", side_effect=["", ""]), \
             patch.object(bot, "_press_keycode_safe", return_value=True), \
             patch.object(bot, "_has_element", return_value=False), \
             patch.object(bot, "_find_all", return_value=[]), \
             patch("mobile.damai_app.time.sleep"), \
             patch("mobile.damai_app.time.time", side_effect=[0.0, 4.0]):
            assert bot._submit_search_keyword() is False

    def test_open_target_from_search_results_returns_details_when_opened(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        card = Mock()
        with patch.object(bot, "_find_all", return_value=[card]), \
             patch.object(
                 bot,
                 "_safe_element_text",
                 side_effect=["张杰演唱会", "鸟巢", "北京", "04.06"],
             ), \
             patch.object(bot, "_score_search_result", return_value=90), \
             patch.object(bot, "_click_element_center"), \
             patch.object(bot, "wait_for_page_state", return_value={"state": "detail_page"}), \
             patch.object(bot, "_current_page_matches_target", return_value=True):
            result = bot._open_target_from_search_results(max_scrolls=0, return_details=True)

        assert result["opened"] is True
        assert result["search_results"][0]["title"] == "张杰演唱会"

    def test_open_target_from_search_results_returns_details_when_not_opened(self):
        bot = DamaiBot(config=_u2_config(), setup_driver=False)
        card = Mock()
        with patch.object(bot, "_find_all", return_value=[card]), \
             patch.object(
                 bot,
                 "_safe_element_text",
                 side_effect=["相关演出", "未知场馆", "北京", "04.06"],
             ), \
             patch.object(bot, "_score_search_result", return_value=10):
            result = bot._open_target_from_search_results(max_scrolls=0, return_details=True)

        assert result["opened"] is False
        assert len(result["search_results"]) == 1
