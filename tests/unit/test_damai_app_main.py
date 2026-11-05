# -*- coding: UTF-8 -*-
"""Unit tests for mobile/damai_app/__main__.py — 语义化退出码 + 运行摘要（U-12）。

注意 patch 目标：__main__.py 顶部是 ``from . import DamaiBot``，名字已被
重绑定进 ``mobile.damai_app.__main__`` 命名空间——main() 用例必须 patch
``mobile.damai_app.__main__.DamaiBot``；patch ``mobile.damai_app.DamaiBot``
对 main() 无效。唯一例外是 runpy 用例：runpy 会重新执行模块顶部的
from-import，此时才 patch ``mobile.damai_app.DamaiBot``。
"""

import json
import runpy
import sys
from unittest.mock import Mock, patch

import pytest

import mobile.damai_app.__main__ as main_mod
from mobile.config import ConfigError
from mobile.damai_app.run_report import (
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_INTERRUPTED,
    EXIT_RETRIES_EXHAUSTED,
    EXIT_SUCCESS,
    EXIT_TERMINAL_FAILURE,
    RUN_SUMMARY_SCHEMA_VERSION,
)
from uiautomator2.exceptions import ConnectError

SUMMARY_KEYS = {
    "schema_version",
    "outcome",
    "exit_code",
    "serial",
    "mode",
    "attempts",
    "duration_ms",
    "terminal_reason",
    "submit_intent",
    "started_at",
    "finished_at",
}


def _make_fake_bot(
    run_result=True,
    run_side_effect=None,
    terminal_reason=None,
    outcome="order_submitted",
    attempts=1,
    mode="probe",
    serial="emulator-5554",
):
    """FakeBot：可配置 run_with_retry 行为与摘要相关属性，零真机依赖。"""
    bot = Mock()
    bot.driver = Mock()
    bot._terminal_failure_reason = terminal_reason
    bot._last_run_outcome = outcome
    bot._attempts_made = attempts
    bot._execution_mode_key = Mock(return_value=mode)
    bot.config = Mock()
    bot.config.serial = serial
    if run_side_effect is not None:
        bot.run_with_retry = Mock(side_effect=run_side_effect)
    else:
        bot.run_with_retry = Mock(return_value=run_result)
    return bot


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """默认摘要路径相对 cwd——所有用例 chdir 到 tmp_path，避免污染仓库。"""
    monkeypatch.chdir(tmp_path)


def _read_summary(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestExitCodeMapping:
    def test_main_returns_0_on_success(self, tmp_path):
        bot = _make_fake_bot(run_result=True, outcome="order_submitted")
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_SUCCESS
        summary = _read_summary(result_path)
        assert summary["outcome"] == "order_submitted"
        assert summary["exit_code"] == EXIT_SUCCESS

    def test_main_returns_0_success_outcome_fallback(self, tmp_path):
        bot = _make_fake_bot(run_result=True, outcome=None)
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_SUCCESS
        assert _read_summary(result_path)["outcome"] == "success"

    def test_main_returns_10_when_retries_exhausted(self, tmp_path):
        bot = _make_fake_bot(run_result=False, terminal_reason=None, outcome=None)
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_RETRIES_EXHAUSTED
        summary = _read_summary(result_path)
        assert summary["outcome"] == "retries_exhausted"
        assert summary["terminal_reason"] is None

    @pytest.mark.parametrize(
        "reason",
        ["sold_out", "session_invalid", "attendee_unselected", "submit_unverified"],
    )
    def test_main_returns_11_on_terminal_failure(self, tmp_path, reason):
        bot = _make_fake_bot(run_result=False, terminal_reason=reason, outcome=None)
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_TERMINAL_FAILURE
        summary = _read_summary(result_path)
        assert summary["outcome"] == "terminal_failure"
        assert summary["terminal_reason"] == reason

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("bad config"),
            ConfigError("占位符未填"),
            RuntimeError("boom"),
            KeyError("users"),  # 回归点：旧代码只捕 (ValueError, RuntimeError)
            FileNotFoundError("config.jsonc"),
            ConnectError("no device"),
        ],
    )
    def test_main_returns_12_on_init_exception(self, tmp_path, exc):
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", side_effect=exc):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_CONFIG_OR_DEVICE_ERROR
        summary = _read_summary(result_path)
        assert summary["outcome"] == "config_or_device_error"
        assert summary["terminal_reason"].startswith("init_error:")
        assert summary["attempts"] == 0
        assert summary["mode"] is None

    def test_main_returns_12_on_runtime_exception(self, tmp_path):
        bot = _make_fake_bot(run_side_effect=Exception("device gone"), outcome=None)
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_CONFIG_OR_DEVICE_ERROR
        reason = _read_summary(result_path)["terminal_reason"]
        assert reason.startswith("run_error:")
        assert "device gone" in reason

    def test_main_returns_130_on_keyboard_interrupt(self, tmp_path):
        bot = _make_fake_bot(run_side_effect=KeyboardInterrupt(), outcome=None)
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_INTERRUPTED
        summary = _read_summary(result_path)
        assert summary["outcome"] == "interrupted"
        assert summary["terminal_reason"] == "keyboard_interrupt"
        assert summary["exit_code"] == EXIT_INTERRUPTED


