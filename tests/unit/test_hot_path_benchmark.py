"""Focused tests for the device-free hot-path microbenchmark."""

import json
from unittest.mock import patch

import pytest

from mobile import hot_path_benchmark
from mobile.hot_path_benchmark import (
    _measure_stage_ms,
    _nearest_rank_percentile,
    format_local_report,
    run_local_benchmark,
    summarize_stage_samples,
)


def test_nearest_rank_percentile_is_deterministic_without_interpolation():
    samples = [4.0, 1.0, 3.0, 2.0]

    assert _nearest_rank_percentile(samples, 50) == 2.0
    assert _nearest_rank_percentile(samples, 95) == 4.0
    assert _nearest_rank_percentile(samples, 100) == 4.0


def test_summarize_stage_samples_reports_percentiles_counts_and_budget():
    summary = summarize_stage_samples(
        {
            "fast": [0.01, 0.02, 0.03, 0.04],
            "slow": [0.10, 0.20, 0.30, 0.40],
        },
        {"fast": 0.04, "slow": 0.25},
    )

    assert list(summary["stages"]) == ["fast", "slow"]
    assert summary["stage_count"] == 2
    assert summary["stages"]["fast"] == {
        "sample_count": 4,
        "p50_ms": 0.02,
        "p95_ms": 0.04,
        "max_ms": 0.04,
        "budget_metric": "p95_ms",
        "budget_ms": 0.04,
        "budget_pass": True,
    }
    assert summary["stages"]["slow"]["budget_pass"] is False
    assert summary["budget_pass"] is False


def test_measure_stage_uses_warmup_and_monotonic_nanosecond_clock():
    calls = []
    ticks = iter([100, 150, 200, 300])

    measured = _measure_stage_ms(
        lambda: calls.append("called"),
        samples=2,
        warmup=1,
        timer_ns=lambda: next(ticks),
    )

    assert calls == ["called", "called", "called"]
    assert measured == [0.00005, 0.0001]


def test_run_local_benchmark_has_safe_json_serializable_schema():
    workloads = {
        "beta": lambda: None,
        "alpha": lambda: None,
    }

    with patch(
        "mobile.hot_path_benchmark._measure_stage_ms",
        side_effect=[[0.01, 0.02, 0.03], [0.04, 0.05, 0.06]],
    ):
        payload = run_local_benchmark(
            samples=3,
            warmup=2,
            workloads=workloads,
            stage_budgets_ms={"alpha": 0.03, "beta": 0.10},
        )

    assert payload["mode"] == "local_microbenchmark"
    assert payload["sample_count_per_stage"] == 3
    assert payload["warmup_count_per_stage"] == 2
    assert payload["safety"] == {
        "device_required": False,
        "network_access": False,
        "purchase_click": False,
        "order_submission": False,
    }
    assert list(payload["stages"]) == ["alpha", "beta"]
    assert payload["budget_pass"] is True
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


@pytest.mark.parametrize(
    ("samples", "warmup", "message"),
    [
        (0, 0, "samples"),
        (1, -1, "warmup"),
    ],
)
def test_run_local_benchmark_rejects_invalid_counts(samples, warmup, message):
    with pytest.raises(ValueError, match=message):
        run_local_benchmark(samples=samples, warmup=warmup)


def test_format_local_report_contains_required_metrics_and_scope():
    payload = run_local_benchmark(
        samples=2,
        warmup=0,
        workloads={"stage": lambda: None},
        stage_budgets_ms={"stage": 1000.0},
    )

    report = format_local_report(payload)

    assert "p50=" in report
    assert "p95=" in report
    assert "max=" in report
    assert "n=2" in report
    assert "总预算: PASS" in report
    assert "不连接设备" in report


def test_local_main_skips_config_and_device_and_emits_json(capsys):
    payload = {
        "schema_version": 1,
        "mode": "local_microbenchmark",
        "scope": "offline",
        "safety": {
            "device_required": False,
            "network_access": False,
            "purchase_click": False,
            "order_submission": False,
        },
        "clock": "perf_counter_ns",
        "deterministic_workloads": True,
        "sample_count_per_stage": 2,
        "warmup_count_per_stage": 1,
        "stage_count": 1,
        "stages": {},
        "budget_pass": True,
    }

    with patch(
        "mobile.hot_path_benchmark.run_local_benchmark",
        return_value=payload,
    ) as run_local, patch(
        "mobile.hot_path_benchmark.Config.load_config"
    ) as load_config, patch(
        "mobile.hot_path_benchmark.DamaiBot"
    ) as bot_class:
        exit_code = hot_path_benchmark.main(
            ["--local", "--samples", "2", "--warmup", "1", "--json"]
        )

    assert exit_code == 0
    run_local.assert_called_once_with(samples=2, warmup=1)
    load_config.assert_not_called()
    bot_class.assert_not_called()
    assert json.loads(capsys.readouterr().out) == payload
