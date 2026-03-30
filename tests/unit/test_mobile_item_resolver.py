# -*- coding: UTF-8 -*-
"""Unit tests for mobile/item_resolver.py."""

import json
from http.cookiejar import CookieJar
from unittest.mock import Mock, patch, MagicMock

import pytest

from mobile.item_resolver import (
    DamaiItemDetail,
    DamaiItemResolveError,
    DamaiItemResolver,
    build_search_keyword,
    city_keyword,
    extract_item_id,
    normalize_text,
)


# ---------------------------------------------------------------------------
# extract_item_id
# ---------------------------------------------------------------------------

class TestExtractItemId:

    def test_extracts_from_full_url(self):
        url = "https://m.damai.cn/shows/item.html?itemId=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_raw_number(self):
        assert extract_item_id("1016133935724") == "1016133935724"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="itemId"):
            extract_item_id("https://m.damai.cn/shows/item.html")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            extract_item_id("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            extract_item_id("   ")

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            extract_item_id(None)

    def test_extracts_from_lowercase_itemid_param(self):
        url = "https://m.damai.cn/shows/item.html?itemid=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_path_segment(self):
        url = "https://m.damai.cn/shows/1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_extracts_from_id_query_param(self):
        url = "https://detail.damai.cn/item.htm?id=1016133935724"
        assert extract_item_id(url) == "1016133935724"

    def test_no_extractable_id_raises(self):
        with pytest.raises(ValueError):
            extract_item_id("not-a-url-or-number")


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:

    def test_removes_brackets_and_spaces(self):
        assert normalize_text("【北京】 2026 张杰 未·LIVE") == "北京2026张杰未live"

    def test_empty_string_returns_empty(self):
        assert normalize_text("") == ""

    def test_none_returns_empty(self):
        assert normalize_text(None) == ""

    def test_lowercase_conversion(self):
        assert normalize_text("ABC") == "abc"

    def test_removes_various_separators(self):
        result = normalize_text("张杰:演唱会—北京站")
        assert ":" not in result
        assert "—" not in result


# ---------------------------------------------------------------------------
# city_keyword
# ---------------------------------------------------------------------------

class TestCityKeyword:

    def test_strips_shi_suffix(self):
        assert city_keyword("北京市") == "北京"

    def test_strips_zizhizhou_suffix(self):
        assert city_keyword("西双版纳傣族自治州") == "西双版纳傣族"

    def test_strips_diqu_suffix(self):
        assert city_keyword("延边地区") == "延边"

    def test_strips_meng_suffix(self):
        assert city_keyword("兴安盟") == "兴安"

    def test_no_suffix_unchanged(self):
        assert city_keyword("北京") == "北京"

    def test_none_returns_empty(self):
        assert city_keyword(None) == ""

    def test_empty_returns_empty(self):
        assert city_keyword("") == ""


# ---------------------------------------------------------------------------
# build_search_keyword
# ---------------------------------------------------------------------------

class TestBuildSearchKeyword:

    def test_removes_city_bracket_prefix(self):
        title = "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"
        assert build_search_keyword(title) == "2026张杰未·LIVE—「开往1982」演唱会-北京站"

    def test_plain_title_unchanged(self):
        assert build_search_keyword("张杰演唱会") == "张杰演唱会"

    def test_display_name_used_as_fallback(self):
        result = build_search_keyword("", "张杰巡演2026")
        assert result == "张杰巡演2026"

    def test_both_empty_raises(self):
        with pytest.raises(ValueError):
            build_search_keyword("", "")

    def test_none_falls_back_to_display(self):
        result = build_search_keyword(None, "张杰")
        assert result == "张杰"


# ---------------------------------------------------------------------------
# DamaiItemDetail
# ---------------------------------------------------------------------------

class TestDamaiItemDetail:

    def _make_detail(self, **kwargs):
        defaults = dict(
            item_id="123456",
            item_name="张杰演唱会",
            item_name_display="【北京】张杰演唱会",
            city_name="北京市",
            venue_name="国家体育场",
            venue_city_name="北京市",
            show_time="2026-04-06",
            price_range="380-1280",
            raw_data={},
        )
        defaults.update(kwargs)
        return DamaiItemDetail(**defaults)

    def test_search_keyword_strips_city_bracket(self):
        detail = self._make_detail(item_name="【北京】张杰演唱会")
        assert "【北京】" not in detail.search_keyword

    def test_search_keyword_from_item_name(self):
        detail = self._make_detail(item_name="张杰演唱会")
        assert detail.search_keyword == "张杰演唱会"

    def test_city_keyword_strips_suffix(self):
        detail = self._make_detail(city_name="北京市")
        assert detail.city_keyword == "北京"

    def test_city_keyword_no_suffix(self):
        detail = self._make_detail(city_name="上海")
        assert detail.city_keyword == "上海"

    def test_raw_data_accessible(self):
        raw = {"item": {"itemName": "test"}}
        detail = self._make_detail(raw_data=raw)
        assert detail.raw_data == raw


# ---------------------------------------------------------------------------
# DamaiItemResolver
# ---------------------------------------------------------------------------

class TestDamaiItemResolver:

    def _make_resolver(self):
        return DamaiItemResolver(timeout=5)

    def test_referer_uses_provided_item_url(self):
        resolver = self._make_resolver()
        url = "https://m.damai.cn/shows/item.html?itemId=123"
        assert resolver._referer_for_item("123", url) == url

    def test_referer_constructed_from_item_id_when_no_url(self):
        resolver = self._make_resolver()
        referer = resolver._referer_for_item("123456", None)
        assert "itemId=123456" in referer
        assert "m.damai.cn" in referer

    def test_fetch_item_detail_success(self):
        """fetch_item_detail parses a well-formed API response."""
        resolver = self._make_resolver()

        success_payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {
                    "itemId": "123456",
                    "itemName": "张杰演唱会",
                    "itemNameDisplay": "【北京】张杰演唱会",
                    "cityName": "北京市",
                    "showTime": "2026-04-06",
                },
                "venue": {
                    "venueName": "国家体育场",
                    "venueCityName": "北京市",
                },
                "price": {"range": "380-1280"},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(success_payload)):
            detail = resolver.fetch_item_detail(item_id="123456")

        assert detail.item_id == "123456"
        assert detail.item_name == "张杰演唱会"
        assert detail.venue_name == "国家体育场"
        assert detail.price_range == "380-1280"

    def test_fetch_item_detail_api_failure_raises(self):
        """Non-SUCCESS ret raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        failure_payload = {
            "ret": ["FAIL::接口调用失败"],
            "data": None,
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(failure_payload)):
            with pytest.raises(DamaiItemResolveError, match="失败"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_invalid_json_raises(self):
        """Non-JSON response raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value="not json"):
            with pytest.raises(DamaiItemResolveError, match="不可解析"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_missing_item_name_raises(self):
        """Missing item name raises DamaiItemResolveError."""
        resolver = self._make_resolver()

        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {},
                "venue": {},
                "price": {},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="fake_token"), \
             patch.object(resolver, "_request", return_value=json.dumps(payload)):
            with pytest.raises(DamaiItemResolveError, match="演出名称"):
                resolver.fetch_item_detail(item_id="123456")

    def test_fetch_item_detail_accepts_item_url(self):
        """item_url is accepted and item_id is extracted from it."""
        resolver = self._make_resolver()

        payload = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "item": {"itemId": "1016133935724", "itemName": "演唱会"},
                "venue": {},
                "price": {},
            },
        }

        with patch.object(resolver, "_prime_token", return_value="tok"), \
             patch.object(resolver, "_request", return_value=json.dumps(payload)):
            detail = resolver.fetch_item_detail(
                item_url="https://m.damai.cn/shows/item.html?itemId=1016133935724"
            )
        assert detail.item_name == "演唱会"

    def test_prime_token_raises_when_no_cookie(self):
        """_prime_token raises DamaiItemResolveError when cookie is absent."""
        resolver = self._make_resolver()

        with patch.object(resolver, "_request", return_value="ok"):
            with pytest.raises(DamaiItemResolveError, match="_m_h5_tk"):
                resolver._prime_token("123", "https://referer.example", "{}")

    def test_request_reads_response_body(self):
        resolver = self._make_resolver()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        resolver.opener = Mock()
        resolver.opener.open.return_value = response

        body = resolver._request("https://example.com/api", "https://referer.example")

        assert body == '{"ok": true}'
        request = resolver.opener.open.call_args.args[0]
        assert request.full_url == "https://example.com/api"
        assert request.header_items()

    def test_prime_token_returns_cookie_prefix(self):
        resolver = self._make_resolver()
        cookie = Mock()
        cookie.name = "_m_h5_tk"
        cookie.value = "token_part_12345_suffix"
        resolver.cookie_jar = [cookie]

        with patch.object(resolver, "_request", return_value="ok"):
            assert resolver._prime_token("123", "https://referer.example", "{}") == "token"
