# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "配置类"
__Created__ = 2023/10/27 09:54
"""

import json
import logging
import re
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config_validator import validate_non_empty_list

DEFAULT_CONFIG_FILENAME = "config.jsonc"
LOCAL_CONFIG_FILENAME = "config.local.jsonc"
CONFIG_OVERRIDE_ENV_VAR = "HATICKETS_CONFIG_PATH"
DEFAULT_CONFIG_FILENAMES = (DEFAULT_CONFIG_FILENAME,)

PRICE_INDEX_LARGE_WARNING_THRESHOLD = 50

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """配置加载/校验失败。继承 ValueError 以保持现有 except 兼容。"""


def _strip_jsonc_comments(text):
    """移除 JSONC 文件中的 // 和 /* */ 注释"""
    # 移除单行注释（不在字符串内的 //）
    text = re.sub(r"(?<!:)//.*?$", "", text, flags=re.MULTILINE)
    # 移除多行注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def _load_config_dict_from_path(path):
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            raw_text = config_file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"配置文件未找到: {path}")

    try:
        return json.loads(_strip_jsonc_comments(raw_text))
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件格式错误: {e}")


def _dump_config_dict(config_dict):
    return json.dumps(config_dict, ensure_ascii=False, indent=2) + "\n"


def _resolve_explicit_config_path(config_path=None):
    if config_path is not None:
        return os.fspath(config_path)

    env_path = os.environ.get(CONFIG_OVERRIDE_ENV_VAR)
    if env_path and env_path.strip():
        return env_path.strip()

    return None


def _resolve_existing_config_path(config_path=None):
    explicit_path = _resolve_explicit_config_path(config_path)
    if explicit_path is not None:
        if os.path.exists(explicit_path):
            return explicit_path
        raise FileNotFoundError(f"配置文件未找到: {explicit_path}")

    if os.path.exists(DEFAULT_CONFIG_FILENAME):
        return DEFAULT_CONFIG_FILENAME

    raise FileNotFoundError(f"配置文件未找到: {DEFAULT_CONFIG_FILENAME}")


def _resolve_writable_config_path(config_path=None):
    explicit_path = _resolve_explicit_config_path(config_path)
    if explicit_path is not None:
        return explicit_path

    return DEFAULT_CONFIG_FILENAME


def load_config_dict(config_path=None):
    """Load a JSONC config file into a plain dictionary."""
    return _load_config_dict_from_path(_resolve_existing_config_path(config_path))


def save_config_dict(config_dict, config_path=None):
    """Persist a config dictionary back to disk as UTF-8 JSON."""
    resolved_path = _resolve_writable_config_path(config_path)
    with open(resolved_path, "w", encoding="utf-8") as config_file:
        config_file.write(_dump_config_dict(config_dict))


def update_runtime_mode(probe_only, if_commit_order, config_path=None):
    """Update runtime mode flags in the target config file and persist them."""
    if not isinstance(probe_only, bool):
        raise ValueError(f"probe_only 必须是布尔值，实际值: {probe_only!r}")
    if not isinstance(if_commit_order, bool):
        raise ValueError(f"if_commit_order 必须是布尔值，实际值: {if_commit_order!r}")

    config_dict = load_config_dict(config_path)
    previous_flags = {
        "probe_only": config_dict.get("probe_only", False),
        "if_commit_order": config_dict.get("if_commit_order"),
    }
    config_dict["probe_only"] = probe_only
    config_dict["if_commit_order"] = if_commit_order
    save_config_dict(config_dict, config_path)
    return previous_flags, {
        "probe_only": probe_only,
        "if_commit_order": if_commit_order,
    }


