# -*- coding: UTF-8 -*-
"""Clock trust assessment and wall-clock to monotonic deadline mapping.

The ticket sale time is a wall-clock timestamp, but waiting against wall time
is unsafe because the system clock can jump after an NTP correction or a
manual clock change.  This module samples clock trust once and maps the sale
timestamp onto ``time.monotonic()``.  It deliberately does not contact an NTP
server: the default provider only inspects local operating-system sync status.

Callers that already have a trusted time source can inject a provider returning
``ClockObservation`` (or a mapping with equivalent fields).  ``offset_seconds``
uses the convention ``reference_time - local_wall_time``; a positive value
means the local clock is behind the reference clock.
"""

from __future__ import annotations

import math
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Mapping, Optional


WARNING_OFFSET_SECONDS = 0.150
SEVERE_OFFSET_SECONDS = 1.000
WARNING_UNCERTAINTY_SECONDS = 0.250
SEVERE_UNCERTAINTY_SECONDS = 1.000


@dataclass(frozen=True)
class ClockObservation:
    """One best-effort observation of local clock quality."""

    offset_seconds: Optional[float] = None
    uncertainty_seconds: Optional[float] = None
    synchronized: Optional[bool] = None
    source: str = "local_system"
    detail: Optional[str] = None

    @property
    def effective_offset_seconds(self) -> float:
        """Return the correction usable for deadline mapping.

        ``None`` means no measured offset is available, so the compatible
        behavior is to use the local wall clock without correction.
        """

        if self.offset_seconds is None:
            return 0.0
        value = float(self.offset_seconds)
        return value if math.isfinite(value) else 0.0


@dataclass(frozen=True)
class MonotonicSaleDeadline:
    """A sale timestamp anchored to one monotonic-clock reading."""

    target_wall_time: datetime
    anchor_wall_time: datetime
    anchor_monotonic: float
    target_monotonic: float
    observation: ClockObservation
    trust_level: str
    trust_reason: str

    @property
    def degrade_to_cta(self) -> bool:
        return self.trust_level == "severe"

    def remaining_seconds(self, monotonic_now: float) -> float:
        return self.target_monotonic - float(monotonic_now)


def _optional_finite_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("clock measurement must be finite")
    return result


def coerce_clock_observation(value: Any) -> ClockObservation:
    """Normalize an injected provider result.

    Providers may return ``ClockObservation`` directly or a mapping.  Mapping
    aliases ``offset`` and ``uncertainty`` are accepted for lightweight
    integrations.
    """

    if isinstance(value, ClockObservation):
        return value
    if isinstance(value, Mapping):
        offset = value.get("offset_seconds", value.get("offset"))
        uncertainty = value.get(
            "uncertainty_seconds",
            value.get("uncertainty"),
        )
        synchronized = value.get("synchronized")
        if synchronized is not None and not isinstance(synchronized, bool):
            raise ValueError("synchronized must be bool or None")
        return ClockObservation(
            offset_seconds=_optional_finite_float(offset),
            uncertainty_seconds=_optional_finite_float(uncertainty),
            synchronized=synchronized,
            source=str(value.get("source") or "injected"),
            detail=(
                None
                if value.get("detail") is None
                else str(value.get("detail"))
            ),
        )
    raise TypeError("clock provider must return ClockObservation or mapping")


def _run_local_status_command(command: list[str]) -> Optional[str]:
    """Run one local-only clock-status command with a short timeout."""

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=0.75,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    )
    return output or None


