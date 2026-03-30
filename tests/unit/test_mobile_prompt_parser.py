"""Unit tests for mobile/prompt_parser.py"""

import pytest

from mobile.prompt_parser import (
    _extract_digits,
    _parse_chinese_int,
    choose_price_option,
    parse_prompt,
    score_price_option,
)


class TestParsePrompt:
    def test_parse_prompt_rejects_empty_input(self):
        with pytest.raises(ValueError, match="prompt 不能为空"):
            parse_prompt("   ")

    def test_parse_common_concert_prompt(self):
        intent = parse_prompt("帮我抢一张 4 月 6 号张杰的演唱会门票，内场")

        assert intent.quantity == 1
        assert intent.date == "04.06"
        assert intent.artist == "张杰"
        assert intent.search_keyword == "张杰 演唱会"
        assert intent.candidate_keywords[:2] == ["张杰 演唱会", "张杰"]
        assert intent.price_hint == "内场"
        assert intent.seat_hint == "内场"

    def test_parse_numeric_price_hint(self):
        intent = parse_prompt("帮我抢两张 4月6日 张杰演唱会 1280 元")

        assert intent.quantity == 2
        assert intent.date == "04.06"
        assert intent.price_hint == "1280元"
        assert intent.numeric_price_hint == 1280

    def test_parse_prompt_with_city_and_no_concert_word(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")

        assert intent.quantity == 1
        assert intent.date == "04.04"
        assert intent.city == "上海"
        assert intent.artist == "马思唯"
        assert intent.search_keyword == "马思唯 演唱会"
        assert intent.price_hint == "看台899元"
        assert intent.seat_hint == "看台"
        assert intent.numeric_price_hint == 899

    def test_parse_prompt_adds_notes_when_date_and_price_are_missing(self):
        intent = parse_prompt("帮我买一张马思唯上海演唱会")

        assert "提示词中未识别到明确日期" in intent.notes[0]
        assert "提示词中未识别到明确票档偏好" in intent.notes[1]

    def test_parse_prompt_supports_station_city_and_slash_date(self):
        intent = parse_prompt("帮我抢两张 成都站 4/18 顽童mj116 演唱会")

        assert intent.quantity == 2
        assert intent.city == "成都"
        assert intent.date == "04.18"
        assert intent.artist == "顽童mj116"


class TestPromptParserInternals:
    def test_parse_chinese_int_variants(self):
        assert _parse_chinese_int("") is None
        assert _parse_chinese_int("12") == 12
        assert _parse_chinese_int("十六") == 16
        assert _parse_chinese_int("二十") == 20
        assert _parse_chinese_int("二十三") == 23

    def test_extract_digits_returns_first_numeric_price(self):
        assert _extract_digits("看台 899元") == 899
        assert _extract_digits("无价格") is None


class TestChoosePriceOption:
    def test_choose_price_option_matches_exact_numeric_hint(self):
        intent = parse_prompt("帮我抢一张 4 月 6 日张杰演唱会 1280 元")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "1280元", "tag": "可预约"},
            {"index": 2, "text": "1680元", "tag": "可预约"},
        ]

        selected = choose_price_option(intent, options)

        assert selected["index"] == 1
        assert selected["text"] == "1280元"

    def test_choose_price_option_returns_none_when_seat_hint_is_ambiguous(self):
        intent = parse_prompt("帮我抢一张 4 月 6 日张杰演唱会 内场")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "1280元", "tag": "可预约"},
            {"index": 2, "text": "1680元", "tag": "可预约"},
        ]

        selected = choose_price_option(intent, options)

        assert selected is None

    def test_score_price_option_penalizes_unavailable_tags(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
        option = {"index": 5, "text": "看台 899元", "tag": "售罄"}

        assert score_price_option(intent, option) < 0

    def test_choose_price_option_returns_none_when_best_score_is_too_low(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的 999 元票")
        options = [{"index": 0, "text": "380元", "tag": "可选"}]

        assert choose_price_option(intent, options) is None

    def test_choose_price_option_returns_none_for_unavailable_default_choice(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
        options = [{"index": 0, "text": "看台 899元", "tag": "售罄"}]

        assert choose_price_option(intent, options) is None

    def test_choose_price_option_returns_none_for_empty_options(self):
        intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
        assert choose_price_option(intent, []) is None
