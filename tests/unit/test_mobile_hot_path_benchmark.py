"""Unit tests for mobile/hot_path_benchmark.py."""

import json
import pytest
from argparse import Namespace
from unittest.mock import Mock, patch

from mobile.config import Config
from mobile import hot_path_benchmark
from mobile.hot_path_benchmark import (
    _default_config_path,
    _fast_recover_to_detail,
    _require_detail_start,
    _repo_root,
    _shell_back,
    build_benchmark_config,
    format_report,
    parse_args,
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


def _make_summary(avg, min_val, max_val, recovery_avg, runs, success_count):
    return {
        "runs": runs,
        "success_count": success_count,
        "avg_elapsed_seconds": avg,
        "min_elapsed_seconds": min_val,
        "max_elapsed_seconds": max_val,
        "avg_recovery_seconds": recovery_avg,
    }


# ---------------------------------------------------------------------------
# _repo_root / _default_config_path
# ---------------------------------------------------------------------------

def test_repo_root_returns_path():
    root = _repo_root()
    assert root.is_dir()
    assert (root / "mobile").is_dir()


def test_default_config_path_defaults_to_shared_config(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.local.jsonc").write_text("{}", encoding="utf-8")
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.hot_path_benchmark._repo_root", return_value=tmp_path):
        assert hot_path_benchmark._default_config_path() == mobile_dir / "config.jsonc"


def test_default_config_path_respects_env_override(tmp_path, monkeypatch):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.local.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HATICKETS_CONFIG_PATH", str(mobile_dir / "config.local.jsonc"))

    with patch("mobile.hot_path_benchmark._repo_root", return_value=tmp_path):
        assert hot_path_benchmark._default_config_path() == mobile_dir / "config.local.jsonc"


def test_default_config_path_falls_back_to_shared_config(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.hot_path_benchmark._repo_root", return_value=tmp_path):
        assert hot_path_benchmark._default_config_path() == mobile_dir / "config.jsonc"


def test_default_config_path_returns_path(monkeypatch):
    monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
    path = _default_config_path()
    assert path.name == "config.jsonc"


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

def test_parse_args_defaults_use_shared_config_path():
    with patch("mobile.hot_path_benchmark._default_config_path", return_value="/tmp/config.jsonc"):
        args = hot_path_benchmark.parse_args([])

    assert args.config == "/tmp/config.jsonc"
    assert args.runs == 3
    assert args.json_output is False


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.runs == 3
        assert args.price is None
        assert args.price_index is None
        assert args.city is None
        assert args.date is None
        assert args.json_output is False

    def test_all_overrides(self):
        args = parse_args([
            "--runs", "5",
            "--price", "580元",
            "--price-index", "2",
            "--city", "成都",
            "--date", "04.18",
            "--json",
        ])
        assert args.runs == 5
        assert args.price == "580元"
        assert args.price_index == 2
        assert args.city == "成都"
        assert args.date == "04.18"
        assert args.json_output is True

    def test_config_override(self, tmp_path):
        cfg_file = tmp_path / "test_config.jsonc"
        cfg_file.write_text("{}")
        args = parse_args(["--config", str(cfg_file)])
        assert args.config == str(cfg_file)


# ---------------------------------------------------------------------------
# build_benchmark_config
# ---------------------------------------------------------------------------

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


class TestBuildBenchmarkConfigNoneArgs:
    def test_none_price_keeps_base(self):
        base = _make_config()
        args = Namespace(price=None, price_index=None, city=None, date=None)
        cfg = build_benchmark_config(base, args)
        assert cfg.price == "899元"

    def test_none_price_index_keeps_base(self):
        base = _make_config()
        args = Namespace(price=None, price_index=None, city=None, date=None)
        cfg = build_benchmark_config(base, args)
        assert cfg.price_index == 5

    def test_none_city_keeps_base(self):
        base = _make_config()
        args = Namespace(price=None, price_index=None, city=None, date=None)
        cfg = build_benchmark_config(base, args)
        assert cfg.city == "上海"

    def test_none_date_keeps_base(self):
        base = _make_config()
        args = Namespace(price=None, price_index=None, city=None, date=None)
        cfg = build_benchmark_config(base, args)
        assert cfg.date == "04.04"

    def test_partial_overrides(self):
        base = _make_config()
        args = Namespace(price="580元", price_index=None, city=None, date=None)
        cfg = build_benchmark_config(base, args)
        assert cfg.price == "580元"
        assert cfg.price_index == 5  # not overridden


# ---------------------------------------------------------------------------
# _require_detail_start
# ---------------------------------------------------------------------------

def _make_bot_for_recovery(find_all_side_effect=None, using_u2=True):
    """Create a Mock bot for recovery tests.

    Args:
        find_all_side_effect: side_effect for _find_all — each call returns a list.
            Empty list = element not found, non-empty = element found (detail_page).
        using_u2: whether _using_u2() returns True.
    """
    bot = Mock()
    bot._using_u2.return_value = using_u2
    if find_all_side_effect is not None:
        bot._find_all.side_effect = find_all_side_effect
    else:
        bot._find_all.return_value = []
    return bot


def test_shell_back_single_uses_u2_shell():
    bot = _make_bot_for_recovery(using_u2=True)
    _shell_back(bot)
    bot.d.shell.assert_called_once_with("input keyevent 4")


def test_shell_back_batch_fires_single_shell_call():
    bot = _make_bot_for_recovery(using_u2=True)
    _shell_back(bot, count=2)
    bot.d.shell.assert_called_once_with("input keyevent 4; sleep 0.2; input keyevent 4")


def test_shell_back_falls_back_to_press_keycode():
    bot = _make_bot_for_recovery(using_u2=False)
    _shell_back(bot)
    bot._press_keycode_safe.assert_called_once_with(4, context="benchmark回退")


def test_fast_recover_finds_detail_on_first_check():
    bot = _make_bot_for_recovery(find_all_side_effect=[["element"]])
    result = _fast_recover_to_detail(bot)
    assert result["state"] == "detail_page"
    bot.d.shell.assert_not_called()


def test_fast_recover_batch_back_finds_detail():
    # First check: not detail. Batch 2 backs. Second check: detail.
    bot = _make_bot_for_recovery(find_all_side_effect=[[], ["element"]])
    result = _fast_recover_to_detail(bot)
    assert result["state"] == "detail_page"
    bot.d.shell.assert_called_once_with("input keyevent 4; sleep 0.2; input keyevent 4")


def test_fast_recover_incremental_fallback():
    # Batch 2 backs not enough, one more individual back finds detail.
    bot = _make_bot_for_recovery(find_all_side_effect=[[], [], ["element"]])
    result = _fast_recover_to_detail(bot)
    assert result["state"] == "detail_page"
    assert bot.d.shell.call_count == 2  # batch + 1 individual


def test_fast_recover_falls_back_to_full_probe():
    bot = _make_bot_for_recovery(find_all_side_effect=[[] for _ in range(10)])
    bot.probe_current_page.return_value = {"state": "unknown"}
    result = _fast_recover_to_detail(bot)
    assert result["state"] == "unknown"
    bot.probe_current_page.assert_called_once()


def test_require_detail_start_accepts_detail_page():
    bot = _make_bot_for_recovery(find_all_side_effect=[["element"]])
    result = _require_detail_start(bot, "开始")
    assert result["state"] == "detail_page"
    bot._recover_to_detail_page_for_local_retry.assert_not_called()


def test_require_detail_start_fast_recovery_succeeds():
    # Fast check fails, fast_recover backs once and finds detail.
    bot = _make_bot_for_recovery(find_all_side_effect=[[], [], ["element"]])
    result = _require_detail_start(bot, "开始")
    assert result["state"] == "detail_page"
    bot._recover_to_detail_page_for_local_retry.assert_not_called()


def test_require_detail_start_heavy_fallback():
    # Fast check and fast_recover both fail, heavy fallback succeeds.
    bot = _make_bot_for_recovery(find_all_side_effect=[[] for _ in range(12)])
    bot.probe_current_page.return_value = {"state": "unknown"}
    bot._recover_to_detail_page_for_local_retry.return_value = {"state": "detail_page"}
    result = _require_detail_start(bot, "开始")
    assert result["state"] == "detail_page"


def test_require_detail_start_raises_for_unrecoverable_page():
    bot = _make_bot_for_recovery(find_all_side_effect=[[] for _ in range(12)])
    bot.probe_current_page.return_value = {"state": "loading"}
    bot._recover_to_detail_page_for_local_retry.return_value = {"state": "loading"}
    with pytest.raises(RuntimeError, match="当前状态: loading"):
        _require_detail_start(bot, "开始")


# ---------------------------------------------------------------------------
# summarize_results
# ---------------------------------------------------------------------------

def test_summarize_results_basic():
    summary = summarize_results([
        {"success": True, "elapsed_seconds": 2.1, "recovery_seconds": 3.6},
        {"success": False, "elapsed_seconds": 1.9, "recovery_seconds": None},
    ])

    assert summary["runs"] == 2
    assert summary["success_count"] == 1
    assert summary["avg_elapsed_seconds"] == 2.0
    assert summary["min_elapsed_seconds"] == 1.9
    assert summary["max_elapsed_seconds"] == 2.1
    assert summary["avg_recovery_seconds"] == 3.6


class TestSummarizeResults:
    def test_single_run(self):
        results = [
            {"elapsed_seconds": 5.0, "success": True, "recovery_seconds": None},
        ]
        summary = summarize_results(results)
        assert summary["avg_elapsed_seconds"] == 5.0
        assert summary["avg_recovery_seconds"] is None

    def test_no_recovery_returns_none_avg(self):
        results = [
            {"elapsed_seconds": 8.0, "success": True, "recovery_seconds": None},
            {"elapsed_seconds": 5.0, "success": True, "recovery_seconds": None},
            {"elapsed_seconds": 7.0, "success": True, "recovery_seconds": None},
        ]
        summary = summarize_results(results)
        assert summary["avg_recovery_seconds"] is None
        assert summary["runs"] == 3
        assert summary["success_count"] == 3
        assert summary["avg_elapsed_seconds"] == round((8.0 + 5.0 + 7.0) / 3, 2)

    def test_with_recovery_values(self):
        results = [
            {"elapsed_seconds": 9.0, "success": True, "recovery_seconds": 2.0},
            {"elapsed_seconds": 5.0, "success": True, "recovery_seconds": 3.0},
            {"elapsed_seconds": 7.0, "success": False, "recovery_seconds": 4.0},
        ]
        summary = summarize_results(results)
        assert summary["avg_recovery_seconds"] == 3.0
        assert summary["success_count"] == 2

    def test_mixed_recovery_values(self):
        results = [
            {"elapsed_seconds": 9.0, "success": True, "recovery_seconds": 2.0},
            {"elapsed_seconds": 5.0, "success": True, "recovery_seconds": 3.0},
            {"elapsed_seconds": 7.0, "success": True, "recovery_seconds": None},
        ]
        summary = summarize_results(results)
        assert summary["avg_recovery_seconds"] == 2.5


# ---------------------------------------------------------------------------
# run_benchmark
# ---------------------------------------------------------------------------

def test_run_one_returns_success_elapsed_and_timeline():
    from mobile.hot_path_benchmark import _run_one
    bot = Mock()
    bot.run_ticket_grabbing.return_value = True
    bot.probe_current_page.return_value = {"state": "order_confirm_page", "submit_button": True}

    with patch("mobile.hot_path_benchmark.time.time", side_effect=[0.0, 3.5]):
        success, elapsed, final_probe, timeline = _run_one(bot, {"state": "detail_page"}, "第 1 轮")

    assert success is True
    assert elapsed == 3.5
    assert final_probe["state"] == "order_confirm_page"


def test_run_benchmark_rejects_invalid_runs():
    with pytest.raises(ValueError, match="runs 必须大于等于 1"):
        run_benchmark(Mock(), runs=0)


def test_run_benchmark_raises_for_negative_runs():
    bot = Mock()
    with pytest.raises(ValueError):
        run_benchmark(bot, runs=-1)


def test_run_benchmark_collects_results_and_recovery():
    bot = Mock()
    bot.config.price = "580元"
    bot.config.price_index = 2
    bot.probe_current_page.side_effect = [
        {"state": "order_confirm_page", "submit_button": True},
        {"state": "order_confirm_page", "submit_button": True},
    ]
    bot.run_ticket_grabbing.side_effect = [True, True]
    bot._get_detail_title_text.return_value = "【成都】顽童MJ116 OGS巡回演唱会-成都站"
    bot._get_current_activity.return_value = ".trade.newtradeorder.ui.projectdetail.ui.activity.ProjectDetailActivity"

    with patch("mobile.hot_path_benchmark._require_detail_start", return_value={"state": "detail_page"}), \
         patch("mobile.hot_path_benchmark.time.time", side_effect=[0.0, 8.1, 9.0, 12.6, 13.0, 18.9]):
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


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

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
                "recovery_state": "detail_page",
                "step_timeline": [],
            },
            {
                "run": 2,
                "success": True,
                "elapsed_seconds": 2.5,
                "final_state": "order_confirm_page",
                "submit_button_ready": True,
                "recovery_seconds": None,
                "recovery_state": "order_confirm_page",
                "step_timeline": [],
            },
        ],
        "summary": _make_summary(2.5, 2.5, 2.5, 3.83, 2, 2),
    }

    report = format_report(payload)

    assert "演出: 【成都】顽童MJ116 OGS巡回演唱会-成都站" in report
    assert "1. 8.11s | success" in report
    assert "2. 2.50s | success" in report
    assert "runs=2, success=2/2" in report
    assert "汇总:" in report
    assert "avg/min/max" in report
    assert "recovery avg = 3.83s" in report


