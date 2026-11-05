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

import hashlib
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Dict

from mobile.logger import get_logger
from mobile.page_probe import (
    MANUAL_VERIFICATION_MARKERS,
    PENDING_ORDER_MARKERS,
    TRANSIENT_ERROR_MARKERS,
    _find_local_transient_retry_coords,
)

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe
    from mobile.event_navigator import EventNavigator

logger = get_logger(__name__)

_TARGET_STATES = {"detail_page", "sku_page"}
_HOMEPAGE_STATES = {"homepage"}
_MAX_BACK_STEPS = 8
_BACK_DELAY = 0.15
_TRANSIENT_ACTION_BUDGET = 3
_TRANSIENT_ACTION_COOLDOWN_S = 0.8
_TRANSIENT_SNAPSHOT_MAX_AGE_S = 0.25


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
        self._transient_action_budget = _TRANSIENT_ACTION_BUDGET
        self._transient_actions_used = 0
        self._transient_last_action_at = None
        self._transient_last_fingerprint = None
        self._transient_dispatched_fingerprints = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_transient_action_budget(self) -> None:
        """Reset the bounded refresh/foreground-restore budget for a new run."""
        self._transient_actions_used = 0
        self._transient_last_action_at = None
        self._transient_last_fingerprint = None
        self._transient_dispatched_fingerprints.clear()

    def export_transient_action_state(self) -> Dict[str, object]:
        """Snapshot the per-run action budget before a driver rebind."""
        return {
            "budget": self._transient_action_budget,
            "used": self._transient_actions_used,
            "last_action_at": self._transient_last_action_at,
            "last_fingerprint": self._transient_last_fingerprint,
            "dispatched_fingerprints": tuple(
                self._transient_dispatched_fingerprints
            ),
        }

    def restore_transient_action_state(self, state) -> None:
        """Restore a budget snapshot onto a helper bound to a new driver."""
        if not isinstance(state, dict):
            return
        budget = state.get("budget")
        used = state.get("used")
        if type(budget) is not int or budget < 0:
            return
        if type(used) is not int or used < 0 or used > budget:
            return
        self._transient_action_budget = budget
        self._transient_actions_used = used
        self._transient_last_action_at = state.get("last_action_at")
        self._transient_last_fingerprint = state.get("last_fingerprint")
        dispatched = state.get("dispatched_fingerprints", ())
        restored = {
            fingerprint
            for fingerprint in dispatched
            if isinstance(fingerprint, tuple)
        } if isinstance(dispatched, (tuple, list, set, frozenset)) else set()
        if (
            not restored
            and used > 0
            and isinstance(self._transient_last_fingerprint, tuple)
        ):
            # Older snapshots did not carry the full set. Preserve at least
            # the last dispatched fingerprint across a reconnect.
            restored.add(self._transient_last_fingerprint)
        self._transient_dispatched_fingerprints = restored

    @staticmethod
    def _transient_fingerprint(snapshot) -> tuple:
        """Return a content-free fingerprint for cooldown accounting."""
        return (
            bool(snapshot.get("transient")),
            bool(snapshot.get("blank")),
            snapshot.get("package"),
            snapshot.get("activity"),
            snapshot.get("layout_fingerprint"),
            tuple(snapshot.get("error_markers") or ()),
            snapshot.get("retry_coords"),
        )

    def _reserve_transient_action(self, snapshot) -> str:
        """Reserve one recovery action, returning allowed/cooldown/exhausted."""
        now = time.monotonic()
        fingerprint = self._transient_fingerprint(snapshot)
        if fingerprint in self._transient_dispatched_fingerprints:
            if (
                self._transient_last_action_at is not None
                and fingerprint == self._transient_last_fingerprint
                and now - self._transient_last_action_at
                < _TRANSIENT_ACTION_COOLDOWN_S
            ):
                return "cooldown"
            return "duplicate"
        if self._transient_actions_used >= self._transient_action_budget:
            return "exhausted"
        if (
            self._transient_last_action_at is not None
            and fingerprint == self._transient_last_fingerprint
            and now - self._transient_last_action_at
            < _TRANSIENT_ACTION_COOLDOWN_S
        ):
            return "cooldown"

        self._transient_actions_used += 1
        self._transient_last_action_at = now
        self._transient_last_fingerprint = fingerprint
        self._transient_dispatched_fingerprints.add(fingerprint)
        return "allowed"

    @staticmethod
    def _shell_dispatch_succeeded(response) -> bool:
        """Return True only for an explicit successful u2 shell response."""
        exit_code = getattr(response, "exit_code", None)
        return type(exit_code) is int and exit_code == 0

    def _uncertain_dispatch_result(self, last_state) -> Dict[str, object]:
        """Fail closed when Android may or may not have received an action."""
        return {
            **last_state,
            "device_error": True,
            "recovered": False,
            "action_dispatch_uncertain": True,
            "recovery_actions_used": self._transient_actions_used,
            "action_dispatched": True,
        }

    def _inspect_transient_failure(self):
        """Return transient-error metadata from one hierarchy snapshot."""
        package = None
        activity = None
        try:
            current = self._device.app_current()
            if isinstance(current, dict):
                if isinstance(current.get("package"), str):
                    package = current["package"]
                if isinstance(current.get("activity"), str):
                    activity = current["activity"]
        except Exception:
            pass
        try:
            xml = self._device.dump_hierarchy() or ""
            root = ET.fromstring(xml) if xml else None
        except Exception as exc:
            logger.debug("Recovery: transient hierarchy read failed: %s", exc)
            return {
                "transient": False,
                "manual_verification": False,
                "pending_order": False,
                "retry_coords": None,
                "blank": False,
                "hierarchy_error": True,
                "package": package,
                "activity": activity,
                "layout_fingerprint": None,
                "error_markers": (),
            }

        if root is None:
            return {
                "transient": False,
                "manual_verification": False,
                "pending_order": False,
                "retry_coords": None,
                "blank": True,
                "hierarchy_error": False,
                "package": package,
                "activity": activity,
                "layout_fingerprint": "blank",
                "error_markers": (),
            }

        merged_texts = []
        meaningful_node = False
        nodes = list(root.iter("node"))
        for node in nodes:
            if node.get("visible-to-user") == "false":
                continue
            if any(
                (node.get(attr) or "").strip()
                for attr in ("resource-id", "text", "content-desc")
            ) or any(
                node.get(attr) == "true"
                for attr in ("clickable", "scrollable", "checkable")
            ):
                meaningful_node = True
            text = " ".join(
                part.strip()
                for part in (
                    node.get("text", ""),
                    node.get("content-desc", ""),
                )
                if part and part.strip()
            )
            if not text:
                continue
            merged_texts.append(text)

        merged = " ".join(merged_texts)
        error_markers = tuple(
            sorted(
                marker
                for marker in TRANSIENT_ERROR_MARKERS
                if marker in merged
            )
        )
        structural_nodes = tuple(
            (
                node.get("resource-id") or "",
                node.get("class") or "",
                node.get("bounds") or "",
                node.get("clickable") or "",
            )
            for node in nodes
            if node.get("visible-to-user") != "false"
        )
        layout_fingerprint = hashlib.sha256(
            repr(structural_nodes).encode("utf-8")
        ).hexdigest()[:20]
        return {
            "transient": bool(error_markers),
            "manual_verification": any(
                marker in merged for marker in MANUAL_VERIFICATION_MARKERS
            ),
            "pending_order": any(
                marker in merged for marker in PENDING_ORDER_MARKERS
            ),
            "retry_coords": _find_local_transient_retry_coords(root),
            "blank": not meaningful_node,
            "hierarchy_error": False,
            "package": package,
            "activity": activity,
            "layout_fingerprint": layout_fingerprint,
            "error_markers": error_markers,
        }

    def recover_transient_failure(
        self,
        *,
        expected_states=None,
        max_attempts: int = 2,
        settle_timeout_s: float = 0.8,
        initial_probe: Dict[str, object] | None = None,
        observe_only: bool = False,
    ) -> Dict[str, object]:
        """Recover a loading/network error without restarting the device session.

        The fast path only clicks an exact retry/refresh action when the same
        hierarchy contains a known transient-error marker.  Captcha or human
        verification screens are never operated automatically.  Callers that
        have just detected an overlay may pass ``initial_probe`` so recovery
        does not immediately repeat the same full page probe in the critical
        sale window.
        """
        expected = set(expected_states or _TARGET_STATES)
        last_state: Dict[str, object] = {"state": "unknown"}
        action_dispatched = False
        seeded_probe = (
            dict(initial_probe) if isinstance(initial_probe, dict) else None
        )

        for attempt in range(max(1, int(max_attempts))):
            if seeded_probe is not None:
                last_state = seeded_probe
                seeded_probe = None
            elif attempt == 0:
                self._probe.invalidate_cache()
                last_state = self._probe.probe_current_page(fast=False)
            # On later attempts ``last_state`` is the final full probe from the
            # previous settle window.  Reuse it instead of issuing an
            # identical back-to-back hierarchy read.
            page = str(last_state.get("state", "unknown"))
            if page in expected:
                return last_state

            snapshot = None
            observed_at = last_state.get("snapshot_observed_at")
            retry_hint = last_state.get("transient_retry_coords")
            if (
                observe_only
                and
                page == "transient_error"
                and isinstance(observed_at, (int, float))
                and not isinstance(observed_at, bool)
                and isinstance(retry_hint, (tuple, list))
                and len(retry_hint) == 2
                and all(type(value) is int for value in retry_hint)
            ):
                snapshot_age = time.monotonic() - float(observed_at)
                if 0 <= snapshot_age <= _TRANSIENT_SNAPSHOT_MAX_AGE_S:
                    snapshot = {
                        "transient": True,
                        "manual_verification": False,
                        "pending_order": False,
                        "retry_coords": tuple(retry_hint),
                        "blank": False,
                        "hierarchy_error": False,
                        "reused_snapshot": True,
                        "package": last_state.get("package"),
                        "activity": last_state.get("activity"),
                        "layout_fingerprint": last_state.get(
                            "layout_fingerprint"
                        ),
                        "error_markers": tuple(
                            last_state.get("transient_error_markers") or ()
                        ),
                    }
            if snapshot is None:
                snapshot = self._inspect_transient_failure()
            if snapshot["hierarchy_error"]:
                logger.warning("Recovery: device hierarchy unavailable; reconnect required")
                return {
                    **last_state,
                    "device_error": True,
                    "recovered": False,
                }
            if snapshot["pending_order"]:
                logger.warning(
                    "Recovery: detected an existing pending-payment order; "
                    "automatic refresh is disabled"
                )
                return {
                    **last_state,
                    "state": "pending_order_dialog",
                    "pending_order_dialog": True,
                    "recovered": False,
                }
            if snapshot["manual_verification"]:
                logger.warning(
                    "Recovery: detected manual verification; automatic refresh is disabled"
                )
                return {
                    **last_state,
                    "manual_verification": True,
                    "recovered": False,
                }

            if (
                page == "transient_error"
                and not snapshot["transient"]
                and not snapshot["blank"]
            ):
                # The error overlay can disappear between the caller's probe
                # and this hierarchy read.  Observe the clean hierarchy once
                # instead of acting on stale transient state.
                self._probe.invalidate_cache()
                observed_state = self._probe.probe_current_page(fast=False)
                observed_page = str(observed_state.get("state", "unknown"))
                return {
                    **observed_state,
                    "recovered": observed_page in expected,
                    "action_dispatched": action_dispatched,
                    "recovery_actions_used": self._transient_actions_used,
                }

            retry_coords = snapshot["retry_coords"]
            if snapshot["transient"] and retry_coords is not None:
                if observe_only:
                    return {
                        **last_state,
                        "recovered": False,
                        "observe_only": True,
                        "planned_action": "tap_retry",
                        "planned_action_coords": retry_coords,
                        "reused_snapshot": bool(
                            snapshot.get("reused_snapshot")
                        ),
                        "recovery_actions_used": self._transient_actions_used,
                        "action_dispatched": False,
                    }
                action_state = self._reserve_transient_action(snapshot)
                if action_state == "exhausted":
                    logger.warning(
                        "Recovery: refresh action budget exhausted; "
                        "stopping automatic refresh"
                    )
                    return {
                        **last_state,
                        "recovered": False,
                        "recovery_budget_exhausted": True,
                        "recovery_actions_used": self._transient_actions_used,
                        "action_dispatched": action_dispatched,
                    }
                if action_state == "cooldown":
                    return {
                        **last_state,
                        "recovered": False,
                        "recovery_cooldown": True,
                        "recovery_actions_used": self._transient_actions_used,
                        "action_dispatched": action_dispatched,
                    }
                if action_state == "duplicate":
                    logger.warning(
                        "Recovery: unchanged error fingerprint was already "
                        "acted on; stopping automatic refresh"
                    )
                    return {
                        **last_state,
                        "recovered": False,
                        "recovery_duplicate_fingerprint": True,
                        "recovery_actions_used": self._transient_actions_used,
                        "action_dispatched": action_dispatched,
                    }
                x, y = retry_coords
                logger.warning(
                    "Recovery: transient page failure detected; clicking retry (%d/%d)",
                    attempt + 1,
                    max_attempts,
                )
                try:
                    shell_response = self._device.shell(
                        f"input tap {int(x)} {int(y)}"
                    )
                    if not self._shell_dispatch_succeeded(shell_response):
                        logger.warning(
                            "Recovery: retry shell result is non-zero or unknown; "
                            "reconnect and observe before any further click"
                        )
                        return self._uncertain_dispatch_result(last_state)
                    action_dispatched = True
                except Exception:
                    # The shell command may have reached Android before the
                    # transport failed.  A fallback click could double-tap an
                    # action whose result is unknown, so reconnect and observe.
                    return self._uncertain_dispatch_result(last_state)
            elif snapshot["transient"]:
                logger.warning(
                    "Recovery: transient error has no locally paired retry "
                    "control; stopping automatic refresh"
                )
                return {
                    **last_state,
                    "recovered": False,
                    "retry_action_unconfirmed": True,
                    "recovery_actions_used": self._transient_actions_used,
                    "action_dispatched": action_dispatched,
                }
            elif page == "unknown" and snapshot["blank"]:
                # Bring the existing task to foreground without clearing login
                # or app data.  This is a recovery action, not a cold restart.
                config = getattr(self._navigator, "_config", None)
                package = getattr(config, "app_package", None)
                if package:
                    if observe_only:
                        return {
                            **last_state,
                            "recovered": False,
                            "observe_only": True,
                            "planned_action": "app_start",
                            "planned_action_package": package,
                            "recovery_actions_used": self._transient_actions_used,
                            "action_dispatched": False,
                        }
                    action_state = self._reserve_transient_action(snapshot)
                    if action_state == "exhausted":
                        return {
                            **last_state,
                            "recovered": False,
                            "recovery_budget_exhausted": True,
                            "recovery_actions_used": self._transient_actions_used,
                            "action_dispatched": action_dispatched,
                        }
                    if action_state == "cooldown":
                        return {
                            **last_state,
                            "recovered": False,
                            "recovery_cooldown": True,
                            "recovery_actions_used": self._transient_actions_used,
                            "action_dispatched": action_dispatched,
                        }
                    if action_state == "duplicate":
                        return {
                            **last_state,
                            "recovered": False,
                            "recovery_duplicate_fingerprint": True,
                            "recovery_actions_used": self._transient_actions_used,
                            "action_dispatched": action_dispatched,
                        }
                    try:
                        self._device.app_start(package, stop=False)
                        action_dispatched = True
                    except Exception:
                        return {
                            **last_state,
                            "device_error": True,
                            "recovered": False,
                            "action_dispatch_uncertain": True,
                            "recovery_actions_used": self._transient_actions_used,
                            "action_dispatched": True,
                        }
            else:
                return last_state

            deadline = time.monotonic() + max(0.05, float(settle_timeout_s))
            while time.monotonic() < deadline:
                self._probe.invalidate_cache()
                last_state = self._probe.probe_current_page(fast=False)
                if str(last_state.get("state", "unknown")) in expected:
                    return {
                        **last_state,
                        "recovered": True,
                        "action_dispatched": action_dispatched,
                        "recovery_actions_used": self._transient_actions_used,
                    }
                time.sleep(0.04)

        return {
            **last_state,
            "action_dispatched": action_dispatched,
            "recovery_actions_used": self._transient_actions_used,
        }

    def recover_to_detail_page(self) -> Dict[str, object]:
        """Attempt to recover to the detail/SKU page using a layered strategy.

        Returns:
            The page-probe result dict from the last successful probe.
        """
        # Layer 1 — already on a target page?
        state = self._probe.probe_current_page(fast=True)
        page = state.get("state", "unknown")
        if page in _TARGET_STATES:
            logger.info("Recovery: already on target page (%s)", page)
            return state

        logger.info("Recovery: current page is '%s', starting deep back", page)

        # Layer 2 — deep back (up to 8 presses)
        for step in range(1, _MAX_BACK_STEPS + 1):
            previous_page = page
            try:
                self._device.press("back")
            except Exception as exc:
                logger.warning("Recovery: back press failed at step %d: %s", step, exc)
                break

            # Back navigation is asynchronous.  Poll briefly for a state
            # change before deciding to press Back again, otherwise two rapid
            # presses can skip past the desired detail/SKU page.
            change_deadline = time.monotonic() + _BACK_DELAY
            while True:
                self._probe.invalidate_cache()
                state = self._probe.probe_current_page(fast=True)
                page = state.get("state", "unknown")
                if page != previous_page or time.monotonic() >= change_deadline:
                    break
                time.sleep(0.03)

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
        self._navigator.navigate_to_target_event(initial_probe=state)
        self._probe.invalidate_cache()
        state = self._probe.probe_current_page(fast=True)
        page = state.get("state", "unknown")

        if page in _TARGET_STATES:
            logger.info("Recovery: forward nav reached target page (%s)", page)
            return state

        # Layer 4 — all strategies exhausted; return last known state
        logger.error(
            "Recovery: all strategies exhausted, last known page is '%s'", page
        )
        return state
