"""Unit tests for mobile/prompt_parser.py"""

import pytest

from mobile.prompt_parser import (
    PromptIntent,
    _parse_chinese_int,
    _parse_city,
    _parse_date,
    _parse_price_hints,
    _parse_quantity,
    choose_price_option,
    is_price_option_available,
    parse_prompt,
    score_price_option,
)


class TestParsePrompt:
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


# ---------------------------------------------------------------------------
# _parse_chinese_int
# ---------------------------------------------------------------------------

class TestParseChineseInt:
    def test_zero(self):
        assert _parse_chinese_int("零") == 0

    def test_one(self):
        assert _parse_chinese_int("一") == 1

    def test_two_liang(self):
        assert _parse_chinese_int("两") == 2

    def test_ten(self):
        assert _parse_chinese_int("十") == 10

    def test_ten_plus_five(self):
        assert _parse_chinese_int("十五") == 15

    def test_five_tens(self):
        assert _parse_chinese_int("五十") == 50

    def test_five_tens_five(self):
        assert _parse_chinese_int("五十五") == 55

    def test_arabic_digit(self):
        assert _parse_chinese_int("3") == 3

    def test_empty_string_returns_none(self):
        assert _parse_chinese_int("") is None

    def test_invalid_token_returns_none(self):
        assert _parse_chinese_int("abc") is None

    def test_whitespace_returns_none(self):
        assert _parse_chinese_int("   ") is None


# ---------------------------------------------------------------------------
# _parse_quantity
# ---------------------------------------------------------------------------

class TestParseQuantity:
    def test_arabic_digit(self):
        assert _parse_quantity("买3张") == 3

    def test_chinese_two(self):
        assert _parse_quantity("两张票") == 2

    def test_chinese_ten(self):
        assert _parse_quantity("十张门票") == 10

    def test_chinese_three(self):
        assert _parse_quantity("三张") == 3

    def test_embedded_in_sentence(self):
        assert _parse_quantity("帮我买5张门票") == 5

    def test_no_quantity_defaults_to_one(self):
        assert _parse_quantity("帮我买票") == 1

    def test_just_concert_name_defaults_to_one(self):
        assert _parse_quantity("张杰演唱会") == 1

    def test_yi_zhang(self):
        assert _parse_quantity("要一张") == 1

    def test_two_digit(self):
        assert _parse_quantity("抢12张") == 12


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_month_day_ri(self):
        assert _parse_date("3月15日演唱会") == "03.15"

    def test_month_day_hao(self):
        assert _parse_date("3月15号") == "03.15"

    def test_december_first(self):
        assert _parse_date("12月1日") == "12.01"

    def test_dot_separator(self):
        assert _parse_date("3.15") == "03.15"

    def test_slash_separator(self):
        assert _parse_date("12/1") == "12.01"

    def test_dash_separator(self):
        assert _parse_date("12-1演唱会") == "12.01"

    def test_no_date_returns_none(self):
        assert _parse_date("张杰演唱会") is None

    def test_invalid_month_returns_none(self):
        # 月份 > 12，pattern不匹配有效范围
        assert _parse_date("13月5日") is None

    def test_invalid_day_returns_none(self):
        assert _parse_date("3月32日") is None

    def test_embedded_date(self):
        assert _parse_date("帮我抢 4 月 6 日张杰的演唱会") == "04.06"


# ---------------------------------------------------------------------------
# _parse_city
# ---------------------------------------------------------------------------

class TestParseCity:
    def test_known_city_exact(self):
        assert _parse_city("北京演唱会") == "北京"

    def test_known_city_shanghai(self):
        assert _parse_city("上海演唱会") == "上海"

    def test_zhan_pattern(self):
        # "鸟巢站" - not a known city, matches regex
        assert _parse_city("鸟巢站演唱会") == "鸟巢"

    def test_known_city_with_zhan_suffix(self):
        # "南京" is in list, found before regex runs
        assert _parse_city("南京站") == "南京"

    def test_unknown_city_returns_none(self):
        assert _parse_city("纽约演唱会") is None

    def test_empty_string_returns_none(self):
        assert _parse_city("") is None

    def test_chengdu(self):
        assert _parse_city("成都跨年演唱会") == "成都"

    def test_no_city_in_prompt(self):
        assert _parse_city("张杰演唱会门票") is None