class TestSerialFlag:
    def test_serial_flag_sets_env_before_bot_init(self, tmp_path, monkeypatch):
        """时序守卫：环境变量必须在 DamaiBot() 之前写入才能被 load_config 消费。"""
        import os

        seen = {}

        def factory():
            seen["serial_at_init"] = os.environ.get("HATICKETS_SERIAL")
            return _make_fake_bot()

        with patch.object(main_mod, "DamaiBot", side_effect=factory):
            rc = main_mod.main(
                ["--serial", "emulator-5554", "--result-json", str(tmp_path / "r.json")]
            )
        assert rc == EXIT_SUCCESS
        assert seen["serial_at_init"] == "emulator-5554"

    def test_serial_flag_strips_whitespace(self, tmp_path, monkeypatch):
        import os

        bot = _make_fake_bot()
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            main_mod.main(
                ["--serial", "  dev1  ", "--result-json", str(tmp_path / "r.json")]
            )
        assert os.environ.get("HATICKETS_SERIAL") == "dev1"

    def test_serial_flag_blank_exits_12_without_starting_bot(self, monkeypatch):
        import os

        monkeypatch.delenv("HATICKETS_SERIAL", raising=False)
        factory = Mock()
        with patch.object(main_mod, "DamaiBot", factory):
            rc = main_mod.main(["--serial", "   "])
        assert rc == EXIT_CONFIG_OR_DEVICE_ERROR
        factory.assert_not_called()
        assert "HATICKETS_SERIAL" not in os.environ


