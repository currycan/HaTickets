"""Unit tests for mobile/hot_path_benchmark.py."""

import json
from argparse import Namespace
from unittest.mock import Mock, patch

import pytest

from mobile.config import Config
from mobile import hot_path_benchmark
from mobile.hot_path_benchmark import (
    _require_retryable_start,
    build_benchmark_config,
    format_report,
    run_benchmark,
    summarize_results,
)


def _make_config():
    return Config(
        server_url="http://127.0.0.1:4723",
        device_name="Android",
        udid="ABC123",
        platform_version="16",
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="旧关键词",
        users=["张志涛"],
        city="上海",
        date="04.04",
        price="899元",
        price_index=5,
        if_commit_order=True,
        probe_only=True,
        auto_navigate=True,
        rush_mode=False,
        item_url="https://example.com/item",
        item_id="123456",
        target_title="旧标题",
        target_venue="旧场馆",
    )


def test_default_config_path_prefers_local_file(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.local.jsonc").write_text("{}", encoding="utf-8")
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.hot_path_benchmark._repo_root", return_value=tmp_path):
        assert hot_path_benchmark._default_config_path() == mobile_dir / "config.local.jsonc"


def test_default_config_path_falls_back_to_shared_config(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.hot_path_benchmark._repo_root", return_value=tmp_path):
        assert hot_path_benchmark._default_config_path() == mobile_dir / "config.jsonc"


def test_parse_args_defaults_use_safe_local_config_path():
    with patch("mobile.hot_path_benchmark._default_config_path", return_value="/tmp/config.local.jsonc"):
        args = hot_path_benchmark.parse_args([])

    assert args.config == "/tmp/config.local.jsonc"
    assert args.runs == 3
    assert args.json_output is False


def test_build_benchmark_config_forces_safe_manual_mode():
    base_config = _make_config()
    args = Namespace(price="580元", price_index=2, city="成都", date="04.18")

    cfg = build_benchmark_config(base_config, args)

    assert cfg.price == "580元"
    assert cfg.price_index == 2
    assert cfg.city == "成都"
    assert cfg.date == "04.18"
    assert cfg.if_commit_order is False
    assert cfg.probe_only is False
    assert cfg.auto_navigate is False
    assert cfg.rush_mode is True
    assert cfg.item_url is None
    assert cfg.item_id is None
    assert cfg.target_title is None
    assert cfg.target_venue is None
    assert cfg.users == ["张志涛"]
    assert cfg.udid == "ABC123"


def test_require_retryable_start_accepts_detail_page():
    bot = Mock()
    bot.probe_current_page.return_value = {"state": "detail_page"}

    assert _require_retryable_start(bot, "开始") == {"state": "detail_page"}
    bot._recover_to_detail_page_for_local_retry.assert_not_called()


def test_require_retryable_start_uses_recovery_when_needed():
    bot = Mock()
    bot.probe_current_page.return_value = {"state": "unknown"}
    bot._recover_to_detail_page_for_local_retry.return_value = {"state": "sku_page"}

    assert _require_retryable_start(bot, "开始") == {"state": "sku_page"}


def test_require_retryable_start_raises_for_unrecoverable_page():
    bot = Mock()
    bot.probe_current_page.return_value = {"state": "unknown"}
    bot._recover_to_detail_page_for_local_retry.return_value = {"state": "loading"}

    with pytest.raises(RuntimeError, match="当前状态: loading"):
        _require_retryable_start(bot, "开始")


def test_summarize_results_calculates_elapsed_and_recovery():
    summary = summarize_results([
        {"success": True, "elapsed_seconds": 8.1, "recovery_seconds": 3.6},
        {"success": False, "elapsed_seconds": 5.9, "recovery_seconds": None},
    ])

    assert summary == {
        "runs": 2,
        "success_count": 1,
        "avg_elapsed_seconds": 7.0,
        "min_elapsed_seconds": 5.9,
        "max_elapsed_seconds": 8.1,
        "avg_recovery_seconds": 3.6,
    }


def test_run_benchmark_collects_results_and_recovery():
    bot = Mock()
    bot.config.price = "580元"
    bot.config.price_index = 2
    bot.probe_current_page.side_effect = [
        {"state": "detail_page", "submit_button": False},
        {"state": "detail_page", "submit_button": False},
        {"state": "order_confirm_page", "submit_button": True},
        {"state": "sku_page", "submit_button": False},
        {"state": "order_confirm_page", "submit_button": True},
    ]
    bot._recover_to_detail_page_for_local_retry.return_value = {"state": "sku_page", "submit_button": False}
    bot.run_ticket_grabbing.side_effect = [True, True]
    bot._get_detail_title_text.return_value = "【成都】顽童MJ116 OGS巡回演唱会-成都站"
    bot._get_current_activity.return_value = ".trade.newtradeorder.ui.projectdetail.ui.activity.ProjectDetailActivity"

    with patch("mobile.hot_path_benchmark.time.time", side_effect=[0.0, 8.1, 9.0, 12.6, 13.0, 18.9]):
        payload = run_benchmark(bot, runs=2)

    assert payload["title"] == "【成都】顽童MJ116 OGS巡回演唱会-成都站"
    assert payload["summary"]["runs"] == 2
    assert payload["summary"]["success_count"] == 2
    assert payload["summary"]["avg_elapsed_seconds"] == 7.0
    assert payload["summary"]["avg_recovery_seconds"] == 3.6
    assert payload["results"][0]["elapsed_seconds"] == 8.1
    assert payload["results"][0]["recovery_seconds"] == 3.6
    assert payload["results"][1]["elapsed_seconds"] == 5.9
    assert payload["results"][1]["recovery_seconds"] is None


def test_run_benchmark_rejects_invalid_runs():
    with pytest.raises(ValueError, match="runs 必须大于等于 1"):
        run_benchmark(Mock(), runs=0)


def test_format_report_includes_summary_lines():
    payload = {
        "title": "【成都】顽童MJ116 OGS巡回演唱会-成都站",
        "initial_state": "detail_page",
        "initial_activity": ".trade.newtradeorder.ui.projectdetail.ui.activity.ProjectDetailActivity",
        "price": "580元",
        "price_index": 2,
        "results": [
            {
                "run": 1,
                "success": True,
                "elapsed_seconds": 8.11,
                "final_state": "order_confirm_page",
                "submit_button_ready": True,
                "recovery_seconds": 3.83,
                "recovery_state": "sku_page",
            }
        ],
        "summary": {
            "runs": 1,
            "success_count": 1,
            "avg_elapsed_seconds": 8.11,
            "min_elapsed_seconds": 8.11,
            "max_elapsed_seconds": 8.11,
            "avg_recovery_seconds": 3.83,
        },
    }

    report = format_report(payload)

    assert "演出: 【成都】顽童MJ116 OGS巡回演唱会-成都站" in report
    assert "1. 8.11s | success | final=order_confirm_page | submit_ready=True | recover=3.83s -> sku_page" in report
    assert "runs=1, success=1/1" in report


def test_main_rejects_runs_below_one(capsys):
    assert hot_path_benchmark.main(["--runs", "0"]) == 1
    assert "runs 必须大于等于 1" in capsys.readouterr().err


def test_main_prints_human_report_and_quits_driver(capsys):
    bot = Mock()
    bot.driver = Mock()
    payload = {
        "summary": {
            "runs": 2,
            "success_count": 2,
            "avg_elapsed_seconds": 2.0,
            "min_elapsed_seconds": 1.9,
            "max_elapsed_seconds": 2.1,
            "avg_recovery_seconds": 3.0,
        }
    }

    with patch("mobile.hot_path_benchmark.Config.load_config", return_value=_make_config()), \
         patch("mobile.hot_path_benchmark.DamaiBot", return_value=bot), \
         patch("mobile.hot_path_benchmark.run_benchmark", return_value=payload), \
         patch("mobile.hot_path_benchmark.format_report", return_value="report-body"):
        assert hot_path_benchmark.main([]) == 0

    assert capsys.readouterr().out.strip() == "report-body"
    bot.driver.quit.assert_called_once()


def test_main_prints_json_and_returns_one_when_any_run_fails(capsys):
    bot = Mock()
    bot.driver = Mock()
    payload = {
        "summary": {
            "runs": 2,
            "success_count": 1,
            "avg_elapsed_seconds": 2.0,
            "min_elapsed_seconds": 1.9,
            "max_elapsed_seconds": 2.1,
            "avg_recovery_seconds": None,
        }
    }

    with patch("mobile.hot_path_benchmark.Config.load_config", return_value=_make_config()), \
         patch("mobile.hot_path_benchmark.DamaiBot", return_value=bot), \
         patch("mobile.hot_path_benchmark.run_benchmark", return_value=payload):
        assert hot_path_benchmark.main(["--json"]) == 1

    assert json.loads(capsys.readouterr().out)["summary"]["success_count"] == 1


def test_main_returns_one_on_exception(capsys):
    with patch("mobile.hot_path_benchmark.Config.load_config", side_effect=RuntimeError("boom")):
        assert hot_path_benchmark.main([]) == 1

    assert "热路径压测失败: boom" in capsys.readouterr().err
