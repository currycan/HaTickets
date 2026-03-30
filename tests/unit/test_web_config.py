"""Unit tests for web/config.py"""
import pytest

from config import Config

_VALID = dict(
    index_url="https://www.damai.cn/",
    login_url="https://passport.damai.cn/login",
    target_url="https://detail.damai.cn/item.htm?id=1",
    users=["Alice"],
    city=None,
    dates=None,
    prices=None,
    if_listen=False,
    if_commit_order=False,
)


def _make(**overrides):
    return {**_VALID, **overrides}


class TestWebConfig:

    def test_config_init_stores_all_attributes(self):
        cfg = Config(
            index_url="https://www.damai.cn/",
            login_url="https://passport.damai.cn/login",
            target_url="https://detail.damai.cn/item.htm?id=1",
            users=["Alice", "Bob"],
            city="上海",
            dates=["2026-05-01"],
            prices=["580"],
            if_listen=True,
            if_commit_order=False,
            max_retries=500,
            fast_mode=False,
            page_load_delay=3,
        )
        assert cfg.index_url == "https://www.damai.cn/"
        assert cfg.login_url == "https://passport.damai.cn/login"
        assert cfg.target_url == "https://detail.damai.cn/item.htm?id=1"
        assert cfg.users == ["Alice", "Bob"]
        assert cfg.city == "上海"
        assert cfg.dates == ["2026-05-01"]
        assert cfg.prices == ["580"]
        assert cfg.if_listen is True
        assert cfg.if_commit_order is False
        assert cfg.max_retries == 500
        assert cfg.fast_mode is False
        assert cfg.page_load_delay == 3

    def test_config_init_default_values(self):
        cfg = Config(**_make())
        assert cfg.max_retries == 1000
        assert cfg.fast_mode is True
        assert cfg.page_load_delay == 2

    def test_config_init_custom_overrides_defaults(self):
        cfg = Config(**_make(max_retries=1, fast_mode=False, page_load_delay=0.5))
        assert cfg.max_retries == 1
        assert cfg.fast_mode is False
        assert cfg.page_load_delay == 0.5


class TestWebConfigValidation:

    def test_invalid_index_url_raises(self):
        with pytest.raises(ValueError, match="index_url"):
            Config(**_make(index_url="not-a-url"))

    def test_invalid_login_url_raises(self):
        with pytest.raises(ValueError, match="login_url"):
            Config(**_make(login_url="ftp://invalid"))

    def test_invalid_target_url_raises(self):
        with pytest.raises(ValueError, match="target_url"):
            Config(**_make(target_url=""))

    def test_http_url_is_valid(self):
        cfg = Config(**_make(index_url="http://example.com"))
        assert cfg.index_url == "http://example.com"

    def test_empty_users_raises(self):
        with pytest.raises(ValueError, match="users"):
            Config(**_make(users=[]))

    def test_users_not_list_raises(self):
        with pytest.raises(ValueError, match="users"):
            Config(**_make(users="Alice"))

    def test_users_with_non_string_raises(self):
        with pytest.raises(ValueError, match="users"):
            Config(**_make(users=[1, 2]))

    def test_city_empty_string_raises(self):
        with pytest.raises(ValueError, match="city"):
            Config(**_make(city=""))

    def test_city_none_is_valid(self):
        cfg = Config(**_make(city=None))
        assert cfg.city is None

    def test_dates_non_list_raises(self):
        with pytest.raises(ValueError, match="dates"):
            Config(**_make(dates="2026-01-01"))

    def test_dates_none_is_valid(self):
        cfg = Config(**_make(dates=None))
        assert cfg.dates is None

    def test_prices_non_list_raises(self):
        with pytest.raises(ValueError, match="prices"):
            Config(**_make(prices="580"))

    def test_prices_none_is_valid(self):
        cfg = Config(**_make(prices=None))
        assert cfg.prices is None

    def test_max_retries_zero_raises(self):
        with pytest.raises(ValueError, match="max_retries"):
            Config(**_make(max_retries=0))

    def test_max_retries_negative_raises(self):
        with pytest.raises(ValueError, match="max_retries"):
            Config(**_make(max_retries=-1))

    def test_max_retries_float_raises(self):
        with pytest.raises(ValueError, match="max_retries"):
            Config(**_make(max_retries=1.5))

    def test_max_retries_capped_at_100000(self):
        cfg = Config(**_make(max_retries=999999))
        assert cfg.max_retries == 100000

    def test_max_retries_exactly_100000(self):
        cfg = Config(**_make(max_retries=100000))
        assert cfg.max_retries == 100000

    def test_page_load_delay_negative_raises(self):
        with pytest.raises(ValueError, match="page_load_delay"):
            Config(**_make(page_load_delay=-0.1))

    def test_page_load_delay_zero_is_valid(self):
        cfg = Config(**_make(page_load_delay=0))
        assert cfg.page_load_delay == 0

    def test_page_load_delay_float_is_valid(self):
        cfg = Config(**_make(page_load_delay=1.5))
        assert cfg.page_load_delay == 1.5

    def test_dates_with_non_string_element_raises(self):
        """dates list containing non-string element should raise."""
        with pytest.raises(ValueError):
            Config(**_make(dates=["2026-05-01", 123]))

    def test_prices_with_non_string_element_raises(self):
        """prices list containing non-string element should raise."""
        with pytest.raises(ValueError):
            Config(**_make(prices=["580", None]))
