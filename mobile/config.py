# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "配置类"
__Created__ = 2023/10/27 09:54
"""
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config_validator import validate_url, validate_non_empty_list

DEFAULT_CONFIG_FILENAME = "config.jsonc"
LOCAL_CONFIG_FILENAME = "config.local.jsonc"
CONFIG_OVERRIDE_ENV_VAR = "HATICKETS_CONFIG_PATH"
DEFAULT_CONFIG_FILENAMES = (DEFAULT_CONFIG_FILENAME,)


def _strip_jsonc_comments(text):
    """移除 JSONC 文件中的 // 和 /* */ 注释"""
    # 移除单行注释（不在字符串内的 //）
    text = re.sub(r'(?<!:)//.*?$', '', text, flags=re.MULTILINE)
    # 移除多行注释
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def _load_config_dict_from_path(path):
    try:
        with open(path, 'r', encoding='utf-8') as config_file:
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
    with open(resolved_path, 'w', encoding='utf-8') as config_file:
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
    def __init__(self, server_url, keyword, users, city, date, price, price_index, if_commit_order,
                 probe_only=False, device_name="Android", udid=None, platform_version=None,
                 app_package="cn.damai", app_activity=".launcher.splash.SplashMainActivity",
                 sell_start_time=None, countdown_lead_ms=3000,
                 wait_cta_ready_timeout_ms=0,
                 fast_retry_count=8, fast_retry_interval_ms=120,
                 rush_mode=False,
                 item_url=None, item_id=None, auto_navigate=True,
                 target_title=None, target_venue=None):
        # Validate server_url
        validate_url(server_url, "server_url")

        # Validate users
        validate_non_empty_list(users, "users")

        # Validate price_index
        if not isinstance(price_index, int) or isinstance(price_index, bool) or price_index < 0:
            raise ValueError(f"price_index 必须是非负整数，实际值: {price_index!r}")

        has_item_reference = item_url is not None or item_id is not None
        if keyword is not None and (not isinstance(keyword, str) or len(keyword.strip()) == 0):
            raise ValueError(f"keyword 必须是非空字符串或 null，实际值: {keyword!r}")
        if keyword is None and not has_item_reference:
            raise ValueError("keyword 不能为空；如果不提供 keyword，至少需要提供 item_url 或 item_id")

        if not isinstance(if_commit_order, bool):
            raise ValueError(f"if_commit_order 必须是布尔值，实际值: {if_commit_order!r}")

        if not isinstance(probe_only, bool):
            raise ValueError(f"probe_only 必须是布尔值，实际值: {probe_only!r}")

        if not isinstance(device_name, str) or len(device_name.strip()) == 0:
            raise ValueError(f"device_name 必须是非空字符串，实际值: {device_name!r}")

        if udid is not None and (not isinstance(udid, str) or len(udid.strip()) == 0):
            raise ValueError(f"udid 必须是非空字符串或 null，实际值: {udid!r}")

        if platform_version is not None and (not isinstance(platform_version, str) or len(platform_version.strip()) == 0):
            raise ValueError(f"platform_version 必须是非空字符串或 null，实际值: {platform_version!r}")

        if not isinstance(app_package, str) or len(app_package.strip()) == 0:
            raise ValueError(f"app_package 必须是非空字符串，实际值: {app_package!r}")

        if not isinstance(app_activity, str) or len(app_activity.strip()) == 0:
            raise ValueError(f"app_activity 必须是非空字符串，实际值: {app_activity!r}")

        if item_url is not None:
            validate_url(item_url, "item_url")

        if item_id is not None and (not isinstance(item_id, str) or not item_id.strip().isdigit()):
            raise ValueError(f"item_id 必须是纯数字字符串或 null，实际值: {item_id!r}")

        if not isinstance(auto_navigate, bool):
            raise ValueError(f"auto_navigate 必须是布尔值，实际值: {auto_navigate!r}")

        if target_title is not None and (not isinstance(target_title, str) or len(target_title.strip()) == 0):
            raise ValueError(f"target_title 必须是非空字符串或 null，实际值: {target_title!r}")

        if target_venue is not None and (not isinstance(target_venue, str) or len(target_venue.strip()) == 0):
            raise ValueError(f"target_venue 必须是非空字符串或 null，实际值: {target_venue!r}")

        # Validate sell_start_time
        if sell_start_time is not None:
            if not isinstance(sell_start_time, str):
                raise ValueError(f"sell_start_time 必须是 ISO 格式的时间字符串或 null，实际值: {sell_start_time!r}")
            try:
                datetime.fromisoformat(sell_start_time)
            except (ValueError, TypeError):
                raise ValueError(f"sell_start_time 无法解析为 ISO 时间格式，实际值: {sell_start_time!r}")

        # Validate countdown_lead_ms
        if not isinstance(countdown_lead_ms, int) or isinstance(countdown_lead_ms, bool) or countdown_lead_ms < 0:
            raise ValueError(f"countdown_lead_ms 必须是非负整数，实际值: {countdown_lead_ms!r}")

        if not isinstance(wait_cta_ready_timeout_ms, int) or \
                isinstance(wait_cta_ready_timeout_ms, bool) or wait_cta_ready_timeout_ms < 0:
            raise ValueError(
                f"wait_cta_ready_timeout_ms 必须是非负整数，实际值: {wait_cta_ready_timeout_ms!r}"
            )

        # Validate fast_retry_count
        if not isinstance(fast_retry_count, int) or isinstance(fast_retry_count, bool) or fast_retry_count < 0:
            raise ValueError(f"fast_retry_count 必须是非负整数，实际值: {fast_retry_count!r}")

        # Validate fast_retry_interval_ms
        if not isinstance(fast_retry_interval_ms, int) or isinstance(fast_retry_interval_ms, bool) or fast_retry_interval_ms < 0:
            raise ValueError(f"fast_retry_interval_ms 必须是非负整数，实际值: {fast_retry_interval_ms!r}")

        if not isinstance(rush_mode, bool):
            raise ValueError(f"rush_mode 必须是布尔值，实际值: {rush_mode!r}")

        self.server_url = server_url
        self.keyword = keyword.strip() if isinstance(keyword, str) else None
        self.users = users
        self.city = city
        self.date = date
        self.price = price
        self.price_index = price_index
        self.if_commit_order = if_commit_order
        self.probe_only = probe_only
        self.device_name = device_name
        self.udid = udid
        self.platform_version = platform_version
        self.app_package = app_package
        self.app_activity = app_activity
        self.sell_start_time = sell_start_time
        self.countdown_lead_ms = countdown_lead_ms
        self.wait_cta_ready_timeout_ms = wait_cta_ready_timeout_ms
        self.fast_retry_count = fast_retry_count
        self.fast_retry_interval_ms = fast_retry_interval_ms
        self.rush_mode = rush_mode
        self.item_url = item_url
        self.item_id = item_id
        self.auto_navigate = auto_navigate
        self.target_title = target_title.strip() if isinstance(target_title, str) else None
        self.target_venue = target_venue.strip() if isinstance(target_venue, str) else None

    def to_dict(self):
        """Return the config as a plain dictionary for rewriting config.jsonc."""
        return {
            "server_url": self.server_url,
            "device_name": self.device_name,
            "udid": self.udid,
            "platform_version": self.platform_version,
            "app_package": self.app_package,
            "app_activity": self.app_activity,
            "item_url": self.item_url,
            "item_id": self.item_id,
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
        }

    @staticmethod
    def load_config(config_path=None):
        config = load_config_dict(config_path)

        required_keys = ['server_url', 'users', 'city', 'date', 'price', 'price_index', 'if_commit_order']
        missing = [k for k in required_keys if k not in config]
        if missing:
            raise KeyError(f"配置文件缺少必需字段: {', '.join(missing)}")

        if "keyword" not in config and "item_url" not in config and "item_id" not in config:
            raise KeyError("配置文件缺少必需字段: keyword 或 item_url 或 item_id")

        return Config(config['server_url'],
                      config.get('keyword'),
                      config['users'],
                      config['city'],
                      config['date'],
                      config['price'],
                      config['price_index'],
                      config['if_commit_order'],
                      config.get('probe_only', False),
                      config.get('device_name', 'Android'),
                      config.get('udid'),
                      config.get('platform_version'),
                      config.get('app_package', 'cn.damai'),
                      config.get('app_activity', '.launcher.splash.SplashMainActivity'),
                      config.get('sell_start_time'),
                      config.get('countdown_lead_ms', 3000),
                      config.get('wait_cta_ready_timeout_ms', 0),
                      config.get('fast_retry_count', 8),
                      config.get('fast_retry_interval_ms', 120),
                      config.get('rush_mode', False),
                      config.get('item_url'),
                      config.get('item_id'),
                      config.get('auto_navigate', True),
                      config.get('target_title'),
                      config.get('target_venue'))
