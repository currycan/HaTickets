"""Durable submit-intent journal safety and privacy tests."""

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from mobile.config import Config
from mobile.damai_app.submit_journal import (
    ActiveSubmitIntentError,
    SubmitJournal,
    SubmitJournalIdentityUnavailable,
    SubmitJournalUnavailable,
    bind_process_device_identity,
    main,
    reset_process_device_identity,
)


def _config(**overrides):
    values = {
        "serial": "DEVICE-SECRET-123",
        "keyword": "秘密演出",
        "target_title": "秘密演出 上海站",
        "target_venue": "秘密体育馆",
        "users": ["张三", "李四"],
        "city": "上海",
        "date": "2026-08-01",
        "price": "秘密票档 880元",
        "price_index": 2,
        "if_commit_order": True,
        "probe_only": False,
    }
    values.update(overrides)
    return Config(**values)


def _journal(tmp_path, serial="DEVICE-SECRET-123"):
    return SubmitJournal(
        serial,
        state_dir=tmp_path / "state",
        physical_device_id=serial,
    )


class TestSubmitJournalLifecycle:
    def test_identity_is_required_for_formal_journal(self, tmp_path):
        journal = _journal(tmp_path, serial=None)
        with pytest.raises(SubmitJournalIdentityUnavailable):
            journal.load_active()
        with pytest.raises(SubmitJournalIdentityUnavailable):
            journal.arm(config=_config())

    def test_arm_is_durable_private_and_active(self, tmp_path):
        journal = _journal(tmp_path)

        record = journal.arm(config=_config())

        assert record["state"] == "ARMED"
        assert record["click_attempts"] == 0
        assert journal.load_active()["intent_id"] == record["intent_id"]
        assert stat.S_IMODE(journal.active_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(journal.active_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(journal.key_path.stat().st_mode) == 0o600
        assert "DEVICE-SECRET-123" not in str(journal.active_path)

        all_bytes = b"".join(
            path.read_bytes()
            for path in (journal.active_path, journal.key_path)
        )
        for secret in (
            "DEVICE-SECRET-123",
            "秘密演出",
            "秘密体育馆",
            "秘密票档",
            "张三",
            "李四",
        ):
            assert secret.encode("utf-8") not in all_bytes

    def test_second_arm_is_always_blocked_even_for_different_target(self, tmp_path):
        journal = _journal(tmp_path)
        journal.arm(config=_config())

        with pytest.raises(ActiveSubmitIntentError):
            journal.arm(config=_config(keyword="完全不同演出", users=["王五"]))

    def test_click_and_observation_states_never_clear_active(self, tmp_path):
        journal = _journal(tmp_path)
        armed = journal.arm(config=_config())

        clicked = journal.mark_click_dispatched(armed["intent_id"])
        observed = journal.record_observation(clicked["intent_id"], "success")

        assert clicked["state"] == "CLICK_DISPATCHED"
        assert clicked["click_attempts"] == 1
        assert observed["state"] == "VERIFIED_SUCCESS"
        assert journal.active_path.exists()
        assert journal.summary() == {
            "intent_id": armed["intent_id"],
            "state": "VERIFIED_SUCCESS",
        }

    @pytest.mark.parametrize(
        "observation,state",
        [
            ("existing_order", "ORDER_OBSERVED"),
            ("sold_out", "REJECTED_OBSERVED"),
            ("captcha", "MANUAL_VERIFICATION"),
            ("timeout", "RESULT_UNKNOWN"),
            ("unexpected", "RESULT_UNKNOWN"),
        ],
    )
    def test_observation_mapping(self, tmp_path, observation, state):
        journal = _journal(tmp_path)
        armed = journal.arm(config=_config())

        record = journal.record_observation(armed["intent_id"], observation)

        assert record["state"] == state
        assert record["click_attempts"] == 1
        assert journal.active_path.exists()

    def test_result_state_cannot_be_overwritten_or_rearmed(self, tmp_path):
        journal = _journal(tmp_path)
        armed = journal.arm(config=_config())
        journal.record_observation(armed["intent_id"], "success")

        with pytest.raises(SubmitJournalUnavailable, match="状态禁止"):
            journal.record_observation(armed["intent_id"], "timeout")
        with pytest.raises(ActiveSubmitIntentError):
            journal.arm(config=_config())

    def test_acknowledge_archives_instead_of_deleting_evidence(self, tmp_path):
        journal = _journal(tmp_path)
        armed = journal.arm(config=_config())
        journal.mark_click_dispatched(armed["intent_id"])

        archive = journal.acknowledge(armed["intent_id"], "paid")

        assert not journal.active_path.exists()
        archived = json.loads(archive.read_text(encoding="utf-8"))
        assert archived["state"] == "ACKNOWLEDGED"
        assert archived["resolution"] == "paid"
        assert archived["intent_id"] == armed["intent_id"]
        assert journal.load_active() is None

    def test_acknowledge_rejects_wrong_id_and_resolution(self, tmp_path):
        journal = _journal(tmp_path)
        armed = journal.arm(config=_config())

        with pytest.raises(ValueError):
            journal.acknowledge(armed["intent_id"], "force")
        with pytest.raises(ActiveSubmitIntentError):
            journal.acknowledge(
                "00000000-0000-4000-8000-000000000000",
                "confirmed-no-order",
            )
        assert journal.active_path.exists()


class TestSubmitJournalPhysicalIdentity:
    def test_different_transports_share_one_active_journal(self, tmp_path):
        state_dir = tmp_path / "state"
        physical_id = "PHONE-PHYSICAL-ABC"
        usb = SubmitJournal(
            "USB-ROUTE",
            state_dir=state_dir,
            physical_device_id=physical_id,
        )
        wifi = SubmitJournal(
            "192.168.1.20:5555",
            state_dir=state_dir,
            physical_device_id=physical_id,
        )

        armed = usb.arm(config=_config())

        assert usb.active_path == wifi.active_path
        assert wifi.load_active()["intent_id"] == armed["intent_id"]
        with pytest.raises(ActiveSubmitIntentError):
            wifi.arm(config=_config())

    def test_cross_transport_arm_race_has_exactly_one_winner(self, tmp_path):
        state_dir = tmp_path / "state"
        physical_id = "PHONE-PHYSICAL-RACE"
        journals = (
            SubmitJournal(
                "USB-ROUTE",
                state_dir=state_dir,
                physical_device_id=physical_id,
            ),
            SubmitJournal(
                "10.0.0.8:5555",
                state_dir=state_dir,
                physical_device_id=physical_id,
            ),
        )
        barrier = threading.Barrier(2)

        def arm(journal):
            barrier.wait()
            try:
                journal.arm(config=_config())
                return "armed"
            except ActiveSubmitIntentError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(arm, journals))

        assert sorted(outcomes) == ["armed", "blocked"]
        assert journals[0].active_path == journals[1].active_path

    def test_process_binding_maps_transport_to_canonical_identity(self, tmp_path):
        token = bind_process_device_identity("USB-ROUTE", "PHONE-ABC")
        try:
            journal = SubmitJournal(
                "USB-ROUTE",
                state_dir=tmp_path / "state",
            )
        finally:
            reset_process_device_identity(token)

        assert journal.physical_device_id == "PHONE-ABC"

    def test_separate_process_bindings_share_journal_across_transports(
        self,
        tmp_path,
    ):
        state_dir = tmp_path / "state"
        usb_token = bind_process_device_identity("USB-ROUTE", "PHONE-ABC")
        try:
            usb = SubmitJournal("USB-ROUTE", state_dir=state_dir)
            armed = usb.arm(config=_config())
        finally:
            reset_process_device_identity(usb_token)

        wifi_token = bind_process_device_identity(
            "192.168.1.20:5555",
            "PHONE-ABC",
        )
        try:
            wifi = SubmitJournal(
                "192.168.1.20:5555",
                state_dir=state_dir,
            )
        finally:
            reset_process_device_identity(wifi_token)

        assert wifi.active_path == usb.active_path
        assert wifi.load_active()["intent_id"] == armed["intent_id"]
        with pytest.raises(ActiveSubmitIntentError):
            wifi.arm(config=_config())

    @pytest.mark.parametrize(
        "serial",
        ["USB-ROUTE", "192.168.1.20:5555"],
    )
    def test_raw_transport_without_physical_proof_fails_closed(
        self,
        tmp_path,
        serial,
    ):
        journal = SubmitJournal(
            serial,
            state_dir=tmp_path / "state",
        )
        with pytest.raises(SubmitJournalIdentityUnavailable):
            journal.load_active()
        with pytest.raises(SubmitJournalIdentityUnavailable):
            journal.arm(config=_config())

    def test_mixed_source_wifi_unknown_cannot_create_second_intent(
        self,
        tmp_path,
    ):
        state_dir = tmp_path / "state"
        usb_token = bind_process_device_identity("USB-ROUTE", "PHONE-ABC")
        try:
            usb = SubmitJournal("USB-ROUTE", state_dir=state_dir)
            usb.arm(config=_config())
        finally:
            reset_process_device_identity(usb_token)

        wifi_token = bind_process_device_identity(
            "10.0.0.8:5555",
            None,
        )
        try:
            wifi = SubmitJournal(
                "10.0.0.8:5555",
                state_dir=state_dir,
            )
        finally:
            reset_process_device_identity(wifi_token)

        assert wifi.physical_device_id is None
        with pytest.raises(SubmitJournalIdentityUnavailable):
            wifi.load_active()
        with pytest.raises(SubmitJournalIdentityUnavailable):
            wifi.arm(config=_config())


class TestSubmitJournalFailClosed:
    def test_corrupt_active_file_blocks_instead_of_being_replaced(self, tmp_path):
        journal = _journal(tmp_path)
        journal.active_dir.mkdir(parents=True)
        journal.active_path.write_text("{broken", encoding="utf-8")
        before = journal.active_path.read_bytes()

        with pytest.raises(SubmitJournalUnavailable, match="损坏"):
            journal.load_active()
        with pytest.raises(SubmitJournalUnavailable):
            journal.arm(config=_config())
        assert journal.active_path.read_bytes() == before

    def test_unknown_schema_blocks(self, tmp_path):
        journal = _journal(tmp_path)
        journal.active_dir.mkdir(parents=True)
        journal.active_path.write_text(
            json.dumps({"schema_version": 99}), encoding="utf-8"
        )

        with pytest.raises(SubmitJournalUnavailable, match="schema"):
            journal.load_active()

    def test_invalid_key_blocks_fingerprint_and_arm(self, tmp_path):
        journal = _journal(tmp_path)
        journal.state_dir.mkdir(parents=True)
        journal.key_path.write_bytes(b"short")

        with pytest.raises(SubmitJournalUnavailable, match="长度异常"):
            journal.arm(config=_config())
        assert not journal.active_path.exists()

    def test_active_path_as_directory_fails_closed(self, tmp_path):
        journal = _journal(tmp_path)
        journal.active_path.mkdir(parents=True)

        with pytest.raises(SubmitJournalUnavailable):
            journal.load_active()


class TestSubmitFingerprint:
    def test_nfkc_and_outer_whitespace_are_stable(self, tmp_path):
        first = _journal(tmp_path)
        fp1 = first.fingerprint(
            _config(
                keyword=" ＡＢＣ ",
                target_title=None,
                users=[" 张三 "],
            )
        )
        fp2 = first.fingerprint(
            _config(
                keyword="ABC",
                target_title=None,
                users=["张三"],
            )
        )
        assert fp1 == fp2

    @pytest.mark.parametrize(
        "change",
        [
            {"target_title": "另一个演出"},
            {"target_venue": "另一个场馆"},
            {"city": "北京"},
            {"date": "2026-08-02"},
            {"price": "1280元"},
            {"price_index": 3},
            {"users": ["李四", "张三"]},
        ],
    )
    def test_sensitive_target_change_changes_fingerprint(self, tmp_path, change):
        journal = _journal(tmp_path)
        assert journal.fingerprint(_config()) != journal.fingerprint(_config(**change))

    def test_fingerprint_key_is_local_and_random(self, tmp_path):
        first = _journal(tmp_path / "one")
        second = _journal(tmp_path / "two")
        cfg = _config()

        assert first.fingerprint(cfg) != second.fingerprint(cfg)
        assert len(first.key_path.read_bytes()) == 32
        assert first.key_path.read_bytes() != second.key_path.read_bytes()


class TestSubmitJournalCli:
    def test_status_is_read_only_and_minimal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HATICKETS_STATE_DIR", str(tmp_path / "state"))
        journal = SubmitJournal(
            "DEVICE-SECRET-123",
            physical_device_id="DEVICE-SECRET-123",
        )
        armed = journal.arm(config=_config())

        with patch(
            "mobile.damai_app.runtime_lock.resolve_physical_device_identity",
            return_value="DEVICE-SECRET-123",
        ):
            assert main(["status", "--serial", "DEVICE-SECRET-123"]) == 0

        shown = json.loads(capsys.readouterr().out)
        assert shown == {"intent_id": armed["intent_id"], "state": "ARMED"}
        assert journal.active_path.exists()

    def test_acknowledge_rejects_non_tty_without_removing_active(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HATICKETS_STATE_DIR", str(tmp_path / "state"))
        journal = SubmitJournal(
            "DEVICE-SECRET-123",
            physical_device_id="DEVICE-SECRET-123",
        )
        armed = journal.arm(config=_config())

        with (
            patch(
                "mobile.damai_app.runtime_lock.resolve_physical_device_identity",
                return_value="DEVICE-SECRET-123",
            ),
            patch("sys.stdin.isatty", return_value=False),
        ):
            rc = main(
                [
                    "acknowledge",
                    "--serial",
                    "DEVICE-SECRET-123",
                    "--intent-id",
                    armed["intent_id"],
                    "--resolution",
                    "paid",
                ]
            )

        assert rc == 2
        assert journal.active_path.exists()

    def test_acknowledge_requires_full_typed_id_and_archives(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HATICKETS_STATE_DIR", str(tmp_path / "state"))
        journal = SubmitJournal(
            "DEVICE-SECRET-123",
            physical_device_id="DEVICE-SECRET-123",
        )
        armed = journal.arm(config=_config())
        argv = [
            "acknowledge",
            "--serial",
            "DEVICE-SECRET-123",
            "--intent-id",
            armed["intent_id"],
            "--resolution",
            "confirmed-no-order",
        ]

        with patch(
            "mobile.damai_app.runtime_lock.resolve_physical_device_identity",
            return_value="DEVICE-SECRET-123",
        ):
            with patch("sys.stdin.isatty", return_value=True), patch(
                "builtins.input",
                return_value="wrong",
            ):
                assert main(argv) == 2
        assert journal.active_path.exists()

        with patch(
            "mobile.damai_app.runtime_lock.resolve_physical_device_identity",
            return_value="DEVICE-SECRET-123",
        ):
            with patch("sys.stdin.isatty", return_value=True), patch(
                "builtins.input",
                return_value=armed["intent_id"],
            ):
                assert main(argv) == 0
        assert not journal.active_path.exists()
        assert list(journal.archive_root.rglob(f"*_{armed['intent_id']}.json"))
