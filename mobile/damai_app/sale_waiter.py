# -*- coding: UTF-8 -*-
"""Sale-start waiting helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Reads constants from the package namespace so that tests'
``patch("mobile.damai_app.time")`` / ``patch("mobile.damai_app.datetime")``
patches still take effect via the package's mirror hook.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from . import (
    SALE_READY_TEXTS,
    _CTA_BLOCKED_KEYWORDS,
    _CTA_READY_KEYWORDS,
    logger,
)

try:
    from mobile.item_resolver import normalize_text
except ImportError:  # pragma: no cover
    from item_resolver import normalize_text  # type: ignore[no-redef]

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:  # pragma: no cover
    from ui_primitives import ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:  # pragma: no cover
    raise


class SaleWaiterMixin:
    """Mixin contributing sale-start detection helpers to ``DamaiBot``."""

    def _purchase_bar_text_ready(self):
        """Inspect the detail-page CTA text and decide whether sale has opened."""
        try:
            purchase_bar = self._find(
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            )
        except Exception:
            return False

        texts = [
            text.strip()
            for text in self._collect_descendant_texts(purchase_bar)
            if text.strip()
        ]
        merged = normalize_text("".join(texts))
        if not merged:
            return False
        if any(normalize_text(keyword) in merged for keyword in _CTA_BLOCKED_KEYWORDS):
            return False
        return any(normalize_text(keyword) in merged for keyword in _CTA_READY_KEYWORDS)

    def _is_sale_ready(self):
        """Check whether the current UI state is actionable for purchase.

        Sale-readiness is detected via :data:`SALE_READY_TEXTS` (开售文案) plus
        a small set of post-confirm CTAs ("立即购买" / "选座购买" / "提交订单" 等)
        that may appear once the user has already entered the SKU/order page.
        """
        ready_texts = (
            *SALE_READY_TEXTS,
            "立即购买",
            "选座购买",
            "立即提交",
            "提交订单",
        )
        for text in ready_texts:
            if self._has_element(
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{text}")',
            ):
                self._last_sale_ready_text = text
                return True

        if self._has_element(
            By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
        ):
            return not self.is_reservation_sku_mode()

        if self._has_element(
            By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        ):
            return self._purchase_bar_text_ready()

        return False

    def wait_for_sale_start(self):
        """等待开售时间，在开售前 countdown_lead_ms 毫秒开始轮询。"""
        if self.config.sell_start_time is None:
            if self.config.wait_cta_ready_timeout_ms > 0:
                logger.info("未配置 sell_start_time，已跳过 CTA 等待，直接开始执行")
            return

        _tz_shanghai = timezone(timedelta(hours=8))
        sell_time = datetime.fromisoformat(self.config.sell_start_time)
        # Ensure timezone-aware
        if sell_time.tzinfo is None:
            sell_time = sell_time.replace(tzinfo=_tz_shanghai)

        now = datetime.now(tz=_tz_shanghai)
        if now >= sell_time:
            logger.info("开售时间已过，跳过等待")
            return

        lead_delta = timedelta(milliseconds=self.config.countdown_lead_ms)
        poll_start = sell_time - lead_delta
        sleep_seconds = (poll_start - now).total_seconds()

        if sleep_seconds > 0:
            logger.info(
                f"等待开售，将在 {self.config.sell_start_time} 前 "
                f"{self.config.countdown_lead_ms}ms 开始轮询"
            )
            time.sleep(sleep_seconds)

        # Use BuyButtonGuard for precise button-text monitoring
        if hasattr(self, "_guard") and self._guard.wait_until_safe(
            timeout_s=8.0, poll_ms=50
        ):
            logger.info("BuyButtonGuard 检测到可购买按钮")
            return

        # Tight polling loop with multiple purchase signals until the page becomes actionable.
        deadline = sell_time + timedelta(seconds=8)
        polls = 0
        while datetime.now(tz=_tz_shanghai) < deadline:
            polls += 1
            if self._is_sale_ready():
                cta_text = getattr(self, "_last_sale_ready_text", None) or "?"
                logger.info(f"CTA_MATCH: text={cta_text!r} polls={polls} (开售已开始)")
                return
            time.sleep(0.08)

        logger.warning(f"等待开售超时（轮询 {polls} 次），继续执行")
