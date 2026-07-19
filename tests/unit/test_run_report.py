# -*- coding: UTF-8 -*-
"""Unit tests for mobile/damai_app/run_report.py — 退出码常量契约 + 摘要原子落盘（U-12）。"""

import json

import pytest

from mobile.damai_app import logger as damai_logger
from mobile.damai_app.run_report import (
    DEFAULT_RESULT_JSON,
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_INTERRUPTED,
    EXIT_LOCK_CONFLICT,
    EXIT_RETRIES_EXHAUSTED,
    EXIT_SUCCESS,
    EXIT_TERMINAL_FAILURE,
    RESULT_JSON_ENV_VAR,
    RUN_SUMMARY_SCHEMA_VERSION,
    write_run_summary,
)


@pytest.fixture(autouse=True)
def _enable_logger_propagation():
    """Enable propagation on the damai_app logger so caplog can capture messages."""
    damai_logger.propagate = True
    yield
    damai_logger.propagate = False


class TestExitCodeContract:
    def test_exit_code_constants_frozen(self):
        """契约测试：退出码是 systemd/cron 的外部 API，改动必须先撞这条。"""
        assert EXIT_SUCCESS == 0
        assert EXIT_RETRIES_EXHAUSTED == 10
        assert EXIT_TERMINAL_FAILURE == 11
        assert EXIT_CONFIG_OR_DEVICE_ERROR == 12
        assert EXIT_LOCK_CONFLICT == 13
        assert EXIT_INTERRUPTED == 130
        assert RUN_SUMMARY_SCHEMA_VERSION == 2
        assert RESULT_JSON_ENV_VAR == "HATICKETS_RESULT_JSON"
        assert DEFAULT_RESULT_JSON == "tmp/run_summary.json"


class TestWriteRunSummary:
    def test_write_run_summary_creates_parents_and_returns_true(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "run.json"
        assert write_run_summary(path, {"outcome": "probe_ready"}) is True
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "outcome": "probe_ready"
        }

    def test_write_run_summary_utf8_not_ascii_escaped(self, tmp_path):
        path = tmp_path / "run.json"
        assert (
            write_run_summary(path, {"terminal_reason": "票档已售罄"}) is True
        )
        raw = path.read_text(encoding="utf-8")
        assert "票档已售罄" in raw  # ensure_ascii=False
        assert raw.endswith("\n")

    def test_write_run_summary_atomic_no_part_leftover(self, tmp_path):
        path = tmp_path / "run.json"
        assert write_run_summary(path, {"attempt": 1}) is True
        assert write_run_summary(path, {"attempt": 2}) is True
        # os.replace 覆盖语义：第二次内容生效
        assert json.loads(path.read_text(encoding="utf-8")) == {"attempt": 2}
        # 无 *.part 残留
        assert list(tmp_path.glob("*.part")) == []

    def test_write_run_summary_failure_returns_false_logs_warning(
        self, tmp_path, caplog
    ):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a dir", encoding="utf-8")
        path = blocker / "x.json"  # 父级是普通文件 → mkdir 必失败
        with caplog.at_level("WARNING", logger="mobile.damai_app"):
            assert write_run_summary(path, {"outcome": "x"}) is False
        assert "run summary 写入失败" in caplog.text

    def test_write_run_summary_unserializable_returns_false(self, tmp_path):
        path = tmp_path / "run.json"
        # set() 不可 JSON 序列化 → json.dumps 失败也走兜底，不抛异常
        assert write_run_summary(path, {"bad": {1, 2, 3}}) is False
        assert not path.exists()
