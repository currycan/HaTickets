# -*- coding: UTF-8 -*-
"""Clock trust and monotonic sale-deadline regression tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from mobile.clock_sync import (
    ClockObservation,
    assess_clock_trust,
    build_monotonic_sale_deadline,
    observe_local_clock,
    sample_clock,
)
from mobile.damai_app.sale_waiter import SaleWaiterMixin
from mobile.damai_app.coords_cache import CoordsCacheMixin


_TZ_SHANGHAI = timezone(timedelta(hours=8))


class _FakeTime:
    def __init__(self, start=100.0):
        self.now = float(start)
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        seconds = float(seconds)
        self.sleeps.append(seconds)
        self.now += seconds


class _CanvasGuard:
    _last_matched_resource_id = "cn.damai:id/purchase_container"

    def __init__(self):
        self.center_calls = 0

    def get_current_text(self):
        return None

    def get_cta_center_coords(self):
        self.center_calls += 1
        return (500, 1200)


class _SaleWaiterHarness(SaleWaiterMixin, CoordsCacheMixin):
    def __init__(self, observation):
        self.config = SimpleNamespace(
            sell_start_time=None,
            countdown_lead_ms=3000,
            presale_prewarm_enabled=True,
            wait_cta_ready_timeout_ms=0,
            rush_mode=False,
        )
        self._clock_offset_provider = lambda: observation
        self._guard = _CanvasGuard()
        self._cached_hot_path_coords = {}
        self._cached_hot_path_no_match = set()
        self._ensure_hot_cache_sidecars()
        self._terminal_failure_reason = None
        self.cta_wait_timeout_ms = None
        self.awake_calls = 0

    def _probe_hot_blocking_overlay(self):
        return None

    def _dismiss_fast_blocking_dialogs(self):
        return False

    def _invalidate_page_probe_cache(self):
        return None

    def _is_sale_ready(self):
        return False

    def _wait_for_cta_without_schedule(self, timeout_ms):
        self.cta_wait_timeout_ms = timeout_ms
        return True

    def _ensure_device_awake(self):
        self.awake_calls += 1
        return True

    def _get_current_activity(self):
        return "ProjectDetailActivity"


def test_wall_target_maps_once_to_monotonic_with_offset():
    anchor = datetime(2026, 7, 18, 10, 0, 0, tzinfo=_TZ_SHANGHAI)
    target = anchor + timedelta(seconds=10)
    observation = ClockObservation(
        offset_seconds=0.2,
        uncertainty_seconds=0.02,
        synchronized=True,
        source="test_reference",
    )

    deadline = build_monotonic_sale_deadline(
        target,
        anchor,
        100.0,
        observation,
    )

    # Reference time is 200ms ahead of local wall time.
    assert deadline.target_monotonic == pytest.approx(109.8)
    assert deadline.remaining_seconds(104.0) == pytest.approx(5.8)
    assert deadline.trust_level == "warning"
    assert deadline.degrade_to_cta is False


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (
            ClockObservation(
                offset_seconds=1.0,
                uncertainty_seconds=0.01,
                synchronized=True,
            ),
            "offset_exceeds_severe_threshold",
        ),
        (
            ClockObservation(
                offset_seconds=0.0,
                uncertainty_seconds=1.0,
                synchronized=True,
            ),
            "uncertainty_exceeds_severe_threshold",
        ),
        (
            ClockObservation(synchronized=False),
            "system_clock_not_synchronized",
        ),
    ],
)
def test_severe_clock_observations_require_cta_degradation(
    observation,
    reason,
):
    assert assess_clock_trust(observation) == ("severe", reason)


def test_provider_failure_is_unknown_and_preserves_compatible_mapping():
    def broken_provider():
        raise RuntimeError("offline")

    observation = sample_clock(broken_provider)

    assert observation.offset_seconds is None
    assert observation.uncertainty_seconds is None
    assert assess_clock_trust(observation) == (
        "unknown",
        "clock_quality_unavailable",
    )
    assert observation.detail == "provider_error:RuntimeError"


def test_default_observation_reads_only_local_macos_sync_status():
    observe_local_clock.cache_clear()
    completed = SimpleNamespace(
        stdout="Network Time: On\n",
        stderr="",
    )
    try:
        with patch("mobile.clock_sync.platform.system", return_value="Darwin"):
            with patch(
                "mobile.clock_sync.subprocess.run",
                return_value=completed,
            ) as run:
                observation = observe_local_clock()
    finally:
        observe_local_clock.cache_clear()

    assert observation.synchronized is True
    command = run.call_args.args[0]
    assert command == [
        "/usr/sbin/systemsetup",
        "-getusingnetworktime",
    ]
    assert all("://" not in part for part in command)


def test_sale_wait_uses_one_wall_anchor_even_if_wall_clock_would_jump():
    anchor = datetime(2026, 7, 18, 10, 0, 0, tzinfo=_TZ_SHANGHAI)
    target = anchor + timedelta(seconds=10)
    fake_time = _FakeTime(100.0)
    harness = _SaleWaiterHarness(
        ClockObservation(
            offset_seconds=0.0,
            uncertainty_seconds=0.01,
            synchronized=True,
            source="test_reference",
        )
    )
    harness.config.sell_start_time = target.isoformat()

    mock_datetime = Mock()
    mock_datetime.fromisoformat = datetime.fromisoformat
    # A second read would jump the wall clock back by one day.  The monotonic
    # implementation must never consume it.
    mock_datetime.now.side_effect = [anchor, anchor - timedelta(days=1)]

    with patch("mobile.damai_app.sale_waiter.datetime", mock_datetime):
        with patch(
            "mobile.damai_app.sale_waiter.time.monotonic",
            side_effect=fake_time.monotonic,
        ):
            with patch(
                "mobile.damai_app.sale_waiter.time.sleep",
                side_effect=fake_time.sleep,
            ):
                assert harness.wait_for_sale_start() is True

    assert mock_datetime.now.call_count == 1
    assert 110.0 <= fake_time.now <= 110.021
    assert harness._sale_clock_deadline.target_monotonic == pytest.approx(
        110.0
    )


def test_severe_offset_waits_monotonically_then_uses_cta_only():
    anchor = datetime(2026, 7, 18, 10, 0, 0, tzinfo=_TZ_SHANGHAI)
    target = anchor + timedelta(seconds=10)
    fake_time = _FakeTime(100.0)
    harness = _SaleWaiterHarness(
        ClockObservation(
            offset_seconds=2.0,
            uncertainty_seconds=0.02,
            synchronized=True,
            source="test_reference",
        )
    )
    harness.config.sell_start_time = target.isoformat()

    mock_datetime = Mock()
    mock_datetime.fromisoformat = datetime.fromisoformat
    mock_datetime.now.return_value = anchor

    with patch("mobile.damai_app.sale_waiter.datetime", mock_datetime):
        with patch(
            "mobile.damai_app.sale_waiter.time.monotonic",
            side_effect=fake_time.monotonic,
        ):
            with patch(
                "mobile.damai_app.sale_waiter.time.sleep",
                side_effect=fake_time.sleep,
            ):
                assert harness.wait_for_sale_start() is True

    # The 2s correction makes the reference sale 8s away; polling begins 3s
    # before that, after one 5s monotonic sleep.
    assert fake_time.sleeps == [pytest.approx(5.0)]
    assert harness.awake_calls == 1
    assert harness.cta_wait_timeout_ms == 11000
    assert harness._guard.center_calls == 0
    assert harness._sale_clock_deadline.degrade_to_cta is True


def test_rush_prewarm_reuses_single_canvas_inspection_before_deadline():
    anchor = datetime(2026, 7, 18, 10, 0, 0, tzinfo=_TZ_SHANGHAI)
    target = anchor + timedelta(seconds=3)
    fake_time = _FakeTime(100.0)
    harness = _SaleWaiterHarness(
        ClockObservation(
            offset_seconds=0.0,
            uncertainty_seconds=0.01,
            synchronized=True,
            source="test_reference",
        )
    )
    harness.config.sell_start_time = target.isoformat()
    harness.config.rush_mode = True

    mock_datetime = Mock()
    mock_datetime.fromisoformat = datetime.fromisoformat
    mock_datetime.now.return_value = anchor

    with patch("mobile.damai_app.sale_waiter.datetime", mock_datetime):
        with patch(
            "mobile.damai_app.sale_waiter.time.monotonic",
            side_effect=fake_time.monotonic,
        ):
            with patch(
                "mobile.damai_app.sale_waiter.time.sleep",
                side_effect=fake_time.sleep,
            ):
                assert harness.wait_for_sale_start() is True

    assert mock_datetime.now.call_count == 1
    assert harness._guard.center_calls == 1
    assert harness._cached_hot_path_coords["detail_buy"] == (500, 1200)
    assert harness._presale_readiness_snapshot["cta_kind"] == "canvas"
    assert harness._sale_clock_deadline.target_monotonic == pytest.approx(
        103.0
    )
