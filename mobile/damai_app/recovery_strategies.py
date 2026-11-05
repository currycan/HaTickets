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

    def _invalidate_page_probe_cache(self):
        probe = getattr(self, "_page_probe", None)
        if probe is not None:
            probe.invalidate_cache()

    def _invalidate_hot_cache_after_navigation(self, reason):
        """Drop layout-bound state after a dispatched page-changing action."""
        self._invalidate_all_hot_cache(reason=reason)
        self._verified_price_selection = None
        self._invalidate_formal_session_proof()

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

            self._invalidate_hot_cache_after_navigation(
                "exit_non_target_event"
            )
            if not self._press_keycode_safe(4, context="退出非目标演出页"):
                break
            time.sleep(back_delay)
            self._invalidate_page_probe_cache()
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
            self._invalidate_hot_cache_after_navigation(
                "recover_navigation_back"
            )
            if not self._press_keycode_safe(4, context="恢复导航起点"):
                break
            time.sleep(0.4)
            self._invalidate_page_probe_cache()
            current_probe = self.probe_current_page()
            if current_probe["state"] in navigable_states:
                return current_probe

        try:
            # Foregrounding can recreate an Activity or redraw the current
            # hierarchy.  Invalidate before dispatch so an ambiguous RPC
            # result cannot leave coordinates that are unsafe to reuse.
            self._invalidate_hot_cache_after_navigation(
                "recover_navigation_foreground"
            )
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
        if self.config.probe_only:
            self._set_terminal_failure("probe_local_recovery_blocked")
            logger.warning(
                "probe_only 为只读模式，未执行返回键、前台拉起或自动导航恢复"
            )
            return initial_probe or self.probe_current_page(fast=False)

        # Delegate to RecoveryHelper if available
        if hasattr(self, "_recovery") and initial_probe is None:
            self._invalidate_hot_cache_after_navigation(
                "local_retry_deep_recovery"
            )
            result = self._recovery.recover_to_detail_page()
            if result["state"] in {"detail_page", "sku_page"}:
                return result
            # Fall through to existing logic if recovery failed

        # Original logic below (unchanged)
        current_probe = initial_probe or self.probe_current_page(fast=True)
        retryable_states = {"detail_page", "sku_page"}

        if current_probe["state"] in retryable_states and (
            self._current_page_matches_target(current_probe)
        ):
            return current_probe

        self.dismiss_startup_popups()
        current_probe = self.probe_current_page()
        if current_probe["state"] in retryable_states and (
            self._current_page_matches_target(current_probe)
        ):
            return current_probe

        for _ in range(max_back_steps):
            self._invalidate_hot_cache_after_navigation(
                "local_retry_back"
            )
            if not self._press_keycode_safe(4, context="本地快速回退"):
                break
            time.sleep(back_delay)
            self._invalidate_page_probe_cache()
            # Use lightweight probe during back-navigation (skip popup
            # dismissal and full probe — saves ~2s per step).
            current_probe = self.probe_current_page(fast=True)
            if current_probe["state"] in retryable_states and (
                self._current_page_matches_target(current_probe)
            ):
                return current_probe

        # If we ended up on homepage, try forward navigation
        if current_probe["state"] in {"homepage"}:
            logger.info("回退到首页，尝试正向导航回详情页")
            self._invalidate_hot_cache_after_navigation(
                "local_retry_forward_navigation"
            )
            self.navigate_to_target_event()
            current_probe = self.probe_current_page()

        return current_probe

    def _recover_transient_page_failure(self, initial_probe=None):
        """Try an exact same-page retry before generic back/navigation recovery."""
        recovery = getattr(self, "_recovery", None)
        if recovery is None:
            return None

        recovery_kwargs = dict(
            expected_states={
                "detail_page",
                "sku_page",
                "order_confirm_page",
                "pending_order_dialog",
            },
            max_attempts=2
            if getattr(self.config, "rush_aggressive_retry", False)
            else 1,
        )
        if initial_probe is not None:
            recovery_kwargs["initial_probe"] = initial_probe
        if getattr(self.config, "probe_only", False):
            recovery_kwargs["observe_only"] = True
            recovery_kwargs["max_attempts"] = 1
        result = recovery.recover_transient_failure(**recovery_kwargs)
        if result.get("recovered") or result.get("action_dispatched"):
            # A refresh/foreground action may redraw the page even when the
            # result is still uncertain.  Every cached coordinate and
            # negative preselection result belongs to the old hierarchy and
            # must be discarded before another tap.
            self._invalidate_all_hot_cache(
                reason="transient_page_recovery"
            )
            self._verified_price_selection = None
            self._invalidate_formal_session_proof()
            self._invalidate_page_probe_cache()
            self._scheduled_recovery_invalidated_preparation = True
        if result.get("manual_verification"):
            self._set_terminal_failure("manual_verification_required")
            logger.warning("检测到验证码/人机验证，已停止自动刷新，请手动处理")
        if result.get("device_error"):
            self._device_reconnect_required = True
            logger.warning("检测到设备连接异常，将跳过热重试并重连驱动")
        if result.get("recovery_budget_exhausted"):
            self._set_terminal_failure("refresh_recovery_budget_exhausted")
            logger.warning("刷新恢复动作已达到本轮上限，停止自动刷新")
        if result.get("observe_only"):
            self._set_terminal_failure("probe_transient_error_observed")
            logger.warning(
                "probe_only 只读观察到刷新故障；planned_action=%s，未执行任何恢复动作",
                result.get("planned_action"),
            )
        return result

    def _fast_retry_from_current_state(self):
        """根据当前页面状态进行快速重试。"""
        self._invalidate_page_probe_cache()
        page_probe = self.probe_current_page()
        state = page_probe["state"]

        if state in {"manual_verification", "transient_error", "unknown"}:
            recovered = self._recover_transient_page_failure(
                initial_probe=page_probe
            )
            if recovered is not None:
                page_probe = recovered
                state = page_probe.get("state", "unknown")
            if self._terminal_failure_reason:
                return False
            if self._device_reconnect_required:
                return False
            if self.config.probe_only and state == "unknown":
                self._set_terminal_failure("probe_unknown_observed")
                logger.warning(
                    "probe_only 只读观察到未知页面；未执行返回键或自动导航"
                )
                return False

        navigation_allowed = (
            self.config.auto_navigate and not self.config.probe_only
        )
        if (
            not navigation_allowed
            and state in {"homepage", "search_page"}
        ):
            self._set_terminal_failure("manual_navigation_required")
            logger.warning(
                "快速重试已落到稳定非目标页，停止回退；"
                "请手动打开目标演出详情页"
            )
            return False

        if state in ("detail_page", "sku_page", "session_picker"):
            if (
                state in {"detail_page", "sku_page"}
                and not self._current_page_matches_target(page_probe)
            ):
                if not navigation_allowed:
                    self._set_terminal_failure("manual_navigation_required")
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
            if self.config.probe_only:
                self._set_terminal_failure("probe_read_only_boundary")
                logger.warning(
                    "probe_only 快速重试落在订单确认页；"
                    "为保持只读，停止观演人和订单控件操作"
                )
                return False
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
                ok = self.smart_wait_for_element(
                    *submit_selectors[0], submit_selectors[1:]
                )
                if ok:
                    # 与主路径 validation 口径一致，成功日志按真实结局输出
                    self._set_run_outcome("validation_ready")
                return ok
            if not self._formal_order_context_is_verified():
                self._set_terminal_failure("target_order_unconfirmed")
                logger.error(
                    "快速恢复落在确认页，但缺少本轮目标验证链，"
                    "正式模式拒绝提交"
                )
                return False
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
            # U-10：提交后必须复用主路径同源的 verify_order_result 验证结果，
            # 禁止把「点击成功」直接当抢票成功虚报。
            logger.info("快速重试：确认页重新提交订单，提交后验证下单结果")
            result = self._submit_order_fast(submit_selectors)
            return self._finalize_submit_result(result)
        elif state == "pending_order_dialog":
            return self._report_pending_order_dialog()
        else:
            if navigation_allowed:
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
