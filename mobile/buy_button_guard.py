# -*- coding: UTF-8 -*-
"""
Safety guard that prevents accidental clicks on reservation ("预约抢票") buttons.

The Damai app shows "预约抢票" before sale opens and "立即购票" after.
Clicking the reservation button enters the WRONG flow. This module ensures
only purchase-ready button texts are treated as safe to click.
"""

import time
from typing import Any, Dict, Optional, Tuple

from uiautomator2.exceptions import UiObjectNotFoundError

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)
_monotonic = time.monotonic

SAFE_TEXTS = frozenset(
    {
        "立即购买",
        "立即购票",
        "立即抢票",
        "立即预定",
        "立即预订",  # 大麦 2026-04 后新增（issue #29）
        "Book Now",  # 国际化场景兜底（issue #29）
        "选座购买",
        "购买",
        "抢票",
        "预定",
    }
)

BLOCKED_TEXTS = frozenset(
    {
        "预约抢票",
        "预约",
        "预售",
        "即将开抢",
        "待开售",
        "未开售",
        "提交抢票预约",
    }
)

# CTA 候选 resource-id（issue #41）：
# - v8.x 传统购买按钮 btn_buy_view（带文案，可做安全文案校验），旧 ID 在前保兼容；
# - 大麦 ≥9.0.2x 详情页移除了 btn_buy_view，CTA 变为 Canvas 自绘，仅剩底部购买栏
#   容器 trade_project_detail_purchase_status_bar_container_fl（无任何文案节点）。
_BUY_BUTTON_RESOURCE_IDS = (
    "cn.damai:id/btn_buy_view",
    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
)

# 兼容别名：保留旧常量名，防外部引用破坏（issue #41）
_BUY_BUTTON_RESOURCE_ID = _BUY_BUTTON_RESOURCE_IDS[0]


