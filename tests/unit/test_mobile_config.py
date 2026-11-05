"""Unit tests for mobile/config.py"""

import json
import logging
from pathlib import Path

import pytest

from mobile.config import (
    Config,
    ConfigError,
    DEPRECATED_CONFIG_KEYS,
    KNOWN_CONFIG_KEYS,
    PLACEHOLDER_LITERALS,
    PRICE_INDEX_LARGE_WARNING_THRESHOLD,
    _load_config_dict_from_path,
    _resolve_existing_config_path,
    _resolve_writable_config_path,
    _strip_jsonc_comments,
    _strip_jsonc_comments_scanned,
    _verify_patched,
    filter_known_config_keys,
    load_config_dict,
    migrate_deprecated_config,
    patch_jsonc_text,
    read_runtime_mode,
    save_config_dict,
    unknown_config_keys,
    update_config_values,
    update_runtime_mode,
    validate_config_dict,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_TEMPLATE = _REPO_ROOT / "mobile" / "config.example.jsonc"


_VALID = dict(
    serial=None,
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
            keyword="周深",
            users=["张三", "李四"],
            city="深圳",
            date="12.06",
            price="799元",
            price_index=1,
            if_commit_order=True,
            probe_only=True,
            app_package="cn.damai",
            app_activity=".launcher.splash.SplashMainActivity",
        )
        assert cfg.serial is None
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
    def test_serial_string_is_valid(self):
        cfg = Config(**_make(serial="c6c4eb67"))
        assert cfg.serial == "c6c4eb67"

    def test_serial_empty_raises(self):
        with pytest.raises(ValueError, match="serial"):
            Config(**_make(serial=""))

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

    def test_keyword_none_raises(self):
        with pytest.raises(ValueError, match="keyword 不能为空"):
            Config(**_make(keyword=None))

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

    def test_presale_prewarm_enabled_defaults_true(self):
        cfg = Config(**_make())
        assert cfg.presale_prewarm_enabled is True

    def test_presale_prewarm_enabled_requires_bool(self):
        with pytest.raises(ValueError, match="presale_prewarm_enabled"):
            Config(**_make(presale_prewarm_enabled=1))

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


class TestRushSubFlags:
    """P1 #25 — rush_mode 拆为 3 个子开关。"""

    def test_defaults_match_rush_true_baseline(self):
        cfg = Config(**_make())
        assert cfg.rush_skip_session is False
        assert cfg.rush_skip_price_dump is True
        assert cfg.rush_aggressive_retry is True

    def test_explicit_sub_flag_values_kept_when_rush_mode_false(self):
        cfg = Config(
            **_make(
                rush_mode=False,
                rush_skip_session=True,
                rush_skip_price_dump=False,
                rush_aggressive_retry=False,
            )
        )
        assert cfg.rush_skip_session is True
        assert cfg.rush_skip_price_dump is False
        assert cfg.rush_aggressive_retry is False

    def test_rush_mode_true_forces_skip_session_false(self):
        """rush_mode=True 永不允许 rush_skip_session=True（issue #25 根因防御）。"""
        cfg = Config(
            **_make(rush_mode=True, rush_skip_session=True),
        )
        assert cfg.rush_mode is True
        assert cfg.rush_skip_session is False

    def test_rush_mode_true_keeps_other_sub_flag_overrides(self):
        cfg = Config(
            **_make(
                rush_mode=True,
                rush_skip_price_dump=False,
                rush_aggressive_retry=False,
            )
        )
        assert cfg.rush_mode is True
        assert cfg.rush_skip_price_dump is False
        assert cfg.rush_aggressive_retry is False

    def test_rush_skip_session_non_bool_raises(self):
        with pytest.raises(ValueError, match="rush_skip_session"):
            Config(**_make(rush_skip_session="yes"))

    def test_rush_skip_price_dump_non_bool_raises(self):
        with pytest.raises(ValueError, match="rush_skip_price_dump"):
            Config(**_make(rush_skip_price_dump=1))

    def test_rush_aggressive_retry_non_bool_raises(self):
        with pytest.raises(ValueError, match="rush_aggressive_retry"):
            Config(**_make(rush_aggressive_retry="off"))

    def test_to_dict_includes_sub_flags(self):
        cfg = Config(**_make())
        data = cfg.to_dict()
        assert data["rush_skip_session"] is False
        assert data["rush_skip_price_dump"] is True
        assert data["rush_aggressive_retry"] is True
        assert data["rush_mode"] is False  # alias still emitted

    def test_load_config_reads_sub_flags(self, tmp_path):
        cfg_path = tmp_path / "config.jsonc"
        payload = json.dumps(
            {
                "keyword": "周深",
                "users": ["张三"],
                "city": "深圳",
                "date": "12.06",
                "price": "799元",
                "price_index": 0,
                "if_commit_order": False,
                "rush_skip_session": True,
                "rush_skip_price_dump": False,
                "rush_aggressive_retry": False,
            }
        )
        cfg_path.write_text(payload, encoding="utf-8")
        cfg = Config.load_config(config_path=cfg_path)
        assert cfg.rush_skip_session is True
        assert cfg.rush_skip_price_dump is False
        assert cfg.rush_aggressive_retry is False

    def test_load_config_alias_overrides_skip_session_to_false(self, tmp_path):
        """rush_mode=True 在 config.jsonc 显式 + rush_skip_session=True 时
        日志 warning + 强制 skip_session=False。"""
        cfg_path = tmp_path / "config.jsonc"
        payload = json.dumps(
            {
                "keyword": "周深",
                "users": ["张三"],
                "city": "深圳",
                "date": "12.06",
                "price": "799元",
                "price_index": 0,
                "if_commit_order": False,
                "rush_mode": True,
                "rush_skip_session": True,
            }
        )
        cfg_path.write_text(payload, encoding="utf-8")
        cfg = Config.load_config(config_path=cfg_path)
        assert cfg.rush_mode is True
        assert cfg.rush_skip_session is False


class TestMobileConfigLoadConfig:
    def test_load_config_dict_from_missing_path_raises(self, tmp_path):
        with pytest.raises(
            FileNotFoundError, match=f"配置文件未找到: {tmp_path / 'missing.jsonc'}"
        ):
            _load_config_dict_from_path(tmp_path / "missing.jsonc")

    def test_resolve_existing_config_path_uses_default_config(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        (tmp_path / "config.local.jsonc").write_text("{}", encoding="utf-8")
        (tmp_path / "config.jsonc").write_text("{}", encoding="utf-8")

        assert _resolve_existing_config_path() == "config.jsonc"

    def test_resolve_existing_config_path_uses_env_override(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.local.jsonc").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        assert _resolve_existing_config_path() == "config.local.jsonc"

    def test_resolve_writable_config_path_defaults_to_shared(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        (tmp_path / "config.jsonc").write_text("{}", encoding="utf-8")

        assert _resolve_writable_config_path() == "config.jsonc"

    def test_resolve_writable_config_path_uses_env_override(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        assert _resolve_writable_config_path() == "config.local.jsonc"

    def test_load_config_success(self, mock_mobile_config_file, monkeypatch):
        mock_mobile_config_file()
        monkeypatch.chdir(
            mock_mobile_config_file.__wrapped__
            if hasattr(mock_mobile_config_file, "__wrapped__")
            else mock_mobile_config_file().parent
        )
        # Re-create since chdir changed
        config_data = {
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
        assert cfg.app_package == "cn.damai"
        assert cfg.app_activity == ".launcher.splash.SplashMainActivity"
        assert cfg.keyword == "test"
        assert cfg.users == ["A"]
        assert cfg.city == "北京"
        assert cfg.if_commit_order is False
        assert cfg.probe_only is True
        assert cfg.serial is None

    def test_load_config_reads_serial(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_data = {
            "serial": "abc123",
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        cfg = Config.load_config()
        assert cfg.serial == "abc123"

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
        (tmp_path / "config.jsonc").write_text('{"keyword": "test"}', encoding="utf-8")
        with pytest.raises(KeyError, match="缺少必需字段"):
            Config.load_config()

    def test_load_config_requires_keyword(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_data = {
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        with pytest.raises(KeyError, match="keyword"):
            Config.load_config()

    def test_load_config_jsonc_with_comments(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        jsonc_content = """{
  // server URL (ignored, kept for compat)
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
        assert cfg.price_index == 0
        assert cfg.probe_only is True

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
        (tmp_path / "config.jsonc").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        cfg = Config.load_config()
        assert cfg.rush_mode is True

    def test_load_config_reads_wait_cta_ready_timeout_ms(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_data = {
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
        (tmp_path / "config.jsonc").write_text(
            json.dumps(config_data), encoding="utf-8"
        )

        cfg = Config.load_config()
        assert cfg.wait_cta_ready_timeout_ms == 45000

    def test_load_config_defaults_to_config_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        shared_fields = {
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(
            json.dumps(
                {
                    **shared_fields,
                    "keyword": "from-default",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "config.local.jsonc").write_text(
            json.dumps(
                {
                    **shared_fields,
                    "keyword": "from-local",
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load_config()

        assert cfg.keyword == "from-default"

    def test_load_config_uses_env_override_for_config_local_jsonc(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        shared_fields = {
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        (tmp_path / "config.jsonc").write_text(
            json.dumps(
                {
                    **shared_fields,
                    "keyword": "from-default",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "config.local.jsonc").write_text(
            json.dumps(
                {
                    **shared_fields,
                    "keyword": "from-local",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", "config.local.jsonc")

        cfg = Config.load_config()

        assert cfg.keyword == "from-local"

    def test_save_config_dict_defaults_to_config_jsonc(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
        source = {
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

    def test_save_config_dict_uses_env_override_for_config_local_jsonc(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        source = {
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
    def test_keyword_none_raises(self):
        """keyword=None raises ValueError."""
        with pytest.raises(ValueError, match="keyword"):
            Config(**_make(keyword=None))

    def test_sell_start_time_non_string_raises(self):
        """sell_start_time as int (not str) raises ValueError."""
        with pytest.raises(ValueError, match="sell_start_time"):
            Config(**_make(sell_start_time=12345))

    def test_load_config_missing_keyword_raises(self, tmp_path, monkeypatch):
        """Config.load_config raises KeyError when keyword is absent."""
        monkeypatch.chdir(tmp_path)
        source = {
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


# ---------------------------------------------------------------------------
# Step 4 — load_config price_index range validation (P1 #31)
# ---------------------------------------------------------------------------


class TestLoadConfigPriceIndexRange:
    def _config_dict(self, price_index):
        return {
            "keyword": "周深",
            "users": ["A"],
            "city": "深圳",
            "date": "01.01",
            "price": "100元",
            "price_index": price_index,
            "if_commit_order": False,
        }

    def test_negative_price_index_raises_config_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.jsonc").write_text(
            json.dumps(self._config_dict(-1)), encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="price_index 不能为负数"):
            Config.load_config()

    def test_config_error_is_value_error_subclass(self):
        # ConfigError keeps backwards-compat with broad "except ValueError"
        assert issubclass(ConfigError, ValueError)

    def test_large_price_index_logs_warning(self, tmp_path, monkeypatch, caplog):
        monkeypatch.chdir(tmp_path)
        oversized = PRICE_INDEX_LARGE_WARNING_THRESHOLD + 1
        (tmp_path / "config.jsonc").write_text(
            json.dumps(self._config_dict(oversized)), encoding="utf-8"
        )
        with caplog.at_level("WARNING", logger="mobile.config"):
            cfg = Config.load_config()
        assert cfg.price_index == oversized
        assert any(
            "price_index" in rec.message and "异常大" in rec.message
            for rec in caplog.records
        )

    def test_threshold_value_does_not_warn(self, tmp_path, monkeypatch, caplog):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.jsonc").write_text(
            json.dumps(self._config_dict(PRICE_INDEX_LARGE_WARNING_THRESHOLD)),
            encoding="utf-8",
        )
        with caplog.at_level("WARNING", logger="mobile.config"):
            cfg = Config.load_config()
        assert cfg.price_index == PRICE_INDEX_LARGE_WARNING_THRESHOLD
        assert not any("异常大" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# read_runtime_mode（U-03：shell 模式判定与 Python 实际执行同源）
# ---------------------------------------------------------------------------


class TestReadRuntimeMode:
    def _write(self, tmp_path, text):
        p = tmp_path / "config.jsonc"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_commented_line_does_not_shadow_live_value(self, tmp_path):
        """验收核心：// 注释行 + 生效行并存时，以生效行为准（旧 grep 在此分叉）。"""
        path = self._write(
            tmp_path,
            """{
  // "probe_only": true,
  "probe_only": false,
  "if_commit_order": true
}""",
        )
        assert read_runtime_mode(path) == ("false", "true")

    def test_commented_only_key_reports_missing(self, tmp_path):
        """只有注释行而无生效键：旧 grep 误报 true，--probe 下会跳过改写反而真下单。"""
        path = self._write(tmp_path, '{\n  // "probe_only": true\n  \n}')
        assert read_runtime_mode(path) == ("__missing__", "__missing__")

    def test_mode_flags_agree_with_config_load(self, tmp_path):
        """同源性：read_runtime_mode 与 Config.load_config 对同一文件结论一致。"""
        path = self._write(
            tmp_path,
            """{
  // "probe_only": true,
  "keyword": "周深",
  "users": ["张三"],
  "city": "深圳",
  "date": "12.06",
  "price": "799元",
  "price_index": 0,
  "probe_only": false,
  "if_commit_order": true
}""",
        )
        flags = read_runtime_mode(path)
        cfg = Config.load_config(path)
        assert flags == (str(cfg.probe_only).lower(), str(cfg.if_commit_order).lower())

    def test_missing_keys_report_missing(self, tmp_path):
        path = self._write(tmp_path, "{}")
        assert read_runtime_mode(path) == ("__missing__", "__missing__")

    def test_explicit_null_reports_missing(self, tmp_path):
        path = self._write(tmp_path, '{"probe_only": null, "if_commit_order": null}')
        assert read_runtime_mode(path) == ("__missing__", "__missing__")

    def test_non_boolean_values_report_invalid(self, tmp_path):
        # int 1 必须归为 __invalid__ 而非 true——_normalize_flag 必须用
        # `is True / is False` 身份判断（bool 是 int 子类，truthiness 会误判）
        path = self._write(tmp_path, '{"probe_only": "true", "if_commit_order": 1}')
        assert read_runtime_mode(path) == ("__invalid__", "__invalid__")

    def test_block_comment_does_not_shadow_live_value(self, tmp_path):
        path = self._write(
            tmp_path,
            '{\n  /* "probe_only": true */\n  "probe_only": false\n}',
        )
        assert read_runtime_mode(path)[0] == "false"

    def test_compact_no_space_format(self, tmp_path):
        path = self._write(tmp_path, '{"probe_only":true,"if_commit_order":false}')
        assert read_runtime_mode(path) == ("true", "false")

    def test_multiline_value_format(self, tmp_path):
        """键与值跨行：旧 grep 必报 __missing__，此为隐性行为修正，pin 住防回退。"""
        path = self._write(
            tmp_path, '{\n  "probe_only":\n    true,\n  "if_commit_order": false\n}'
        )
        assert read_runtime_mode(path) == ("true", "false")

    def test_url_with_slashes_not_treated_as_comment(self, tmp_path):
        path = self._write(
            tmp_path,
            '{"item_url": "https://m.damai.cn/xx", "probe_only": false}',
        )
        assert read_runtime_mode(path)[0] == "false"

    def test_malformed_jsonc_raises_value_error(self, tmp_path):
        path = self._write(tmp_path, '{"probe_only": tru')
        with pytest.raises(ValueError):
            read_runtime_mode(path)

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_runtime_mode(str(tmp_path / "nope.jsonc"))

    def test_respects_env_var_override(self, tmp_path, monkeypatch):
        """shell heredoc 契约：配置路径经 HATICKETS_CONFIG_PATH 传入。"""
        path = self._write(tmp_path, '{"probe_only": true, "if_commit_order": false}')
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", path)
        assert read_runtime_mode() == ("true", "false")

    def test_explicit_path_beats_env_var(self, tmp_path, monkeypatch):
        a = tmp_path / "a.jsonc"
        a.write_text('{"probe_only": true}', encoding="utf-8")
        b = tmp_path / "b.jsonc"
        b.write_text('{"probe_only": false}', encoding="utf-8")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", str(a))
        assert read_runtime_mode(str(b))[0] == "false"

    def test_read_after_update_runtime_mode_roundtrip(self, tmp_path):
        """写与读走同一解析器闭环：pin 住 shell 侧 EFFECTIVE_*=DESIRED_* 的假设。"""
        path = self._write(tmp_path, '{"probe_only": false, "if_commit_order": true}')
        update_runtime_mode(True, False, path)
        assert read_runtime_mode(path) == ("true", "false")


class TestReadRuntimeModeShellContract:
    """subprocess 契约测试：与 start_ticket_grabbing.sh 的 heredoc 同体的调用方式。

    守护「stdout 纯净」契约：mobile.config import 链路任何 print / logging
    stdout 污染都会破坏 shell 的按行拆分，把错误旗标喂给横幅。
    """

    _HEREDOC_CODE = (
        "from mobile.config import read_runtime_mode\n"
        "probe_only, if_commit_order = read_runtime_mode()\n"
        "print(probe_only)\n"
        "print(if_commit_order)\n"
    )

    def _env(self, config_path):
        import os

        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_ROOT)
        env["HATICKETS_CONFIG_PATH"] = str(config_path)
        return env

    def test_heredoc_contract_exactly_two_lines_stdout(self, tmp_path):
        import subprocess
        import sys

        config = tmp_path / "config.jsonc"
        config.write_text(
            '{\n  // "probe_only": true,\n  "probe_only": false,\n  "if_commit_order": true\n}',
            encoding="utf-8",
        )
        r = subprocess.run(
            [sys.executable, "-c", self._HEREDOC_CODE],
            capture_output=True,
            text=True,
            env=self._env(config),
            timeout=30,
        )
        assert r.returncode == 0
        # 精确 == 断言（不是 in）：stdout 必须恰好两行旗标
        assert r.stdout == "false\ntrue\n"

    def test_heredoc_contract_malformed_config_nonzero_exit(self, tmp_path):
        import subprocess
        import sys

        config = tmp_path / "config.jsonc"
        config.write_text('{"probe_only": tru', encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-c", self._HEREDOC_CODE],
            capture_output=True,
            text=True,
            env=self._env(config),
            timeout=30,
        )
        assert r.returncode != 0
        assert r.stdout == ""  # traceback 只落 stderr，对应 shell 的 fail-fast 分支


# ---------------------------------------------------------------------------
# U-05 — 启动期配置防护：占位符黑名单 + 未知/废弃字段提示
# ---------------------------------------------------------------------------


class TestPlaceholderValidation:
    """AC1：占位符黑名单——strip 后精确全等匹配，收集全部违规后一次性抛。"""

    def test_placeholder_serial_raises_config_error(self):
        config = _make(serial="你的设备序列号")
        with pytest.raises(ConfigError) as excinfo:
            validate_config_dict(config)
        # ConfigError 继承 ValueError：damai_app/__main__.py 的
        # except (ValueError, RuntimeError) 能干净打印中文错误
        assert isinstance(excinfo.value, ValueError)
        message = str(excinfo.value)
        assert "serial" in message
        assert "你的设备序列号" in message
        assert "adb devices" in message

    def test_placeholder_users_element_lists_only_bad_entry(self):
        config = _make(users=["张三", "你的真实观演人姓名"])
        with pytest.raises(ConfigError) as excinfo:
            validate_config_dict(config)
        message = str(excinfo.value)
        # 逐元素检查：错误消息只列出占位符条目，不冤枉真实姓名
        assert '- users = "你的真实观演人姓名"' in message
        assert "张三" not in message

    @pytest.mark.parametrize(
        "key,placeholder",
        [("city", "演出城市"), ("date", "场次日期"), ("price", "票档原文")],
    )
    def test_placeholder_city_date_price_each_raise(self, key, placeholder):
        config = _make(**{key: placeholder})
        with pytest.raises(ConfigError) as excinfo:
            validate_config_dict(config)
        assert key in str(excinfo.value)

    def test_multiple_placeholders_collected_in_single_error(self):
        config = _make(serial="你的设备序列号", users=["你的真实观演人姓名"])
        with pytest.raises(ConfigError) as excinfo:
            validate_config_dict(config)
        message = str(excinfo.value)
        # 收集全部违规后一次性抛，而非 fail-fast
        assert '- serial = "你的设备序列号"' in message
        assert '- users = "你的真实观演人姓名"' in message

    def test_exact_match_after_strip_no_fuzzy(self):
        # strip 后全等仍抛（前后空格不逃逸）
        with pytest.raises(ConfigError):
            validate_config_dict(_make(serial=" 你的设备序列号 "))
        # 绝不模糊匹配：任何真实值都不可能被误伤
        validate_config_dict(_make(serial="我的设备序列号"))
        validate_config_dict(_make(serial="你的设备序列号2"))
        validate_config_dict(_make(city="演出城市广州"))

    def test_legit_values_pass_including_example_keyword(self, caplog):
        # keyword 的模板示例值「张杰 演唱会」是合法真实搜索词，刻意不入黑名单
        config = _make(
            serial="R5CT10ABC",
            keyword="张杰 演唱会",
            users=["张三"],
            city="北京",
            date="04.06",
            price="1280元",
        )
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            validate_config_dict(config)
        assert not [r for r in caplog.records if r.name == "mobile.config"]

    def test_serial_null_and_non_string_values_skip_check(self):
        # serial=None（单设备自动识别）、非字符串值、users 混入非字符串元素
        # 都不抛 TypeError、不误报
        validate_config_dict(_make(serial=None))
        validate_config_dict(_make(users=["张三", 123]))
        validate_config_dict(_make(price_index=0))

    def test_strict_false_downgrades_to_warning(self, caplog):
        config = _make(serial="你的设备序列号")
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            validate_config_dict(config, strict_placeholders=False)
        # prompt 模式合法起点的契约：不抛，但警告文本含占位符指引
        assert "模板占位符" in caplog.text
        assert "serial" in caplog.text


class TestUnknownDeprecatedKeys:
    """AC2：未知/废弃 key 显式警告但永不失败（正常抢票路径向后兼容）。"""

    def test_unknown_key_warns_never_raises(self, caplog):
        config = _make()
        config["pricce_index"] = 1  # 拼错的 key
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            validate_config_dict(config)
        assert "pricce_index" in caplog.text
        assert "未识别" in caplog.text

    def test_deprecated_udid_and_item_url_warn(self, caplog):
        config = _make()
        config["udid"] = "ABC123"
        config["item_url"] = "https://m.damai.cn/item.html?id=1"
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            validate_config_dict(config)
        assert "udid" in caplog.text
        assert "已废弃，请改用 serial" in caplog.text
        assert "item_url" in caplog.text

    def test_underscore_prefix_treated_as_comment(self, caplog):
        config = _make()
        config["_note"] = "这是用户注释键"
        assert "_note" not in unknown_config_keys(config)
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            validate_config_dict(config)
        assert "_note" not in caplog.text

    def test_known_keys_constant_matches_to_dict(self):
        # schema 漂移守卫：未来新增配置字段忘更 KNOWN_CONFIG_KEYS 会在此失败，
        # 而不是让用户看到假「未识别」警告
        assert KNOWN_CONFIG_KEYS == frozenset(Config(**_VALID).to_dict().keys())

    def test_unknown_config_keys_sorted(self):
        config = {
            "keyword": "x",  # known
            "udid": "ABC",  # deprecated
            "_note": "注释",  # 下划线注释键
            "zzz": 1,
            "aaa": 2,
        }
        assert unknown_config_keys(config) == ["aaa", "zzz"]

    def test_filter_known_config_keys_drops_unknown_and_deprecated(self):
        config = _make()
        config.update({"udid": "ABC", "pricce_index": 1, "_note": "x"})
        filtered = filter_known_config_keys(config)
        assert set(filtered) <= KNOWN_CONFIG_KEYS
        for bad in ("udid", "pricce_index", "_note"):
            assert bad not in filtered


class TestMigrateDeprecated:
    def test_udid_migrated_to_serial(self):
        source = _make(serial=None)
        source["udid"] = "ABC"
        cleaned, warnings = migrate_deprecated_config(source)
        assert cleaned["serial"] == "ABC"
        assert "udid" not in cleaned
        assert any("已自动迁移" in w for w in warnings)
        # 返回副本：输入 dict 未被原地修改
        assert source["udid"] == "ABC"

    def test_serial_wins_when_both_present(self):
        source = _make(serial="NEW")
        source["udid"] = "OLD"
        cleaned, _ = migrate_deprecated_config(source)
        assert cleaned["serial"] == "NEW"
        assert "udid" not in cleaned

    def test_appium_era_keys_removed_with_warnings(self):
        source = _make()
        source.update(
            {
                "server_url": "http://127.0.0.1:4723",
                "device_name": "Android",
                "platform_version": "16",
                "driver_backend": "u2",
            }
        )
        cleaned, warnings = migrate_deprecated_config(source)
        for key in ("server_url", "device_name", "platform_version", "driver_backend"):
            assert key not in cleaned
            assert any(key in w for w in warnings)


class TestLoadConfigGuards:
    """Config.load_config 接入 U-05 防护后的行为契约。"""

    def _write(self, tmp_path, config):
        path = tmp_path / "config.jsonc"
        path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return path

    def test_load_config_placeholder_raises_before_return(self, tmp_path):
        path = self._write(tmp_path, _make(serial="你的设备序列号"))
        # 不传参即触发：锁定 strict_placeholders 默认值为 True
        with pytest.raises(ConfigError):
            Config.load_config(str(path))
        # 异常层级验证：except ValueError 链路可捕获
        with pytest.raises(ValueError):
            Config.load_config(str(path))

    def test_load_config_strict_false_returns_config(self, tmp_path, caplog):
        path = self._write(tmp_path, _make(serial="你的设备序列号"))
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            cfg = Config.load_config(str(path), strict_placeholders=False)
        assert isinstance(cfg, Config)
        assert "模板占位符" in caplog.text

    def test_load_config_udid_fallback_effective(self, tmp_path, caplog):
        config = _make()
        del config["serial"]
        config["udid"] = "XYZ789"
        path = self._write(tmp_path, config)
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            cfg = Config.load_config(str(path))
        # 老用户从「静默丢弃」升级为「回退生效 + 废弃警告」
        assert cfg.serial == "XYZ789"
        assert "已废弃" in caplog.text

    def test_load_config_unknown_key_warns_but_loads(self, tmp_path, caplog):
        config = _make()
        config["kyword"] = "拼错的键"
        path = self._write(tmp_path, config)
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            cfg = Config.load_config(str(path))
        assert isinstance(cfg, Config)
        assert "未识别" in caplog.text


# ---------------------------------------------------------------------------
# U-06 — 注释保留的 JSONC 定点补丁 + 文件锁 + 原子写回
# ---------------------------------------------------------------------------


class TestJsoncPatch:
    def test_patch_probe_flags_on_example_template_only_two_lines_change(self):
        """AC-1 核心：真实模板两向切换，除两行值外全文逐行不变。"""
        original = _EXAMPLE_TEMPLATE.read_text(encoding="utf-8")
        patched = patch_jsonc_text(
            original, {"probe_only": False, "if_commit_order": True}
        )
        orig_lines = original.splitlines()
        new_lines = patched.splitlines()
        assert len(orig_lines) == len(new_lines)
        changed = [
            i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b
        ]
        assert len(changed) == 2
        for i in changed:
            assert '"probe_only"' in orig_lines[i] or '"if_commit_order"' in orig_lines[i]
            # 变化行里仅值 token 翻转，行首缩进与行内其余字节相同
            assert new_lines[i] in (
                orig_lines[i].replace("true", "false"),
                orig_lines[i].replace("false", "true"),
            )
        # 所有注释行（含三态说明里出现 probe_only/if_commit_order 字样的注释、
        # issue #25 警告注释）byte-identical
        for i, line in enumerate(orig_lines):
            if line.lstrip().startswith("//"):
                assert new_lines[i] == line
        # 解析等价：新文本 == 旧字典应用 updates
        expected = json.loads(_strip_jsonc_comments_scanned(original))
        expected.update({"probe_only": False, "if_commit_order": True})
        assert json.loads(_strip_jsonc_comments_scanned(patched)) == expected

    def test_patch_two_way_switch_roundtrip_byte_identical(self):
        original = _EXAMPLE_TEMPLATE.read_text(encoding="utf-8")
        to_commit = patch_jsonc_text(
            original, {"probe_only": False, "if_commit_order": True}
        )
        back = patch_jsonc_text(
            to_commit, {"probe_only": True, "if_commit_order": False}
        )
        # 往返 100% byte-identical：注释、字段顺序、行尾、末尾换行零漂移
        assert back == original
        # 幂等：连续两次施加相同 updates，输出不变
        assert (
            patch_jsonc_text(
                to_commit, {"probe_only": False, "if_commit_order": True}
            )
            == to_commit
        )

    def test_patch_key_only_in_comment_treated_as_missing(self):
        text = '{\n  // "probe_only": true 是说明\n  "keyword": "x"\n}\n'
        patched = patch_jsonc_text(text, {"probe_only": True})
        # 注释行原样保留，键作为缺键追加在最外层 } 前
        assert '// "probe_only": true 是说明' in patched
        parsed = json.loads(_strip_jsonc_comments_scanned(patched))
        assert parsed == {"keyword": "x", "probe_only": True}

    def test_patch_key_inside_string_value_not_touched(self):
        text = '{"keyword": "张杰 probe_only 演唱会", "probe_only": true}'
        patched = patch_jsonc_text(text, {"probe_only": False})
        assert '"keyword": "张杰 probe_only 演唱会"' in patched
        assert json.loads(patched)["probe_only"] is False
        # 含转义引号的字符串值同样不被误改
        tricky = '{"keyword": "含\\"probe_only\\": true字样", "probe_only": true}'
        patched2 = patch_jsonc_text(tricky, {"probe_only": False})
        parsed2 = json.loads(patched2)
        assert parsed2["keyword"] == '含"probe_only": true字样'
        assert parsed2["probe_only"] is False

    def test_patch_preserves_trailing_line_comment_and_comma(self):
        text = '{\n  "rush_mode": true, // 说明文字\n  "price_index": 0 // 兜底索引\n}'
        patched = patch_jsonc_text(text, {"rush_mode": False, "price_index": 3})
        assert '"rush_mode": false, // 说明文字' in patched
        assert '"price_index": 3 // 兜底索引' in patched

    def test_patch_comment_between_colon_and_value(self):
        text = '{ "probe_only": /* 注 */ true }'
        patched = patch_jsonc_text(text, {"probe_only": False})
        assert patched == '{ "probe_only": /* 注 */ false }'

    def test_patch_duplicate_keys_all_replaced(self):
        # 手改畸形配置：同键深度 1 出现两次 → 全部替换，
        # 与 json.loads 取末次的语义对齐，杜绝「改第一处读第二处」错位
        text = '{"probe_only": true, "keyword": "x", "probe_only": true}'
        patched = patch_jsonc_text(text, {"probe_only": False})
        assert patched.count("false") == 2
        assert "true" not in patched
        assert json.loads(_strip_jsonc_comments_scanned(patched))["probe_only"] is False

    def test_patch_nested_same_key_not_touched(self):
        text = '{"probe_only": true, "extra": {"probe_only": true}}'
        patched = patch_jsonc_text(text, {"probe_only": False})
        # 深度==1 守卫：嵌套对象里的同名键不动
        assert '"extra": {"probe_only": true}' in patched
        assert patched.startswith('{"probe_only": false')

    def test_patch_array_object_values_span_replacement(self):
        text = '{\n  "users": ["旧名"], // 观演人\n  "extra": {"a": 1}\n}'
        patched = patch_jsonc_text(text, {"users": ["张三", "李四"], "extra": {}})
        # 数组整段替换 + 行尾注释保留 + ensure_ascii=False 中文不转义
        assert '"users": ["张三", "李四"], // 观演人' in patched
        assert '"extra": {}' in patched
        # 数组元素含 ']' 与 '//' 时配对扫描不被骗
        tricky = '{"users": ["a]b", "c//d"], "probe_only": true}'
        patched2 = patch_jsonc_text(tricky, {"probe_only": False})
        assert '"users": ["a]b", "c//d"]' in patched2
        assert '"probe_only": false' in patched2
        patched3 = patch_jsonc_text(tricky, {"users": ["新"]})
        assert '"users": ["新"]' in patched3
        assert '"probe_only": true' in patched3

    def test_patch_scalar_type_matrix(self):
        text = (
            "{\n"
            '  "target_title": null,\n'
            '  "price_index": 0,\n'
            '  "sell_start_time": "2026-04-06T12:00:00",\n'
            '  "keyword": "say \\"hi\\""\n'
            "}"
        )
        updates = {
            "target_title": "张杰演唱会",  # null → 中文字符串
            "price_index": 6,  # int
            "sell_start_time": None,  # string → null
            "keyword": 'say "bye"',  # 含转义引号字符串
        }
        patched = patch_jsonc_text(text, updates)
        assert '"target_title": "张杰演唱会"' in patched
        assert json.loads(_strip_jsonc_comments_scanned(patched)) == updates

    def test_patch_missing_key_appended_empty_and_nonempty_object(self):
        # 空对象
        patched = patch_jsonc_text("{}", {"probe_only": True})
        assert json.loads(_strip_jsonc_comments_scanned(patched)) == {
            "probe_only": True
        }
        # 非空对象（无尾逗号）：逗号语法正确
        patched2 = patch_jsonc_text('{"a": 1}', {"probe_only": True})
        assert json.loads(_strip_jsonc_comments_scanned(patched2)) == {
            "a": 1,
            "probe_only": True,
        }
        # 同一次 updates 既有替换又有追加
        patched3 = patch_jsonc_text(
            '{"probe_only": false}', {"probe_only": True, "if_commit_order": False}
        )
        assert json.loads(_strip_jsonc_comments_scanned(patched3)) == {
            "probe_only": True,
            "if_commit_order": False,
        }

    def test_patch_crlf_line_endings_preserved(self, tmp_path):
        text = '{\r\n  "probe_only": true,\r\n  "keyword": "x"\r\n}\r\n'
        expected = '{\r\n  "probe_only": false,\r\n  "keyword": "x"\r\n}\r\n'
        assert patch_jsonc_text(text, {"probe_only": False}) == expected
        # 端到端：写回链路（newline='' 读写）同样不重排 CRLF —— 防 Windows
        # 用户 git diff 全文行尾噪音（issue #50 已证明存在 Windows 用户）
        path = tmp_path / "config.jsonc"
        path.write_bytes(text.encode("utf-8"))
        update_config_values({"probe_only": False}, str(path))
        assert path.read_bytes() == expected.encode("utf-8")


class TestVerifyPatched:
    def test_verify_patched_rejects_unparseable_and_wrong_value(self):
        with pytest.raises(ConfigError):
            _verify_patched('{"probe_only": tru', {"probe_only": True})
        with pytest.raises(ConfigError) as excinfo:
            _verify_patched('{"probe_only": false}', {"probe_only": True})
        message = str(excinfo.value)
        assert "probe_only" in message
        assert "True" in message and "False" in message

    def test_verify_patched_string_value_with_double_slash(self):
        # 钉住正则剥注释缺陷：字符串值 "A//B"（非 http:// 形态）不得误报——
        # verify 必须走扫描器版剥注释，而非 config.py 的 (?<!:)// 正则
        _verify_patched('{"target_title": "A//B"}', {"target_title": "A//B"})
        assert json.loads(_strip_jsonc_comments_scanned('{"t": "A//B"}')) == {
            "t": "A//B"
        }


class TestUpdateConfigValues:
    _ORIGINAL = '{\n  // 顶部注释\n  "probe_only": true,\n  "keyword": "x"\n}\n'

    def _write(self, tmp_path):
        path = tmp_path / "config.jsonc"
        path.write_text(self._ORIGINAL, encoding="utf-8")
        return path

    def test_update_config_values_writes_bak_and_logs(self, tmp_path, caplog):
        path = self._write(tmp_path)
        with caplog.at_level(logging.INFO, logger="mobile.config"):
            update_config_values({"probe_only": False}, str(path))
        bak = tmp_path / "config.jsonc.bak"
        # AC-3：.bak 内容 == 写前原文（含注释），且 INFO 日志说明备份路径
        assert bak.read_text(encoding="utf-8") == self._ORIGINAL
        assert "备份" in caplog.text
        assert str(bak) in caplog.text
        new_text = path.read_text(encoding="utf-8")
        assert "// 顶部注释" in new_text
        assert '"probe_only": false' in new_text

    def test_update_config_values_returns_previous_and_new(self, tmp_path):
        path = self._write(tmp_path)
        previous, new = update_config_values(
            {"probe_only": False, "if_commit_order": True}, str(path)
        )
        # previous 仅含文件中原本存在的键（缺键不出现，调用方决定缺省语义）
        assert previous == {"probe_only": True}
        assert new == {"probe_only": False, "if_commit_order": True}

    def test_update_config_values_verify_failure_leaves_file_untouched(
        self, tmp_path, monkeypatch
    ):
        path = self._write(tmp_path)
        monkeypatch.setattr(
            "mobile.config.patch_jsonc_text", lambda text, updates: "垃圾{{{"
        )
        with pytest.raises(ConfigError):
            update_config_values({"probe_only": False}, str(path))
        # 一个字节不落盘；备份在 verify 之后，不生成 .bak；无 tmp 残留
        assert path.read_text(encoding="utf-8") == self._ORIGINAL
        assert not (tmp_path / "config.jsonc.bak").exists()
        assert not list(tmp_path.glob("*.tmp"))

    def test_atomic_write_no_tmp_leftover_on_exception(self, tmp_path, monkeypatch):
        import os

        path = self._write(tmp_path)

        def _boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            update_config_values({"probe_only": False}, str(path))
        assert path.read_text(encoding="utf-8") == self._ORIGINAL
        assert not list(tmp_path.glob("*.tmp"))

    def test_update_config_values_env_override_path_sidecars_follow(
        self, tmp_path, monkeypatch
    ):
        local = tmp_path / "config.local.jsonc"
        local.write_text('{"probe_only": true}\n', encoding="utf-8")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", str(local))
        update_config_values({"probe_only": False})
        # 写回、.bak、.lock 全部跟随实际写入路径生成
        assert json.loads(local.read_text(encoding="utf-8"))["probe_only"] is False
        assert (tmp_path / "config.local.jsonc.bak").exists()
        assert (tmp_path / "config.local.jsonc.lock").exists()

    def test_update_config_values_missing_file_raises_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            update_config_values(
                {"probe_only": True}, str(tmp_path / "nope.jsonc")
            )
        assert list(tmp_path.iterdir()) == []


class TestWriteLock:
    def test_write_lock_blocks_concurrent_flock(self, tmp_path):
        # 确定性并发测试（不靠 sleep）：flock 以 open file description 为粒度，
        # 同进程第二个 fd 的 LOCK_NB 尝试即可验证互斥
        fcntl = pytest.importorskip("fcntl")
        from mobile.config import _config_write_lock

        path = tmp_path / "config.jsonc"
        path.write_text("{}", encoding="utf-8")
        lock_path = str(path) + ".lock"
        with _config_write_lock(str(path)):
            with open(lock_path, "a+") as other:
                with pytest.raises(OSError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # 退出上下文后可再次获取
        with open(lock_path, "a+") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_UN)

    def test_lock_sidecar_inode_stable_across_replaces(self, tmp_path):
        import os

        path = tmp_path / "config.jsonc"
        path.write_text('{"probe_only": true}', encoding="utf-8")
        lock_path = str(path) + ".lock"
        update_config_values({"probe_only": False}, str(path))
        inode_first = os.stat(lock_path).st_ino
        update_config_values({"probe_only": True}, str(path))
        # 锁文件本体永不被 os.replace 换掉 → 锁语义稳定
        assert os.stat(lock_path).st_ino == inode_first

    def test_lock_degrades_to_noop_with_warning(self, tmp_path, monkeypatch, caplog):
        from unittest.mock import Mock

        path = tmp_path / "config.jsonc"
        path.write_text('{"probe_only": true}', encoding="utf-8")
        # 三级降级末端：fcntl / msvcrt 都不可用 → no-op + warning，功能不阻断
        monkeypatch.setattr("mobile.config.fcntl", None)
        monkeypatch.setattr("mobile.config.msvcrt", None)
        with caplog.at_level(logging.WARNING, logger="mobile.config"):
            update_config_values({"probe_only": False}, str(path))
        assert "不支持文件锁" in caplog.text
        assert json.loads(path.read_text(encoding="utf-8"))["probe_only"] is False
        # msvcrt 分支（mac/Linux CI 物理不可达）：fake 注入覆盖加解锁调用
        fake_msvcrt = Mock()
        fake_msvcrt.LK_LOCK = 2
        fake_msvcrt.LK_UNLCK = 0
        monkeypatch.setattr("mobile.config.msvcrt", fake_msvcrt)
        update_config_values({"probe_only": True}, str(path))
        assert fake_msvcrt.locking.call_count == 2

    @pytest.mark.slow
    def test_concurrent_update_runtime_mode_no_corruption(self, tmp_path):
        """AC-4：两进程并发模式改写——最终状态为合法组合之一，无交叉损坏。"""
        import os
        import subprocess
        import sys

        path = tmp_path / "config.jsonc"
        path.write_text(
            json.dumps(_make(probe_only=True, if_commit_order=False)),
            encoding="utf-8",
        )
        worker_code = (
            "import sys\n"
            "from mobile.config import update_runtime_mode\n"
            "probe = sys.argv[2] == 'probe'\n"
            "for _ in range(20):\n"
            "    if probe:\n"
            "        update_runtime_mode(True, False, sys.argv[1])\n"
            "    else:\n"
            "        update_runtime_mode(False, True, sys.argv[1])\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_ROOT)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", worker_code, str(path), side],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for side in ("probe", "commit")
        ]
        for proc in procs:
            assert proc.wait(timeout=60) == 0, proc.stderr.read().decode()
        # 文件可完整解析，两键值属于两个合法组合之一，绝无半截文件
        loaded = load_config_dict(str(path))
        assert (loaded["probe_only"], loaded["if_commit_order"]) in {
            (True, False),
            (False, True),
        }
        # .bak 也可解析；目录无 *.tmp 残留
        load_config_dict(str(path) + ".bak")
        assert not list(tmp_path.glob("*.tmp"))


class TestUpdateRuntimeModeContract:
    """U-06 改造后 update_runtime_mode 的契约回归（新增部分）。

    既有 test_update_runtime_mode_writes_probe_flags / submit_flags
    一字未改仍在上方 TestMobileConfigLoadConfig 中运行。
    """

    def test_update_runtime_mode_previous_defaults_match_old_semantics(
        self, tmp_path
    ):
        # 缺键时与旧实现逐位对齐：probe_only 缺省 False、if_commit_order 缺省 None
        path = tmp_path / "config.jsonc"
        path.write_text('{"keyword": "x"}', encoding="utf-8")
        previous, updated = update_runtime_mode(True, False, str(path))
        assert previous == {"probe_only": False, "if_commit_order": None}
        assert updated == {"probe_only": True, "if_commit_order": False}
        loaded = load_config_dict(str(path))
        assert loaded["probe_only"] is True
        assert loaded["if_commit_order"] is False

    def test_update_runtime_mode_rejects_non_bool_before_any_io(self, tmp_path):
        path = tmp_path / "config.jsonc"
        path.write_text('{"probe_only": true}', encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        with pytest.raises(ValueError):
            update_runtime_mode("true", False, str(path))
        with pytest.raises(ValueError):
            update_runtime_mode(True, "false", str(path))
        # 参数校验先于任何 IO：文件未变、无 .bak/.lock 副作用
        assert path.read_text(encoding="utf-8") == before
        assert not (tmp_path / "config.jsonc.bak").exists()
        assert not (tmp_path / "config.jsonc.lock").exists()

    def test_update_runtime_mode_on_template_preserves_comments(self, tmp_path):
        # AC-1 端到端版：走完整「锁 + 备份 + 原子写」链路后注释/顺序仍保留
        import shutil

        target = tmp_path / "config.jsonc"
        shutil.copyfile(_EXAMPLE_TEMPLATE, target)
        original = target.read_text(encoding="utf-8")
        update_runtime_mode(False, True, str(target))
        new_text = target.read_text(encoding="utf-8")
        orig_lines = original.splitlines()
        new_lines = new_text.splitlines()
        assert len(orig_lines) == len(new_lines)
        changed = [
            i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b
        ]
        assert len(changed) == 2
        # issue #25 警告注释等安全注释长存文件内
        assert "issue #25" in new_text
        assert (tmp_path / "config.jsonc.bak").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# U-05/U-06 — 入口脚本与 .gitignore 的文本回归护栏
# ---------------------------------------------------------------------------


def test_start_script_uses_ephemeral_mode_and_syntax_ok():
    import subprocess

    script = _REPO_ROOT / "mobile" / "scripts" / "start_ticket_grabbing.sh"
    text = script.read_text(encoding="utf-8")
    assert "HATICKETS_RUN_MODE" in text
    assert "HATICKETS_COMMIT_AUTH" in text
    assert "update_runtime_mode" not in text
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr.decode()


def test_gitignore_covers_bak_and_lock():
    text = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "mobile/*.jsonc.bak" in text
    assert "mobile/*.jsonc.lock" in text


# ---------------------------------------------------------------------------
# U-12 — HATICKETS_SERIAL 环境变量覆盖 serial（--serial 的单一透传通道）
# ---------------------------------------------------------------------------


class TestSerialEnvOverride:
    """Config.load_config 的 serial 覆盖：环境变量非空时优先于文件值。"""

    def _write_config(self, tmp_path, serial="filedev", include_serial=True):
        config_data = {
            "keyword": "test",
            "users": ["A"],
            "city": "北京",
            "date": "01.01",
            "price": "100元",
            "price_index": 0,
            "if_commit_order": False,
        }
        if include_serial:
            config_data["serial"] = serial
        path = tmp_path / "config.jsonc"
        path.write_text(json.dumps(config_data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_env_serial_overrides_file_serial(self, tmp_path, monkeypatch):
        path = self._write_config(tmp_path, serial="filedev")
        monkeypatch.setenv("HATICKETS_SERIAL", "envdev")
        assert Config.load_config(path).serial == "envdev"

    def test_env_serial_blank_falls_back_to_file(self, tmp_path, monkeypatch):
        """空/纯空白环境变量视为未设置（与 HATICKETS_CONFIG_PATH 口径一致）。"""
        path = self._write_config(tmp_path, serial="filedev")
        monkeypatch.setenv("HATICKETS_SERIAL", "   ")
        assert Config.load_config(path).serial == "filedev"

    def test_env_serial_unset_behavior_unchanged(self, tmp_path, monkeypatch):
        """回归锚：环境变量未设置时行为与现状逐字节一致。"""
        monkeypatch.delenv("HATICKETS_SERIAL", raising=False)
        path = self._write_config(tmp_path, serial="filedev")
        assert Config.load_config(path).serial == "filedev"
        # 文件无 serial 字段 → serial is None
        (tmp_path / "no_serial").mkdir()
        path2 = self._write_config(tmp_path / "no_serial", include_serial=False)
        assert Config.load_config(path2).serial is None

    def test_env_serial_works_when_file_has_no_serial(self, tmp_path, monkeypatch):
        path = self._write_config(tmp_path, include_serial=False)
        monkeypatch.setenv("HATICKETS_SERIAL", "envdev")
        assert Config.load_config(path).serial == "envdev"

    def test_env_serial_whitespace_stripped(self, tmp_path, monkeypatch):
        """经过 Config.__init__ 既有 strip/非空校验路径，不绕过校验。"""
        path = self._write_config(tmp_path, serial="filedev")
        monkeypatch.setenv("HATICKETS_SERIAL", " dev1 ")
        assert Config.load_config(path).serial == "dev1"


class TestProcessRuntimeModeOverride:
    """P0：模式是本次进程能力，不通过共享配置文件持久化。"""

    def _write_config(self, tmp_path, *, probe_only, if_commit_order):
        path = tmp_path / "config.jsonc"
        path.write_text(
            json.dumps(
                _make(
                    probe_only=probe_only,
                    if_commit_order=if_commit_order,
                    target_title="周深演唱会深圳站",
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_probe_env_forces_safe_mode_over_stale_commit_config(
        self, tmp_path, monkeypatch
    ):
        path = self._write_config(
            tmp_path, probe_only=False, if_commit_order=True
        )
        before = path.read_bytes()
        monkeypatch.setenv("HATICKETS_RUN_MODE", "probe")

        cfg = Config.load_config(path)

        assert cfg.probe_only is True
        assert cfg.if_commit_order is False
        assert path.read_bytes() == before

    def test_validation_env_never_commits(self, tmp_path, monkeypatch):
        path = self._write_config(
            tmp_path, probe_only=True, if_commit_order=True
        )
        monkeypatch.setenv("HATICKETS_RUN_MODE", "validation")

        cfg = Config.load_config(path)

        assert cfg.probe_only is False
        assert cfg.if_commit_order is False

    def test_commit_env_requires_second_authorization(self, tmp_path, monkeypatch):
        path = self._write_config(
            tmp_path, probe_only=True, if_commit_order=False
        )
        monkeypatch.setenv("HATICKETS_RUN_MODE", "commit")
        monkeypatch.delenv("HATICKETS_COMMIT_AUTH", raising=False)

        with pytest.raises(ConfigError, match="正式提交授权缺失"):
            Config.load_config(path)

    def test_commit_env_authorizes_only_loaded_process_config(
        self, tmp_path, monkeypatch
    ):
        path = self._write_config(
            tmp_path, probe_only=True, if_commit_order=False
        )
        before = path.read_bytes()
        monkeypatch.setenv("HATICKETS_RUN_MODE", "commit")
        monkeypatch.setenv(
            "HATICKETS_COMMIT_AUTH", "confirmed-by-start-script"
        )

        cfg = Config.load_config(path)

        assert cfg.probe_only is False
        assert cfg.if_commit_order is True
        assert path.read_bytes() == before

    @pytest.mark.parametrize("field,value", [
        ("target_title", None),
        ("target_title", "  "),
        ("date", ""),
        ("price", None),
    ])
    def test_commit_env_requires_exact_non_empty_target_fields(
        self, tmp_path, monkeypatch, field, value
    ):
        path = self._write_config(
            tmp_path, probe_only=True, if_commit_order=False
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        data[field] = value
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("HATICKETS_RUN_MODE", "commit")
        monkeypatch.setenv(
            "HATICKETS_COMMIT_AUTH", "confirmed-by-start-script"
        )

        with pytest.raises(ConfigError, match=field):
            Config.load_config(path)

    def test_invalid_runtime_mode_fails_closed(self, tmp_path, monkeypatch):
        path = self._write_config(
            tmp_path, probe_only=True, if_commit_order=False
        )
        monkeypatch.setenv("HATICKETS_RUN_MODE", "typo")

        with pytest.raises(ConfigError, match="probe/validation/commit"):
            Config.load_config(path)
