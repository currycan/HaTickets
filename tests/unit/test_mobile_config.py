"""Unit tests for mobile/config.py"""
import json
import os

import pytest

from mobile.config import (
    Config,
    _load_config_dict_from_path,
    _resolve_existing_config_path,
    _resolve_writable_config_path,
    _strip_jsonc_comments,
    load_config_dict,
    save_config_dict,
    update_runtime_mode,
)


_VALID = dict(
    server_url="http://localhost:4723",
    device_name="Android",
    udid=None,
    platform_version=None,
    app_package="cn.damai",
    app_activity=".launcher.splash.SplashMainActivity",
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
            device_name="Pixel 8",
            udid="R58M123456A",
            platform_version="14",
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
        )
        assert cfg.server_url == "http://localhost:4723"
        assert cfg.device_name == "Pixel 8"
        assert cfg.udid == "R58M123456A"
        assert cfg.platform_version == "14"
        assert cfg.app_package == "cn.damai"
        assert cfg.app_activity == ".launcher.splash.SplashMainActivity"
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

    def test_keyword_can_be_none_when_item_url_is_provided(self):
        cfg = Config(**_make(
            keyword=None,
            item_url="https://m.damai.cn/shows/item.html?itemId=1016133935724",
        ))
        assert cfg.keyword is None
        assert cfg.item_url.endswith("1016133935724")

    def test_keyword_none_without_item_reference_raises(self):
        with pytest.raises(ValueError, match="keyword 不能为空"):
            Config(**_make(keyword=None))

    def test_item_id_invalid_raises(self):
        with pytest.raises(ValueError, match="item_id"):
            Config(**_make(item_id="abc123"))

    def test_target_title_empty_raises(self):
        with pytest.raises(ValueError, match="target_title"):
            Config(**_make(target_title=""))

    def test_target_venue_empty_raises(self):
        with pytest.raises(ValueError, match="target_venue"):
            Config(**_make(target_venue=""))

    def test_auto_navigate_non_bool_raises(self):
        with pytest.raises(ValueError, match="auto_navigate"):
            Config(**_make(auto_navigate="yes"))

    def test_if_commit_order_non_bool_raises(self):
        with pytest.raises(ValueError, match="if_commit_order"):
            Config(**_make(if_commit_order="no"))

    def test_probe_only_non_bool_raises(self):
        with pytest.raises(ValueError, match="probe_only"):
            Config(**_make(probe_only="yes"))

    def test_device_name_empty_raises(self):
        with pytest.raises(ValueError, match="device_name"):
            Config(**_make(device_name=""))

    def test_udid_empty_raises(self):
        with pytest.raises(ValueError, match="udid"):
            Config(**_make(udid=""))

    def test_platform_version_empty_raises(self):
        with pytest.raises(ValueError, match="platform_version"):
            Config(**_make(platform_version=""))

    def test_app_package_empty_raises(self):
        with pytest.raises(ValueError, match="app_package"):
            Config(**_make(app_package=""))

    def test_app_activity_empty_raises(self):
        with pytest.raises(ValueError, match="app_activity"):
            Config(**_make(app_activity=""))


class TestMobileConfigNewFields:

    def test_sell_start_time_valid_iso(self):
        cfg = Config(**_make(sell_start_time="2026-04-01T20:00:00+08:00"))
        assert cfg.sell_start_time == "2026-04-01T20:00:00+08:00"

    def test_sell_start_time_non_string_raises(self):
        with pytest.raises(ValueError, match="sell_start_time 必须是 ISO 格式"):
            Config(**_make(sell_start_time=123))

    def test_sell_start_time_invalid_raises(self):
        with pytest.raises(ValueError, match="sell_start_time"):
            Config(**_make(sell_start_time="not-a-date"))

    def test_sell_start_time_none_is_valid(self):
        cfg = Config(**_make(sell_start_time=None))
        assert cfg.sell_start_time is None

    def test_countdown_lead_ms_default(self):
        cfg = Config(**_make())
        assert cfg.countdown_lead_ms == 3000

    def test_countdown_lead_ms_negative_raises(self):
        with pytest.raises(ValueError, match="countdown_lead_ms"):
            Config(**_make(countdown_lead_ms=-1))

    def test_wait_cta_ready_timeout_ms_default(self):
        cfg = Config(**_make())
        assert cfg.wait_cta_ready_timeout_ms == 0

    def test_wait_cta_ready_timeout_ms_negative_raises(self):
        with pytest.raises(ValueError, match="wait_cta_ready_timeout_ms"):
            Config(**_make(wait_cta_ready_timeout_ms=-1))

    def test_fast_retry_count_default(self):
        cfg = Config(**_make())
        assert cfg.fast_retry_count == 8

    def test_fast_retry_count_negative_raises(self):
        with pytest.raises(ValueError, match="fast_retry_count"):
            Config(**_make(fast_retry_count=-1))

    def test_fast_retry_interval_ms_negative_raises(self):
        with pytest.raises(ValueError, match="fast_retry_interval_ms"):
            Config(**_make(fast_retry_interval_ms=-1))

    def test_rush_mode_default_false(self):
        cfg = Config(**_make())
        assert cfg.rush_mode is False

    def test_rush_mode_non_bool_raises(self):
        with pytest.raises(ValueError, match="rush_mode"):
            Config(**_make(rush_mode="yes"))

    def test_fast_retry_interval_ms_default(self):
        cfg = Config(**_make())
        assert cfg.fast_retry_interval_ms == 120


