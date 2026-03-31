# -*- coding: UTF-8 -*-
"""Prompt-driven entrypoint for the Damai mobile flow."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from selenium.common.exceptions import WebDriverException

try:
    from mobile.config import CONFIG_OVERRIDE_ENV_VAR, Config, load_config_dict, save_config_dict
    from mobile.damai_app import DamaiBot
    from mobile.logger import get_logger
    from mobile.prompt_parser import choose_price_option, is_price_option_available, parse_prompt
except ImportError:
    from config import CONFIG_OVERRIDE_ENV_VAR, Config, load_config_dict, save_config_dict
    from damai_app import DamaiBot
    from logger import get_logger
    from prompt_parser import choose_price_option, is_price_option_available, parse_prompt


logger = get_logger(__name__)

MODE_FLAGS = {
    "apply": {"probe_only": True, "if_commit_order": False, "execute": False},
    "probe": {"probe_only": True, "if_commit_order": False, "execute": True},
}

_ANSI_RESET = "\033[0m"
_ANSI_STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE") == "1":
        return True
    if stream is None or not hasattr(stream, "isatty"):
        return False
    if not stream.isatty():
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def _paint(text: str, *styles: str, stream=None) -> str:
    stream = stream or sys.stdout
    if not _supports_color(stream):
        return text

    prefix = "".join(_ANSI_STYLES[style] for style in styles if style in _ANSI_STYLES)
    if not prefix:
        return text
    return f"{prefix}{text}{_ANSI_RESET}"


def _label(label_text: str, stream=None) -> str:
    return _paint(label_text, "bold", "cyan", stream=stream)


def _status_text(state: str, stream=None) -> str:
    style = {
        "sku_page": ("bold", "green"),
        "detail_page": ("bold", "green"),
        "order_confirm_page": ("bold", "green"),
        "search_page": ("bold", "yellow"),
        "homepage": ("bold", "yellow"),
    }.get(state, ("bold", "yellow"))
    return _paint(state, *style, stream=stream)


def _print_result(success: bool, detail: str, stream=None):
    stream = stream or (sys.stdout if success else sys.stderr)
    title = "执行结果：成功" if success else "执行结果：失败"
    title_style = ("bold", "green") if success else ("bold", "red")
    detail_style = ("green",) if success else ("red",)
    print(
        f"{_paint(title, *title_style, stream=stream)}\n"
        f"{_paint(detail, *detail_style, stream=stream)}",
        file=stream,
    )


def _config_path(config_path: str | None = None) -> Path:
    if config_path:
        return Path(config_path).expanduser()

    env_path = os.environ.get(CONFIG_OVERRIDE_ENV_VAR)
    if env_path and env_path.strip():
        return Path(env_path.strip()).expanduser()

    mobile_dir = _repo_root() / "mobile"
    return mobile_dir / "config.jsonc"


def _config_path_description(config_path: Path) -> str:
    """Return a user-facing description of where prompt mode will write config."""
    if config_path.name == "config.local.jsonc":
        return f"{config_path}（开发者本地覆盖配置）"
    return str(config_path)


def _list_connected_device_ids() -> list[str] | None:
    """Return adb-connected Android device ids, or None if adb is unavailable."""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    device_ids = []
    for line in result.stdout.splitlines():
        if "\tdevice" not in line:
            continue
        device_ids.append(line.split("\t", 1)[0].strip())
    return device_ids


def _read_device_android_version(udid: str) -> str | None:
    """Return the Android version reported by adb for a device."""
    try:
        result = subprocess.run(
            ["adb", "-s", udid, "shell", "getprop", "ro.build.version.release"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    return result.stdout.strip() or None


def _auto_sync_device_config(base_config: dict, mode: str) -> tuple[dict, str | None]:
    """Auto-sync udid/platform_version from the currently connected Android device when safe."""
    connected_devices = _list_connected_device_ids()
    if not connected_devices:
        return dict(base_config), None

    updated_config = dict(base_config)
    configured_udid = updated_config.get("udid")
    resolved_udid = None

    if len(connected_devices) == 1:
        resolved_udid = connected_devices[0]
    elif configured_udid in connected_devices:
        resolved_udid = configured_udid
    else:
        return updated_config, None

    changed_fields = []
    if resolved_udid and updated_config.get("udid") != resolved_udid:
        updated_config["udid"] = resolved_udid
        changed_fields.append("udid")

    resolved_platform = _read_device_android_version(resolved_udid) if resolved_udid else None
    if resolved_platform and updated_config.get("platform_version") != resolved_platform:
        updated_config["platform_version"] = resolved_platform
        changed_fields.append("platform_version")

    if not changed_fields:
        return updated_config, None

    prefix = "已自动识别当前设备配置"
    suffix = "；summary 模式仅本次运行使用，不会写回配置" if mode == "summary" else "；写配置时会一并保存"
    message = (
        f"{prefix}: udid={updated_config.get('udid')}, "
        f"platform_version={updated_config.get('platform_version')}{suffix}"
    )
    return updated_config, message


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


def _format_available_price_options(options: list[dict]) -> str:
    if not options:
        return "无"
    return "、".join(_format_price_option(option) for option in options)


def _format_human_date(date_text: str | None) -> str:
    if not date_text:
        return "<日期>"
    try:
        month_text, day_text = date_text.split(".", 1)
        return f"{int(month_text)} 月 {int(day_text)} 号"
    except (TypeError, ValueError):
        return date_text


def _format_quantity_text(quantity: int) -> str:
    if quantity <= 1:
        return "一张"
    return f"{quantity}张"


def _should_include_quantity(attendee_names, quantity: int, force_quantity: bool = False) -> bool:
    if force_quantity:
        return True
    if attendee_names:
        return quantity != len(attendee_names)
    return quantity > 1


def _build_prompt_suggestion(intent, attendee_names=None, quantity=None, force_quantity: bool = False) -> str:
    attendee_names = intent.attendee_names if attendee_names is None else attendee_names
    quantity = intent.quantity if quantity is None else quantity
    attendee_text = "、".join(attendee_names) if attendee_names else "<观演人姓名>"
    concert_name = f"{intent.artist}的演唱会门票" if intent.artist else "<演出名称>门票"

    opening = f"帮{attendee_text}抢"
    if _should_include_quantity(attendee_names, quantity, force_quantity=force_quantity):
        opening = f"{opening}{_format_quantity_text(quantity)}"

    parts = [opening, _format_human_date(intent.date), concert_name]
    if intent.seat_hint:
        parts.append(intent.seat_hint)
    if intent.numeric_price_hint:
        parts.append(f"票价 {intent.numeric_price_hint} 元")
    elif intent.price_hint:
        parts.append(intent.price_hint)
    return "，".join(parts)


def _build_prompt_template(intent, attendee_text="<观演人姓名列表>") -> str:
    concert_name = f"{intent.artist}的演唱会门票" if intent.artist else "<演出名称>门票"
    parts = [f"帮{attendee_text}抢", _format_human_date(intent.date), concert_name]
    if intent.seat_hint:
        parts.append(intent.seat_hint)
    if intent.numeric_price_hint:
        parts.append(f"票价 {intent.numeric_price_hint} 元")
    elif intent.price_hint:
        parts.append(intent.price_hint)
    return "，".join(parts)


def _build_retry_command(prompt_text: str, mode: str) -> str:
    return (
        f"./mobile/scripts/run_from_prompt.sh --mode {mode} --yes "
        f"{shlex.quote(prompt_text)}"
    )


def _build_missing_keyword_error(base_config: dict, mode: str) -> str:
    configured_user_list = base_config.get("users") or []
    configured_users = "、".join(configured_user_list) or "未配置"
    generic_prompt = "帮<观演人姓名列表>抢，<日期>，<演出名称>的演唱会门票，<票档偏好>"
    generic_command = _build_retry_command(generic_prompt, mode)

    configured_example = ""
    if configured_user_list:
        configured_prompt = (
            f"帮{'、'.join(configured_user_list)}抢，"
            "<日期>，<演出名称>的演唱会门票，<票档偏好>"
        )
        configured_command = _build_retry_command(configured_prompt, mode)
        configured_example = (
            f"2. 如果你就是给当前配置里的 {len(configured_user_list)} 位观演人买票：\n"
            f"{configured_command}\n"
        )

    return (
        "提示词有问题，已停止执行。\n"
        "缺少关键信息：演出名称或搜索关键词。\n"
        f"当前配置文件里的观演人是：{configured_users}\n"
        "当前无法判断你想抢哪场演出，所以不会继续搜索、连接 Appium 或写配置。\n"
        "请至少写清楚：观演人姓名、日期、演出名称。\n"
        "请直接复制下面任意一种格式，补全后重试：\n"
        f"1. 通用模板：\n"
        f"{generic_command}\n"
        f"{configured_example}"
        "示例提示词：\n"
        "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
    )


def _validate_prompt_requirements(intent, base_config: dict, mode: str):
    configured_user_list = base_config.get("users") or []
    configured_users = "、".join(configured_user_list) or "未配置"
    generic_prompt = _build_prompt_template(intent)
    generic_command = _build_retry_command(generic_prompt, mode)

    if not intent.attendee_names:
        configured_example = ""
        if configured_user_list:
            configured_prompt = _build_prompt_suggestion(
                intent,
                attendee_names=configured_user_list,
                quantity=len(configured_user_list),
            )
            configured_command = _build_retry_command(configured_prompt, mode)
            configured_example = (
                f"2. 如果你就是给当前配置里的 {len(configured_user_list)} 位观演人买票：\n"
                f"{configured_command}\n"
            )
        raise ValueError(
            "提示词有问题，已停止执行。\n"
            "缺少关键信息：观演人姓名。\n"
            f"当前配置文件里的观演人是：{configured_users}\n"
            "为了避免误用旧观演人，当前不会继续搜索、连接 Appium 或写配置。\n"
            "请直接复制下面任意一种格式，补全后重试：\n"
            f"1. 通用模板：\n"
            f"{generic_command}\n"
            f"{configured_example}"
            "规范提示词模板：\n"
            f"{generic_prompt}"
        )

    if intent.quantity_explicit and len(intent.attendee_names) != intent.quantity:
        attendee_count_prompt = _build_prompt_suggestion(
            intent,
            attendee_names=intent.attendee_names,
            quantity=len(intent.attendee_names),
        )
        attendee_count_command = _build_retry_command(attendee_count_prompt, mode)
        single_attendee_prompt = _build_prompt_suggestion(
            intent,
            attendee_names=intent.attendee_names[:max(1, min(intent.quantity, len(intent.attendee_names)))],
            quantity=min(intent.quantity, len(intent.attendee_names)),
        )
        single_attendee_command = _build_retry_command(single_attendee_prompt, mode)
        raise ValueError(
            "提示词有问题，已停止执行。\n"
            f"观演人数量与购票张数不一致：识别到 {len(intent.attendee_names)} 个观演人，"
            f"但你写的是 {_format_quantity_text(intent.quantity)}。\n"
            "为了避免误下单，当前不会继续搜索、连接 Appium 或写配置。\n"
            "请直接复制下面任意一条正确命令重新执行：\n"
            f"1. 给这 {len(intent.attendee_names)} 位观演人都买票：\n"
            f"{attendee_count_command}\n"
            f"2. 只买 {_format_quantity_text(min(intent.quantity, len(intent.attendee_names)))}：\n"
            f"{single_attendee_command}\n"
            "对应的规范提示词分别是：\n"
            f"- {attendee_count_prompt}\n"
            f"- {single_attendee_prompt}"
        )


def _format_summary(intent, discovery, chosen_price):
    summary = discovery["summary"]
    candidates = discovery.get("search_results") or []
    stdout = sys.stdout
    page_state = summary.get("state") or "unknown"
    reservation_text = "是" if summary.get("reservation_mode") else "否"
    recommendation = _format_price_option(chosen_price) if chosen_price else "未能自动确定，需要确认"

    lines = [
        f"{_label('提示词:', stream=stdout)} {intent.raw_prompt}",
        f"{_label('规范提示词:', stream=stdout)} {_paint(_build_prompt_suggestion(intent), 'bold', stream=stdout)}",
        f"{_label('搜索关键词:', stream=stdout)} {_paint(discovery['used_keyword'], 'magenta', stream=stdout)}",
        f"{_label('观演人:', stream=stdout)} {_paint(', '.join(intent.attendee_names) if intent.attendee_names else '未识别', 'yellow', stream=stdout)}",
        f"{_label('数量:', stream=stdout)} {_paint(str(intent.quantity), 'yellow', stream=stdout)}",
        f"{_label('目标日期:', stream=stdout)} {_paint(intent.date or '待确认', 'yellow', stream=stdout)}",
        f"{_label('票档偏好:', stream=stdout)} {_paint(intent.price_hint or '待确认', 'yellow', stream=stdout)}",
        "",
        _label("匹配结果:", stream=stdout),
        f"- {_label('标题:', stream=stdout)} {_paint(summary.get('title') or '未识别', 'green', stream=stdout)}",
        f"- {_label('场馆:', stream=stdout)} {_paint(summary.get('venue') or '未识别', 'green', stream=stdout)}",
        f"- {_label('页面状态:', stream=stdout)} {_status_text(page_state, stream=stdout)}",
        f"- {_label('预约流:', stream=stdout)} {_paint(reservation_text, 'yellow' if reservation_text == '是' else 'green', stream=stdout)}",
        f"- {_label('可见场次:', stream=stdout)} {_paint(', '.join(summary.get('dates') or []) or '未识别', 'blue', stream=stdout)}",
        f"- {_label('可见票档:', stream=stdout)}",
    ]

    price_options = summary.get("price_options") or []
    if price_options:
        for option in price_options:
            lines.append(f"  {_format_price_option(option)}")
    else:
        lines.append("  未识别")

    if candidates:
        lines.append(f"- {_label('搜索候选:', stream=stdout)}")
        for candidate in candidates[:3]:
            candidate_text = (
                f"{candidate['score']:>3} | {candidate['title']} | {candidate.get('city') or '-'} | "
                f"{candidate.get('venue') or '-'} | {candidate.get('time') or '-'}"
            )
            lines.append(
                _paint(f"  {candidate_text}", "green" if candidate is candidates[0] else "dim", stream=stdout)
            )

    lines.append(
        f"- {_label('推荐票档:', stream=stdout)} "
        f"{_paint(recommendation, 'green' if chosen_price else 'yellow', stream=stdout)}"
    )

    if intent.attendee_names:
        attendee_text = "、".join(intent.attendee_names)
        lines.append("")
        lines.append(_label("重要提醒:", stream=stdout))
        lines.append(
            f"- {_paint(f'请先在大麦 App 中确认你已添加并保存以下观演人：{attendee_text}', 'yellow', stream=stdout)}"
        )

    if intent.notes:
        lines.append("")
        lines.append(_label("提示:", stream=stdout))
        for note in intent.notes:
            lines.append(f"- {_paint(note, 'yellow', stream=stdout)}")

    return "\n".join(lines)


def _success_detail_for_mode(mode: str, target_config: str | None = None) -> str:
    if mode == "summary":
        return "已识别目标演出并输出摘要。你现在可以根据推荐票档继续执行 apply / probe。"
    if mode == "apply":
        return f"已更新配置文件：{target_config}。接下来可以执行 start_ticket_grabbing.sh 或继续用 prompt 的 probe 模式。"
    if mode == "probe":
        return "安全探测完成：脚本已验证目标演出页和关键购票控件。"
    return "任务已成功完成。"


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
        requested_hint = intent.price_hint or "未提供明确票档偏好"
        visible_options = _format_available_price_options(available)
        raise ValueError(
            "提示词票档偏好未能自动映射到明确票档，无法在 --yes 模式下安全继续。\n"
            f"提示词里的目标票档是：{requested_hint}\n"
            f"当前页面可选票档是：{visible_options}\n"
            "请修改提示词里的票价/票档描述后重试。"
        )

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
        "users": intent.attendee_names or base_config["users"],
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
        choices=["summary", "apply", "probe"],
        default="summary",
        help="summary=只查询摘要；apply=写配置不执行；probe=写配置并执行探测",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="自动确认，不再交互提问")
    parser.add_argument(
        "--config",
        help="显式指定配置文件路径；默认写入 mobile/config.jsonc。开发者本地覆盖可配合 HATICKETS_CONFIG_PATH 或 mobile/config.local.jsonc 使用。",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config_path = _config_path(args.config)
    bot = None
    try:
        base_config_dict = load_config_dict(str(config_path))
        try:
            intent = parse_prompt(args.prompt)
        except ValueError as exc:
            if str(exc) == "无法从提示词中提取搜索关键词":
                logger.error(_build_missing_keyword_error(base_config_dict, args.mode))
                _print_result(False, "输入信息不满足运行条件，本次已停止执行。请根据上面的提示修正后重试。")
                return 1
            raise
        _validate_prompt_requirements(intent, base_config_dict, args.mode)

        base_config_dict, device_sync_message = _auto_sync_device_config(base_config_dict, args.mode)
        if device_sync_message:
            logger.info(device_sync_message)
        base_config = Config(**{
            **Config.load_config(str(config_path)).to_dict(),
            "udid": base_config_dict.get("udid"),
            "platform_version": base_config_dict.get("platform_version"),
        })

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
            _print_result(True, _success_detail_for_mode(args.mode))
            return 0

        date_text = _resolve_confirmed_date(intent, discovery["summary"], args.yes)
        if not date_text:
            logger.info("未确认场次，取消写入配置")
            _print_result(False, "未确认场次，配置没有写入。请确认日期后重试。")
            return 1

        price_option = _resolve_confirmed_price(intent, discovery["summary"], chosen_price, args.yes)
        if not price_option:
            logger.info("未确认票档，取消写入配置")
            _print_result(False, "未确认票档，配置没有写入。请确认票档后重试。")
            return 1

        updated_config_dict = build_updated_config(base_config_dict, intent, discovery, date_text, price_option, args.mode)

        target_config = _config_path_description(config_path)
        if not args.yes and not _prompt_yes_no(f"确认将以上结果写入 {target_config} 吗？"):
            logger.info("用户取消写入配置")
            _print_result(False, "你已取消写入配置，本次没有修改任何文件。")
            return 1

        save_config_dict(updated_config_dict, str(config_path))
        print(f"\n{_label('已更新配置:', stream=sys.stdout)} {target_config}")

        if not MODE_FLAGS[args.mode]["execute"]:
            _print_result(True, _success_detail_for_mode(args.mode, target_config))
            return 0

        bot.config = Config(**updated_config_dict)
        bot.item_detail = None
        success = bot.run_with_retry(max_retries=1)
        if success:
            _print_result(True, _success_detail_for_mode(args.mode, target_config))
        else:
            failure_detail = "安全探测没有通过，请检查终端里的步骤日志后重试。"
            _print_result(False, failure_detail)
        return 0 if success else 1
    except (ValueError, KeyError) as exc:
        logger.error(str(exc))
        _print_result(False, "输入信息不满足运行条件，本次已停止执行。请根据上面的提示修正后重试。")
        return 1
    except RuntimeError as exc:
        logger.error(str(exc))
        _print_result(False, "未能根据提示词打开目标演出，请检查提示词、当前页面状态或大麦搜索结果后重试。")
        return 1
    except WebDriverException as exc:
        message = str(exc).splitlines()[0]
        logger.error(f"启动 Appium 会话失败: {message}")
        _print_result(False, "Appium 会话未成功建立，请先确认手机连接、Appium 服务和设备配置。")
        return 1
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