def test_format_report_no_recovery():
    payload = {
        "title": "张杰演唱会",
        "initial_state": "detail_page",
        "initial_activity": None,
        "price": "580元",
        "price_index": 0,
        "results": [
            {
                "run": 1,
                "success": False,
                "elapsed_seconds": 3.5,
                "final_state": "detail_page",
                "submit_button_ready": False,
                "recovery_seconds": None,
                "recovery_state": "detail_page",
                "step_timeline": [],
            }
        ],
        "summary": _make_summary(3.5, 3.5, 3.5, None, 1, 0),
    }
    report = format_report(payload)
    assert "recover=" not in report
    assert "fail" in report
    assert "recovery avg = -" in report


def test_format_report_no_title():
    payload = {
        "title": None,
        "initial_state": "sku_page",
        "initial_activity": None,
        "price": "280元",
        "price_index": 1,
        "results": [
            {
                "run": 1,
                "success": True,
                "elapsed_seconds": 2.0,
                "final_state": "order_confirm_page",
                "submit_button_ready": True,
                "recovery_seconds": None,
                "recovery_state": "order_confirm_page",
                "step_timeline": [],
            }
        ],
        "summary": _make_summary(2.0, 2.0, 2.0, None, 1, 1),
    }
    report = format_report(payload)
    assert "未识别" in report


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def test_main_rejects_runs_below_one(capsys):
    assert hot_path_benchmark.main(["--runs", "0"]) == 1
    assert "runs 必须大于等于 1" in capsys.readouterr().err


