# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

from __future__ import annotations

import re
import sys
import time
import types
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError as e:
    raise SystemExit(
        "依赖缺失：selenium 未安装。\n"
        "→ 请在项目根目录运行：poetry install\n"
        "→ 然后通过 mobile/scripts/start_ticket_grabbing.sh 启动而非直接运行 .py"
    ) from e

try:
    from mobile.config import Config
except ImportError:
    from config import Config  # type: ignore[no-redef]

try:
    from mobile.item_resolver import (
        DamaiItemResolver,
        DamaiItemResolveError,
        city_keyword,
        normalize_text,
    )
except ImportError:
    from item_resolver import (  # type: ignore[no-redef]
        normalize_text,
    )

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger  # type: ignore[no-redef]

try:
    from mobile.ui_primitives import UIPrimitives, ANDROID_UIAUTOMATOR
except ImportError:
    from ui_primitives import UIPrimitives, ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from mobile.buy_button_guard import BuyButtonGuard
    from mobile.page_probe import PageProbe, PageState
    from mobile.fast_pipeline import FastPipeline, poll_until, batch_shell_taps
    from mobile.recovery import RecoveryHelper
    from mobile.event_navigator import (
        EventNavigator,
        SessionNotFoundError,
        select_session,
    )
    from mobile.price_selector import (
        PriceSelector,
        PriceSelectorError,
        SoldOutError,
    )
    from mobile.attendee_selector import AttendeeSelector
except ImportError:
    from buy_button_guard import BuyButtonGuard  # type: ignore[no-redef]
    from page_probe import PageProbe  # type: ignore[no-redef]
    from fast_pipeline import FastPipeline  # type: ignore[no-redef]
    from recovery import RecoveryHelper  # type: ignore[no-redef]
    from event_navigator import EventNavigator  # type: ignore[no-redef]
    from price_selector import PriceSelector  # type: ignore[no-redef]
    from attendee_selector import AttendeeSelector  # type: ignore[no-redef]


logger = get_logger(__name__)

_PRICE_UNAVAILABLE_TAGS = {
    "无票",
    "缺货",
    "缺货登记",
    "售罄",
    "已售罄",
    "不可选",
    "暂不可售",
}
# 开售后大麦详情页 / 票务面板上"可点购票"的安全文案集合（issue #29）。
# 任意一个文案出现即视为开售已开放购买入口；不在此列的文案（如"预约抢票""即将开抢"）
# 一律视为未开售，避免误点预约入口。
SALE_READY_TEXTS: tuple[str, ...] = (
    "立即购票",
    "立即预定",
    "立即预订",  # 大麦 2026-04 后新增（issue #29）
    "立即抢票",
    "Book Now",  # 国际化场景兜底
)
# UiSelector textMatches 用：基于 SALE_READY_TEXTS 自动生成的正则联合
# （新增/修改 SALE_READY_TEXTS 时此处自动同步，避免文案分散）
_SALE_READY_TEXT_REGEX_OR = "|".join(f".*{t}.*" for t in SALE_READY_TEXTS)
_CTA_READY_KEYWORDS = (
    *SALE_READY_TEXTS,
    "立即购买",
    "选座购买",
    "购买",
    "抢票",
    "预定",
    "提交订单",
    "去结算",
    "确定",
)
_CTA_BLOCKED_KEYWORDS = (
    "预约",
    "预售",
    "即将开抢",
    "待开售",
    "未开售",
    "倒计时",
    "无票",
    "售罄",
    "缺货",
)
_MANUAL_STEP_BASELINES = {
    "搜索页输入并提交关键词": 6.0,
    "搜索结果扫描并打开目标": 12.0,
}


# --------------------------------------------------------------------------- #
# Test compatibility hook
# --------------------------------------------------------------------------- #
# Before W4-01, mobile/damai_app.py was a single module.  Tests rely on
# ``patch("mobile.damai_app.time")`` / ``patch("mobile.damai_app.datetime")``
# / ``patch("mobile.damai_app.logger")`` to swap module-level bindings.
# After splitting into a package whose code lives under
# ``mobile.damai_app.<submodule>``, those patches no longer reach the
# submodule's local binding.  To preserve zero-test-modification behavior,
# this custom module class mirrors writes of a small whitelist of attributes
# back onto every submodule that hosts code formerly inside damai_app.py.
_MIRROR_SUBMODULES: tuple[str, ...] = tuple(
    f"{__name__}.{_n}"
    for _n in (
        "orchestrator",
        "sale_waiter",
        "purchase_flow",
        "recovery_strategies",
        "coords_cache",
        "state_probe",
        "delegators",
    )
)
_MIRRORED_ATTRS: frozenset[str] = frozenset({"time", "datetime", "logger", "re"})


class _DamaiPackage(types.ModuleType):
    """Package module that mirrors selected attribute writes to submodules.

    Allows tests written against the pre-split single-file layout to
    monkeypatch ``mobile.damai_app.time`` / ``mobile.damai_app.datetime`` /
    ``mobile.damai_app.logger`` and have those patches reach the actual code
    in submodules.  Only the attributes in :data:`_MIRRORED_ATTRS` are mirrored
    to avoid surprising behavior.
    """

    def __setattr__(self, name: str, value):  # type: ignore[override]
        super().__setattr__(name, value)
        if name in _MIRRORED_ATTRS:
            for submod_name in _MIRROR_SUBMODULES:
                submod = sys.modules.get(submod_name)
                if submod is not None:
                    object.__setattr__(submod, name, value)


sys.modules[__name__].__class__ = _DamaiPackage


# Load the orchestrator (transitively loads each mixin submodule).
from .orchestrator import DamaiBot  # noqa: E402


__all__ = [
    "DamaiBot",
    "SALE_READY_TEXTS",
    "logger",
    "Config",
    "ANDROID_UIAUTOMATOR",
    "UIPrimitives",
    "PageProbe",
    "PageState",
    "FastPipeline",
    "RecoveryHelper",
    "EventNavigator",
    "SessionNotFoundError",
    "select_session",
    "PriceSelector",
    "PriceSelectorError",
    "SoldOutError",
    "AttendeeSelector",
    "BuyButtonGuard",
    "DamaiItemResolver",
    "DamaiItemResolveError",
    "city_keyword",
    "normalize_text",
    "get_logger",
    "By",
    "Path",
    "datetime",
    "timezone",
    "timedelta",
    "contextmanager",
]