# ---------------------------------------------------------------------------
# _parse_price_hints
# ---------------------------------------------------------------------------

class TestParsePriceHints:
    def test_seat_and_price(self):
        hint, seat, numeric = _parse_price_hints("VIP500元")
        assert seat == "VIP"
        assert numeric == 500
        assert "VIP" in hint
        assert "500" in hint

    def test_seat_and_price_neichang(self):
        hint, seat, numeric = _parse_price_hints("内场280")
        assert seat == "内场"
        assert numeric == 280
        assert hint == "内场280元"

    def test_seat_only(self):
        hint, seat, numeric = _parse_price_hints("看台")
        assert seat == "看台"
        assert numeric is None
        assert hint == "看台"

    def test_price_only(self):
        hint, seat, numeric = _parse_price_hints("1280元")
        assert hint == "1280元"
        assert seat is None
        assert numeric == 1280

    def test_no_hints(self):
        hint, seat, numeric = _parse_price_hints("张杰演唱会")
        assert hint is None
        assert seat is None
        assert numeric is None

    def test_front_row_vip(self):
        # _SEAT_HINTS 遍历顺序：内场, 看台, VIP...；前排排在VIP后面
        hint, seat, numeric = _parse_price_hints("前排VIP1680")
        # "内场"不在，"看台"不在，"VIP"在 → seat="VIP"
        assert seat == "VIP"
        assert numeric == 1680


# ---------------------------------------------------------------------------
# is_price_option_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_available_tag(self):
        assert is_price_option_available({"tag": "可预约"}) is True

    def test_empty_tag(self):
        assert is_price_option_available({"tag": ""}) is True

    def test_missing_tag_key(self):
        assert is_price_option_available({}) is True

    def test_no_ticket(self):
        assert is_price_option_available({"tag": "无票"}) is False

    def test_sold_out(self):
        assert is_price_option_available({"tag": "售罄"}) is False

    def test_already_sold_out(self):
        assert is_price_option_available({"tag": "已售罄"}) is False

    def test_not_selectable(self):
        assert is_price_option_available({"tag": "不可选"}) is False

    def test_temp_unavailable(self):
        assert is_price_option_available({"tag": "暂不可售"}) is False


# ---------------------------------------------------------------------------
# score_price_option
# ---------------------------------------------------------------------------

class TestScorePriceOption:
    def _intent(self, price_hint=None, seat_hint=None, numeric_price=None):
        return PromptIntent(
            raw_prompt="test",
            price_hint=price_hint,
            seat_hint=seat_hint,
            numeric_price_hint=numeric_price,
        )

    def test_no_hints_available_score_zero(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "可预约"})
        # 可预约 +10, no other hints
        assert score == 10

    def test_unavailable_penalized(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "售罄"})
        assert score == -1000

    def test_price_hint_match(self):
        intent = self._intent(price_hint="内场")
        score = score_price_option(intent, {"text": "内场580元", "tag": "可预约"})
        # +120 price_hint match + 10 可预约
        assert score >= 120

    def test_seat_hint_match(self):
        intent = self._intent(seat_hint="内场")
        score = score_price_option(intent, {"text": "内场580元", "tag": ""})
        # +80 seat_hint match
        assert score >= 80

    def test_numeric_exact_match(self):
        intent = self._intent(numeric_price=1280)
        score = score_price_option(intent, {"text": "1280元", "tag": ""})
        # +150 exact numeric match
        assert score >= 150

    def test_numeric_mismatch_penalty(self):
        intent = self._intent(numeric_price=1280)
        score_near = score_price_option(intent, {"text": "1380元", "tag": ""})
        score_far = score_price_option(intent, {"text": "580元", "tag": ""})
        assert score_near > score_far

    def test_presale_tag_bonus(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "预售"})
        assert score == 10

    def test_ke_xuan_tag_bonus(self):
        intent = self._intent()
        score = score_price_option(intent, {"text": "580元", "tag": "可选"})
        assert score == 10


# ---------------------------------------------------------------------------
# choose_price_option (extended)
# ---------------------------------------------------------------------------