class Config:
    def __init__(
        self,
        keyword,
        users,
        city,
        date,
        price,
        price_index,
        if_commit_order,
        probe_only=False,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        sell_start_time=None,
        countdown_lead_ms=3000,
        wait_cta_ready_timeout_ms=0,
        fast_retry_count=8,
        fast_retry_interval_ms=120,
        rush_mode=False,
        rush_skip_session=False,
        rush_skip_price_dump=True,
        rush_aggressive_retry=True,
        auto_navigate=True,
        target_title=None,
        target_venue=None,
        serial=None,
        # Deprecated Appium-era params — accepted for config file compat, ignored
        driver_backend="u2",
        server_url=None,
        device_name="Android",
        udid=None,
        platform_version=None,
    ):

        # Validate users
        validate_non_empty_list(users, "users")

        # Validate price_index
        if (
            not isinstance(price_index, int)
            or isinstance(price_index, bool)
            or price_index < 0
        ):
            raise ValueError(f"price_index 必须是非负整数，实际值: {price_index!r}")

        if keyword is None or not isinstance(keyword, str) or len(keyword.strip()) == 0:
            raise ValueError(f"keyword 不能为空，必须是非空字符串，实际值: {keyword!r}")

        if not isinstance(if_commit_order, bool):
            raise ValueError(
                f"if_commit_order 必须是布尔值，实际值: {if_commit_order!r}"
            )

        if not isinstance(probe_only, bool):
            raise ValueError(f"probe_only 必须是布尔值，实际值: {probe_only!r}")

        if serial is not None and (
            not isinstance(serial, str) or len(serial.strip()) == 0
        ):
            raise ValueError(f"serial 必须是非空字符串或 null，实际值: {serial!r}")

        if not isinstance(app_package, str) or len(app_package.strip()) == 0:
            raise ValueError(f"app_package 必须是非空字符串，实际值: {app_package!r}")

        if not isinstance(app_activity, str) or len(app_activity.strip()) == 0:
            raise ValueError(f"app_activity 必须是非空字符串，实际值: {app_activity!r}")

        if not isinstance(auto_navigate, bool):
            raise ValueError(f"auto_navigate 必须是布尔值，实际值: {auto_navigate!r}")

        if target_title is not None and (
            not isinstance(target_title, str) or len(target_title.strip()) == 0
        ):
            raise ValueError(
                f"target_title 必须是非空字符串或 null，实际值: {target_title!r}"
            )

        if target_venue is not None and (
            not isinstance(target_venue, str) or len(target_venue.strip()) == 0
        ):
            raise ValueError(
                f"target_venue 必须是非空字符串或 null，实际值: {target_venue!r}"
            )

        # Validate sell_start_time
        if sell_start_time is not None:
            if not isinstance(sell_start_time, str):
                raise ValueError(
                    f"sell_start_time 必须是 ISO 格式的时间字符串或 null，实际值: {sell_start_time!r}"
                )
            try:
                datetime.fromisoformat(sell_start_time)
            except (ValueError, TypeError):
                raise ValueError(
                    f"sell_start_time 无法解析为 ISO 时间格式，实际值: {sell_start_time!r}"
                )

        # Validate countdown_lead_ms
        if (
            not isinstance(countdown_lead_ms, int)
            or isinstance(countdown_lead_ms, bool)
            or countdown_lead_ms < 0
        ):
            raise ValueError(
                f"countdown_lead_ms 必须是非负整数，实际值: {countdown_lead_ms!r}"
            )

        if (
            not isinstance(wait_cta_ready_timeout_ms, int)
            or isinstance(wait_cta_ready_timeout_ms, bool)
            or wait_cta_ready_timeout_ms < 0
        ):
            raise ValueError(
                f"wait_cta_ready_timeout_ms 必须是非负整数，实际值: {wait_cta_ready_timeout_ms!r}"
            )

        # Validate fast_retry_count
        if (
            not isinstance(fast_retry_count, int)
            or isinstance(fast_retry_count, bool)
            or fast_retry_count < 0
        ):
            raise ValueError(
                f"fast_retry_count 必须是非负整数，实际值: {fast_retry_count!r}"
            )

        # Validate fast_retry_interval_ms
        if (
            not isinstance(fast_retry_interval_ms, int)
            or isinstance(fast_retry_interval_ms, bool)
            or fast_retry_interval_ms < 0
        ):
            raise ValueError(
                f"fast_retry_interval_ms 必须是非负整数，实际值: {fast_retry_interval_ms!r}"
            )

        if not isinstance(rush_mode, bool):
            raise ValueError(f"rush_mode 必须是布尔值，实际值: {rush_mode!r}")

        for _name, _value in (
            ("rush_skip_session", rush_skip_session),
            ("rush_skip_price_dump", rush_skip_price_dump),
            ("rush_aggressive_retry", rush_aggressive_retry),
        ):
            if not isinstance(_value, bool):
                raise ValueError(f"{_name} 必须是布尔值，实际值: {_value!r}")

        self.keyword = keyword.strip()
        self.users = users
        self.city = city
        self.date = date
        self.price = price
        self.price_index = price_index
        self.if_commit_order = if_commit_order
        self.probe_only = probe_only
        self.app_package = app_package
        self.app_activity = app_activity
        self.sell_start_time = sell_start_time
        self.countdown_lead_ms = countdown_lead_ms
        self.wait_cta_ready_timeout_ms = wait_cta_ready_timeout_ms
        self.fast_retry_count = fast_retry_count
        self.fast_retry_interval_ms = fast_retry_interval_ms
        self.rush_mode = rush_mode
        # rush_mode 是 alias：当前 release 周期保留兼容；W4 评估废弃。
        # 解析规则：rush_mode=True 时统一翻转 3 个子开关到「快速」侧；
        # 但 rush_skip_session 强制为 False — 多场次场景下永远不能跳过选场（issue #25 根因）。
        if rush_mode:
            if rush_skip_session:
                logger.warning(
                    "rush_mode=True 不会启用 rush_skip_session（多场次场景需选场）"
                )
            self.rush_skip_session = False
            self.rush_skip_price_dump = rush_skip_price_dump
            self.rush_aggressive_retry = rush_aggressive_retry
        else:
            self.rush_skip_session = rush_skip_session
            self.rush_skip_price_dump = rush_skip_price_dump
            self.rush_aggressive_retry = rush_aggressive_retry

        logger.info(
            "rush effective: rush_mode=%s, skip_session=%s, skip_price_dump=%s, aggressive_retry=%s",
            self.rush_mode,
            self.rush_skip_session,
            self.rush_skip_price_dump,
            self.rush_aggressive_retry,
        )

        self.auto_navigate = auto_navigate
        self.target_title = (
            target_title.strip() if isinstance(target_title, str) else None
        )
        self.target_venue = (
            target_venue.strip() if isinstance(target_venue, str) else None
        )
        self.serial = serial.strip() if isinstance(serial, str) else None

    def to_dict(self):
        """Return the config as a plain dictionary for rewriting config.jsonc."""
        return {
            "serial": self.serial,
            "app_package": self.app_package,
            "app_activity": self.app_activity,
            "keyword": self.keyword,
            "target_title": self.target_title,
            "target_venue": self.target_venue,
            "users": self.users,
            "city": self.city,
            "date": self.date,
            "price": self.price,
            "price_index": self.price_index,
            "if_commit_order": self.if_commit_order,
            "probe_only": self.probe_only,
            "auto_navigate": self.auto_navigate,
            "sell_start_time": self.sell_start_time,
            "countdown_lead_ms": self.countdown_lead_ms,
            "wait_cta_ready_timeout_ms": self.wait_cta_ready_timeout_ms,
            "fast_retry_count": self.fast_retry_count,
            "fast_retry_interval_ms": self.fast_retry_interval_ms,
            "rush_mode": self.rush_mode,
            "rush_skip_session": self.rush_skip_session,
            "rush_skip_price_dump": self.rush_skip_price_dump,
            "rush_aggressive_retry": self.rush_aggressive_retry,
        }

    @staticmethod
    def load_config(config_path=None):
        config = load_config_dict(config_path)

        required_keys = [
            "users",
            "city",
            "date",
            "price",
            "price_index",
            "if_commit_order",
        ]
        missing = [k for k in required_keys if k not in config]
        if missing:
            raise KeyError(f"配置文件缺少必需字段: {', '.join(missing)}")

        if "keyword" not in config:
            raise KeyError("配置文件缺少必需字段: keyword")

        # P1 #31 — 启动期校验 price_index 范围
        raw_price_index = config["price_index"]
        if isinstance(raw_price_index, int) and not isinstance(raw_price_index, bool):
            if raw_price_index < 0:
                raise ConfigError(f"price_index 不能为负数（当前 {raw_price_index}）")
            if raw_price_index > PRICE_INDEX_LARGE_WARNING_THRESHOLD:
                logger.warning(
                    "price_index=%d 异常大（>%d），请确认 mobile/config.jsonc 是否填错",
                    raw_price_index,
                    PRICE_INDEX_LARGE_WARNING_THRESHOLD,
                )

        return Config(
            keyword=config.get("keyword"),
            users=config["users"],
            city=config["city"],
            date=config["date"],
            price=config["price"],
            price_index=config["price_index"],
            if_commit_order=config["if_commit_order"],
            probe_only=config.get("probe_only", False),
            app_package=config.get("app_package", "cn.damai"),
            app_activity=config.get(
                "app_activity", ".launcher.splash.SplashMainActivity"
            ),
            sell_start_time=config.get("sell_start_time"),
            countdown_lead_ms=config.get("countdown_lead_ms", 3000),
            wait_cta_ready_timeout_ms=config.get("wait_cta_ready_timeout_ms", 0),
            fast_retry_count=config.get("fast_retry_count", 8),
            fast_retry_interval_ms=config.get("fast_retry_interval_ms", 120),
            rush_mode=config.get("rush_mode", False),
            rush_skip_session=config.get("rush_skip_session", False),
            rush_skip_price_dump=config.get("rush_skip_price_dump", True),
            rush_aggressive_retry=config.get("rush_aggressive_retry", True),
            auto_navigate=config.get("auto_navigate", True),
            target_title=config.get("target_title"),
            target_venue=config.get("target_venue"),
            serial=config.get("serial"),
        )
