# -*- coding: UTF-8 -*-
"""Per-device process lock for the complete Damai bot lifecycle.

The lock is deliberately kept outside the hot purchase path.  A stable,
user-scoped sidecar file plus ``flock(LOCK_NB)`` prevents two local processes
from controlling the same Android device at the same time.
"""

from __future__ import annotations

import errno
import hashlib
import os
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from . import logger

try:
    from mobile.proc_utils import run_captured
except ImportError:  # pragma: no cover - cwd=mobile compatibility
    from proc_utils import run_captured  # type: ignore[no-redef]

try:  # macOS / Linux
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - unsupported non-POSIX platform
    _fcntl = None

_DEFAULT_BACKEND = object()
_INVALID_DEVICE_VALUES = frozenset(
    {
        "",
        "0",
        "unknown",
        "null",
        "none",
        "n/a",
        "not found",
        "<unknown>",
        "0123456789abcdef",
        "1234567890abcdef",
        "emulator35x0x0x0",
    }
)


def _normalise_serial(value):
    """Return the effective non-empty serial, otherwise ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def resolve_effective_serial(config_loader, environ=None):
    """Resolve the serial before constructing ``DamaiBot``.

    Resolution mirrors :meth:`mobile.config.Config.load_config`: a non-empty
    ``HATICKETS_SERIAL`` override wins, followed by ``serial`` and legacy
    ``udid`` from the selected config.  Config errors are intentionally ignored
    here; normal bot initialisation reports the authoritative error while the
    startup is conservatively protected by the global lock.
    """
    env = os.environ if environ is None else environ
    override = _normalise_serial(env.get("HATICKETS_SERIAL"))
    if override:
        return override

    try:
        config = config_loader()
    except Exception as exc:  # noqa: BLE001 - best-effort pre-init lookup
        logger.debug("运行锁无法预读配置，将使用 global 锁: %s", exc)
        return None

    if not isinstance(config, dict):
        return None
    # Keep the same raw-value precedence as Config.load_config, then normalise
    # the selected value exactly as Config.__init__ does.
    selected = config.get("serial") or config.get("udid")
    return _normalise_serial(selected)


def _normalise_device_value(value):
    """Normalise one ADB identity field without accepting ambiguous output."""
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFKC", value).strip()
    if (
        not value
        or "\n" in value
        or "\r" in value
        or any(ord(char) < 32 for char in value)
        or value.casefold() in _INVALID_DEVICE_VALUES
        or len(value) > 256
    ):
        return None
    return value


def _adb_shell_value(transport_serial, args, *, runner):
    command = ["adb"]
    if transport_serial is not None:
        command.extend(["-s", transport_serial])
    command.extend(["shell", *args])
    try:
        completed = runner(command, timeout=1.5, check=False)
    except (
        FileNotFoundError,
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
    ):
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    return _normalise_device_value(getattr(completed, "stdout", None))


def resolve_physical_device_identity(transport_serial, *, runner=None):
    """Resolve a transport-independent identity from the connected phone.

    ``adb devices`` identifiers are connection routes, not always physical
    identities: the same handset can appear as a USB serial and later as
    ``ip:5555``.  The only accepted canonical scheme is ``ro.serialno``.

    Alternate identity sources are intentionally not used.  If one transport
    can read ``ro.serialno`` while another transiently falls back to Android ID
    or a hardware composite, the same phone could otherwise acquire two
    different known-device locks.  A missing/invalid read therefore returns
    ``None`` so the caller takes the global namespace lock and formal submit
    fails closed.
    """
    transport = _normalise_serial(transport_serial)
    invoke = runner or run_captured
    return _adb_shell_value(
        transport,
        ["getprop", "ro.serialno"],
        runner=invoke,
    )


def _default_lock_dir():
    """Return a user-scoped runtime directory suitable for persistent locks."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir:
        return Path(runtime_dir) / "hatickets"
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"hatickets-{uid}"


