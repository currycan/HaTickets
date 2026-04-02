# -*- coding: UTF-8 -*-
"""
Layered recovery strategy for navigating back to the detail page.

When the navigation stack is deep, a simple back-press loop may land on the
homepage instead of the target detail page.  RecoveryHelper implements a
three-layer approach:

1. Check current page — return immediately if already at target.
2. Deep back — up to 8 back presses, probing after each one.
3. Forward navigation — re-navigate to the target event if we hit the homepage.
4. Graceful fallback — return last known state (never crash or infinite-loop).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict

from mobile.logger import get_logger

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe
    from mobile.event_navigator import EventNavigator

logger = get_logger(__name__)

_TARGET_STATES = {"detail_page", "sku_page"}
_HOMEPAGE_STATES = {"homepage"}
_MAX_BACK_STEPS = 8
_BACK_DELAY = 0.15


class RecoveryHelper:
    """Layered recovery strategy to return to the event detail page."""

    def __init__(
        self,
        device,
        probe: "PageProbe",
        navigator: "EventNavigator",
    ) -> None:
        self._device = device
        self._probe = probe
        self._navigator = navigator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recover_to_detail_page(self) -> Dict[str, object]:
        """Attempt to recover to the detail/SKU page using a layered strategy.

        Returns:
            The page-probe result dict from the last successful probe.
        """
        # Layer 1 — already on a target page?
        state = self._probe.probe_current_page(fast=True)
        page = state.get("page", "unknown")
        if page in _TARGET_STATES:
            logger.info("Recovery: already on target page (%s)", page)
            return state

        logger.info("Recovery: current page is '%s', starting deep back", page)

        # Layer 2 — deep back (up to 8 presses)
        for step in range(1, _MAX_BACK_STEPS + 1):
            self._device.press("back")
            self._probe.invalidate_cache()
            state = self._probe.probe_current_page(fast=True)
            page = state.get("page", "unknown")
            time.sleep(_BACK_DELAY)

            if page in _TARGET_STATES:
                logger.info(
                    "Recovery: reached target page (%s) after %d back(s)",
                    page,
                    step,
                )
                return state

            if page in _HOMEPAGE_STATES:
                logger.warning(
                    "Recovery: hit homepage after %d back(s), switching to forward nav",
                    step,
                )
                break
        else:
            # Exhausted all back steps without reaching target or homepage.
            logger.warning(
                "Recovery: exhausted %d backs without target or homepage (last: %s)",
                _MAX_BACK_STEPS,
                page,
            )

        # Layer 3 — forward navigation
        logger.info("Recovery: attempting forward navigation to target event")
        self._navigator.navigate_to_target_event()
        self._probe.invalidate_cache()
        state = self._probe.probe_current_page(fast=True)
        page = state.get("page", "unknown")

        if page in _TARGET_STATES:
            logger.info("Recovery: forward nav reached target page (%s)", page)
            return state

        # Layer 4 — all strategies exhausted; return last known state
        logger.error(
            "Recovery: all strategies exhausted, last known page is '%s'", page
        )
        return state
