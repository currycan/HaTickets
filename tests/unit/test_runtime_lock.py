# -*- coding: UTF-8 -*-
"""Per-device runtime lock and CLI integration regression tests."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import mobile.damai_app.__main__ as main_mod
import mobile.damai_app.runtime_lock as lock_mod
from mobile.config import Config
from mobile.damai_app.run_report import (
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_INTERRUPTED,
    EXIT_LOCK_CONFLICT,
    EXIT_SUCCESS,
)
from mobile.damai_app.runtime_lock import (
    DeviceInstanceLock,
    resolve_effective_serial,
    resolve_physical_device_identity,
)
from mobile.damai_app.submit_journal import (
    SubmitJournal,
    SubmitJournalIdentityUnavailable,
)


def _fake_bot(*, commit=False, run_result=True, run_side_effect=None):
    bot = Mock()
    bot.driver = Mock()
    bot.config = Mock(serial="device-a", if_commit_order=commit)
    bot._terminal_failure_reason = None
    bot._last_run_outcome = "probe_ready"
    bot._attempts_made = 1
    bot._execution_mode_key = Mock(return_value="commit" if commit else "probe")
    if run_side_effect is not None:
        bot.run_with_retry.side_effect = run_side_effect
    else:
        bot.run_with_retry.return_value = run_result
    return bot


@pytest.fixture(autouse=True)
def _clean_runtime_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        main_mod,
        "resolve_physical_device_identity",
        lambda serial: f"physical::{serial}" if serial else None,
    )
    for key in (
        "HATICKETS_SERIAL",
        "HATICKETS_RESULT_JSON",
        main_mod.RUN_MODE_ENV_VAR,
        main_mod.COMMIT_AUTH_ENV_VAR,
    ):
        monkeypatch.delenv(key, raising=False)


class TestResolveEffectiveSerial:
    def test_environment_override_wins_and_is_stripped(self):
        loader = Mock(return_value={"serial": "config-device"})
        serial = resolve_effective_serial(
            loader, {"HATICKETS_SERIAL": "  cli-device  "}
        )
        assert serial == "cli-device"
        loader.assert_not_called()

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            ({"serial": " dev-1 "}, "dev-1"),
            ({"udid": "legacy-1"}, "legacy-1"),
            ({"serial": " ", "udid": "legacy-1"}, None),
            ({"serial": 123, "udid": None}, None),
            ({}, None),
        ],
    )
    def test_config_and_global_fallback(self, config, expected):
        assert resolve_effective_serial(lambda: config, {}) == expected

    def test_config_error_falls_back_to_global(self):
        def fail():
            raise ValueError("invalid json")

        assert resolve_effective_serial(fail, {}) is None


class TestResolvePhysicalDeviceIdentity:
    @staticmethod
    def _runner(values):
        def run(command, **_kwargs):
            key = tuple(command[-2:])
            return SimpleNamespace(
                returncode=0,
                stdout=values.get(key, "") + "\n",
                stderr="",
            )

        return run

    def test_usb_and_wireless_routes_share_hardware_serial(self):
        runner = self._runner({("getprop", "ro.serialno"): "PHONE-ABC"})

        usb = resolve_physical_device_identity("USB-ROUTE", runner=runner)
        wifi = resolve_physical_device_identity(
            "192.168.1.20:5555",
            runner=runner,
        )

        assert usb == wifi == "PHONE-ABC"

    def test_missing_ro_serial_does_not_try_alternate_identity_sources(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="\n", stderr="")

        assert (
            resolve_physical_device_identity(
                "10.0.0.8:5555",
                runner=runner,
            )
            is None
        )
        assert calls == [
            [
                "adb",
                "-s",
                "10.0.0.8:5555",
                "shell",
                "getprop",
                "ro.serialno",
            ]
        ]

    def test_missing_stable_identity_returns_none(self):
        runner = self._runner(
            {
                ("getprop", "ro.serialno"): "unknown",
            }
        )
        assert (
            resolve_physical_device_identity(
                "192.168.1.20:5555",
                runner=runner,
            )
            is None
        )

    @pytest.mark.skipif(lock_mod._fcntl is None, reason="POSIX flock unavailable")
    def test_mixed_source_usb_wifi_falls_back_to_global_exclusion(
        self,
        tmp_path,
    ):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            transport = command[2]
            output = "PHONE-ABC\n" if transport == "USB-ROUTE" else "\n"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        usb = resolve_physical_device_identity(
            "USB-ROUTE",
            runner=runner,
        )
        wifi = resolve_physical_device_identity(
            "10.0.0.8:5555",
            runner=runner,
        )

        assert usb == "PHONE-ABC"
        assert wifi is None
        assert all(
            command[-2:] == ["getprop", "ro.serialno"]
            for command in calls
        )
        usb_lock = DeviceInstanceLock(usb, lock_dir=tmp_path)
        wifi_lock = DeviceInstanceLock(wifi, lock_dir=tmp_path)
        try:
            assert usb_lock.acquire() is True
            assert wifi_lock.acquire() is False
        finally:
            usb_lock.release()
            wifi_lock.release()


class TestDeviceInstanceLock:
    @pytest.mark.skipif(lock_mod._fcntl is None, reason="POSIX flock unavailable")
    def test_same_device_conflicts_non_blocking_then_releases(self, tmp_path):
        # The transports differ, but both resolvers yielded the same physical
        # identity before these locks were constructed.
        first = DeviceInstanceLock("PHONE-A", lock_dir=tmp_path)
        second = DeviceInstanceLock("PHONE-A", lock_dir=tmp_path)

        assert first.acquire() is True
        assert second.acquire() is False
        assert second.acquired is False

        first.release()
        assert second.acquire() is True
        second.release()

    @pytest.mark.skipif(lock_mod._fcntl is None, reason="POSIX flock unavailable")
    def test_different_devices_can_run_together(self, tmp_path):
        first = DeviceInstanceLock("device-a", lock_dir=tmp_path)
        second = DeviceInstanceLock("device-b", lock_dir=tmp_path)
        try:
            assert first.acquire() is True
            assert second.acquire() is True
            assert first.path != second.path
        finally:
            first.release()
            second.release()

    @pytest.mark.skipif(lock_mod._fcntl is None, reason="POSIX flock unavailable")
    def test_unresolved_identity_excludes_every_known_device(self, tmp_path):
        unresolved = DeviceInstanceLock(None, lock_dir=tmp_path)
        known = DeviceInstanceLock("PHONE-A", lock_dir=tmp_path)
        try:
            assert unresolved.acquire() is True
            assert known.acquire() is False
        finally:
            unresolved.release()
            known.release()

        try:
            assert known.acquire() is True
            assert unresolved.acquire() is False
        finally:
            known.release()
            unresolved.release()

    def test_no_serial_uses_stable_global_key_without_leaking_serial(self, tmp_path):
        lock = DeviceInstanceLock(None, lock_dir=tmp_path, backend=None)
        assert lock.key == "global"
        assert "global" not in lock.path.name
        with pytest.raises(RuntimeError, match="reliable_instance_lock_unavailable"):
            lock.acquire()
        lock.release()
        lock.release()  # idempotent

    def test_backend_can_be_injected(self, tmp_path):
        backend = Mock(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4, LOCK_SH=8)
        lock = DeviceInstanceLock("device-a", lock_dir=tmp_path, backend=backend)
        assert lock.acquire() is True
        lock.release()
        assert backend.flock.call_count == 4
        assert backend.flock.call_args_list[0].args[1] == 10
        assert backend.flock.call_args_list[1].args[1] == 3
        assert backend.flock.call_args_list[2].args[1] == 4
        assert backend.flock.call_args_list[3].args[1] == 4


class TestMainLockIntegration:
    def test_main_binds_lock_and_journal_to_same_physical_identity(
        self,
        tmp_path,
    ):
        lock = Mock()
        lock.acquire.return_value = True
        lock_factory = Mock(return_value=lock)
        bot = _fake_bot()
        observed = {}

        def bot_factory():
            journal = SubmitJournal(
                "device-a",
                state_dir=tmp_path / "state",
            )
            observed["physical_device_id"] = journal.physical_device_id
            return bot

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(
                main_mod,
                "resolve_physical_device_identity",
                return_value="PHONE-PHYSICAL-A",
            ),
            patch.object(main_mod, "DeviceInstanceLock", lock_factory),
            patch.object(main_mod, "DamaiBot", side_effect=bot_factory),
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "physical.json")]
            )

        assert rc == EXIT_SUCCESS
        lock_factory.assert_called_once_with("PHONE-PHYSICAL-A")
        assert observed["physical_device_id"] == "PHONE-PHYSICAL-A"

    def test_unresolved_identity_uses_global_lock_and_enforces_journal_failure(
        self,
        tmp_path,
    ):
        lock = Mock()
        lock.acquire.return_value = True
        lock_factory = Mock(return_value=lock)
        bot = _fake_bot()
        observed = {}

        def bot_factory():
            journal = SubmitJournal(
                "192.168.1.20:5555",
                state_dir=tmp_path / "state",
            )
            observed["physical_device_id"] = journal.physical_device_id
            observed["journal"] = journal
            return bot

        with (
            patch.object(
                main_mod,
                "resolve_effective_serial",
                return_value="192.168.1.20:5555",
            ),
            patch.object(
                main_mod,
                "resolve_physical_device_identity",
                return_value=None,
            ),
            patch.object(main_mod, "DeviceInstanceLock", lock_factory),
            patch.object(main_mod, "DamaiBot", side_effect=bot_factory),
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "unknown.json")]
            )

        assert rc == EXIT_SUCCESS
        lock_factory.assert_called_once_with(None)
        assert observed["physical_device_id"] is None
        with pytest.raises(SubmitJournalIdentityUnavailable, match="物理设备身份"):
            observed["journal"].load_active()

    @pytest.mark.parametrize("probe_only", [True, False])
    def test_unknown_identity_allows_noncommit_probe_and_validation_flow(
        self,
        probe_only,
    ):
        config = Config(
            serial="10.0.0.8:5555",
            keyword="测试演出",
            target_title="测试演出",
            target_venue="测试场馆",
            users=["UserA"],
            city="上海",
            date="2026-08-01",
            price="880元",
            price_index=1,
            if_commit_order=False,
            probe_only=probe_only,
        )
        bot = main_mod.DamaiBot(config=config, setup_driver=False)

        with patch.object(
            bot,
            "run_ticket_grabbing",
            return_value=True,
        ) as run_flow:
            assert bot.run_with_retry(max_retries=1) is True

        run_flow.assert_called_once()
        assert bot._terminal_failure_reason is None

    def test_unknown_identity_blocks_formal_before_ticket_flow(self):
        config = Config(
            serial="10.0.0.8:5555",
            keyword="测试演出",
            target_title="测试演出",
            target_venue="测试场馆",
            users=["UserA"],
            city="上海",
            date="2026-08-01",
            price="880元",
            price_index=1,
            if_commit_order=True,
            probe_only=False,
        )
        bot = main_mod.DamaiBot(config=config, setup_driver=False)

        with patch.object(bot, "run_ticket_grabbing") as run_flow:
            assert bot.run_with_retry(max_retries=1) is False

        run_flow.assert_not_called()
        assert (
            bot._terminal_failure_reason
            == "submit_journal_identity_unavailable"
        )

    def test_conflict_returns_13_writes_summary_and_never_builds_bot(self, tmp_path):
        result_path = tmp_path / "run.json"
        lock = Mock()
        lock.acquire.return_value = False
        factory = Mock()

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot", factory),
        ):
            rc = main_mod.main(["--result-json", str(result_path)])

        assert rc == EXIT_LOCK_CONFLICT
        factory.assert_not_called()
        lock.release.assert_called_once()
        summary = json.loads(result_path.read_text(encoding="utf-8"))
        assert summary["exit_code"] == EXIT_LOCK_CONFLICT
        assert summary["outcome"] == "instance_lock_conflict"
        assert summary["terminal_reason"] == "instance_lock_conflict"
        assert summary["serial"] == "device-a"

    def test_lock_wraps_bot_init_run_and_driver_cleanup(self, tmp_path):
        events = []
        lock = Mock()
        lock.acquire.side_effect = lambda: events.append("lock") or True
        lock.release.side_effect = lambda: events.append("release")
        bot = _fake_bot()
        bot.run_with_retry.side_effect = lambda **_kwargs: events.append("run") or True
        bot.driver.quit.side_effect = lambda: events.append("quit")

        def bot_factory():
            events.append("init")
            return bot

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot", side_effect=bot_factory),
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "lifecycle.json")]
            )

        assert rc == EXIT_SUCCESS
        assert events == ["lock", "init", "run", "quit", "release"]

    def test_keyboard_interrupt_releases_lock(self, tmp_path):
        lock = Mock()
        lock.acquire.return_value = True
        bot = _fake_bot(run_side_effect=KeyboardInterrupt())

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot", return_value=bot),
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "interrupted.json")]
            )

        assert rc == EXIT_INTERRUPTED
        bot.driver.quit.assert_called_once()
        lock.release.assert_called_once()

    def test_keyboard_interrupt_during_lock_acquire_releases_lock(self, tmp_path):
        lock = Mock()
        lock.acquire.side_effect = KeyboardInterrupt()

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot") as factory,
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "lock-interrupted.json")]
            )

        assert rc == EXIT_INTERRUPTED
        factory.assert_not_called()
        lock.release.assert_called_once()


class TestCommitAuthorization:
    @pytest.mark.parametrize(
        ("run_mode", "commit_auth"),
        [
            (None, None),
            ("commit", None),
            ("probe", main_mod.COMMIT_AUTH_VALUE),
            ("commit", "wrong-token"),
        ],
    )
    def test_commit_fails_closed_without_both_authorizers(
        self, tmp_path, monkeypatch, run_mode, commit_auth
    ):
        if run_mode is not None:
            monkeypatch.setenv(main_mod.RUN_MODE_ENV_VAR, run_mode)
        if commit_auth is not None:
            monkeypatch.setenv(main_mod.COMMIT_AUTH_ENV_VAR, commit_auth)

        lock = Mock()
        lock.acquire.return_value = True
        bot = _fake_bot(commit=True)
        result_path = tmp_path / "commit.json"
        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot", return_value=bot),
        ):
            rc = main_mod.main(["--result-json", str(result_path)])

        assert rc == EXIT_CONFIG_OR_DEVICE_ERROR
        bot.run_with_retry.assert_not_called()
        lock.release.assert_called_once()
        summary = json.loads(result_path.read_text(encoding="utf-8"))
        assert summary["outcome"] == "config_or_device_error"
        assert summary["terminal_reason"] == "commit_authorization_missing"

    def test_commit_runs_with_start_script_authorization(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(main_mod.RUN_MODE_ENV_VAR, "commit")
        monkeypatch.setenv(
            main_mod.COMMIT_AUTH_ENV_VAR, main_mod.COMMIT_AUTH_VALUE
        )
        lock = Mock()
        lock.acquire.return_value = True
        bot = _fake_bot(commit=True)

        with (
            patch.object(main_mod, "resolve_effective_serial", return_value="device-a"),
            patch.object(main_mod, "DeviceInstanceLock", return_value=lock),
            patch.object(main_mod, "DamaiBot", return_value=bot),
        ):
            rc = main_mod.main(
                ["--result-json", str(tmp_path / "authorized.json")]
            )

        assert rc == EXIT_SUCCESS
        bot.run_with_retry.assert_called_once_with(max_retries=3)
