# -*- coding: UTF-8 -*-
"""Environment snapshot helpers for boot-time observability.

Used by ``DamaiBot.__init__`` to record a structured ``event=boot`` log line
including the runtime Python version, the installed Damai app version (when
detectable via ``adb``) and the bound device serial.

Failure semantics: every helper here is best-effort.  Missing ``adb``,
unbound device, or unexpected ``pm dump`` output all map to ``None`` so the
caller can record ``damai_version=unknown`` instead of crashing the boot.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

# Damai Android package id (matches Config.app_package default).
_DAMAI_PACKAGE = "cn.damai"

# `adb shell pm dump cn.damai` exposes the version near the start, e.g.:
#     versionName=10.6.1
#     versionCode=10060100
_VERSION_NAME_RE = re.compile(r"versionName=([^\s]+)")


def _run_adb_pm_dump(
    serial: Optional[str] = None,
    package: str = _DAMAI_PACKAGE,
    timeout_s: float = 3.0,
) -> Optional[str]:
    """Invoke ``adb shell pm dump <package>`` and return stdout, or None on any failure.

    Args:
        serial: Optional device serial; when given, ``-s <serial>`` is added.
        package: Android package to dump (defaults to Damai).
        timeout_s: Subprocess timeout in seconds.
    """
    if shutil.which("adb") is None:
        return None

    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", "pm", "dump", package])

    try:
        result = subprocess.run(  # noqa: S603 — adb is a trusted binary
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    return result.stdout or None


def detect_damai_app_version(
    serial: Optional[str] = None,
    *,
    package: str = _DAMAI_PACKAGE,
    _runner=None,
) -> Optional[str]:
    """Return the installed Damai app ``versionName`` (e.g. ``"10.6.1"``).

    Args:
        serial: Optional device serial passed to ``adb -s``.
        package: Android package id to query.
        _runner: Test seam — callable matching :func:`_run_adb_pm_dump`'s
            signature.  Production callers should not set this.

    Returns:
        The ``versionName`` string, or ``None`` if adb is unavailable, the
        device cannot be reached, or no ``versionName=...`` line is present.
    """
    runner = _runner or _run_adb_pm_dump
    output = runner(serial=serial, package=package)
    if not output:
        return None
    match = _VERSION_NAME_RE.search(output)
    if not match:
        return None
    return match.group(1).strip() or None
