# -*- coding: UTF-8 -*-
"""Durable, privacy-preserving submit-intent journal.

The journal is the crash boundary around the single money-moving UI action:
an active record is fsynced before the submit click and is never cleared
automatically.  A later process may observe order state read-only, but it must
not issue another submit click until the operator explicitly acknowledges the
record.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

try:  # macOS / Linux
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is below
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


SCHEMA_VERSION = 1
STATE_DIR_ENV_VAR = "HATICKETS_STATE_DIR"
ALLOWED_RESOLUTIONS = frozenset({"paid", "cancelled", "confirmed-no-order"})
OBSERVATION_STATES = {
    "success": "VERIFIED_SUCCESS",
    "existing_order": "ORDER_OBSERVED",
    "sold_out": "REJECTED_OBSERVED",
    "captcha": "MANUAL_VERIFICATION",
    "timeout": "RESULT_UNKNOWN",
}
_KNOWN_STATES = frozenset(
    {
        "ARMED",
        "CLICK_DISPATCHED",
        "VERIFIED_SUCCESS",
        "ORDER_OBSERVED",
        "REJECTED_OBSERVED",
        "MANUAL_VERIFICATION",
        "RESULT_UNKNOWN",
    }
)
_UNSET = object()
_NO_PROCESS_BINDING = object()
_PROCESS_DEVICE_BINDING = ContextVar(
    "hatickets_process_device_binding",
    default=_NO_PROCESS_BINDING,
)


class SubmitJournalError(RuntimeError):
    """Base class for fail-closed journal errors."""


class ActiveSubmitIntentError(SubmitJournalError):
    """A device already has an unacknowledged submit intent."""


class SubmitJournalUnavailable(SubmitJournalError):
    """The journal cannot safely establish or read its durable state."""


class SubmitJournalIdentityUnavailable(SubmitJournalUnavailable):
    """A formal submit cannot be scoped to a confirmed physical device."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _normalise_text(value):
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _normalise_serial(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalise_physical_device_id(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFKC", value).strip()
    if (
        not value
        or len(value) > 256
        or "\n" in value
        or "\r" in value
        or any(ord(char) < 32 for char in value)
        or value.casefold() in {"unknown", "null", "none", "n/a", "<unknown>"}
    ):
        return None
    return value


def bind_process_device_identity(transport_serial, physical_device_id):
    """Bind journals created in this run to the pre-lock physical identity.

    The binding is context-local, not environment-controlled, so an inherited
    or user-supplied environment variable cannot redirect money-moving state to
    another journal.  ``physical_device_id=None`` is an intentional enforced
    failure state: formal submission remains disabled instead of falling back
    to an IP transport route.
    """
    binding = (
        _normalise_serial(transport_serial),
        _normalise_physical_device_id(physical_device_id),
    )
    return _PROCESS_DEVICE_BINDING.set(binding)


def reset_process_device_identity(token):
    """Restore the previous process identity binding."""
    _PROCESS_DEVICE_BINDING.reset(token)


def _default_state_dir():
    override = os.environ.get(STATE_DIR_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HaTickets" / "state"
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI only
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return root / "HaTickets" / "state"
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    return root / "hatickets"


def _device_key(physical_device_id):
    physical_device_id = _normalise_physical_device_id(physical_device_id)
    if physical_device_id is None:
        raise SubmitJournalIdentityUnavailable(
            "正式提交无法确认物理设备身份，已停止以避免跨 transport 重复下单"
        )
    return hashlib.sha256(physical_device_id.encode("utf-8")).hexdigest()[:24]


def _fingerprint_payload(config):
    users = getattr(config, "users", None) or []
    return {
        "app_package": _normalise_text(getattr(config, "app_package", "")),
        "target": _normalise_text(
            getattr(config, "target_title", None)
            or getattr(config, "keyword", "")
        ),
        "target_venue": _normalise_text(
            getattr(config, "target_venue", "")
        ),
        "city": _normalise_text(getattr(config, "city", "")),
        "date": _normalise_text(getattr(config, "date", "")),
        "price": _normalise_text(getattr(config, "price", "")),
        "price_index": getattr(config, "price_index", None),
        "users": [_normalise_text(user) for user in users],
        "attendee_count": len(users),
    }


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError as exc:
        raise SubmitJournalUnavailable(f"无法设置 journal 权限: {exc}") from exc


class SubmitJournal:
    """One active durable submit intent per physical Android device."""

    def __init__(
        self,
        serial,
        state_dir=None,
        *,
        physical_device_id=_UNSET,
    ):
        self.serial = _normalise_serial(serial)
        self.state_dir = (
            Path(state_dir).expanduser()
            if state_dir is not None
            else _default_state_dir()
        )

        if physical_device_id is not _UNSET:
            resolved_identity = _normalise_physical_device_id(
                physical_device_id
            )
        else:
            binding = _PROCESS_DEVICE_BINDING.get()
            if binding is not _NO_PROCESS_BINDING:
                bound_transport, bound_identity = binding
                if (
                    bound_transport is not None
                    and self.serial is not None
                    and bound_transport != self.serial
                ):
                    # A different raw route appeared after the canonical
                    # identity was established.  The orchestrator will stop a
                    # formal reconnect; the journal independently fails closed.
                    resolved_identity = None
                else:
                    resolved_identity = bound_identity
            else:
                # A raw ADB route is never trusted as a physical identity,
                # including USB-looking serials.  Formal callers must arrive
                # through the launcher-installed binding or pass an explicitly
                # resolved canonical physical ID.
                resolved_identity = None

        self.physical_device_id = resolved_identity
        self._device_key = (
            _device_key(self.physical_device_id)
            if self.physical_device_id is not None
            else None
        )

    @property
    def device_key(self):
        if self._device_key is None:
            raise SubmitJournalIdentityUnavailable(
                "正式提交无法确认物理设备身份，已停止以避免跨 transport 重复下单"
            )
        return self._device_key

    @property
    def active_dir(self):
        return self.state_dir / "submit-intents" / "active"

    @property
    def archive_root(self):
        return self.state_dir / "submit-intents" / "archive"

    @property
    def active_path(self):
        return self.active_dir / f"device-{self.device_key}.json"

    @property
    def lock_path(self):
        return self.state_dir / "submit-intents" / f"device-{self.device_key}.lock"

    @property
    def key_path(self):
        return self.state_dir / "fingerprint.key"

    def _ensure_dirs(self):
        for path in (
            self.state_dir,
            self.state_dir / "submit-intents",
            self.active_dir,
            self.archive_root,
        ):
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                _safe_chmod(path, 0o700)
            except OSError as exc:
                raise SubmitJournalUnavailable(
                    f"无法创建 submit journal 目录: {exc}"
                ) from exc

    @contextmanager
    def _locked(self):
        self._ensure_dirs()
        lock_file = None
        try:
            lock_file = self.lock_path.open("a+b")
            _safe_chmod(self.lock_path, 0o600)
        except (OSError, SubmitJournalError) as exc:
            if lock_file is not None:
                lock_file.close()
            if isinstance(exc, SubmitJournalError):
                raise
            raise SubmitJournalUnavailable(f"无法打开 journal 锁: {exc}") from exc
        try:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - unsupported platform must fail closed
                raise SubmitJournalUnavailable("当前平台不支持可靠的 journal 文件锁")
            yield
        except OSError as exc:
            raise SubmitJournalUnavailable(f"journal 文件锁失败: {exc}") from exc
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            lock_file.close()

    @staticmethod
    def _fsync_dir(path):
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise SubmitJournalUnavailable(f"无法打开 journal 目录做 fsync: {exc}") from exc
        try:
            os.fsync(fd)
        except OSError as exc:
            raise SubmitJournalUnavailable(f"journal 目录 fsync 失败: {exc}") from exc
        finally:
            os.close(fd)

    def _write_atomic(self, path, payload):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _safe_chmod(path.parent, 0o700)
        raw = _canonical_json(payload) + b"\n"
        fd = None
        tmp_path = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            tmp_path = Path(tmp_name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, path)
            tmp_path = None
            _safe_chmod(path, 0o600)
            self._fsync_dir(path.parent)
        except SubmitJournalError:
            raise
        except OSError as exc:
            raise SubmitJournalUnavailable(f"submit journal 持久化失败: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _load_unlocked(self):
        path = self.active_path
        if not path.exists():
            return None
        try:
            _safe_chmod(path, 0o600)
            with path.open("r", encoding="utf-8") as stream:
                record = json.load(stream)
        except SubmitJournalError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise SubmitJournalUnavailable(
                "active submit journal 损坏或不可读；已禁止自动提交"
            ) from exc
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise SubmitJournalUnavailable(
                "active submit journal schema 未知；已禁止自动提交"
            )
        if record.get("device_key") != self.device_key:
            raise SubmitJournalUnavailable(
                "active submit journal 设备标识不一致；已禁止自动提交"
            )
        if record.get("state") not in _KNOWN_STATES:
            raise SubmitJournalUnavailable(
                "active submit journal 状态未知；已禁止自动提交"
            )
        intent_id = record.get("intent_id")
        try:
            uuid.UUID(str(intent_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SubmitJournalUnavailable(
                "active submit journal intent_id 无效；已禁止自动提交"
            ) from exc
        return record

    def load_active(self):
        """Return the active record; any malformed existing file fails closed."""
        # Do not create local state for a device with no prior journal merely by
        # running probe/validation.  Formal mode calls this only after identity
        # has been confirmed.
        if not self.active_path.exists():
            return None
        with self._locked():
            return self._load_unlocked()

    def _load_or_create_key(self):
        self._ensure_dirs()
        if not self.key_path.exists():
            fd = None
            tmp_path = None
            installed = False
            try:
                fd, tmp_name = tempfile.mkstemp(
                    prefix=".fingerprint.key.",
                    suffix=".tmp",
                    dir=self.state_dir,
                )
                tmp_path = Path(tmp_name)
                os.fchmod(fd, 0o600)
                key = secrets.token_bytes(32)
                with os.fdopen(fd, "wb") as stream:
                    fd = None
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
                # A fully fsynced temp file becomes visible atomically.  Two
                # devices arming concurrently cannot observe a partial key:
                # exactly one hard-link wins and all others read that winner.
                try:
                    os.link(tmp_path, self.key_path)
                    installed = True
                except FileExistsError:
                    pass
                if installed:
                    self._fsync_dir(self.state_dir)
            except OSError as exc:
                raise SubmitJournalUnavailable(
                    f"无法创建 fingerprint key: {exc}"
                ) from exc
            finally:
                if fd is not None:
                    os.close(fd)
                if tmp_path is not None:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
        try:
            _safe_chmod(self.key_path, 0o600)
            key = self.key_path.read_bytes()
        except (OSError, SubmitJournalError) as exc:
            if isinstance(exc, SubmitJournalError):
                raise
            raise SubmitJournalUnavailable(f"fingerprint key 不可读: {exc}") from exc
        if len(key) != 32:
            raise SubmitJournalUnavailable(
                "fingerprint key 长度异常；已禁止自动提交"
            )
        return key

    def fingerprint(self, config):
        key = self._load_or_create_key()
        return hmac.new(
            key,
            _canonical_json(_fingerprint_payload(config)),
            hashlib.sha256,
        ).hexdigest()

    def arm(self, *, config):
        """Durably create ARMED before any submit click is attempted."""
        with self._locked():
            if self._load_unlocked() is not None:
                raise ActiveSubmitIntentError(
                    "设备已有未确认 submit intent，禁止再次自动提交"
                )
            now = _utc_now()
            intent_id = str(uuid.uuid4())
            users = getattr(config, "users", None) or []
            record = {
                "schema_version": SCHEMA_VERSION,
                "intent_id": intent_id,
                "device_key": self.device_key,
                "intent_fingerprint": self.fingerprint(config),
                "state": "ARMED",
                "created_at": now,
                "updated_at": now,
                "pid": os.getpid(),
                "attendee_count": len(users),
                "click_attempts": 0,
                "observation": None,
                "history": [{"state": "ARMED", "at": now}],
            }
            self._write_atomic(self.active_path, record)
            return record

    def _transition(self, intent_id, state, *, observation=None, click_attempts=None):
        if state not in _KNOWN_STATES:
            raise ValueError(f"未知 submit journal 状态: {state}")
        with self._locked():
            record = self._load_unlocked()
            if record is None:
                raise SubmitJournalUnavailable(
                    "active submit journal 意外消失；已禁止继续"
                )
            if record["intent_id"] != intent_id:
                raise ActiveSubmitIntentError("submit intent_id 不匹配")
            current = record["state"]
            if current not in {"ARMED", "CLICK_DISPATCHED"} and state != current:
                raise SubmitJournalUnavailable(
                    f"submit journal 状态禁止回退/覆盖: {current} -> {state}"
                )
            now = _utc_now()
            record["state"] = state
            record["updated_at"] = now
            if observation is not None:
                record["observation"] = observation
            if click_attempts is not None:
                if click_attempts not in (0, 1):
                    raise SubmitJournalUnavailable("click_attempts 只能是 0 或 1")
                record["click_attempts"] = click_attempts
            record.setdefault("history", []).append({"state": state, "at": now})
            self._write_atomic(self.active_path, record)
            return record

    def mark_click_dispatched(self, intent_id):
        return self._transition(
            intent_id,
            "CLICK_DISPATCHED",
            click_attempts=1,
        )

    def record_observation(self, intent_id, observation):
        state = OBSERVATION_STATES.get(observation, "RESULT_UNKNOWN")
        return self._transition(
            intent_id,
            state,
            observation=_normalise_text(observation) or "unknown",
            click_attempts=1,
        )

    def summary(self):
        """Return only non-sensitive fields suitable for run_summary.json."""
        record = self.load_active()
        if record is None:
            return None
        return {
            "intent_id": record["intent_id"],
            "state": record["state"],
        }

    def acknowledge(self, intent_id, resolution):
        """Archive an active intent after explicit human review."""
        if resolution not in ALLOWED_RESOLUTIONS:
            raise ValueError(f"resolution 不允许: {resolution}")
        with self._locked():
            record = self._load_unlocked()
            if record is None:
                raise SubmitJournalUnavailable("没有 active submit intent")
            if record["intent_id"] != intent_id:
                raise ActiveSubmitIntentError("submit intent_id 不匹配")
            now = _utc_now()
            archived = dict(record)
            archived["state"] = "ACKNOWLEDGED"
            archived["updated_at"] = now
            archived["resolution"] = resolution
            archived.setdefault("history", []).append(
                {"state": "ACKNOWLEDGED", "at": now}
            )
            archive_dir = self.archive_root / f"device-{self.device_key}"
            timestamp = now.replace(":", "").replace("-", "").replace(".", "")
            archive_path = archive_dir / f"{timestamp}_{intent_id}.json"
            self._write_atomic(archive_path, archived)
            try:
                self.active_path.unlink()
                self._fsync_dir(self.active_dir)
            except OSError as exc:
                raise SubmitJournalUnavailable(
                    "archive 已写入但 active journal 未能移除；仍保持安全阻塞"
                ) from exc
            return archive_path


def _build_parser():
    parser = argparse.ArgumentParser(prog="python -m mobile.damai_app.submit_journal")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="只读查看当前 submit intent")
    status.add_argument("--serial", required=True)
    acknowledge = subparsers.add_parser(
        "acknowledge",
        help="人工核实订单后归档并解除提交阻塞",
    )
    acknowledge.add_argument("--serial", required=True)
    acknowledge.add_argument("--intent-id", required=True)
    acknowledge.add_argument("--resolution", required=True, choices=sorted(ALLOWED_RESOLUTIONS))
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    # Resolve the physical phone before deriving either the journal path or the
    # acknowledgement runtime lock.  USB and wireless ADB routes to the same
    # handset must operate on the same durable intent.
    from .runtime_lock import (
        DeviceInstanceLock,
        resolve_physical_device_identity,
    )

    physical_device_id = resolve_physical_device_identity(args.serial)
    if physical_device_id is None:
        print(
            "无法确认物理设备身份；未读取或修改 submit intent",
            file=sys.stderr,
        )
        return 2
    journal = SubmitJournal(
        args.serial,
        physical_device_id=physical_device_id,
    )
    if args.command == "status":
        summary = journal.summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if not sys.stdin.isatty():
        print("拒绝解除：acknowledge 必须在交互终端中执行", file=sys.stderr)
        return 2

    instance_lock = DeviceInstanceLock(physical_device_id)
    if not instance_lock.acquire():
        print("同一设备仍有 HaTickets 实例运行，不能解除 submit intent", file=sys.stderr)
        return 13
    try:
        record = journal.load_active()
        if record is None:
            print("没有 active submit intent", file=sys.stderr)
            return 2
        print("请先在大麦订单列表核实付款/取消/确无订单。")
        typed = input(
            f"请输入完整 intent ID {record['intent_id']} 以确认归档："
        ).strip()
        if typed != record["intent_id"] or args.intent_id != record["intent_id"]:
            print("intent ID 不匹配，未解除阻塞", file=sys.stderr)
            return 2
        archive = journal.acknowledge(args.intent_id, args.resolution)
    finally:
        instance_lock.release()
    print(f"已归档并解除阻塞: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