class DeviceInstanceLock:
    """Non-blocking inter-process lock keyed by physical Android identity.

    Known devices hold a shared namespace guard plus their own exclusive lock.
    An unresolved identity holds the namespace guard exclusively.  Therefore a
    failed identity lookup cannot race a successfully identified run, while
    separate known phones may still run concurrently.
    """

    def __init__(
        self,
        physical_device_id=None,
        lock_dir=None,
        backend=_DEFAULT_BACKEND,
    ):
        self.physical_device_id = _normalise_device_value(physical_device_id)
        # ``serial`` remains as a compatibility attribute for diagnostics/tests.
        self.serial = self.physical_device_id
        self.key = self.physical_device_id or "global"
        self.lock_dir = Path(lock_dir) if lock_dir is not None else _default_lock_dir()
        self._backend = _fcntl if backend is _DEFAULT_BACKEND else backend
        self._guard_file = None
        self._file = None

        digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:24]
        self.path = self.lock_dir / f"device-{digest}.lock"
        guard_digest = hashlib.sha256(b"hatickets-device-namespace-v1").hexdigest()[
            :24
        ]
        self.guard_path = self.lock_dir / f"namespace-{guard_digest}.lock"

    @property
    def acquired(self):
        return self._guard_file is not None

    def acquire(self):
        """Try once to acquire the device lock; never wait for another run."""
        if self._guard_file is not None:
            return True

        self.lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        guard_file = self.guard_path.open("a+", encoding="utf-8")

        if self._backend is None:
            guard_file.close()
            raise RuntimeError(
                "reliable_instance_lock_unavailable: 当前平台不支持 flock"
            )

        guard_mode = (
            self._backend.LOCK_SH
            if self.physical_device_id is not None
            else self._backend.LOCK_EX
        )
        try:
            self._backend.flock(
                guard_file.fileno(),
                guard_mode | self._backend.LOCK_NB,
            )
        except OSError as exc:
            guard_file.close()
            if isinstance(exc, BlockingIOError) or exc.errno in {
                errno.EACCES,
                errno.EAGAIN,
            }:
                return False
            raise
        except BaseException:
            guard_file.close()
            raise

        lock_file = None
        try:
            if self.physical_device_id is not None:
                lock_file = self.path.open("a+", encoding="utf-8")
                try:
                    self._backend.flock(
                        lock_file.fileno(),
                        self._backend.LOCK_EX | self._backend.LOCK_NB,
                    )
                except OSError as exc:
                    lock_file.close()
                    lock_file = None
                    if isinstance(exc, BlockingIOError) or exc.errno in {
                        errno.EACCES,
                        errno.EAGAIN,
                    }:
                        self._backend.flock(
                            guard_file.fileno(),
                            self._backend.LOCK_UN,
                        )
                        guard_file.close()
                        return False
                    raise

            self._guard_file = guard_file
            self._file = lock_file
            metadata_file = lock_file or guard_file
            try:
                metadata_file.seek(0)
                metadata_file.truncate()
                metadata_file.write(f"pid={os.getpid()}\n")
                metadata_file.flush()
            except OSError:
                # Metadata is diagnostic only; kernel locks are authoritative.
                pass
        except BaseException:
            if lock_file is not None:
                try:
                    self._backend.flock(
                        lock_file.fileno(),
                        self._backend.LOCK_UN,
                    )
                except OSError:
                    pass
                lock_file.close()
            try:
                self._backend.flock(
                    guard_file.fileno(),
                    self._backend.LOCK_UN,
                )
            except OSError:
                pass
            guard_file.close()
            raise
        return True

    def release(self):
        """Release the kernel lock and close the sidecar file (idempotent)."""
        lock_file, self._file = self._file, None
        guard_file, self._guard_file = self._guard_file, None
        if guard_file is None:
            return
        try:
            if lock_file is not None and self._backend is not None:
                try:
                    self._backend.flock(lock_file.fileno(), self._backend.LOCK_UN)
                except OSError as exc:
                    # Closing the descriptor also releases flock.  Cleanup must
                    # never replace the bot's semantic exit code.
                    logger.warning("运行锁显式释放失败，将通过关闭文件兜底: %s", exc)
        finally:
            if lock_file is not None:
                try:
                    lock_file.close()
                except OSError as exc:
                    logger.warning("运行锁文件关闭失败: %s", exc)
            try:
                if self._backend is not None:
                    try:
                        self._backend.flock(
                            guard_file.fileno(),
                            self._backend.LOCK_UN,
                        )
                    except OSError as exc:
                        logger.warning(
                            "运行锁门闩释放失败，将通过关闭文件兜底: %s",
                            exc,
                        )
            finally:
                try:
                    guard_file.close()
                except OSError as exc:
                    logger.warning("运行锁门闩文件关闭失败: %s", exc)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("instance_lock_conflict")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()
        return False
