# -*- coding: UTF-8 -*-
"""Benchmark the final mobile hot path from the current Damai ticket page."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from statistics import mean

try:
    from mobile.config import CONFIG_OVERRIDE_ENV_VAR, Config
    from mobile.damai_app import DamaiBot
except ImportError:
    from config import CONFIG_OVERRIDE_ENV_VAR, Config
    from damai_app import DamaiBot


START_STATE = "detail_page"


class StepTimelineRecorder(logging.Handler):
    """Collect run-time step logs and per-step deltas from damai_app logger."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events = []
        self._last_created = None

    def emit(self, record: logging.LogRecord):
        if record.levelno < logging.INFO:
            return
        message = record.getMessage()
        if not message:
            return

        delta = 0.0 if self._last_created is None else round(record.created - self._last_created, 2)
        self._last_created = record.created
        self.events.append({
            "level": record.levelname,
            "message": message,
            "delta_seconds": delta,
        })


def _attach_timeline_recorder():
    """Attach timeline recorder to known damai_app logger names."""
    recorder = StepTimelineRecorder()
    attached_loggers = []
    for logger_name in ("mobile.damai_app", "damai_app"):
        target_logger = logging.getLogger(logger_name)
        target_logger.addHandler(recorder)
        attached_loggers.append(target_logger)
    return recorder, attached_loggers


def _detach_timeline_recorder(recorder: StepTimelineRecorder, attached_loggers):
    for target_logger in attached_loggers:
        try:
            target_logger.removeHandler(recorder)
        except Exception:
            pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_config_path() -> Path:
    env_path = os.environ.get(CONFIG_OVERRIDE_ENV_VAR)
    if env_path and env_path.strip():
        return Path(env_path.strip()).expanduser()

    mobile_dir = _repo_root() / "mobile"
    return mobile_dir / "config.jsonc"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="从详情页出发，安全压测最后热路径")
    parser.add_argument(
        "--config",
        default=str(_default_config_path()),
        help="配置文件路径，默认使用 mobile/config.jsonc；开发者可显式传入其他路径",
    )
    parser.add_argument("--runs", type=int, default=3, help="压测轮次，默认 3")
    parser.add_argument("--price", help="覆盖当前配置中的票档文本，例如 580元")
    parser.add_argument("--price-index", type=int, dest="price_index", help="覆盖当前配置中的 price_index")
    parser.add_argument("--city", help="覆盖当前配置中的城市")
    parser.add_argument("--date", help="覆盖当前配置中的场次日期，例如 04.18")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON 结果")
    return parser.parse_args(argv)


def build_benchmark_config(base_config: Config, args) -> Config:
    """Build a safe manual-start config for benchmarking without writing to disk."""
    config_data = base_config.to_dict()

    if args.price is not None:
        config_data["price"] = args.price
    if args.price_index is not None:
        config_data["price_index"] = args.price_index
    if args.city is not None:
        config_data["city"] = args.city
    if args.date is not None:
        config_data["date"] = args.date

    config_data.update({
        "target_title": None,
        "target_venue": None,
        "auto_navigate": False,
        "rush_mode": True,
        "if_commit_order": False,
        "probe_only": False,
        "sell_start_time": None,
        "wait_cta_ready_timeout_ms": 0,
    })
    return Config(**config_data)


_DETAIL_PAGE_PROBE = {
    "state": "detail_page", "purchase_button": True, "price_container": False,
    "quantity_picker": False, "submit_button": False, "reservation_mode": False,
    "pending_order_dialog": False,
}


