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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.config_validator import validate_url, validate_non_empty_list


def _strip_jsonc_comments(text):
    """移除 JSONC 文件中的 // 和 /* */ 注释"""
    # 移除单行注释（不在字符串内的 //）
    text = re.sub(r'(?<!:)//.*?$', '', text, flags=re.MULTILINE)
    # 移除多行注释
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


class Config:
    def __init__(
        self,
        server_url,
        keyword,
        users,
        city,
        date,
        price,
        price_index,
        if_commit_order,
        city_index=0,
        date_index=0,
        date_strict=False,
        fast_mode=True,
        device_name="emulator-5554",
        platform_version="16",
        udid=None,
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        automation_name="UiAutomator2",
    ):
        # Validate server_url
        validate_url(server_url, "server_url")

        # Validate users
        validate_non_empty_list(users, "users")

        # Validate price_index
        if not isinstance(price_index, int) or isinstance(price_index, bool) or price_index < 0:
            raise ValueError(f"price_index 必须是非负整数，实际值: {price_index!r}")

        # Validate keyword
        if not isinstance(keyword, str) or len(keyword.strip()) == 0:
            raise ValueError(f"keyword 必须是非空字符串，实际值: {keyword!r}")

        self.server_url = server_url
        self.keyword = keyword
        self.users = users
        self.city = city
        self.date = date
        self.price = price
        self.price_index = price_index
        self.city_index = city_index
        self.date_index = date_index
        self.if_commit_order = if_commit_order
        self.date_strict = bool(date_strict)
        self.fast_mode = bool(fast_mode)
        self.device_name = device_name
        self.platform_version = platform_version
        self.udid = udid
        self.app_package = app_package
        self.app_activity = app_activity
        self.automation_name = automation_name

    @staticmethod
    def load_config():
        try:
            with open('config.jsonc', 'r', encoding='utf-8') as config_file:
                raw_text = config_file.read()
        except FileNotFoundError:
            raise FileNotFoundError("配置文件 config.jsonc 未找到，请确认文件存在")

        try:
            config = json.loads(_strip_jsonc_comments(raw_text))
        except json.JSONDecodeError as e:
            raise ValueError(f"配置文件格式错误: {e}")

        required_keys = ['server_url', 'keyword', 'users', 'city', 'date', 'price', 'price_index', 'if_commit_order']
        missing = [k for k in required_keys if k not in config]
        if missing:
            raise KeyError(f"配置文件缺少必需字段: {', '.join(missing)}")

        return Config(
            config['server_url'],
            config['keyword'],
            config['users'],
            config['city'],
            config['date'],
            config['price'],
            config['price_index'],
            config['if_commit_order'],
            config.get('city_index', 0),
            config.get('date_index', 0),
            config.get('date_strict', False),
            config.get('fast_mode', True),
            config.get('device_name', 'emulator-5554'),
            str(config.get('platform_version', '16')),
            config.get('udid'),
            config.get('app_package', 'cn.damai'),
            config.get('app_activity', '.launcher.splash.SplashMainActivity'),
            config.get('automation_name', 'UiAutomator2'),
        )
