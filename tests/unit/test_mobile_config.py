"""Unit tests for mobile/config.py"""
import json
import os

import pytest

from mobile.config import Config, _strip_jsonc_comments


_VALID = dict(
    server_url="http://localhost:4723",
    keyword="周深",
    users=["张三"],
    city="深圳",
    date="12.06",
    price="799元",
    price_index=0,
    if_commit_order=False,
    probe_only=False,
)


def _make(**overrides):
    return {**_VALID, **overrides}


class TestStripJsoncComments:

    def test_strip_single_line_comments(self):
        text = '{\n  "key": "value" // this is a comment\n}'
        result = _strip_jsonc_comments(text)
        assert json.loads(result) == {"key": "value"}

    def test_strip_multi_line_comments(self):
        text = '{\n  /* comment */\n  "key": "value"\n}'
        result = _strip_jsonc_comments(text)
        assert json.loads(result) == {"key": "value"}

    def test_preserves_urls(self):
        text = '{"url": "https://example.com"}'
        result = _strip_jsonc_comments(text)
        assert json.loads(result) == {"url": "https://example.com"}

    def test_no_comments(self):
        text = '{"key": "value"}'
        assert _strip_jsonc_comments(text) == text


class TestMobileConfigInit:

    def test_config_init_stores_all_attributes(self):
        cfg = Config(
            server_url="http://localhost:4723",
            keyword="周深",
            users=["张三", "李四"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=True,
        )
        assert cfg.server_url == "http://localhost:4723"
        assert cfg.keyword == "周深"
        assert cfg.users == ["张三", "李四"]
        assert cfg.city == "深圳"
        assert cfg.date == "12.06"
        assert cfg.price == "799元"
        assert cfg.price_index == 1
        assert cfg.if_commit_order is True
        assert cfg.probe_only is True


class TestMobileConfigValidation:

    def test_invalid_server_url_raises(self):
        with pytest.raises(ValueError, match="server_url"):
            Config(**_make(server_url="localhost:4723"))

    def test_server_url_ftp_raises(self):
        with pytest.raises(ValueError, match="server_url"):
            Config(**_make(server_url="ftp://localhost:4723"))

    def test_server_url_https_is_valid(self):
        cfg = Config(**_make(server_url="https://remote.appium.io"))
        assert cfg.server_url == "https://remote.appium.io"

    def test_empty_users_raises(self):
        with pytest.raises(ValueError, match="users"):
            Config(**_make(users=[]))

    def test_users_not_list_raises(self):
        with pytest.raises(ValueError, match="users"):
            Config(**_make(users="张三"))

    def test_price_index_negative_raises(self):
        with pytest.raises(ValueError, match="price_index"):
            Config(**_make(price_index=-1))

    def test_price_index_zero_is_valid(self):
        cfg = Config(**_make(price_index=0))
        assert cfg.price_index == 0

    def test_price_index_float_raises(self):
        with pytest.raises(ValueError, match="price_index"):
            Config(**_make(price_index=1.5))

    def test_empty_keyword_raises(self):
        with pytest.raises(ValueError, match="keyword"):
            Config(**_make(keyword=""))

    def test_whitespace_keyword_raises(self):
        with pytest.raises(ValueError, match="keyword"):
            Config(**_make(keyword="   "))

    def test_keyword_non_string_raises(self):
        with pytest.raises(ValueError, match="keyword"):
            Config(**_make(keyword=123))

    def test_probe_only_non_bool_raises(self):
        with pytest.raises(ValueError, match="probe_only"):
            Config(**_make(probe_only="yes"))


class TestMobileConfigLoadConfig:

    def test_load_config_success(self, mock_mobile_config_file, monkeypatch):
        mock_mobile_config_file()
        monkeypatch.chdir(mock_mobile_config_file.__wrapped__ if hasattr(mock_mobile_config_file, '__wrapped__') else mock_mobile_config_file().parent)
        # Re-create since chdir changed
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
            "probe_only": True,
        }
        with open("config.jsonc", "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        cfg = Config.load_config()
        assert cfg.server_url == "http://127.0.0.1:4723"
        assert cfg.keyword == "test"
        assert cfg.users == ["A"]
        assert cfg.city == "北京"
        assert cfg.if_commit_order is False
        assert cfg.probe_only is True

    def test_load_config_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="config.jsonc"):
            Config.load_config()

    def test_load_config_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.jsonc").write_text("{invalid json", encoding="utf-8")
        with pytest.raises(ValueError, match="配置文件格式错误"):
            Config.load_config()

    def test_load_config_missing_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.jsonc").write_text('{"server_url": "x"}', encoding="utf-8")
        with pytest.raises(KeyError, match="缺少必需字段"):
            Config.load_config()

    def test_load_config_jsonc_with_comments(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        jsonc_content = """{
  // Appium server URL
  "server_url": "http://127.0.0.1:4723",
  "keyword": "test",
  "users": ["A"],
  "city": "北京",
  "date": "01.01",
  "price": "100元",
  /* price index */
  "price_index": 0,
  "if_commit_order": false,
  "probe_only": true
}"""
        (tmp_path / "config.jsonc").write_text(jsonc_content, encoding="utf-8")
        cfg = Config.load_config()
        assert cfg.server_url == "http://127.0.0.1:4723"
        assert cfg.price_index == 0
        assert cfg.probe_only is True
