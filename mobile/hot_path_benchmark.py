# -*- coding: UTF-8 -*-
"""Benchmark the final mobile hot path from the current Damai ticket page."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import mean

try:
    from mobile.config import Config
    from mobile.damai_app import DamaiBot
except ImportError:
    from config import Config
    from damai_app import DamaiBot


RETRYABLE_STATES = {"detail_page", "sku_page"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_config_path() -> Path:
    mobile_dir = _repo_root() / "mobile"
    local_path = mobile_dir / "config.local.jsonc"
    if local_path.exists():
        return local_path
    return mobile_dir / "config.jsonc"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="从当前抢票界面出发，安全压测最后热路径")
    parser.add_argument(
        "--config",
        default=str(_default_config_path()),
        help="配置文件路径，默认优先使用 mobile/config.local.jsonc，否则回退到 mobile/config.jsonc",
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
        "item_url": None,
        "item_id": None,
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


def _require_retryable_start(bot: DamaiBot, run_label: str) -> dict:
    """Ensure the current page can start or recover into the hot path."""
    page_probe = bot.probe_current_page()
    if page_probe["state"] in RETRYABLE_STATES:
        return page_probe

    recovered_probe = bot._recover_to_detail_page_for_local_retry(page_probe)
    if recovered_probe["state"] in RETRYABLE_STATES:
        return recovered_probe

    raise RuntimeError(f"{run_label}前未处于可抢票页面，当前状态: {recovered_probe['state']}")


def summarize_results(results: list[dict]) -> dict:
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


def run_benchmark(bot: DamaiBot, runs: int) -> dict:
    """Run repeated safe hot-path measurements from the current manual-start page."""
    if runs < 1:
        raise ValueError("runs 必须大于等于 1")

    initial_probe = _require_retryable_start(bot, "开始")
    initial_title = bot._get_detail_title_text()
    initial_activity = bot._get_current_activity()

    results = []
    for index in range(runs):
        _require_retryable_start(bot, f"第 {index + 1} 轮开始")

        start_time = time.time()
        success = bot.run_ticket_grabbing()
        elapsed_seconds = round(time.time() - start_time, 2)
        final_probe = bot.probe_current_page()

        recovery_seconds = None
        recovery_state = final_probe["state"]
        if index < runs - 1:
            recovery_start = time.time()
            recovery_probe = bot._recover_to_detail_page_for_local_retry(final_probe)
            recovery_seconds = round(time.time() - recovery_start, 2)
            recovery_state = recovery_probe["state"]

        results.append({
            "run": index + 1,
            "success": bool(success),
            "elapsed_seconds": elapsed_seconds,
            "final_state": final_probe["state"],
            "submit_button_ready": bool(final_probe.get("submit_button")),
            "recovery_seconds": recovery_seconds,
            "recovery_state": recovery_state,
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

    summary = payload["summary"]
    lines.extend([
        "",
        "汇总:",
        f"runs={summary['runs']}, success={summary['success_count']}/{summary['runs']}",
        (
            f"elapsed avg/min/max = {summary['avg_elapsed_seconds']:.2f}s / "
            f"{summary['min_elapsed_seconds']:.2f}s / {summary['max_elapsed_seconds']:.2f}s"
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