def _fast_check_detail_page(bot: DamaiBot) -> dict | None:
    """Light-weight detail_page check using a single element lookup.

    Returns a minimal probe dict when on detail_page, None otherwise.
    Falls back to the full probe when the fast check is ambiguous.
    """
    from selenium.webdriver.common.by import By
    try:
        if hasattr(bot, "_find_all"):
            els = bot._find_all(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl")
        else:
            els = bot.driver.find_elements(
                by=By.ID,
                value="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            )
        if not isinstance(els, (list, tuple)):
            try:
                els = list(els)
            except TypeError:
                return None
        if els:
            return dict(_DETAIL_PAGE_PROBE)
    except Exception:
        pass
    return None


def _shell_back(bot: DamaiBot, count: int = 1):
    """Press Android back via shell (faster than Appium press_keycode).

    When count > 1, fires all back presses in a single shell call with
    short sleeps between them to allow page transitions.
    """
    if bot._using_u2() and hasattr(bot, "d") and bot.d is not None:
        if count == 1:
            bot.d.shell("input keyevent 4")
        else:
            # Batch: "input keyevent 4; sleep 0.2; input keyevent 4; ..."
            parts = []
            for i in range(count):
                parts.append("input keyevent 4")
                if i < count - 1:
                    parts.append("sleep 0.2")
            bot.d.shell("; ".join(parts))
    else:
        for _ in range(count):
            bot._press_keycode_safe(4, context="benchmark回退")
            if count > 1:
                time.sleep(0.2)


def _fast_recover_to_detail(bot: DamaiBot, max_backs: int = 4) -> dict:
    """Fast benchmark recovery: incremental back + check after each.

    Presses one back at a time and re-checks for detail_page to avoid
    overshooting when the app is already partway through the stack
    (e.g. on sku_page after a failed run).
    """
    # Already on detail?
    fast = _fast_check_detail_page(bot)
    if fast is not None:
        return fast

    # Incremental back: press one back at a time and re-check to avoid
    # overshooting when the app is already partway through the stack
    # (e.g. on sku_page after a failed run).
    for _ in range(max_backs):
        _shell_back(bot, count=1)
        time.sleep(0.15)
        fast = _fast_check_detail_page(bot)
        if fast is not None:
            return fast

    # Fallback: full probe (only reached in unusual page states).
    return bot.probe_current_page()


def _require_detail_start(bot: DamaiBot, run_label: str) -> dict:
    """Ensure each benchmark run always starts from detail_page."""
    # Fast path: single element lookup ~50ms vs full probe ~1.5s.
    fast = _fast_check_detail_page(bot)
    if fast is not None:
        return fast

    # Fast recovery: shell back + lightweight check (~1-2s total).
    recovered_probe = _fast_recover_to_detail(bot)
    if recovered_probe["state"] == START_STATE:
        return recovered_probe

    # Heavy fallback: full recovery via bot method.
    recovered_probe = bot._recover_to_detail_page_for_local_retry(recovered_probe)
    if recovered_probe["state"] == START_STATE:
        return recovered_probe

    raise RuntimeError(
        f"{run_label}前未回到 {START_STATE}，当前状态: {recovered_probe['state']}"
    )


def summarize_results(results: list[dict]) -> dict:
    """Summarise benchmark results across all runs."""
    elapsed_values = [item["elapsed_seconds"] for item in results]
    recovery_values = [item["recovery_seconds"] for item in results if item["recovery_seconds"] is not None]

    return {
        "runs": len(results),
        "success_count": sum(1 for item in results if item["success"]),
        "avg_elapsed_seconds": round(mean(elapsed_values), 2),
        "min_elapsed_seconds": round(min(elapsed_values), 2),
        "max_elapsed_seconds": round(max(elapsed_values), 2),
        "avg_recovery_seconds": round(mean(recovery_values), 2) if recovery_values else None,
    }


def _run_one(bot: DamaiBot, run_start_probe: dict, run_label: str) -> tuple[bool, float, dict, list]:
    """Execute one timed ticket-grabbing attempt and return (success, elapsed, final_probe, timeline)."""
    recorder, attached_loggers = _attach_timeline_recorder()
    try:
        start_time = time.time()
        success = bot.run_ticket_grabbing(initial_page_probe=run_start_probe)
        elapsed_seconds = round(time.time() - start_time, 2)
    finally:
        _detach_timeline_recorder(recorder, attached_loggers)
    final_probe = bot.probe_current_page()
    return bool(success), elapsed_seconds, final_probe, recorder.events


def run_benchmark(bot: DamaiBot, runs: int) -> dict:
    """Run repeated hot-path measurements from the current detail page.

    Each run measures the real end-to-end flow: detail page → click buy →
    SKU selection → order confirm → attendee selection (no pre-warm pass).
    This matches the real ticket-grabbing scenario where you get one shot.
    """
    if runs < 1:
        raise ValueError("runs 必须大于等于 1")

    initial_probe = _require_detail_start(bot, "开始")
    initial_title = bot._get_detail_title_text()
    initial_activity = bot._get_current_activity()

    results = []
    for index in range(runs):
        run_start_probe = initial_probe if index == 0 else _require_detail_start(bot, f"第 {index + 1} 轮开始")

        success, elapsed_seconds, final_probe, timeline = _run_one(
            bot, run_start_probe, f"第 {index + 1} 轮"
        )

        recovery_seconds = None
        recovery_state = final_probe["state"]
        if index < runs - 1:
            recovery_start = time.time()
            recovery_probe = _require_detail_start(bot, f"第 {index + 2} 轮准备")
            recovery_seconds = round(time.time() - recovery_start, 2)
            recovery_state = recovery_probe["state"]

        results.append({
            "run": index + 1,
            "success": success,
            "elapsed_seconds": elapsed_seconds,
            "final_state": final_probe["state"],
            "submit_button_ready": bool(final_probe.get("submit_button")),
            "recovery_seconds": recovery_seconds,
            "recovery_state": recovery_state,
            "step_timeline": timeline,
        })

    return {
        "title": initial_title,
        "initial_state": initial_probe["state"],
        "initial_activity": initial_activity,
        "price": bot.config.price,
        "price_index": bot.config.price_index,
        "results": results,
        "summary": summarize_results(results),
    }


def format_report(payload: dict) -> str:
    """Render a human-readable benchmark report."""
    lines = [
        f"演出: {payload.get('title') or '未识别'}",
        f"起始状态: {payload.get('initial_state')} | Activity: {payload.get('initial_activity') or '-'}",
        f"票档: {payload.get('price')} | price_index={payload.get('price_index')}",
        "",
        "每轮结果:",
    ]

    for item in payload["results"]:
        line = (
            f"{item['run']}. {item['elapsed_seconds']:.2f}s | "
            f"{'success' if item['success'] else 'fail'} | "
            f"final={item['final_state']} | submit_ready={item['submit_button_ready']}"
        )
        if item["recovery_seconds"] is not None:
            line += f" | recover={item['recovery_seconds']:.2f}s -> {item['recovery_state']}"
        lines.append(line)
        if item.get("step_timeline"):
            lines.append("   步骤耗时:")
            for step_index, step in enumerate(item["step_timeline"], start=1):
                lines.append(
                    f"   {step_index:02d}. +{step['delta_seconds']:.2f}s "
                    f"[{step['level']}] {step['message']}"
                )

    summary = payload["summary"]
    lines.extend([
        "",
        "汇总:",
        f"runs={summary['runs']}, success={summary['success_count']}/{summary['runs']}",
        (
            f"avg/min/max = {summary['avg_elapsed_seconds']:.2f}s / "
            f"{summary['min_elapsed_seconds']:.2f}s / {summary['max_elapsed_seconds']:.2f}s"
            f"  (n={summary['runs']})"
        ),
        (
            "recovery avg = "
            f"{summary['avg_recovery_seconds']:.2f}s" if summary["avg_recovery_seconds"] is not None else
            "recovery avg = -"
        ),
    ])
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    if args.runs < 1:
        print("runs 必须大于等于 1", file=sys.stderr)
        return 1

    config_path = Path(args.config).resolve()
    bot = None
    try:
        base_config = Config.load_config(str(config_path))
        runtime_config = build_benchmark_config(base_config, args)
        bot = DamaiBot(config=runtime_config)
        payload = run_benchmark(bot, args.runs)
    except Exception as exc:
        print(f"热路径压测失败: {exc}", file=sys.stderr)
        return 1
    finally:
        if bot is not None and bot.driver is not None:
            try:
                bot.driver.quit()
            except Exception:
                pass

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(format_report(payload))

    summary = payload["summary"]
    return 0 if summary["success_count"] == summary["runs"] else 1


if __name__ == "__main__":
    sys.exit(main())
