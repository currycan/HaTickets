# -*- coding: UTF-8 -*-
"""Sale-start waiting helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Reads constants from the package namespace so that tests'
``patch("mobile.damai_app.time")`` / ``patch("mobile.damai_app.datetime")``
patches still take effect via the package's mirror hook.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from . import (
    SALE_READY_TEXTS,
    _CTA_BLOCKED_KEYWORDS,
    _CTA_READY_KEYWORDS,
    logger,
)

try:
    from mobile.logger import log_event
except ImportError:  # pragma: no cover
    from logger import log_event  # type: ignore[no-redef]

try:
    from mobile.item_resolver import normalize_text
except ImportError:  # pragma: no cover
    from item_resolver import normalize_text  # type: ignore[no-redef]

try:
    from mobile.clock_sync import (
        build_monotonic_sale_deadline,
        sample_clock,
    )
except ImportError:  # pragma: no cover
    from clock_sync import (  # type: ignore[no-redef]
        build_monotonic_sale_deadline,
        sample_clock,
    )

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:  # pragma: no cover
    from ui_primitives import ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:  # pragma: no cover
    raise


_PREWARM_MIN_SLACK_MS = 1200
_PREWARM_WARN_DURATION_MS = 500


class SaleWaiterMixin:
    """Mixin contributing sale-start detection helpers to ``DamaiBot``."""

    def _remember_detail_buy_coord(self, coords, resource_id=None):
        """Stamp one detail CTA coordinate using already-observed structure."""
        if coords is None:
            return None
        anchor = resource_id or getattr(
            getattr(self, "_guard", None),
            "_last_matched_resource_id",
            None,
        )
        anchor_layout = (
            f"detail-cta:{anchor or 'unknown'}:{int(coords[0])}:{int(coords[1])}"
        )
        existing_layout = getattr(
            self, "_hot_cache_layout_digests", {}
        ).get("detail")
        existing_coord = getattr(
            self, "_cached_hot_path_coords", {}
        ).get("detail_buy")
        if (
            existing_coord is not None
            and existing_coord != coords
        ) or (
            isinstance(existing_layout, str)
            and existing_layout.startswith("detail-cta:")
            and existing_layout != anchor_layout
        ):
            self._invalidate_hot_cache(
                scope="detail",
                reason="detail_cta_changed",
            )
            existing_layout = None
        layout_digest = existing_layout or anchor_layout
        self._set_hot_cache_context(
            scope="detail",
            page_state="detail_page",
            layout_digest=layout_digest,
        )
        return self._put_hot_coord(
            "detail_buy",
            coords,
            scope="detail",
            page_state="detail_page",
            layout_digest=layout_digest,
        )

    def _capture_presale_readiness(
        self, remaining_ms, page_probe_hint=None
    ):
        """Capture one read-only sale-entry snapshot at the lead boundary.

        The snapshot replaces the old separate CTA text/coordinate lookups.
        It never clicks and never rebuilds the wall-clock deadline.
        """
        started = time.monotonic()
        page_probe = getattr(self, "_page_probe", None)
        borrow_snapshot = getattr(
            page_probe,
            "borrow_fresh_detail_snapshot",
            None,
        )
        borrowed = None
        if page_probe_hint is not None and callable(borrow_snapshot):
            borrowed = borrow_snapshot(page_probe_hint)

        # A borrowed proof comes from the exact full result that the caller
        # just identity-checked.  Its hierarchy already classified overlays,
        # Activity and the detail CTA, so repeating a selector query and
        # app_current here would add two device round trips without adding
        # evidence.  Expired/invalid hints take the unchanged live-query path.
        overlay = (
            borrowed.get("overlay")
            if isinstance(borrowed, dict)
            else self._probe_hot_blocking_overlay()
        )
        overlay_state = (
            overlay.get("state") if isinstance(overlay, dict) else None
        )
        inspection = (
            dict(borrowed["cta_inspection"])
            if isinstance(borrowed, dict)
            and isinstance(borrowed.get("cta_inspection"), dict)
            else None
        )
        inspection_source = (
            "page_snapshot"
            if inspection is not None
            else "selector"
        )
        forced_page_state = (
            "detail_page" if inspection is not None else None
        )
        guard = getattr(self, "_guard", None)
        if (
            borrowed is None
            and overlay is None
            and guard is not None
        ):
            if inspection is None and forced_page_state is None:
                inspect_cta = getattr(guard, "inspect_cta", None)
                if callable(inspect_cta):
                    try:
                        candidate = inspect_cta()
                    except Exception:
                        candidate = None
                    if isinstance(candidate, dict):
                        inspection = candidate

            # Compatibility for older guards and lightweight test doubles.
            if inspection is None and forced_page_state is None:
                text = guard.get_current_text()
                coords = (
                    None
                    if text is not None
                    else guard.get_cta_center_coords()
                )
                inspection = {
                    "resource_id": getattr(
                        guard, "_last_matched_resource_id", None
                    ),
                    "text": text,
                    "coords": coords,
                    "kind": (
                        "text"
                        if text is not None
                        else "canvas"
                        if coords is not None
                        else "missing"
                    ),
                    "device_error": False,
                }

        inspection = inspection or {
            "resource_id": None,
            "text": None,
            "coords": None,
            "kind": "missing",
            "device_error": False,
        }

        # Both CTA resource IDs are specific to the project-detail purchase
        # bar.  A valid CTA payload is therefore also a page identity anchor:
        # avoid a redundant app_current RPC on the normal prewarm path.
        # Activity remains the fail-closed fallback when that anchor is absent.
        if forced_page_state is not None:
            page_state = forced_page_state
        elif inspection.get("kind") in {"text", "canvas"}:
            page_state = "detail_page"
        elif overlay is not None or inspection.get("device_error"):
            page_state = "unknown"
        else:
            activity = self._get_current_activity()
            if "ProjectDetail" in activity:
                page_state = "detail_page"
            elif activity:
                page_state = "other"
            else:
                page_state = "unknown"

        coords = inspection.get("coords")
        if not (
            isinstance(coords, tuple)
            and len(coords) == 2
            and all(isinstance(value, int) for value in coords)
            and coords[0] >= 0
            and coords[1] >= 0
        ):
            coords = None

        device_error = bool(
            inspection.get("device_error")
            or (
                isinstance(overlay, dict)
                and overlay.get("device_error")
            )
        )
        if coords is not None and overlay is None and not device_error:
            self._remember_detail_buy_coord(
                coords, inspection.get("resource_id")
            )

        # An empty Activity is a best-effort backend result, not proof that the
        # page changed.  A known non-detail Activity without a CTA anchor is
        # the only stable page mismatch rejected here.
        ready = bool(
            not device_error
            and overlay is None
            and not (
                page_state == "other"
                and inspection.get("kind") == "missing"
            )
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        snapshot = {
            "ready": ready,
            "page_state": page_state,
            "overlay_state": overlay_state,
            "overlay": overlay,
            "cta_text": inspection.get("text"),
            "cta_coords": coords,
            "cta_kind": inspection.get("kind", "missing"),
            "cta_source": inspection_source,
            "resource_id": inspection.get("resource_id"),
            "device_error": device_error,
            "remaining_ms": max(0, int(remaining_ms)),
            "duration_ms": duration_ms,
        }
        self._presale_readiness_snapshot = snapshot
        log_event(
            logger,
            "presale_prewarm",
            level=(
                logging.WARNING
                if duration_ms > _PREWARM_WARN_DURATION_MS or not ready
                else logging.INFO
            ),
            device_ready=not device_error,
            page_state=page_state,
            overlay_state=overlay_state or "none",
            coord_ready=coords is not None,
            resource_id=inspection.get("resource_id") or "none",
            source=inspection_source,
            remaining_ms=snapshot["remaining_ms"],
            duration_ms=duration_ms,
            ready=ready,
        )
        return snapshot

    def _scheduled_sale_click_gate(self, overlay=None):
        """Cheap final overlay check before releasing a scheduled click."""
        self._scheduled_entry_gate_clean = False
        if overlay is None:
            overlay = self._probe_hot_blocking_overlay()
        if overlay is None:
            self._scheduled_entry_gate_clean = True
            return True
        if overlay.get("device_error"):
            return False
        if overlay and overlay.get("state") == "pending_order_dialog":
            self._report_pending_order_dialog()
            return False
        if overlay and overlay.get("state") in {
            "manual_verification",
            "transient_error",
        }:
            recovered = self._recover_transient_page_failure(
                initial_probe=overlay
            )
            if getattr(self, "_terminal_failure_reason", None):
                return False
            if recovered and recovered.get("state") == "pending_order_dialog":
                self._report_pending_order_dialog()
                return False
            clean = bool(recovered and recovered.get("state") == "detail_page")
            if clean:
                self._invalidate_all_hot_cache(
                    reason="scheduled_overlay_recovery"
                )
                self._invalidate_page_probe_cache()
                self._scheduled_recovery_invalidated_preparation = True
            self._scheduled_entry_gate_clean = clean
            return clean
        return overlay is None

    def _wait_for_cta_without_schedule(self, timeout_ms):
        """Wait for a safe CTA when the exact sale time is unknown.

        This mode is intentionally text/state driven: a Canvas coordinate can
        exist while the CTA still says "待开售", so coordinates alone are not
        proof that buying is allowed.  A full page probe is sampled every
        250ms so network/loading failures are recovered while waiting instead
        of consuming the whole configured timeout.
        """
        timeout_s = max(0.0, float(timeout_ms) / 1000.0)
        deadline = time.monotonic() + timeout_s
        next_page_check = time.monotonic()
        polls = 0
        loop_t0 = time.monotonic()
        guard = getattr(self, "_guard", None)

        while time.monotonic() < deadline:
            polls += 1
            now_mono = time.monotonic()
            if now_mono >= next_page_check:
                page_probe = self._probe_hot_blocking_overlay()
                if page_probe and page_probe.get("device_error"):
                    return False
                if page_probe and page_probe.get("state") in {
                    "manual_verification",
                    "transient_error",
                }:
                    recovered = self._recover_transient_page_failure(
                        initial_probe=page_probe
                    )
                    if getattr(self, "_terminal_failure_reason", None):
                        return False
                    if recovered and recovered.get("state") == "pending_order_dialog":
                        self._report_pending_order_dialog()
                        return False
                    if (
                        not recovered
                        or recovered.get("state")
                        not in {
                            "detail_page",
                            "sku_page",
                            "order_confirm_page",
                        }
                    ):
                        # Never read a safe-looking CTA through a still-active
                        # error overlay.  Back off until the next page check.
                        remaining = deadline - time.monotonic()
                        if remaining > 0:
                            time.sleep(min(0.05, remaining))
                        next_page_check = time.monotonic() + 0.25
                        continue
                elif page_probe and page_probe.get("state") == "pending_order_dialog":
                    self._report_pending_order_dialog()
                    return False
                next_page_check = time.monotonic() + 0.25

            text = guard.get_current_text() if guard is not None else None
            if text and guard.is_safe_to_click(text):
                log_event(
                    logger,
                    "sale_ready",
                    source="cta_wait_guard",
                    cta_text=text,
                    polls=polls,
                    duration_ms=int((time.monotonic() - loop_t0) * 1000),
                )
                return True

            if self._is_sale_ready():
                cta_text = getattr(self, "_last_sale_ready_text", None) or "?"
                log_event(
                    logger,
                    "sale_ready",
                    source="cta_wait_poll",
                    cta_text=cta_text,
                    polls=polls,
                    duration_ms=int((time.monotonic() - loop_t0) * 1000),
                )
                return True

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.05, remaining))

        log_event(
            logger,
            "sale_wait_timeout",
            level=logging.WARNING,
            mode="cta",
            polls=polls,
            duration_ms=int((time.monotonic() - loop_t0) * 1000),
        )
        return False

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
        """等待开售时间，在开售前 countdown_lead_ms 毫秒开始轮询。

        ``sell_start_time`` is converted once from wall time to a monotonic
        deadline.  No wall-clock reads are used after that anchor, so an NTP
        correction or manual system-time change cannot release the click
        early, delay it, or stretch the final timeout.
        """
        if self.config.sell_start_time is None:
            if self.config.wait_cta_ready_timeout_ms > 0:
                logger.info(
                    "未配置 sell_start_time，等待 CTA 变为可购买（最多 %dms）",
                    self.config.wait_cta_ready_timeout_ms,
                )
                return self._wait_for_cta_without_schedule(
                    self.config.wait_cta_ready_timeout_ms
                )
            return True

        _tz_shanghai = timezone(timedelta(hours=8))
        sell_time = datetime.fromisoformat(self.config.sell_start_time)
        # Ensure timezone-aware
        if sell_time.tzinfo is None:
            sell_time = sell_time.replace(tzinfo=_tz_shanghai)

        clock_provider = getattr(self, "_clock_offset_provider", None)
        observation = sample_clock(clock_provider)
        anchor_monotonic_before = time.monotonic()
        anchor_wall = datetime.now(tz=_tz_shanghai)
        anchor_monotonic_after = time.monotonic()
        anchor_monotonic = (
            anchor_monotonic_before + anchor_monotonic_after
        ) / 2.0
        anchor_span_ms = round(
            (anchor_monotonic_after - anchor_monotonic_before) * 1000,
            3,
        )
        sale_deadline = build_monotonic_sale_deadline(
            sell_time,
            anchor_wall,
            anchor_monotonic,
            observation,
        )
        self._sale_clock_deadline = sale_deadline
        self._sale_clock_observation = observation

        offset_ms = (
            "unknown"
            if observation.offset_seconds is None
            else round(observation.offset_seconds * 1000, 3)
        )
        uncertainty_ms = (
            "unknown"
            if observation.uncertainty_seconds is None
            else round(observation.uncertainty_seconds * 1000, 3)
        )
        trust_log_level = (
            logging.INFO
            if sale_deadline.trust_level == "trusted"
            else logging.WARNING
        )
        log_event(
            logger,
            "clock_trust",
            level=trust_log_level,
            source=observation.source,
            trust_level=sale_deadline.trust_level,
            reason=sale_deadline.trust_reason,
            offset_ms=offset_ms,
            uncertainty_ms=uncertainty_ms,
            anchor_span_ms=anchor_span_ms,
            synchronized=observation.synchronized,
            degrade_to_cta=sale_deadline.degrade_to_cta,
        )

        if sale_deadline.remaining_seconds(anchor_monotonic) <= 0:
            logger.info("开售时间已过，跳过等待")
            if sale_deadline.degrade_to_cta:
                fallback_timeout_ms = max(
                    8000,
                    int(self.config.wait_cta_ready_timeout_ms),
                )
                log_event(
                    logger,
                    "clock_degraded_to_cta",
                    level=logging.WARNING,
                    reason=sale_deadline.trust_reason,
                    timeout_ms=fallback_timeout_ms,
                )
                return self._wait_for_cta_without_schedule(
                    fallback_timeout_ms
                )
            return self._scheduled_sale_click_gate()

        lead_seconds = self.config.countdown_lead_ms / 1000.0
        poll_start_monotonic = sale_deadline.target_monotonic - lead_seconds
        sleep_seconds = poll_start_monotonic - time.monotonic()

        if sleep_seconds > 0:
            logger.info(
                f"等待开售，将在 {self.config.sell_start_time} 前 "
                f"{self.config.countdown_lead_ms}ms 开始轮询"
            )
            time.sleep(sleep_seconds)
        # The display may sleep during a multi-minute pre-sale wait.  Check at
        # the lead boundary even when the process started inside that window;
        # the known-awake path avoids a redundant screen-on RPC.
        ensure_awake = getattr(self, "_ensure_device_awake", None)
        if callable(ensure_awake) and not ensure_awake():
            logger.error("开售轮询前无法唤醒设备，已停止本轮")
            return False
        # Keep a logical lower bound in addition to the live monotonic clock.
        # ``time.sleep`` does not return early under normal Python semantics;
        # this lower bound also makes injected/fake clocks deterministic.
        logical_monotonic = max(
            anchor_monotonic,
            min(poll_start_monotonic, sale_deadline.target_monotonic),
            time.monotonic(),
        )

        # Move the multi-selector popup scan out of the release-time hot path.
        # From here until the final gate the watchdog below handles critical
        # overlays with one combined selector, so the CTA click is not delayed
        # by five serial popup queries at the exact sale instant.
        if self.config.rush_mode:
            if self._dismiss_fast_blocking_dialogs():
                self._invalidate_page_probe_cache()

        if sale_deadline.degrade_to_cta:
            fallback_timeout_ms = max(
                8000 + self.config.countdown_lead_ms,
                int(self.config.wait_cta_ready_timeout_ms),
            )
            log_event(
                logger,
                "clock_degraded_to_cta",
                level=logging.WARNING,
                reason=sale_deadline.trust_reason,
                timeout_ms=fallback_timeout_ms,
            )
            return self._wait_for_cta_without_schedule(fallback_timeout_ms)

        prewarm_snapshot = None
        remaining_to_sale_ms = int(
            max(
                0.0,
                sale_deadline.target_monotonic - time.monotonic(),
            )
            * 1000
        )
        if (
            self.config.rush_mode
            and getattr(self.config, "presale_prewarm_enabled", True)
            and remaining_to_sale_ms >= _PREWARM_MIN_SLACK_MS
        ):
            prewarm_snapshot = self._capture_presale_readiness(
                remaining_to_sale_ms
            )
            overlay = prewarm_snapshot.get("overlay")
            if overlay is not None:
                if not self._scheduled_sale_click_gate(overlay):
                    return False
                # Recovery redraws the page and invalidates every old
                # coordinate.  Re-read once only when enough lead remains.
                remaining_to_sale_ms = int(
                    max(
                        0.0,
                        sale_deadline.target_monotonic - time.monotonic(),
                    )
                    * 1000
                )
                prewarm_snapshot = (
                    self._capture_presale_readiness(remaining_to_sale_ms)
                    if remaining_to_sale_ms >= _PREWARM_MIN_SLACK_MS
                    else None
                )
            if (
                prewarm_snapshot is not None
                and (
                    prewarm_snapshot.get("device_error")
                    or not prewarm_snapshot.get("ready")
                )
            ):
                logger.warning("开售前预热未确认详情页就绪，停止本轮")
                return False

        # 交错轮询循环（issue #41）：每轮依次做「guard 文案读取（廉价）→ Canvas
        # 坐标兜底 → 页面结构多信号兜底」。旧实现先串行烧 guard.wait_until_safe
        # 8s，再串行烧文案轮询 8s；大麦 ≥9.0.2x 详情页 CTA 为 Canvas 自绘
        # （btn_buy_view 已移除、无任何文案节点），两段全部超时，开抢比配置晚 ~8s。
        final_deadline_monotonic = sale_deadline.target_monotonic + 8.0
        polls = 0
        saw_cta_text = False
        guard = getattr(self, "_guard", None)
        # 开售前预判 CTA 是否为 Canvas 自绘（大麦 ≥9.0.2x）：读不到任何文案、但能
        # 按候选 resource-id 定位到中心坐标。若是，则开售前不再每轮跑昂贵的
        # _is_sale_ready（对 Canvas 详情页恒 False，单次 ~1s 串行 u2 查询）——否则
        # 当 sell_time 落在某轮 in-flight 的 _is_sale_ready 期间，开抢会被这一次
        # 查询整体拖后 ~1 个周期（真机实测晚 ~2s）。get_current_text 在前，短路
        # 保证 v8.x（有文案）路径不会触发多余的坐标查询（issue #41 收尾）。
        canvas_coords = (
            prewarm_snapshot.get("cta_coords")
            if prewarm_snapshot is not None
            else None
        )
        canvas_cta = bool(
            prewarm_snapshot is not None
            and prewarm_snapshot.get("cta_kind") == "canvas"
            and canvas_coords is not None
        )
        prewarmed_text = (
            prewarm_snapshot.get("cta_text")
            if prewarm_snapshot is not None
            else None
        )
        prewarmed_text_pending = prewarm_snapshot is not None
        if guard is not None and prewarm_snapshot is None:
            try:
                if guard.get_current_text() is None:
                    canvas_coords = guard.get_cta_center_coords()
                    canvas_cta = canvas_coords is not None
                    if canvas_coords is not None:
                        self._remember_detail_buy_coord(canvas_coords)
            except Exception:
                canvas_cta = False
        loop_t0 = time.monotonic()
        next_watchdog_at = (
            logical_monotonic + 0.35
            if prewarm_snapshot is not None
            else logical_monotonic
        )
        while True:
            now_monotonic = max(logical_monotonic, time.monotonic())
            if now_monotonic >= final_deadline_monotonic:
                break
            polls += 1

            # (0) Canvas 自绘 CTA 快路径（大麦 ≥9.0.2x，issue #41 核心+收尾）：
            #     开售前只做 ~20ms 短睡、绝不做任何 u2 文案查询（get_current_text
            #     对纯 Canvas 容器要遍历子树 ~1s，恒返回 None，每轮查询只会把开抢
            #     时刻整体拖后 ~1 个周期）；到点即按候选 resource-id 中心坐标兜底
            #     返回，点击交给下游 _enter_purchase_flow_from_detail_page 的容器
            #     ID/坐标路径（预约风险由 SKU 页 reservation_mode 检测兜底）。置于
            #     文案查询之前，把开抢延迟从 ~1 个 u2 查询周期收紧到一个短睡间隔。
            if canvas_cta and not saw_cta_text:
                if now_monotonic < sale_deadline.target_monotonic:
                    remaining_to_sale = (
                        sale_deadline.target_monotonic - now_monotonic
                    )
                    if (
                        remaining_to_sale > 0.25
                        and now_monotonic >= next_watchdog_at
                    ):
                        watchdog_overlay = self._probe_hot_blocking_overlay()
                        if watchdog_overlay is not None and not self._scheduled_sale_click_gate(
                            watchdog_overlay
                        ):
                            return False
                        if watchdog_overlay is not None:
                            self._invalidate_hot_cache(
                                scope="detail",
                                reason="presale_watchdog_recovery",
                            )
                            canvas_coords = guard.get_cta_center_coords()
                            canvas_cta = canvas_coords is not None
                            if canvas_coords is not None:
                                self._remember_detail_buy_coord(canvas_coords)
                    if now_monotonic >= next_watchdog_at:
                        next_watchdog_at = now_monotonic + 0.35
                    time.sleep(0.02)
                    logical_monotonic = max(
                        now_monotonic + 0.02,
                        time.monotonic(),
                    )
                    continue
                coords = canvas_coords
                if coords is not None:
                    if not self._scheduled_sale_click_gate():
                        return False
                    log_event(
                        logger,
                        "cta_canvas_fallback",
                        resource_id=guard._last_matched_resource_id,
                        coords=coords,
                        waited_ms=int((time.monotonic() - loop_t0) * 1000),
                        polls=polls,
                    )
                    return True
                # 坐标意外丢失：放弃 Canvas 快路径，回落通用轮询兜底。
                canvas_cta = False

            # (1) v8.x 文案路径：读到安全购买文案立即返回（保留开售前文案翻转
            #     即提前返回的能力，SAFE/BLOCKED 校验行为不变）。
            if prewarmed_text_pending:
                text = prewarmed_text
                prewarmed_text_pending = False
            else:
                text = guard.get_current_text() if guard is not None else None
            if text:
                saw_cta_text = True
                if guard.is_safe_to_click(text):
                    if not self._scheduled_sale_click_gate():
                        return False
                    log_event(
                        logger,
                        "sale_ready",
                        source="buy_button_guard",
                        cta_text=text,
                        polls=polls,
                        duration_ms=int((time.monotonic() - loop_t0) * 1000),
                    )
                    return True

            # (2) Canvas 自绘 CTA 兜底（issue #41 核心）：已到开售时刻、全程未读到
            #     任何 CTA 文案、且能按候选 resource-id 定位到 CTA 中心坐标时立即
            #     返回，把点击交给下游 _enter_purchase_flow_from_detail_page 的
            #     容器 ID/坐标路径（预约风险由 SKU 页 reservation_mode 检测兜底）。
            #     必须置于 _is_sale_ready 之前，避免被其每轮 ~1s 的串行 u2 查询拖后。
            if (
                now_monotonic >= sale_deadline.target_monotonic
                and not saw_cta_text
                and guard is not None
            ):
                coords = canvas_coords or guard.get_cta_center_coords()
                if coords is not None:
                    if not self._scheduled_sale_click_gate():
                        return False
                    log_event(
                        logger,
                        "cta_canvas_fallback",
                        resource_id=guard._last_matched_resource_id,
                        coords=coords,
                        waited_ms=int((time.monotonic() - loop_t0) * 1000),
                        polls=polls,
                    )
                    self._remember_detail_buy_coord(coords)
                    return True

            # (3) 老版页面结构兜底：多购买信号文案轮询（行为不变）。
            if self._is_sale_ready():
                if not self._scheduled_sale_click_gate():
                    return False
                cta_text = getattr(self, "_last_sale_ready_text", None) or "?"
                log_event(
                    logger,
                    "sale_ready",
                    source="poll_loop",
                    cta_text=cta_text,
                    polls=polls,
                    duration_ms=int((time.monotonic() - loop_t0) * 1000),
                )
                return True
            time.sleep(0.05)
            logical_monotonic = max(
                now_monotonic + 0.05,
                time.monotonic(),
            )

        log_event(
            logger,
            "sale_wait_timeout",
            level=logging.WARNING,
            polls=polls,
            duration_ms=int((time.monotonic() - loop_t0) * 1000),
        )
        return False