@lru_cache(maxsize=1)
def observe_local_clock() -> ClockObservation:
    """Inspect cached local OS time-sync status without network traffic."""

    system = platform.system()
    if system == "Darwin":
        output = _run_local_status_command(
            ["/usr/sbin/systemsetup", "-getusingnetworktime"]
        )
        normalized = (output or "").lower()
        if "network time: on" in normalized:
            return ClockObservation(
                synchronized=True,
                source="macos_systemsetup",
                detail="network_time_on",
            )
        if "network time: off" in normalized:
            return ClockObservation(
                synchronized=False,
                source="macos_systemsetup",
                detail="network_time_off",
            )
        return ClockObservation(
            source="macos_systemsetup",
            detail="status_unavailable",
        )

    if system == "Linux":
        output = _run_local_status_command(
            [
                "timedatectl",
                "show",
                "--property=NTPSynchronized",
                "--value",
            ]
        )
        normalized = (output or "").strip().lower()
        if normalized == "yes":
            return ClockObservation(
                synchronized=True,
                source="linux_timedatectl",
                detail="ntp_synchronized",
            )
        if normalized == "no":
            return ClockObservation(
                synchronized=False,
                source="linux_timedatectl",
                detail="ntp_not_synchronized",
            )
        return ClockObservation(
            source="linux_timedatectl",
            detail="status_unavailable",
        )

    return ClockObservation(
        source="local_system",
        detail="status_unsupported",
    )


def sample_clock(
    provider: Optional[Callable[[], Any]] = None,
) -> ClockObservation:
    """Sample an injected provider or the cached local-only default."""

    if provider is None:
        return observe_local_clock()
    try:
        return coerce_clock_observation(provider())
    except Exception as exc:
        return ClockObservation(
            source="injected_provider",
            detail="provider_error:%s" % type(exc).__name__,
        )


def assess_clock_trust(observation: ClockObservation) -> tuple[str, str]:
    """Return ``(level, reason)`` for one observation."""

    try:
        offset = _optional_finite_float(observation.offset_seconds)
        uncertainty = _optional_finite_float(
            observation.uncertainty_seconds
        )
    except (TypeError, ValueError):
        return "severe", "invalid_measurement"

    if uncertainty is not None and uncertainty < 0:
        return "severe", "invalid_uncertainty"
    if observation.synchronized is False:
        return "severe", "system_clock_not_synchronized"
    if offset is not None and abs(offset) >= SEVERE_OFFSET_SECONDS:
        return "severe", "offset_exceeds_severe_threshold"
    if (
        uncertainty is not None
        and uncertainty >= SEVERE_UNCERTAINTY_SECONDS
    ):
        return "severe", "uncertainty_exceeds_severe_threshold"
    if offset is not None and abs(offset) >= WARNING_OFFSET_SECONDS:
        return "warning", "offset_exceeds_warning_threshold"
    if (
        uncertainty is not None
        and uncertainty >= WARNING_UNCERTAINTY_SECONDS
    ):
        return "warning", "uncertainty_exceeds_warning_threshold"
    if observation.synchronized is True:
        return "trusted", "system_clock_synchronized"
    if offset is not None or uncertainty is not None:
        return "trusted", "measurement_within_threshold"
    return "unknown", "clock_quality_unavailable"


def build_monotonic_sale_deadline(
    target_wall_time: datetime,
    anchor_wall_time: datetime,
    anchor_monotonic: float,
    observation: ClockObservation,
) -> MonotonicSaleDeadline:
    """Map one wall-clock sale timestamp onto a monotonic deadline."""

    if target_wall_time.tzinfo is None or anchor_wall_time.tzinfo is None:
        raise ValueError("target and anchor wall times must be timezone-aware")

    level, reason = assess_clock_trust(observation)
    try:
        effective_offset = (
            _optional_finite_float(observation.offset_seconds) or 0.0
        )
    except (TypeError, ValueError):
        effective_offset = 0.0
    corrected_anchor = anchor_wall_time + timedelta(
        seconds=effective_offset
    )
    remaining = (target_wall_time - corrected_anchor).total_seconds()
    return MonotonicSaleDeadline(
        target_wall_time=target_wall_time,
        anchor_wall_time=anchor_wall_time,
        anchor_monotonic=float(anchor_monotonic),
        target_monotonic=float(anchor_monotonic) + remaining,
        observation=observation,
        trust_level=level,
        trust_reason=reason,
    )
