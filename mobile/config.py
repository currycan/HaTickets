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
    def __init__(self, server_url, keyword, users, city, date, price, price_index, if_commit_order,
                 probe_only=False, device_name="Android", udid=None, platform_version=None,
                 app_package="cn.damai", app_activity=".launcher.splash.SplashMainActivity"):
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

        self.server_url = server_url
        self.keyword = keyword
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

        return Config(config['server_url'],
                      config['keyword'],
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
                      config.get('app_activity', '.launcher.splash.SplashMainActivity'))
