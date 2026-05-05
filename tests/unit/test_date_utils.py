# -*- coding: UTF-8 -*-
"""mobile/date_utils.py 单元测试。"""

from __future__ import annotations

import pytest

from mobile.date_utils import normalize_date


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4月6号", "04.06"),
        ("4 月 6 日", "04.06"),
        ("04月06日", "04.06"),
        ("4/6", "04.06"),
        ("04-06", "04.06"),
        ("04.06", "04.06"),
        ("2026-04-06", "04.06"),
        ("2026/04/06", "04.06"),
        ("2026.04.06", "04.06"),
        ("12月1日演唱会", "12.01"),
        ("帮我抢 4 月 6 日张杰的演唱会", "04.06"),
    ],
)
def test_normalize_date_valid_formats(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "乱七八糟",
        "13月5日",  # 月份越界
        "3月32日",  # 日期越界
        "abc",
        "20-99",  # 日期越界
    ],
)
def test_normalize_date_invalid_formats_return_none(raw):
    assert normalize_date(raw) is None


def test_normalize_date_none_input_returns_none():
    assert normalize_date(None) is None  # type: ignore[arg-type]


def test_normalize_date_year_form_takes_precedence_over_short_form():
    # 形如 2026-04-06 不应误判成 26-04（先匹配带年份模式）
    assert normalize_date("2026-04-06") == "04.06"


def test_normalize_date_handles_leading_zero_padding():
    assert normalize_date("4/6") == "04.06"
    assert normalize_date("04/06") == "04.06"