class TestMobileConfigLoadConfig:

    def test_load_config_dict_from_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=f"配置文件未找到: {tmp_path / 'missing.jsonc'}"):
            _load_config_dict_from_path(tmp_path / "missing.jsonc")

    def test_resolve_existing_config_path_uses_default_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        (tmp_path / "config.local.jsonc").write_text("{}", encoding="utf-8")
        (tmp_path / "config.jsonc").write_text("{}", encoding="utf-8")

        assert _resolve_existing_config_path() == "config.jsonc"

    def test_resolve_existing_config_path_uses_env_override(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.local.jsonc").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        assert _resolve_existing_config_path() == "config.local.jsonc"

    def test_resolve_writable_config_path_defaults_to_shared(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        (tmp_path / "config.jsonc").write_text("{}", encoding="utf-8")

        assert _resolve_writable_config_path() == "config.jsonc"

    def test_resolve_writable_config_path_uses_env_override(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        assert _resolve_writable_config_path() == "config.local.jsonc"

    def test_load_config_success(self, mock_mobile_config_file, monkeypatch):
        mock_mobile_config_file()
        monkeypatch.chdir(mock_mobile_config_file.__wrapped__ if hasattr(mock_mobile_config_file, '__wrapped__') else mock_mobile_config_file().parent)
        # Re-create since chdir changed
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Pixel 8",
            "udid": "R58M123456A",
            "platform_version": "14",
            "app_package": "cn.damai",
            "app_activity": ".launcher.splash.SplashMainActivity",
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
        assert cfg.device_name == "Pixel 8"
        assert cfg.udid == "R58M123456A"
        assert cfg.platform_version == "14"
        assert cfg.app_package == "cn.damai"
        assert cfg.app_activity == ".launcher.splash.SplashMainActivity"
        assert cfg.keyword == "test"
        assert cfg.users == ["A"]
        assert cfg.city == "北京"
        assert cfg.if_commit_order is False
        assert cfg.probe_only is True

    def test_load_config_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
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

    def test_load_config_requires_keyword_or_item_reference(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        with pytest.raises(KeyError, match="keyword 或 item_url 或 item_id"):
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
        assert cfg.device_name == "Android"
        assert cfg.udid is None
        assert cfg.platform_version is None
        assert cfg.price_index == 0
        assert cfg.probe_only is True

    def test_load_config_accepts_item_url_without_keyword(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_data = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Android",
            "udid": "device-1",
            "platform_version": "16",
            "app_package": "cn.damai",
            "app_activity": ".launcher.splash.SplashMainActivity",
            "item_url": "https://m.damai.cn/shows/item.html?itemId=1016133935724",
            "users": ["A"],
            "city": "北京",
            "date": "04.06",
            "price": "380元",
            "price_index": 0,
            "if_commit_order": False,
            "probe_only": True,
            "auto_navigate": True,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        cfg = Config.load_config()
        assert cfg.keyword is None
        assert cfg.item_url.endswith("1016133935724")
        assert cfg.auto_navigate is True

    def test_load_and_save_config_dict_round_trip(self, tmp_path):
        path = tmp_path / "config.jsonc"
        source = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Android",
            "udid": "ABC",
            "platform_version": "16",
            "app_package": "cn.damai",
            "app_activity": ".launcher.splash.SplashMainActivity",
            "keyword": "张杰 演唱会",
            "target_title": "张杰演唱会北京站",
            "target_venue": "国家体育场-鸟巢",
            "users": ["张三"],
            "city": "北京",
            "date": "04.06",
            "price": "1280元",
            "price_index": 6,
            "if_commit_order": False,
            "probe_only": True,
            "auto_navigate": True,
            "wait_cta_ready_timeout_ms": 60000,
            "rush_mode": True,
        }

        save_config_dict(source, str(path))
        loaded = load_config_dict(str(path))

        assert loaded == source

    def test_update_runtime_mode_writes_probe_flags(self, tmp_path):
        path = tmp_path / "config.jsonc"
        source = _make(if_commit_order=True, probe_only=False)
        save_config_dict(source, str(path))

        previous, updated = update_runtime_mode(True, False, str(path))
        loaded = load_config_dict(str(path))

        assert previous == {"probe_only": False, "if_commit_order": True}
        assert updated == {"probe_only": True, "if_commit_order": False}
        assert loaded["probe_only"] is True
        assert loaded["if_commit_order"] is False

    def test_update_runtime_mode_writes_submit_flags(self, tmp_path):
        path = tmp_path / "config.jsonc"
        source = _make(if_commit_order=False, probe_only=True)
        save_config_dict(source, str(path))

        previous, updated = update_runtime_mode(False, True, str(path))
        loaded = load_config_dict(str(path))

        assert previous == {"probe_only": True, "if_commit_order": False}
        assert updated == {"probe_only": False, "if_commit_order": True}
        assert loaded["probe_only"] is False
        assert loaded["if_commit_order"] is True

    def test_load_config_reads_rush_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
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
            "rush_mode": True,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        cfg = Config.load_config()
        assert cfg.rush_mode is True

    def test_load_config_reads_wait_cta_ready_timeout_ms(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
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
            "wait_cta_ready_timeout_ms": 45000,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps(config_data), encoding="utf-8")

        cfg = Config.load_config()
        assert cfg.wait_cta_ready_timeout_ms == 45000

    def test_load_config_defaults_to_config_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        shared_fields = {
            "server_url": "http://127.0.0.1:4723",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps({
            **shared_fields,
            "keyword": "from-default",
        }), encoding="utf-8")
        (tmp_path / "config.local.jsonc").write_text(json.dumps({
            **shared_fields,
            "keyword": "from-local",
        }), encoding="utf-8")

        cfg = Config.load_config()

        assert cfg.keyword == "from-default"

    def test_load_config_uses_env_override_for_config_local_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        shared_fields = {
            "server_url": "http://127.0.0.1:4723",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(json.dumps({
            **shared_fields,
            "keyword": "from-default",
        }), encoding="utf-8")
        (tmp_path / "config.local.jsonc").write_text(json.dumps({
            **shared_fields,
            "keyword": "from-local",
        }), encoding="utf-8")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        cfg = Config.load_config()

        assert cfg.keyword == "from-local"

    def test_save_config_dict_defaults_to_config_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        source = {
            "server_url": "http://127.0.0.1:4723",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }

        save_config_dict(source)

        assert (tmp_path / "config.jsonc").exists()
        assert load_config_dict() == source

    def test_save_config_dict_uses_env_override_for_config_local_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        source = {
            "server_url": "http://127.0.0.1:4723",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        save_config_dict(source)

        assert (tmp_path / "config.local.jsonc").exists()
        assert load_config_dict("config.local.jsonc") == source


# ---------------------------------------------------------------------------
# Uncovered validation branches
# ---------------------------------------------------------------------------

class TestUncoveredBranches:
    def test_keyword_none_with_no_item_ref_raises(self):
        """keyword=None without item_url or item_id raises ValueError."""
        with pytest.raises(ValueError, match="keyword"):
            Config(**_make(keyword=None, item_url=None, item_id=None))

    def test_sell_start_time_non_string_raises(self):
        """sell_start_time as int (not str) raises ValueError."""
        with pytest.raises(ValueError, match="sell_start_time"):
            Config(**_make(sell_start_time=12345))

    def test_load_config_missing_keyword_and_item_raises(self, tmp_path, monkeypatch):
        """Config.load_config raises KeyError when keyword/item_url/item_id all absent."""
        monkeypatch.chdir(tmp_path)
        source = {
            "server_url": "http://127.0.0.1:4723",
            "device_name": "Android",
            "app_package": "cn.damai",
            "app_activity": ".SplashMainActivity",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
            "probe_only": False,
        }
        import json
        (tmp_path / "config.jsonc").write_text(json.dumps(source))
        with pytest.raises(KeyError, match="keyword"):
            Config.load_config()
