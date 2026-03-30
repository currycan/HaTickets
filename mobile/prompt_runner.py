# -*- coding: UTF-8 -*-
"""Prompt-driven entrypoint for the Damai mobile flow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from mobile.config import Config, load_config_dict, save_config_dict
    from mobile.damai_app import DamaiBot
    from mobile.logger import get_logger
    from mobile.prompt_parser import choose_price_option, is_price_option_available, parse_prompt
except ImportError:
    from config import Config, load_config_dict, save_config_dict
    from damai_app import DamaiBot
    from logger import get_logger
    from prompt_parser import choose_price_option, is_price_option_available, parse_prompt


logger = get_logger(__name__)

MODE_FLAGS = {
    "apply": {"probe_only": True, "if_commit_order": False, "execute": False},
    "probe": {"probe_only": True, "if_commit_order": False, "execute": True},
    "confirm": {"probe_only": False, "if_commit_order": False, "execute": True},
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config_path() -> Path:
    mobile_dir = _repo_root() / "mobile"
    local_path = mobile_dir / "config.local.jsonc"
    if local_path.exists():
        return local_path
    return mobile_dir / "config.jsonc"


def _split_city_and_venue(venue_text: str) -> tuple[str | None, str]:
    text = (venue_text or "").replace("\u00a0", " ").strip()
    if "·" in text:
        left, right = [part.strip() for part in text.split("·", 1)]
        return left.replace("市", ""), right
    return None, text


def _format_price_option(option: dict) -> str:
    tag = f" [{option['tag']}]" if option.get("tag") else ""
    source = " (OCR)" if option.get("source") == "ocr" else ""
    return f"[{option['index']}] {option.get('text') or '(未识别)'}{tag}{source}"


def _format_summary(intent, discovery, chosen_price):
    summary = discovery["summary"]
    candidates = discovery.get("search_results") or []

    lines = [
        f"提示词: {intent.raw_prompt}",
        f"搜索关键词: {discovery['used_keyword']}",
        f"数量: {intent.quantity}",
        f"目标日期: {intent.date or '待确认'}",
        f"票档偏好: {intent.price_hint or '待确认'}",
        "",
        "匹配结果:",
        f"- 标题: {summary.get('title') or '未识别'}",
        f"- 场馆: {summary.get('venue') or '未识别'}",
        f"- 页面状态: {summary.get('state')}",
        f"- 预约流: {'是' if summary.get('reservation_mode') else '否'}",
        f"- 可见场次: {', '.join(summary.get('dates') or []) or '未识别'}",
        "- 可见票档:",
    ]

    price_options = summary.get("price_options") or []
    if price_options:
        for option in price_options:
            lines.append(f"  {_format_price_option(option)}")
    else:
        lines.append("  未识别")

    if candidates:
        lines.append("- 搜索候选:")
        for candidate in candidates[:3]:
            lines.append(
                f"  {candidate['score']:>3} | {candidate['title']} | {candidate.get('city') or '-'} | "
                f"{candidate.get('venue') or '-'} | {candidate.get('time') or '-'}"
            )

    if chosen_price:
        lines.append(f"- 推荐票档: {_format_price_option(chosen_price)}")
    else:
        lines.append("- 推荐票档: 未能自动确定，需要确认")

    return "\n".join(lines)


def _prompt_yes_no(message: str) -> bool:
    reply = input(f"{message} (y/N): ").strip().lower()
    return reply in {"y", "yes"}


def _prompt_choice(message: str, options: list[str]) -> str | None:
    print(message)
    for option in options:
        print(option)
    reply = input("请输入序号，直接回车取消: ").strip()
    return reply or None


def _resolve_confirmed_date(intent, summary, assume_yes: bool):
    visible_dates = summary.get("dates") or []
    if intent.date and (not visible_dates or intent.date in visible_dates):
        return intent.date

    if len(visible_dates) == 1:
        return visible_dates[0]

    if not visible_dates:
        return None

    if assume_yes:
        raise ValueError("提示词日期与页面可见场次未能自动对齐，无法在 --yes 模式下安全继续")

    choice = _prompt_choice(
        "未能自动确定场次，请从以下日期中选择：",
        [f"[{idx}] {value}" for idx, value in enumerate(visible_dates)],
    )
    if choice is None:
        return None

    index = int(choice)
    return visible_dates[index]


def _resolve_confirmed_price(intent, summary, chosen_price, assume_yes: bool):
    if chosen_price:
        return chosen_price

    available = [option for option in summary.get("price_options") or [] if is_price_option_available(option)]
    if len(available) == 1:
        return available[0]

    if not available:
        return None

    if assume_yes:
        raise ValueError("提示词票档偏好未能自动映射到明确票档，无法在 --yes 模式下安全继续")

    choice = _prompt_choice(
        "未能自动确定票档，请从以下列表中选择：",
        [_format_price_option(option) for option in available],
    )
    if choice is None:
        return None

    selected_index = int(choice)
    for option in available:
        if option["index"] == selected_index:
            return option
    raise ValueError(f"无效的 price_index: {selected_index}")


def build_updated_config(base_config: dict, intent, discovery: dict, date_text: str, price_option: dict, mode: str) -> dict:
    flags = MODE_FLAGS[mode]
    summary = discovery["summary"]
    candidate = (discovery.get("search_results") or [{}])[0]

    inferred_city, inferred_venue = _split_city_and_venue(summary.get("venue") or "")
    title = summary.get("title") or candidate.get("title")
    venue = inferred_venue or candidate.get("venue")
    config_data = dict(base_config)
    config_data.update({
        "item_url": None,
        "item_id": None,
        "keyword": discovery["used_keyword"],
        "target_title": title if title and title != "未识别" else None,
        "target_venue": venue if venue and venue != "未识别" else None,
        "users": base_config["users"],
        "city": intent.city or candidate.get("city") or inferred_city or base_config.get("city"),
        "date": date_text,
        "price": price_option["text"],
        "price_index": price_option["index"],
        "probe_only": flags["probe_only"],
        "if_commit_order": flags["if_commit_order"],
        "auto_navigate": True,
    })
    return config_data


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="自然语言提示词驱动的大麦 mobile 流程")
    parser.add_argument("prompt", help="例如：帮我抢一张 4 月 6 号张杰的演唱会门票，内场")
    parser.add_argument(
        "--mode",
        choices=["summary", "apply", "probe", "confirm"],
        default="summary",
        help="summary=只查询摘要；apply=写配置不执行；probe=写配置并执行探测；confirm=写配置并验证到确认页前",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="自动确认，不再交互提问")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config_path = _config_path()
    base_config_dict = load_config_dict(str(config_path))
    base_config = Config.load_config(str(config_path))
    intent = parse_prompt(args.prompt)

    query_config = Config(**{
        **base_config.to_dict(),
        "keyword": intent.search_keyword,
        "target_title": None,
        "target_venue": None,
        "item_url": None,
        "item_id": None,
        "if_commit_order": False,
        "probe_only": True,
        "city": intent.city or base_config.city,
        "date": intent.date or base_config.date,
        "price": intent.price_hint or base_config.price,
        "price_index": base_config.price_index,
        "auto_navigate": True,
    })

    bot = None
    try:
        bot = DamaiBot(config=query_config)
        bot.dismiss_startup_popups()
        page_probe = bot.probe_current_page()
        discovery = bot.discover_target_event(intent.candidate_keywords, initial_probe=page_probe)
        if not discovery:
            raise RuntimeError("未能根据提示词打开目标演出")

        discovery["summary"] = bot.inspect_current_target_event(discovery.get("page_probe"))
        chosen_price = choose_price_option(intent, discovery["summary"].get("price_options") or [])
        print(_format_summary(intent, discovery, chosen_price))

        if args.mode == "summary":
            return 0

        date_text = _resolve_confirmed_date(intent, discovery["summary"], args.yes)
        if not date_text:
            logger.info("未确认场次，取消写入配置")
            return 1

        price_option = _resolve_confirmed_price(intent, discovery["summary"], chosen_price, args.yes)
        if not price_option:
            logger.info("未确认票档，取消写入配置")
            return 1

        updated_config_dict = build_updated_config(base_config_dict, intent, discovery, date_text, price_option, args.mode)

        if not args.yes and not _prompt_yes_no("确认将以上结果写入 mobile/config.jsonc 吗？"):
            logger.info("用户取消写入配置")
            return 1

        save_config_dict(updated_config_dict, str(config_path))
        print(f"\n已更新配置: {config_path}")

        if not MODE_FLAGS[args.mode]["execute"]:
            return 0

        bot.config = Config(**updated_config_dict)
        bot.item_detail = None
        success = bot.run_with_retry(max_retries=1)
        return 0 if success else 1
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