def test_main_returns_one_on_exception(capsys):
    with patch("mobile.hot_path_benchmark.Config.load_config", side_effect=RuntimeError("boom")):
        assert hot_path_benchmark.main([]) == 1

    assert "热路径压测失败: boom" in capsys.readouterr().err


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


def test_main_exception_returns_1(tmp_path):
    result = hot_path_benchmark.main(["--config", str(tmp_path / "nonexistent.jsonc"), "--runs", "1"])
    assert result == 1


def test_main_success_returns_0():
    mock_payload = {
        "title": "张杰演唱会",
        "initial_state": "detail_page",
        "initial_activity": "SomeActivity",
        "price": "580元",
        "price_index": 0,
        "results": [
            {
                "run": 1,
                "success": True,
                "elapsed_seconds": 3.5,
                "final_state": "order_confirm_page",
                "submit_button_ready": True,
                "recovery_seconds": None,
                "recovery_state": "order_confirm_page",
                "step_timeline": [],
            }
        ],
        "summary": _make_summary(3.5, 3.5, 3.5, None, 1, 1),
    }
    with patch("mobile.hot_path_benchmark.Config.load_config") as mock_load, \
         patch("mobile.hot_path_benchmark.build_benchmark_config") as mock_build, \
         patch("mobile.hot_path_benchmark.DamaiBot") as mock_bot_cls, \
         patch("mobile.hot_path_benchmark.run_benchmark", return_value=mock_payload):
        mock_load.return_value = Mock()
        mock_build.return_value = Mock()
        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot_cls.return_value = mock_bot
        result = hot_path_benchmark.main(["--runs", "1"])
    assert result == 0