class BuyButtonGuard:
    """Guards against accidental clicks on reservation/pre-sale buttons.

    Args:
        device: A uiautomator2 device instance.
    """

    def __init__(self, device):
        self._device = device
        # 最近一次命中的候选 resource-id（None 表示未命中），供调用方结构化日志使用
        self._last_matched_resource_id: Optional[str] = None

    def is_safe_to_click(self, button_text: Optional[str]) -> bool:
        """Return True only if button_text is a known safe purchase text.

        Empty, None, unknown, or blocked texts all return False.
        """
        if not button_text:
            return False
        safe = button_text in SAFE_TEXTS
        if not safe and button_text in BLOCKED_TEXTS:
            logger.warning("Blocked unsafe button text: %s", button_text)
        elif not safe:
            logger.warning("Unknown button text rejected: %s", button_text)
        return safe

    def _find_buy_button(self):
        """Find the buy button element by candidate resource IDs.

        依序遍历 :data:`_BUY_BUTTON_RESOURCE_IDS`（旧 ID 在前，保 v8.x 兼容），
        返回首个存在的元素，并将命中的 ID 记入 ``self._last_matched_resource_id``。

        Returns:
            The UI element if found, or None.
        """
        for rid in _BUY_BUTTON_RESOURCE_IDS:
            try:
                el = self._device(resourceId=rid)
                if el.exists:
                    self._last_matched_resource_id = rid
                    return el
            except Exception:
                logger.debug("Failed to find buy button element (resource-id=%s)", rid)
        self._last_matched_resource_id = None
        return None

    def get_current_text(self) -> Optional[str]:
        """Read current button text without clicking.

        Returns:
            The button text string, or None if the button is not found.
        """
        el = self._find_buy_button()
        if el is None:
            return None
        try:
            text = el.get_text()
            return text if text else None
        except Exception:
            logger.debug("Failed to read buy button text")
            return None

    def get_cta_center_coords(self) -> Optional[Tuple[int, int]]:
        """定位 CTA（购买按钮/购买栏容器）的中心坐标（issue #41）。

        大麦 ≥9.0.2x 详情页 CTA 为 Canvas 自绘、读不到任何文案，此方法按候选
        resource-id 取首个存在元素的 bounds 中心，供下游坐标兜底点击。

        Returns:
            (x, y) 中心坐标；候选 ID 均不存在或读取失败时返回 None。
        """
        for rid in _BUY_BUTTON_RESOURCE_IDS:
            try:
                el = self._device(resourceId=rid)
                if el.exists:
                    bounds = el.info["bounds"]
                    x = (int(bounds["left"]) + int(bounds["right"])) // 2
                    y = (int(bounds["top"]) + int(bounds["bottom"])) // 2
                    self._last_matched_resource_id = rid
                    return (x, y)
            except Exception:
                logger.debug("Failed to read CTA bounds (resource-id=%s)", rid)
        return None

    @staticmethod
    def _center_from_bounds(bounds) -> Optional[Tuple[int, int]]:
        """Return a positive-area bounds centre from a u2 ``info`` payload."""
        if not isinstance(bounds, dict):
            return None
        try:
            left = int(bounds["left"])
            top = int(bounds["top"])
            right = int(bounds["right"])
            bottom = int(bounds["bottom"])
        except (KeyError, TypeError, ValueError):
            return None
        if right <= left or bottom <= top:
            return None
        return ((left + right) // 2, (top + bottom) // 2)

    def inspect_cta(self) -> Dict[str, Any]:
        """Read CTA text and bounds from one matched selector.

        This is the presale prewarm primitive.  Unlike calling
        :meth:`get_current_text` and :meth:`get_cta_center_coords` in
        sequence, each candidate is queried directly for its ``info`` payload.
        A present element therefore costs one device RPC instead of a separate
        ``exists`` RPC followed by ``info``.  Candidate order remains unchanged
        so a legacy text CTA always keeps priority over the Canvas container.
        The method never clicks.
        """
        selector_error = False
        for rid in _BUY_BUTTON_RESOURCE_IDS:
            try:
                element = self._device(resourceId=rid)
                info = element.info
            except UiObjectNotFoundError:
                # Normal miss: move to the next candidate without spending a
                # preceding ``exists`` RPC.
                continue
            except Exception:
                selector_error = True
                logger.debug("Failed to inspect CTA selector (resource-id=%s)", rid)
                continue

            # Lightweight fakes and older adapters can represent a missing
            # selector with a non-dict/empty payload rather than raising.
            if not isinstance(info, dict) or not info:
                continue

            self._last_matched_resource_id = rid
            text = (
                info.get("text")
                or info.get("contentDescription")
                or info.get("content-desc")
                or None
            )
            if isinstance(text, str):
                text = text.strip() or None
            else:
                text = None

            # v8.x exposes text in the element payload.  Some older u2
            # adapters omit it from ``info``; only the legacy text button may
            # use get_text as a compatibility fallback.  Never call get_text
            # on the Canvas container because it can traverse the subtree.
            if text is None and rid == _BUY_BUTTON_RESOURCE_IDS[0]:
                try:
                    legacy_text = element.get_text()
                    text = (
                        legacy_text.strip()
                        if isinstance(legacy_text, str) and legacy_text.strip()
                        else None
                    )
                except Exception:
                    text = None

            coords = self._center_from_bounds(info.get("bounds"))
            kind = "text" if text is not None else "canvas" if coords else "missing"
            return {
                "resource_id": rid,
                "text": text,
                "coords": coords,
                "kind": kind,
                "device_error": False,
            }

        self._last_matched_resource_id = None
        return {
            "resource_id": None,
            "text": None,
            "coords": None,
            "kind": "missing",
            "device_error": selector_error,
        }

    def wait_until_safe(self, timeout_s: float = 10.0, poll_ms: int = 50) -> bool:
        """Poll button text until a safe text is detected or timeout expires.

        Args:
            timeout_s: Maximum seconds to wait.
            poll_ms: Milliseconds between polls.

        Returns:
            True if a safe text was detected before timeout, False otherwise.
        """
        deadline = _monotonic() + timeout_s
        poll_s = poll_ms / 1000.0

        while True:
            text = self.get_current_text()
            if text and self.is_safe_to_click(text):
                logger.info("Safe button text detected: %s", text)
                return True

            if _monotonic() >= deadline:
                logger.warning(
                    "Timed out waiting for safe button text (last seen: %s)",
                    text,
                )
                return False

            time.sleep(poll_s)
