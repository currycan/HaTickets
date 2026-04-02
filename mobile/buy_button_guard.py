# -*- coding: UTF-8 -*-
"""
Safety guard that prevents accidental clicks on reservation ("预约抢票") buttons.

The Damai app shows "预约抢票" before sale opens and "立即购票" after.
Clicking the reservation button enters the WRONG flow. This module ensures
only purchase-ready button texts are treated as safe to click.
"""

import time
from typing import Optional

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

SAFE_TEXTS = frozenset({
    "立即购买",
    "立即购票",
    "立即抢票",
    "立即预定",
    "选座购买",
    "购买",
    "抢票",
    "预定",
})

BLOCKED_TEXTS = frozenset({
    "预约抢票",
    "预约",
    "预售",
    "即将开抢",
    "待开售",
    "未开售",
    "提交抢票预约",
})

_BUY_BUTTON_RESOURCE_ID = "cn.damai:id/btn_buy_view"


class BuyButtonGuard:
    """Guards against accidental clicks on reservation/pre-sale buttons.

    Args:
        device: A uiautomator2 device instance.
    """

    def __init__(self, device):
        self._device = device

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
        """Find the buy button element by resource ID.

        Returns:
            The UI element if found, or None.
        """
        try:
            el = self._device(resourceId=_BUY_BUTTON_RESOURCE_ID)
            if el.exists:
                return el
            return None
        except Exception:
            logger.debug("Failed to find buy button element")
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

    def wait_until_safe(self, timeout_s: float = 10.0, poll_ms: int = 50) -> bool:
        """Poll button text until a safe text is detected or timeout expires.

        Args:
            timeout_s: Maximum seconds to wait.
            poll_ms: Milliseconds between polls.

        Returns:
            True if a safe text was detected before timeout, False otherwise.
        """
        deadline = time.time() + timeout_s
        poll_s = poll_ms / 1000.0

        while True:
            text = self.get_current_text()
            if text and self.is_safe_to_click(text):
                logger.info("Safe button text detected: %s", text)
                return True

            if time.time() >= deadline:
                logger.warning(
                    "Timed out waiting for safe button text (last seen: %s)",
                    text,
                )
                return False

            time.sleep(poll_s)