def test_main_bot_driver_quit_called():
    mock_payload = {
        "title": "测试",
        "initial_state": "detail_page",
        "initial_activity": None,
        "price": "580元",
        "price_index": 0,
        "results": [
            {"run": 1, "success": True, "elapsed_seconds": 3.0,
             "final_state": "order_confirm_page", "submit_button_ready": True,
             "recovery_seconds": None, "recovery_state": "order_confirm_page", "step_timeline": []},
        ],
        "summary": _make_summary(3.0, 3.0, 3.0, None, 1, 1),
    }
    mock_driver = Mock()
    with patch("mobile.hot_path_benchmark.Config.load_config") as mock_load, \
         patch("mobile.hot_path_benchmark.build_benchmark_config") as mock_build, \
         patch("mobile.hot_path_benchmark.DamaiBot") as mock_bot_cls, \
         patch("mobile.hot_path_benchmark.run_benchmark", return_value=mock_payload):
        mock_load.return_value = Mock()
        mock_build.return_value = Mock()
        mock_bot = Mock()
        mock_bot.driver = mock_driver
        mock_bot_cls.return_value = mock_bot
        hot_path_benchmark.main(["--runs", "1"])
    mock_driver.quit.assert_called_once()


def test_format_report_includes_step_timeline_lines():
    payload = {
        "title": "测试演出",
        "initial_state": "detail_page",
        "initial_activity": "SomeActivity",
        "price": "680元",
        "price_index": 1,
        "results": [
            {
                "run": 1,
                "success": True,
                "elapsed_seconds": 6.2,
                "final_state": "order_confirm_page",
                "submit_button_ready": True,
                "recovery_seconds": None,
                "recovery_state": "order_confirm_page",
                "step_timeline": [
                    {"level": "INFO", "message": "开始执行正式抢票：会尝试提交订单", "delta_seconds": 0.0},
                    {"level": "INFO", "message": "选择票价...", "delta_seconds": 1.2},
                ],
            }
        ],
        "summary": _make_summary(6.2, 6.2, 6.2, None, 1, 1),
    }

    report = format_report(payload)

    assert "步骤耗时:" in report
    assert "01. +0.00s [INFO] 开始执行正式抢票：会尝试提交订单" in report
    assert "02. +1.20s [INFO] 选择票价..." in report