class TestChoosePriceOptionExtended:
    def _intent(self, price_hint=None, seat_hint=None, numeric_price=None):
        return PromptIntent(
            raw_prompt="test",
            price_hint=price_hint,
            seat_hint=seat_hint,
            numeric_price_hint=numeric_price,
        )

    def test_empty_list_returns_none(self):
        intent = self._intent()
        assert choose_price_option(intent, []) is None

    def test_all_unavailable_with_price_hint_returns_none(self):
        intent = self._intent(price_hint="内场")
        options = [
            {"text": "内场580元", "tag": "售罄"},
            {"text": "580元", "tag": "无票"},
        ]
        result = choose_price_option(intent, options)
        assert result is None

    def test_no_price_hint_selects_best_available(self):
        intent = self._intent()
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        result = choose_price_option(intent, options)
        assert result is not None

    def test_no_price_hint_all_unavailable_returns_none(self):
        intent = self._intent()
        options = [{"text": "580元", "tag": "售罄"}]
        result = choose_price_option(intent, options)
        assert result is None

    def test_score_field_added_to_result(self):
        intent = self._intent(numeric_price=1280)
        options = [{"index": 0, "text": "1280元", "tag": "可预约"}]
        result = choose_price_option(intent, options)
        assert result is not None
        assert "score" in result

    def test_single_available_option_returned(self):
        intent = self._intent()
        options = [{"index": 0, "text": "580元", "tag": "可选"}]
        result = choose_price_option(intent, options)
        assert result is not None
        assert result["index"] == 0

    def test_high_score_option_wins(self):
        intent = self._intent(numeric_price=580)
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
            {"index": 2, "text": "980元", "tag": "可预约"},
        ]
        result = choose_price_option(intent, options)
        assert result["index"] == 1


# ---------------------------------------------------------------------------
# parse_prompt — validation and notes
# ---------------------------------------------------------------------------

class TestParsePromptValidation:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_prompt("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            parse_prompt("   ")

    def test_none_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            parse_prompt(None)

    def test_no_date_adds_note(self):
        intent = parse_prompt("帮我抢张杰演唱会")
        assert any("日期" in note for note in intent.notes)

    def test_no_price_adds_note(self):
        intent = parse_prompt("帮我抢张杰演唱会")
        assert any("票档" in note for note in intent.notes)


class TestParsePromptNotes:
    def test_with_date_and_price_no_notes(self):
        intent = parse_prompt("帮我抢一张 4月6日 张杰演唱会 1280元")
        date_notes = [n for n in intent.notes if "日期" in n]
        price_notes = [n for n in intent.notes if "票档" in n]
        assert len(date_notes) == 0
        assert len(price_notes) == 0

    def test_without_date_note_added(self):
        intent = parse_prompt("帮我抢张杰演唱会 1280元")
        assert any("日期" in n for n in intent.notes)

    def test_without_price_note_added(self):
        intent = parse_prompt("帮我抢 4月6日 张杰演唱会")
        assert any("票档" in n for n in intent.notes)


# ---------------------------------------------------------------------------
# Edge cases: keyword extraction and artist fallback
# ---------------------------------------------------------------------------

class TestKeywordExtractionEdgeCases:
    def test_only_stopwords_raises_value_error(self):
        """Prompt consisting only of stopwords → no keyword → ValueError."""
        with pytest.raises(ValueError):
            parse_prompt("帮我买票")

    def test_artist_without_concert_keyword_uses_tail_fallback(self):
        """When prompt has no 演唱会/live keyword, tail_artist regex is used."""
        intent = parse_prompt("张三北京3月15日")
        # tail fallback: extracts "张三" (or similar), search_keyword is non-empty
        assert intent.search_keyword is not None

    def test_artist_with_live_keyword(self):
        """'live' variant recognized as concert type keyword."""
        intent = parse_prompt("五月天 live 4月6日")
        assert intent.artist is not None
        assert intent.search_keyword is not None

    def test_candidate_keywords_deduplication(self):
        """Duplicate candidate keywords are removed."""
        intent = parse_prompt("张杰演唱会 3月15日")
        # candidates list should not have duplicates
        normalized = set()
        for kw in intent.candidate_keywords:
            assert kw not in normalized, f"Duplicate: {kw}"
            normalized.add(kw)

    def test_numeric_price_mismatch_decreases_score(self):
        """score_price_option penalizes numeric price mismatch."""
        intent = PromptIntent(raw_prompt="test", numeric_price_hint=1280)
        score_close = score_price_option(intent, {"text": "1380元", "tag": ""})
        score_far = score_price_option(intent, {"text": "280元", "tag": ""})
        # further mismatch → lower score
        assert score_close > score_far
