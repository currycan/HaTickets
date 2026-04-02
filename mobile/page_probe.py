# -*- coding: UTF-8 -*-
"""
PageProbe — fast page-state detection for Damai mobile automation.

Provides two probe modes:
- **Fast probe** (~100ms): checks only the Android Activity name.
- **Full probe** (~1.5s): checks element resource IDs for comprehensive state.

Results are cached with a configurable TTL to avoid redundant device queries.
"""

import time
from typing import Any, Dict, Optional

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

# Activity substrings → state mapping (used by fast probe)
_ACTIVITY_STATE_MAP = (
    ("ProjectDetail", "detail_page"),
    ("NcovSku", "sku_page"),
    ("MainActivity", "homepage"),
    ("SearchActivity", "search_page"),
)

# Default result template
_DEFAULT_RESULT: Dict[str, Any] = {
    "state": "unknown",
    "purchase_button": False,
    "price_container": False,
    "quantity_picker": False,
    "submit_button": False,
    "reservation_mode": False,
    "pending_order_dialog": False,
}


def _make_result(**overrides: Any) -> Dict[str, Any]:
    """Create a fresh result dict with optional overrides (immutable pattern)."""
    return {**_DEFAULT_RESULT, **overrides}


class PageProbe:
    """Detects the current page state of the Damai app on an Android device.

    Args:
        device: A uiautomator2 device connection (or compatible mock).
        config: Optional mobile Config instance (reserved for future use).
        cache_ttl_s: How long (seconds) a cached probe result stays valid.
    """

    def __init__(self, device: Any, config: Any = None, cache_ttl_s: float = 0.5) -> None:
        self._device = device
        self._config = config
        self._cache_ttl_s = cache_ttl_s
        self._cached_result: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def probe_current_page(self, fast: bool = False) -> Dict[str, Any]:
        """Detect the current page state.

        Args:
            fast: When True, only inspect the Activity name (~100ms).
                  When False, also check UI elements for full state (~1.5s).

        Returns:
            A dict describing the page state and key element presence.
        """
        now = time.time()
        if self._cached_result is not None and (now - self._cached_at) < self._cache_ttl_s:
            logger.debug("page probe: returning cached result (age=%.3fs)", now - self._cached_at)
            return self._cached_result

        if fast:
            result = self._probe_fast()
        else:
            result = self._probe_full()

        self._cached_result = result
        self._cached_at = time.time()
        return result

    def get_current_activity(self) -> str:
        """Return the current foreground Activity name."""
        try:
            info = self._device.app_current()
            return info.get("activity", "")
        except Exception:
            logger.warning("page probe: failed to get current activity")
            return ""

    def invalidate_cache(self) -> None:
        """Clear the cached probe result so the next call queries the device."""
        self._cached_result = None
        self._cached_at = 0.0

    # ------------------------------------------------------------------
    # Internal: fast probe
    # ------------------------------------------------------------------

    def _probe_fast(self) -> Dict[str, Any]:
        """Probe using only the Activity name (~100ms)."""
        activity = self.get_current_activity()
        logger.debug("page probe (fast): activity=%s", activity)

        for substring, state in _ACTIVITY_STATE_MAP:
            if substring in activity:
                return _make_result(state=state)

        # Activity not recognised — fall through to full probe
        logger.debug("page probe (fast): unknown activity, falling back to full probe")
        return self._probe_full()

    # ------------------------------------------------------------------
    # Internal: full probe
    # ------------------------------------------------------------------

    def _probe_full(self) -> Dict[str, Any]:
        """Probe using element lookups for comprehensive state detection (~1.5s)."""
        logger.debug("page probe (full): starting element checks")

        # Check consent dialog first (blocks everything else)
        if self._exists_by_resource_id("cn.damai:id/id_boot_action_agree"):
            return _make_result(state="consent_dialog")

        # Check pending order dialog
        pending = self._exists_by_text_contains("未支付订单")

        # Check order confirm page (submit button)
        submit = self._exists_by_text("立即提交")
        checkbox = self._exists_by_resource_id("cn.damai:id/checkbox")
        if submit or checkbox:
            return _make_result(
                state="order_confirm_page",
                submit_button=submit,
                pending_order_dialog=pending,
            )

        # Check SKU page
        sku_layout = self._exists_by_resource_id("cn.damai:id/layout_sku")
        sku_container = self._exists_by_resource_id("cn.damai:id/sku_contanier")
        if sku_layout or sku_container:
            return _make_result(
                state="sku_page",
                quantity_picker=True,
                pending_order_dialog=pending,
            )

        # Check detail page
        purchase_bar = self._exists_by_resource_id(
            "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        )
        price_layout = self._exists_by_resource_id("cn.damai:id/project_detail_price_layout")
        title_tv = self._exists_by_resource_id("cn.damai:id/title_tv")
        if purchase_bar or price_layout or title_tv:
            return _make_result(
                state="detail_page",
                purchase_button=purchase_bar,
                price_container=price_layout,
                pending_order_dialog=pending,
            )

        # Check homepage
        search_header = self._exists_by_resource_id("cn.damai:id/homepage_header_search")
        search_btn = self._exists_by_resource_id(
            "cn.damai:id/pioneer_homepage_header_search_btn"
        )
        if search_header or search_btn:
            return _make_result(state="homepage", pending_order_dialog=pending)

        # Check search page
        search_input = self._exists_by_resource_id("cn.damai:id/header_search_v2_input")
        if search_input:
            return _make_result(state="search_page", pending_order_dialog=pending)

        # Check pending order as last resort
        if pending:
            return _make_result(state="unknown", pending_order_dialog=True)

        return _make_result()

    # ------------------------------------------------------------------
    # Element existence helpers
    # ------------------------------------------------------------------

    def _exists_by_resource_id(self, resource_id: str) -> bool:
        try:
            el = self._device(resourceId=resource_id)
            return el.exists
        except Exception:
            return False

    def _exists_by_text(self, text: str) -> bool:
        try:
            el = self._device(text=text)
            return el.exists
        except Exception:
            return False

    def _exists_by_text_contains(self, text: str) -> bool:
        try:
            el = self._device(textContains=text)
            return el.exists
        except Exception:
            return False
