# -*- coding: UTF-8 -*-
"""Unit tests for shared/config_validator.py"""

import pytest

from shared.config_validator import validate_non_empty_list, validate_positive_int, validate_url


# ---------------------------------------------------------------------------
# validate_url
# ---------------------------------------------------------------------------

class TestValidateUrl:
    def test_valid_https(self):
        validate_url("https://www.damai.cn/", "url")  # should not raise

    def test_valid_http(self):
        validate_url("http://damai.cn/", "url")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            validate_url("", "url")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_url(None, "url")

    def test_integer_raises(self):
        with pytest.raises(ValueError):
            validate_url(123, "url")

    def test_ftp_raises(self):
        with pytest.raises(ValueError):
            validate_url("ftp://example.com", "url")

    def test_no_scheme_raises(self):
        with pytest.raises(ValueError):
            validate_url("www.damai.cn", "url")

    def test_error_message_contains_field_name(self):
        with pytest.raises(ValueError, match="target_url"):
            validate_url("not-a-url", "target_url")


# ---------------------------------------------------------------------------
# validate_non_empty_list
# ---------------------------------------------------------------------------

class TestValidateNonEmptyList:
    def test_valid_list(self):
        validate_non_empty_list(["a", "b"], "users")  # should not raise

    def test_single_item_list(self):
        validate_non_empty_list(["only"], "users")

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            validate_non_empty_list([], "users")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_non_empty_list(None, "users")

    def test_string_raises(self):
        with pytest.raises(ValueError):
            validate_non_empty_list("not a list", "users")

    def test_tuple_raises(self):
        with pytest.raises(ValueError):
            validate_non_empty_list(("a", "b"), "users")

    def test_error_message_contains_field_name(self):
        with pytest.raises(ValueError, match="users"):
            validate_non_empty_list([], "users")


# ---------------------------------------------------------------------------
# validate_positive_int
# ---------------------------------------------------------------------------

class TestValidatePositiveInt:
    def test_valid_positive(self):
        result = validate_positive_int(5, "retries")
        assert result == 5

    def test_returns_value(self):
        assert validate_positive_int(100, "count") == 100

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int(0, "retries")

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int(-1, "retries")

    def test_float_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int(1.5, "retries")

    def test_true_raises(self):
        # bool is subclass of int, must be rejected
        with pytest.raises(ValueError):
            validate_positive_int(True, "retries")

    def test_false_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int(False, "retries")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int(None, "retries")

    def test_string_raises(self):
        with pytest.raises(ValueError):
            validate_positive_int("5", "retries")

    def test_max_value_caps_result(self):
        result = validate_positive_int(200, "retries", max_value=100)
        assert result == 100

    def test_below_max_value_unchanged(self):
        result = validate_positive_int(50, "retries", max_value=100)
        assert result == 50

    def test_no_max_value_uncapped(self):
        result = validate_positive_int(999999, "retries")
        assert result == 999999
