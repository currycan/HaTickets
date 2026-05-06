# -*- coding: UTF-8 -*-
"""Recovery / fast-retry helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Hosts the post-failure state-machine dispatcher and back-press
recovery loops shared between the cold-path and warm-path retries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from . import logger

try:
    from mobile.env_snapshot import detect_damai_app_version
    from mobile.logger import log_event
except ImportError:  # pragma: no cover
    from env_snapshot import detect_damai_app_version  # type: ignore[no-redef]
    from logger import log_event  # type: ignore[no-redef]

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:  # pragma: no cover
    from ui_primitives import ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:  # pragma: no cover
    raise


# Failure artifacts root.  Tests override via the ``root`` argument so we
# can lazy-resolve here without binding to a CWD at import time.
def _default_failure_root() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "tmp" / "failures"


_FAILURE_TS_TZ = timezone(timedelta(hours=8))


def _slugify_scene(scene: str) -> str:
    """Compress ``scene`` into a filesystem-safe slug ([a-z0-9_-]+)."""
    cleaned = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in scene.lower()
    )
    cleaned = cleaned.strip("_-") or "scene"
    return cleaned[:48]


def _config_hash(cfg: Any) -> str:
    """Stable short hash of a config object's public attributes."""
    if cfg is None:
        return "none"
    try:
        if hasattr(cfg, "__dict__"):
            payload = {
                k: repr(v) for k, v in vars(cfg).items() if not k.startswith("_")
            }
        else:
            payload = {"repr": repr(cfg)}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except Exception:
        encoded = repr(cfg)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def capture_failure_artifacts(
    bot,
    scene: str,
    *,
    error: Optional[BaseException] = None,
    extra: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Persist UI XML, screenshot, and metadata for a failed run.

    Output: ``tmp/failures/<YYYYmmdd-HHMMSS-mmm>_<scene>.{xml,png,json}``.
    Best-effort — every step is wrapped so logging failures never crash the
    caller (already inside an ``except`` block).

    Args:
        bot: A ``DamaiBot`` (or stand-in) exposing ``d`` (uiautomator2 device)
            and ``config``.
        scene: Short tag identifying the failure context (e.g. ``run_ticket``).
        error: Optional exception that triggered the dump.
        extra: Optional metadata fields merged into the JSON payload.
        root: Override directory.  Defaults to ``<repo>/tmp/failures``.

    Returns:
        A dict describing which artifacts were written: keys ``xml``, ``png``,
        ``json`` map to the absolute path (str) or ``None`` when that artifact
        could not be produced.
    """
    out_root = root or _default_failure_root()
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except Exception:  # pragma: no cover — directory creation is best-effort
        log_event(
            logger,
            "failure_capture_skipped",
            level=logging.WARNING,
            scene=scene,
            reason="mkdir_failed",
            root=str(out_root),
        )
        return {"xml": None, "png": None, "json": None}

    ts = datetime.now(tz=_FAILURE_TS_TZ).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    base = out_root / f"{ts}_{_slugify_scene(scene)}"
    xml_path = base.with_suffix(".xml")
    png_path = base.with_suffix(".png")
    json_path = base.with_suffix(".json")

    written: Dict[str, Any] = {"xml": None, "png": None, "json": None}
    screenshot_failed = False

    device = getattr(bot, "d", None)

    try:
        if device is not None and hasattr(device, "dump_hierarchy"):
            xml_str = device.dump_hierarchy()
            if isinstance(xml_str, bytes):
                xml_path.write_bytes(xml_str)
            else:
                xml_path.write_text(xml_str or "", encoding="utf-8")
            written["xml"] = str(xml_path)
    except Exception as exc:
        logger.debug(f"dump_hierarchy failed during artifact capture: {exc}")

    try:
        if device is not None and hasattr(device, "screenshot"):
            shot = device.screenshot()
            if shot is None:
                screenshot_failed = True
            elif hasattr(shot, "save"):
                shot.save(str(png_path))
                written["png"] = str(png_path)
            elif isinstance(shot, (bytes, bytearray)):
                png_path.write_bytes(bytes(shot))
                written["png"] = str(png_path)
            else:
                screenshot_failed = True
        else:
            screenshot_failed = True
    except Exception as exc:
        screenshot_failed = True
        logger.debug(f"screenshot failed during artifact capture: {exc}")

    cfg = getattr(bot, "config", None)
    serial = (
        getattr(cfg, "serial", None)
        or getattr(cfg, "device_serial", None)
        or os.environ.get("ANDROID_SERIAL")
    )
    metadata: Dict[str, Any] = {
        "timestamp": ts,
        "scene": scene,
        "device": serial or "unknown",
        "damai_version": detect_damai_app_version(serial=serial) or "unknown",
        "config_hash": _config_hash(cfg),
        "screenshot_failed": screenshot_failed,
        "xml_path": written["xml"],
        "png_path": written["png"],
    }
    if error is not None:
        metadata["error_type"] = type(error).__name__
        metadata["error_message"] = str(error)
    if extra:
        metadata.update(extra)

    try:
        json_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        written["json"] = str(json_path)
    except Exception as exc:  # pragma: no cover
        logger.debug(f"failure metadata write failed: {exc}")

    log_event(
        logger,
        "failure_captured",
        level=logging.WARNING,
        scene=scene,
        xml=written["xml"] is not None,
        png=written["png"] is not None,
        json=written["json"] is not None,
        screenshot_failed=screenshot_failed,
    )
    return written


class RecoveryStrategiesMixin:
    """Mixin contributing recovery and fast-retry orchestration to ``DamaiBot``."""

    def _exit_non_target_event_context(
        self, page_probe, max_back_steps=4, back_delay=0.5
    ):
        """Back out from a non-target detail/sku page until search/homepage is reachable."""
        current_probe = page_probe

        for _ in range(max_back_steps):
            if current_probe["state"] not in {"detail_page", "sku_page"}:
                return current_probe
            if self._current_page_matches_target(current_probe):
                return current_probe

            if not self._press_keycode_safe(4, context="退出非目标演出页"):
                break
            time.sleep(back_delay)
            self.dismiss_startup_popups()
            current_probe = self.probe_current_page()

        return current_probe

    def _recover_to_navigation_start(self, page_probe, max_back_steps=3):
        """Recover to a navigable page such as homepage or search page."""
        navigable_states = {"homepage", "search_page", "detail_page", "sku_page"}
        current_probe = page_probe
        if current_probe["state"] in navigable_states:
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="恢复导航起点"):
                break
            time.sleep(0.4)
            current_probe = self.probe_current_page()
            if current_probe["state"] in navigable_states:
                return current_probe

        try:
            if not self._using_u2():
                self.driver.activate_app(self.config.app_package)
            else:
                self.d.app_start(self.config.app_package, stop=False)
            time.sleep(1)
        except Exception:
            pass

        return self.probe_current_page()

    def _recover_to_detail_page_for_local_retry(
        self, initial_probe=None, max_back_steps=8, back_delay=0.15
    ):
        """Recover locally to the current event detail/sku page without rebuilding the Appium session."""
        # Delegate to RecoveryHelper if available
        if hasattr(self, "_recovery") and initial_probe is None:
            result = self._recovery.recover_to_detail_page()
            if result["state"] in {"detail_page", "sku_page"}:
                return result
            # Fall through to existing logic if recovery failed

        # Original logic below (unchanged)
        current_probe = initial_probe or self.probe_current_page(fast=True)
        retryable_states = {"detail_page", "sku_page"}

        if current_probe["state"] in retryable_states and (
            not self.item_detail or self._current_page_matches_target(current_probe)
        ):
            return current_probe

        self.dismiss_startup_popups()
        current_probe = self.probe_current_page()
        if current_probe["state"] in retryable_states and (
            not self.item_detail or self._current_page_matches_target(current_probe)
        ):
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="本地快速回退"):
                break
            time.sleep(back_delay)
            # Use lightweight probe during back-navigation (skip popup
            # dismissal and full probe — saves ~2s per step).
            current_probe = self.probe_current_page(fast=True)
            if current_probe["state"] in retryable_states and (
                not self.item_detail or self._current_page_matches_target(current_probe)
            ):
                return current_probe

        # If we ended up on homepage, try forward navigation
        if current_probe["state"] in {"homepage"}:
            logger.info("回退到首页，尝试正向导航回详情页")
            self.navigate_to_target_event()
            current_probe = self.probe_current_page()

        return current_probe

    def _fast_retry_from_current_state(self):
        """根据当前页面状态进行快速重试。"""
        page_probe = self.probe_current_page()
        state = page_probe["state"]

        if state in ("detail_page", "sku_page"):
            if self.item_detail and not self._current_page_matches_target(page_probe):
                if not self.config.auto_navigate:
                    logger.warning(
                        "当前详情页不是目标演出，手动起跑模式下停止本地快速重试"
                    )
                    return False
                logger.info("当前详情页不是目标演出，转为自动导航")
                return (
                    self.navigate_to_target_event(page_probe)
                    and self.run_ticket_grabbing()
                )
            return self.run_ticket_grabbing()
        elif state == "order_confirm_page":
            if not self.config.if_commit_order:
                if not self._ensure_attendees_selected_on_confirm_page():
                    self._set_terminal_failure("attendee_unselected")
                    logger.error("开发验证模式下观演人未选择完整，已停止")
                    return False
                submit_selectors = [
                    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                    (
                        ANDROID_UIAUTOMATOR,
                        'new UiSelector().textMatches(".*提交.*|.*确认.*")',
                    ),
                    (By.XPATH, '//*[contains(@text,"提交")]'),
                ]
                return self.smart_wait_for_element(
                    *submit_selectors[0], submit_selectors[1:]
                )
            if not self._ensure_attendees_selected_on_confirm_page():
                self._set_terminal_failure("attendee_unselected")
                return False
            submit_selectors = [
                (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textMatches(".*提交.*|.*确认.*")',
                ),
                (By.XPATH, '//*[contains(@text,"提交")]'),
            ]
            return self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:])
        elif state == "pending_order_dialog":
            self._set_run_outcome("order_pending_payment")
            logger.info(
                "检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付"
            )
            return True
        else:
            if self.config.auto_navigate:
                return (
                    self.navigate_to_target_event(page_probe)
                    and self.run_ticket_grabbing()
                )
            recovered_probe = self._recover_to_detail_page_for_local_retry(page_probe)
            if recovered_probe["state"] not in {"detail_page", "sku_page"}:
                logger.warning(
                    f"本地快速回退后仍未回到演出页，当前状态: {recovered_probe['state']}"
                )
                return False
            return self.run_ticket_grabbing()