class TestRunSummary:
    def test_result_json_flag_writes_full_schema(self, tmp_path):
        bot = _make_fake_bot(
            run_result=True, outcome="probe_ready", attempts=3, mode="probe"
        )
        result_path = tmp_path / "a" / "b" / "run.json"  # 父目录不存在，须自动创建
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert result_path.exists()
        summary = _read_summary(result_path)
        assert set(summary) == SUMMARY_KEYS
        assert summary["schema_version"] == RUN_SUMMARY_SCHEMA_VERSION
        assert summary["exit_code"] == rc == EXIT_SUCCESS
        assert summary["outcome"] == "probe_ready"
        assert summary["mode"] == "probe"
        assert summary["attempts"] == 3
        assert summary["serial"] == "emulator-5554"
        assert isinstance(summary["duration_ms"], int)
        assert summary["duration_ms"] >= 0

    def test_result_json_env_var_fallback(self, tmp_path, monkeypatch):
        result_path = tmp_path / "env_run.json"
        monkeypatch.setenv("HATICKETS_RESULT_JSON", str(result_path))
        bot = _make_fake_bot()
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            main_mod.main([])
        assert result_path.exists()

    def test_result_json_cli_overrides_env(self, tmp_path, monkeypatch):
        env_path = tmp_path / "env_run.json"
        cli_path = tmp_path / "cli_run.json"
        monkeypatch.setenv("HATICKETS_RESULT_JSON", str(env_path))
        bot = _make_fake_bot()
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            main_mod.main(["--result-json", str(cli_path)])
        assert cli_path.exists()
        assert not env_path.exists()

    def test_default_result_path_relative_cwd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HATICKETS_RESULT_JSON", raising=False)
        bot = _make_fake_bot()
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            main_mod.main([])
        assert (tmp_path / "tmp" / "run_summary.json").exists()

    def test_summary_on_init_failure_serial_from_env(self, tmp_path, monkeypatch):
        """bot=None 时 serial 从 HATICKETS_SERIAL 兜底，字段齐全无 KeyError。"""
        monkeypatch.setenv("HATICKETS_SERIAL", "dev9")
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", side_effect=RuntimeError("boom")):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_CONFIG_OR_DEVICE_ERROR
        summary = _read_summary(result_path)
        assert set(summary) == SUMMARY_KEYS
        assert summary["serial"] == "dev9"

    def test_summary_write_failure_keeps_exit_code(self, tmp_path):
        """摘要目标目录不可创建（父级是普通文件）→ 退出码不受影响。"""
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a dir", encoding="utf-8")
        result_path = blocker / "x.json"

        bot_ok = _make_fake_bot(run_result=True)
        with patch.object(main_mod, "DamaiBot", return_value=bot_ok):
            assert main_mod.main(["--result-json", str(result_path)]) == EXIT_SUCCESS

        bot_fail = _make_fake_bot(
            run_result=False, terminal_reason="sold_out", outcome=None
        )
        with patch.object(main_mod, "DamaiBot", return_value=bot_fail):
            assert (
                main_mod.main(["--result-json", str(result_path)])
                == EXIT_TERMINAL_FAILURE
            )

    def test_summary_assembly_exception_does_not_mask_exit_code(self, tmp_path):
        """哨兵用例：finally 内摘要组装抛异常不能吞掉 return 的退出码。"""
        bot = _make_fake_bot(run_result=True)
        bot._execution_mode_key = Mock(side_effect=AttributeError("no mode"))
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(tmp_path / "run.json")])
        assert rc == EXIT_SUCCESS


class TestDriverCleanup:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"run_result": True},
            {"run_side_effect": Exception("device gone")},
        ],
    )
    def test_driver_quit_called_in_finally(self, tmp_path, kwargs):
        bot = _make_fake_bot(**kwargs)
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            main_mod.main(["--result-json", str(tmp_path / "run.json")])
        bot.driver.quit.assert_called_once()

    def test_driver_quit_exception_swallowed(self, tmp_path):
        bot = _make_fake_bot(run_result=True)
        bot.driver.quit.side_effect = Exception("quit failed")
        result_path = tmp_path / "run.json"
        with patch.object(main_mod, "DamaiBot", return_value=bot):
            rc = main_mod.main(["--result-json", str(result_path)])
        assert rc == EXIT_SUCCESS
        assert result_path.exists()


class TestModuleEntry:
    def test_dunder_main_uses_sys_exit(self, tmp_path, monkeypatch):
        """守护『模块底部 sys.exit(main())』——否则退出码全链路失效。

        runpy 会重新执行 __main__ 模块顶部的 ``from . import DamaiBot``，
        所以此处（且仅此处）patch 的是包命名空间 mobile.damai_app.DamaiBot。
        """
        result_path = tmp_path / "run.json"
        monkeypatch.setenv("HATICKETS_RESULT_JSON", str(result_path))
        monkeypatch.setattr(sys, "argv", ["damai_app"])
        bot = _make_fake_bot(run_result=False, terminal_reason=None, outcome=None)
        with patch("mobile.damai_app.DamaiBot", return_value=bot):
            with pytest.raises(SystemExit) as excinfo:
                runpy.run_module("mobile.damai_app.__main__", run_name="__main__")
        assert excinfo.value.code == EXIT_RETRIES_EXHAUSTED
        assert result_path.exists()
