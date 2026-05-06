# -*- coding: UTF-8 -*-
"""Tests for mobile.env_snapshot."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mobile.env_snapshot import _run_adb_pm_dump, detect_damai_app_version


# ---------------------------------------------------------------------------
# detect_damai_app_version (uses _runner injection — no subprocess)
# ---------------------------------------------------------------------------


class TestDetectDamaiAppVersion:
    def test_returns_version_when_pm_dump_contains_version_name(self):
        runner = MagicMock(
            return_value=(
                "Packages:\n"
                "  Package [cn.damai]:\n"
                "    versionName=10.6.1\n"
                "    versionCode=10060100\n"
            )
        )
        assert detect_damai_app_version(_runner=runner) == "10.6.1"
        runner.assert_called_once_with(serial=None, package="cn.damai")

    def test_passes_serial_through_to_runner(self):
        runner = MagicMock(return_value="versionName=9.5.0")
        detect_damai_app_version(serial="emulator-5554", _runner=runner)
        runner.assert_called_once_with(serial="emulator-5554", package="cn.damai")

    def test_returns_none_when_runner_returns_none(self):
        runner = MagicMock(return_value=None)
        assert detect_damai_app_version(_runner=runner) is None

    def test_returns_none_when_runner_returns_empty_string(self):
        runner = MagicMock(return_value="")
        assert detect_damai_app_version(_runner=runner) is None

    def test_returns_none_when_no_version_name_line(self):
        runner = MagicMock(return_value="Packages:\n  No matching packages\n")
        assert detect_damai_app_version(_runner=runner) is None

    def test_returns_none_when_version_name_value_is_empty(self):
        runner = MagicMock(return_value="versionName=\nversionCode=1")
        assert detect_damai_app_version(_runner=runner) is None

    def test_picks_first_version_name_when_multiple_present(self):
        runner = MagicMock(return_value="versionName=10.6.1\nversionName=9.0.0")
        assert detect_damai_app_version(_runner=runner) == "10.6.1"

    def test_custom_package_is_forwarded(self):
        runner = MagicMock(return_value="versionName=1.0.0")
        detect_damai_app_version(package="com.other.app", _runner=runner)
        runner.assert_called_once_with(serial=None, package="com.other.app")


# ---------------------------------------------------------------------------
# _run_adb_pm_dump — real-ish behavior with mocked subprocess
#
# NOTE: Avoid Python 3.10's parenthesised ``with (..., ...)`` syntax to keep
# the suite parseable on the project's Python 3.8 baseline.
# ---------------------------------------------------------------------------


class TestRunAdbPmDump:
    def test_returns_none_when_adb_not_on_path(self):
        with patch("mobile.env_snapshot.shutil.which", return_value=None):
            assert _run_adb_pm_dump() is None

    def test_calls_subprocess_run_with_expected_command(self):
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=0, stdout="versionName=1.2.3", stderr=""
        )
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "mobile.env_snapshot.subprocess.run", return_value=completed
            ) as mock_run:
                assert _run_adb_pm_dump(serial=None) == "versionName=1.2.3"
        cmd = mock_run.call_args.args[0]
        assert cmd == ["adb", "shell", "pm", "dump", "cn.damai"]

    def test_includes_serial_flag_when_provided(self):
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=0, stdout="ok", stderr=""
        )
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "mobile.env_snapshot.subprocess.run", return_value=completed
            ) as mock_run:
                _run_adb_pm_dump(serial="dev1")
        cmd = mock_run.call_args.args[0]
        assert cmd == ["adb", "-s", "dev1", "shell", "pm", "dump", "cn.damai"]

    def test_returns_none_on_non_zero_exit(self):
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=1, stdout="", stderr="error"
        )
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch("mobile.env_snapshot.subprocess.run", return_value=completed):
                assert _run_adb_pm_dump() is None

    def test_returns_none_on_subprocess_timeout(self):
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "mobile.env_snapshot.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=3.0),
            ):
                assert _run_adb_pm_dump() is None

    def test_returns_none_on_oserror(self):
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch(
                "mobile.env_snapshot.subprocess.run",
                side_effect=FileNotFoundError("adb missing at runtime"),
            ):
                assert _run_adb_pm_dump() is None

    def test_returns_none_when_stdout_is_empty(self):
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=0, stdout="", stderr=""
        )
        with patch("mobile.env_snapshot.shutil.which", return_value="/usr/bin/adb"):
            with patch("mobile.env_snapshot.subprocess.run", return_value=completed):
                assert _run_adb_pm_dump() is None


@pytest.mark.unit
def test_detect_damai_app_version_smoke_with_real_runner_path_missing(monkeypatch):
    """End-to-end smoke: with adb not on PATH, the public API returns None."""
    monkeypatch.setattr("mobile.env_snapshot.shutil.which", lambda _name: None)
    assert detect_damai_app_version() is None
