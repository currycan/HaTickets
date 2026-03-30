"""Unit tests for mobile/prompt_parser.py"""

from mobile.prompt_parser import choose_price_option, parse_prompt


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
