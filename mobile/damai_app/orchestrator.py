# -*- coding: UTF-8 -*-
"""DamaiBot orchestrator: __init__, main run loops, and helper methods that
remain on the bot class after W4-01 split.

Module constants (``SALE_READY_TEXTS`` etc.), the package-level ``logger``
and shared dependency classes are imported from :mod:`mobile.damai_app`'s
``__init__`` (the package).  Hot-path helpers split into mixins live in
sibling submodules: ``sale_waiter``, ``purchase_flow``,
``recovery_strategies`` and ``coords_cache``.
"""

from __future__ import annotations

import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from selenium.webdriver.common.by import By

try:
    from mobile.env_snapshot import detect_damai_app_version
    from mobile.item_resolver import normalize_text
    from mobile.logger import log_event
except ImportError:  # pragma: no cover
    from env_snapshot import detect_damai_app_version  # type: ignore[no-redef]
    from item_resolver import normalize_text  # type: ignore[no-redef]
    from logger import log_event  # type: ignore[no-redef]

from . import (
    ANDROID_UIAUTOMATOR,
    AttendeeSelector,
    BuyButtonGuard,
    Config,
    EventNavigator,
    FastPipeline,
    PageProbe,
    PageState,
    PriceSelector,
    PriceSelectorError,
    RecoveryHelper,
    SessionNotFoundError,
    SoldOutError,
    UIPrimitives,
    logger,
    select_session,
)
from .coords_cache import CoordsCacheMixin
from .delegators import DelegatorsMixin
from .purchase_flow import PurchaseFlowMixin
from .recovery_strategies import RecoveryStrategiesMixin
from .sale_waiter import SaleWaiterMixin
from .state_probe import StateProbeMixin


# SKU "确定购买" is intentionally dispatched at most once per run.  A delayed
# transition can move the old bottom-button coordinate onto "立即提交"; any
# repeat before the confirm page is proven could bypass attendee and journal
# gates.
_CONFIRM_CLICK_LIMIT = 1
_monotonic = time.monotonic


class DamaiBot(
    SaleWaiterMixin,
    PurchaseFlowMixin,
    RecoveryStrategiesMixin,
    CoordsCacheMixin,
    StateProbeMixin,
    DelegatorsMixin,
    UIPrimitives,
):
    def __init__(self, config=None, setup_driver=True):
        self.config = config or Config.load_config()
        self.item_detail = None
        self.driver = None
        self.d = None
        self.wait = None
        self._terminal_failure_reason = None
        self._last_run_outcome = None
        self._device_reconnect_required = False
        self._scheduled_entry_gate_clean = False
        self._scheduled_recovery_invalidated_preparation = False
        self._presale_readiness_snapshot = None
        # U-12：本次 run_with_retry 的总执行轮次（外层尝试与快速重试各 +1），
        # 供运行摘要 attempts 字段消费；纯整数自增，零热路径开销。
        self._attempts_made = 0
        self._last_discovery_step_timings = []
        # Cache of {key: (x, y)} coordinates for hot-path elements.
        # Populated on the first run and reused on warm retries (1 HTTP call vs 4+).
        self._cached_hot_path_coords: dict = {}
        # Keys of preselect steps that failed on a previous run and should be skipped.
        self._cached_hot_path_no_match: set = set()
        self._ensure_hot_cache_sidecars()
        self._set_hot_cache_context(app_package=self.config.app_package)
        self._verified_price_selection = None
        self._run_generation = 0
        self._formal_run_context = None
        self._last_submit_intent_summary = None
        self._last_formal_submit_boundary = None
        self._dispatched_submit_context = None
        self._last_bound_submit_result = None
        self._prepare_runtime_config()
        if setup_driver:
            self._setup_driver()

        self._bind_device_modules()
        # Lazy import keeps ``python -m mobile.damai_app.submit_journal`` from
        # pre-importing its own CLI module through the package initialiser.
        from .submit_journal import SubmitJournal

        self._submit_journal = SubmitJournal(self._resolve_submit_device_serial())

        if setup_driver:
            self._emit_boot_snapshot()

    def _begin_formal_run_context(self):
        """Create a per-run, local-only proof chain for the submit boundary."""
        self._last_formal_submit_boundary = None
        self._dispatched_submit_context = None
        self._last_bound_submit_result = None
        if not self.config.if_commit_order:
            self._formal_run_context = None
            return
        navigator = getattr(self, "_navigator", None)
        if navigator is not None:
            navigator._last_formal_event_identity = None
        self._formal_run_context = {
            "run_generation": self._run_generation,
            "event_verified": False,
            "event_target_digest": None,
            "event_identity": None,
            "date_verified": False,
            "city_verified": False,
            "session_verified": False,
            "quantity_verified": False,
            "quantity": None,
        }

    def _mark_formal_event_verified(self):
        """Bind the current exact page observation to this formal run."""
        context = self._formal_run_context
        if not isinstance(context, dict):
            return False
        identity = getattr(
            getattr(self, "_navigator", None),
            "_last_formal_event_identity",
            None,
        )
        configured_title = getattr(self.config, "target_title", None)
        configured_venue = getattr(self.config, "target_venue", None)
        target_title = normalize_text(
            configured_title if isinstance(configured_title, str) else ""
        )
        target_venue = normalize_text(
            configured_venue if isinstance(configured_venue, str) else ""
        )
        observed_title = (
            identity.get("observed_title")
            if isinstance(identity, dict)
            and isinstance(identity.get("observed_title"), str)
            else ""
        )
        observed_venue = (
            identity.get("observed_venue")
            if isinstance(identity, dict)
            and isinstance(identity.get("observed_venue"), str)
            else ""
        )
        observed_title_normalized = normalize_text(observed_title)
        observed_venue_normalized = normalize_text(observed_venue)
        identity_is_current = bool(
            target_title
            and isinstance(identity, dict)
            and identity.get("target_title") == target_title
            and identity.get("observed_title_normalized")
            == observed_title_normalized
            and observed_title_normalized == target_title
            and identity.get("target_venue") == target_venue
            and identity.get("observed_venue_normalized")
            == observed_venue_normalized
            and (
                not target_venue
                or observed_venue_normalized == target_venue
            )
        )
        if not identity_is_current:
            context["event_verified"] = False
            context["event_target_digest"] = None
            context["event_identity"] = None
            return False
        context["event_verified"] = True
        context["event_target_digest"] = self._hot_cache_target_digest(
            "detail"
        )
        context["event_identity"] = {
            "run_generation": self._run_generation,
            "target_title": target_title,
            "observed_title": observed_title,
            "observed_title_normalized": observed_title_normalized,
            "target_venue": target_venue,
            "observed_venue": observed_venue,
            "observed_venue_normalized": observed_venue_normalized,
        }
        return True

    def _mark_formal_session_filters_verified(
        self, *, date=False, city=False, joint=False
    ):
        context = self._formal_run_context
        if not isinstance(context, dict):
            return
        if date:
            context["date_verified"] = True
        if city:
            context["city_verified"] = True
        context["session_verified"] = bool(
            joint
            and context.get("date_verified")
            and context.get("city_verified")
        )

    def _invalidate_formal_session_proof(self):
        """Require configured session filters to be re-proved after a redraw."""
        context = self._formal_run_context
        if not isinstance(context, dict):
            return
        context["date_verified"] = False
        context["city_verified"] = False
        context["session_verified"] = False
        context["quantity_verified"] = False
        context["quantity"] = None
        context.pop("session_layout_digest", None)
        context.pop("session_verified_at", None)
        context.pop("quantity_layout_digest", None)
        context.pop("quantity_verified_at", None)

    def _invalidate_formal_event_proof(self):
        """Drop every formal proof derived from the current event page.

        A full-page navigation or redraw can replace the project while keeping
        the same Activity and even the same date/price labels.  Event proof must
        therefore be cleared together with all downstream session/price proof.
        """
        context = self._formal_run_context
        if isinstance(context, dict):
            context["event_verified"] = False
            context["event_target_digest"] = None
            context["event_identity"] = None
        navigator = getattr(self, "_navigator", None)
        if navigator is not None:
            navigator._last_formal_event_identity = None
        self._invalidate_formal_session_proof()
        self._verified_price_selection = None

    def _formal_order_context_is_verified(self):
        """Return whether this run proved event, session and price."""
        if not self.config.if_commit_order:
            return True
        context = self._formal_run_context
        identity = (
            context.get("event_identity")
            if isinstance(context, dict)
            else None
        )
        configured_title = getattr(self.config, "target_title", None)
        configured_venue = getattr(self.config, "target_venue", None)
        target_title = normalize_text(
            configured_title if isinstance(configured_title, str) else ""
        )
        target_venue = normalize_text(
            configured_venue if isinstance(configured_venue, str) else ""
        )
        observed_title = (
            identity.get("observed_title")
            if isinstance(identity, dict)
            and isinstance(identity.get("observed_title"), str)
            else ""
        )
        observed_venue = (
            identity.get("observed_venue")
            if isinstance(identity, dict)
            and isinstance(identity.get("observed_venue"), str)
            else ""
        )
        if not (
            isinstance(context, dict)
            and context.get("run_generation") == self._run_generation
            and context.get("event_verified") is True
            and context.get("event_target_digest")
            == self._hot_cache_target_digest("detail")
            and isinstance(identity, dict)
            and identity.get("run_generation") == self._run_generation
            and target_title
            and identity.get("target_title") == target_title
            and identity.get("observed_title_normalized")
            == normalize_text(observed_title)
            and normalize_text(observed_title) == target_title
            and identity.get("target_venue") == target_venue
            and identity.get("observed_venue_normalized")
            == normalize_text(observed_venue)
            and (
                not target_venue
                or normalize_text(observed_venue) == target_venue
            )
            and context.get("session_verified") is True
            and context.get("quantity_verified") is True
            and context.get("quantity") == len(self.config.users)
        ):
            return False
        proof = self._verified_price_selection
        coords = proof.get("coords") if isinstance(proof, dict) else None
        checker = getattr(
            getattr(self, "_price_sel", None),
            "_formal_cached_price_is_verified",
            None,
        )
        return bool(callable(checker) and checker(coords))

    def _resolve_submit_device_serial(self):
        """Return only a confirmed string serial for the submit safety journal."""
        configured = getattr(self.config, "serial", None)
        configured = (
            configured.strip()
            if isinstance(configured, str) and configured.strip()
            else None
        )
        connected = getattr(self.d, "serial", None) if self.d is not None else None
        connected = (
            connected.strip()
            if isinstance(connected, str) and connected.strip()
            else None
        )
        if configured and connected and configured != connected:
            raise RuntimeError("configured_device_does_not_match_connected_device")
        return configured or connected

    def _refresh_submit_journal_binding(self):
        """Keep the durable submit boundary bound to the connected device.

        Validation/probe runs may safely rebuild the dormant journal wrapper
        after a device change.  A formal run must instead stop: continuing on a
        different physical device while retaining the prior journal would make
        duplicate-submit protection apply to the wrong phone.
        """
        from .submit_journal import SubmitJournal

        resolved_serial = self._resolve_submit_device_serial()
        current = getattr(self, "_submit_journal", None)
        if current is None:
            self._submit_journal = SubmitJournal(resolved_serial)
            return

        bound_serial = getattr(current, "serial", None)
        if bound_serial == resolved_serial:
            return
        if self.config.if_commit_order and bound_serial is not None:
            self._device_reconnect_required = True
            self._set_terminal_failure("submit_journal_device_changed")
            raise RuntimeError("submit_journal_device_changed")
        self._submit_journal = SubmitJournal(resolved_serial)

    def _replace_config(self, config):
        """Atomically replace config across the bot and every bound helper."""
        self.config = config
        for helper_name in (
            "_attendee_sel",
            "_price_sel",
            "_navigator",
            "_page_probe",
            "_pipeline",
        ):
            helper = getattr(self, helper_name, None)
            if helper is not None and hasattr(helper, "_config"):
                helper._config = config
        self._set_hot_cache_context(app_package=config.app_package)
        self._invalidate_all_hot_cache(reason="config_replaced")
        self._verified_price_selection = None
        self._formal_run_context = None

    def _remember_submit_intent(self, record):
        if isinstance(record, dict) and record.get("intent_id") and record.get("state"):
            self._last_submit_intent_summary = {
                "intent_id": record["intent_id"],
                "state": record["state"],
            }

    def _remember_submit_click_dispatch(self, intent, boundary):
        """Freeze the exact local boundary attached to one dispatched click."""
        if not (
            isinstance(intent, dict)
            and intent.get("intent_id")
            and intent.get("state") == "CLICK_DISPATCHED"
            and intent.get("click_attempts") == 1
            and isinstance(boundary, dict)
            and boundary.get("token")
        ):
            self._dispatched_submit_context = None
            return False
        self._dispatched_submit_context = {
            "intent_id": intent["intent_id"],
            "intent_fingerprint": intent.get("intent_fingerprint"),
            "boundary_token": boundary.get("token"),
            "layout_digest": boundary.get("layout_digest"),
            "fields": boundary.get("fields"),
            "quantity": boundary.get("quantity"),
            "run_generation": boundary.get("run_generation"),
            "boundary_observed_at": boundary.get("observed_at"),
        }
        return True

    def _active_submit_verification_context(self, *, observed_at, timeout):
        """Return the in-process proof for the one click we may verify.

        A payment screen or unpaid-order dialog is not attributable to this run
        on its own: the account may already contain an older order.  Automatic
        success therefore requires all three links to still agree in memory and
        on disk: the fresh confirm-page boundary, this process' remembered
        intent, and a durable CLICK_DISPATCHED record.
        """
        from .submit_journal import SubmitJournalError

        try:
            active = self._submit_journal.load_active()
            current_fingerprint = self._submit_journal.fingerprint(self.config)
        except SubmitJournalError as exc:
            logger.warning("提交结果核验无法读取 submit journal: %s", exc)
            return None
        summary = self._last_submit_intent_summary
        boundary = self._last_formal_submit_boundary
        dispatched = self._dispatched_submit_context
        if not (
            isinstance(active, dict)
            and active.get("state") == "CLICK_DISPATCHED"
            and active.get("click_attempts") == 1
            and active.get("intent_fingerprint") == current_fingerprint
            and isinstance(summary, dict)
            and summary.get("intent_id") == active.get("intent_id")
            and summary.get("state") == "CLICK_DISPATCHED"
            and isinstance(boundary, dict)
            and boundary.get("run_generation") == self._run_generation
            and boundary.get("token")
            and isinstance(dispatched, dict)
            and dispatched.get("intent_id") == active.get("intent_id")
            and dispatched.get("intent_fingerprint")
            == active.get("intent_fingerprint")
            and dispatched.get("boundary_token") == boundary.get("token")
            and dispatched.get("layout_digest") == boundary.get("layout_digest")
            and dispatched.get("fields") == boundary.get("fields")
            and dispatched.get("quantity") == boundary.get("quantity")
            and dispatched.get("run_generation") == self._run_generation
            and dispatched.get("boundary_observed_at")
            == boundary.get("observed_at")
        ):
            return None

        boundary_observed_at = boundary.get("observed_at")
        if not isinstance(boundary_observed_at, (int, float)):
            return None
        age = float(observed_at) - float(boundary_observed_at)
        if age < -0.25 or age > max(8.0, float(timeout) + 2.0):
            logger.warning("提交前页面边界已过期，不自动归属当前支付/订单页面")
            return None
        return {"intent": active, "boundary": boundary}

    def _remember_bound_submit_result(self, result, context, *, activity):
        """Record a local-only result proof consumed by the final mapper."""
        intent = context.get("intent") if isinstance(context, dict) else None
        boundary = context.get("boundary") if isinstance(context, dict) else None
        if not (isinstance(intent, dict) and isinstance(boundary, dict)):
            return False
        self._last_bound_submit_result = {
            "intent_id": intent.get("intent_id"),
            "result": result,
            "boundary_token": boundary.get("token"),
            "activity": activity,
            "observed_at": _monotonic(),
        }
        return True

    def _bound_submit_result_matches(self, result):
        """Prove that ``result`` came from this process' current submit click."""
        proof = self._last_bound_submit_result
        summary = self._last_submit_intent_summary
        boundary = self._last_formal_submit_boundary
        dispatched = self._dispatched_submit_context
        return bool(
            isinstance(proof, dict)
            and proof.get("result") == result
            and proof.get("intent_id")
            and isinstance(summary, dict)
            and summary.get("intent_id") == proof.get("intent_id")
            and isinstance(boundary, dict)
            and boundary.get("token") == proof.get("boundary_token")
            and boundary.get("run_generation") == self._run_generation
            and isinstance(dispatched, dict)
            and dispatched.get("intent_id") == proof.get("intent_id")
            and dispatched.get("boundary_token") == proof.get("boundary_token")
            and dispatched.get("run_generation") == self._run_generation
        )

    def _sanitize_submit_observation_attribution(self, observation):
        """Downgrade positive-looking strings that lack current-run proof."""
        if observation == "success" and not self._bound_submit_result_matches(
            "success"
        ):
            logger.warning(
                "支付页信号未绑定到本次提交意图，按结果未知处理并保留锁"
            )
            return "timeout"
        if observation == "existing_order" and not self._bound_submit_result_matches(
            "existing_order"
        ):
            logger.warning(
                "未支付订单信号未绑定到本次提交意图，按历史/未知订单处理"
            )
            return "preexisting_order"
        return observation

    def _observe_active_submit_intent(self):
        """Fail closed on a prior crash boundary before any new UI action.

        ``None`` means there is no active intent and the normal run may start.
        A boolean is the complete read-only recovery result and must be returned
        directly by ``run_with_retry``.
        """
        from .submit_journal import (
            SubmitJournalError,
            SubmitJournalIdentityUnavailable,
        )

        if not self.config.if_commit_order:
            return None
        try:
            record = self._submit_journal.load_active()
        except SubmitJournalIdentityUnavailable as exc:
            self._set_terminal_failure("submit_journal_identity_unavailable")
            logger.error("%s", exc)
            return False
        except SubmitJournalError as exc:
            self._set_terminal_failure("submit_journal_unavailable")
            logger.error("submit journal 不可安全读取，已停止自动提交: %s", exc)
            return False
        if record is None:
            return None

        self._remember_submit_intent(record)
        logger.warning(
            "检测到未人工确认的 submit intent（state=%s）；"
            "本次只读核验订单状态，绝不再次点击提交",
            record["state"],
        )
        if record["state"] != "CLICK_DISPATCHED":
            self._set_run_outcome("submit_intent_pending_review")
            self._set_terminal_failure("submit_intent_pending_review")
            logger.error(
                "submit intent 未持久化为 CLICK_DISPATCHED；"
                "不会依据当前支付/订单页面改写它，请人工核实后再归档"
            )
            return False
        try:
            observation = self.verify_order_result(timeout=1.5)
        except Exception as exc:  # read-only observation failure remains blocked
            logger.warning("只读核验订单状态失败: %s", exc)
            observation = "timeout"
        observation = self._sanitize_submit_observation_attribution(observation)

        if record["state"] == "CLICK_DISPATCHED":
            try:
                record = self._submit_journal.record_observation(
                    record["intent_id"], observation
                )
                self._remember_submit_intent(record)
            except SubmitJournalError as exc:
                logger.error(
                    "submit intent 观察结果无法持久化；active 记录仍保留: %s",
                    exc,
                )

        if observation == "success":
            self._set_run_outcome("order_submitted")
            logger.info("只读核验确认订单已提交；active intent 仍需人工归档")
            return True
        if observation == "existing_order":
            self._set_run_outcome("order_pending_payment")
            logger.info("只读核验发现未支付订单；请支付后人工归档 intent")
            return True
        if observation in {"existing_order", "preexisting_order"}:
            self._set_run_outcome("preexisting_pending_order")

        self._set_run_outcome("submit_intent_pending_review")
        self._set_terminal_failure("submit_intent_pending_review")
        logger.error(
            "订单结果仍需人工核实（observation=%s）；"
            "在 acknowledge 前自动提交保持锁定",
            observation,
        )
        return False

    def _emit_boot_snapshot(self) -> None:
        """Emit a one-shot ``event=boot`` log line with environment metadata."""
        try:
            serial = getattr(self.config, "serial", None) or getattr(
                self.config, "device_serial", None
            )
            damai_version = detect_damai_app_version(serial=serial) or "unknown"
            self._damai_app_version = damai_version
            self._set_hot_cache_context(app_version=damai_version)
            log_event(
                logger,
                "boot",
                py_version=f"{sys.version_info.major}.{sys.version_info.minor}",
                damai_version=damai_version,
                device=serial or "unknown",
                rush_mode=bool(getattr(self.config, "rush_mode", False)),
            )
        except Exception:  # pragma: no cover — boot logging must never crash startup
            logger.debug("boot snapshot failed (suppressed)", exc_info=True)

    def _bind_device_modules(self) -> None:
        """Bind every device-backed helper to the current driver.

        A full retry reconnects uiautomator2.  Replacing only ``self.d`` leaves
        PageProbe, navigator, selectors and the fast pipeline talking to the
        dead session, so rebuild them as one atomic set.
        """
        previous_recovery = getattr(self, "_recovery", None)
        export_recovery_state = getattr(
            previous_recovery, "export_transient_action_state", None
        )
        recovery_state = (
            export_recovery_state() if callable(export_recovery_state) else None
        )
        device = self.d or self.driver
        self._attendee_sel = AttendeeSelector(device, self.config)
        self._attendee_sel.set_bot(self)
        self._price_sel = PriceSelector(device, self.config, probe=None)
        self._price_sel.set_bot(self)
        self._navigator = EventNavigator(device, self.config, probe=None)
        self._navigator.set_bot(self)

        if self.d is None:
            return

        self._page_probe = PageProbe(self.d, self.config)
        self._page_probe.set_bot(self)
        self._guard = BuyButtonGuard(self.d)
        self._pipeline = FastPipeline(
            self.d, self.config, self._page_probe, self._guard
        )
        self._pipeline.set_bot(self)
        self._pipeline._cached_coords = self._cached_hot_path_coords
        self._pipeline._cached_no_match = self._cached_hot_path_no_match
        self._navigator._probe = self._page_probe
        self._recovery = RecoveryHelper(self.d, self._page_probe, self._navigator)
        restore_recovery_state = getattr(
            self._recovery, "restore_transient_action_state", None
        )
        if recovery_state is not None and callable(restore_recovery_state):
            restore_recovery_state(recovery_state)
        self._price_sel._probe = self._page_probe

    def _ensure_pipeline(self):
        """Lazily create the FastPipeline if not yet initialised (e.g. in tests)."""
        if hasattr(self, "_pipeline"):
            return
        device = self.d or self.driver
        probe = getattr(self, "_page_probe", None)
        guard = getattr(self, "_guard", None)
        self._pipeline = FastPipeline(device, self.config, probe, guard)
        self._pipeline.set_bot(self)
        self._pipeline._cached_coords = self._cached_hot_path_coords
        self._pipeline._cached_no_match = self._cached_hot_path_no_match

    _SOLD_OUT_RE = re.compile(r"缺货|售罄|无票")

    _BUY_BUTTON_NODE_IDS = ("btn_buy_view", "cn.damai:id/btn_buy_view")
    _BUY_BAR_CONTAINER_NODE_IDS = (
        "trade_project_detail_purchase_status_bar_container_fl",
        "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
    )

    def _node_shows_sold_out(self, node):
        """判断单个 XML 节点的 text/content-desc 是否包含缺货文案。"""
        text = node.get("text", "")
        desc = node.get("content-desc", "")
        return bool(
            self._SOLD_OUT_RE.search(text) or self._SOLD_OUT_RE.search(desc)
        )

    def _is_buy_button_sold_out(self):
        """Check if the buy button itself shows sold-out text.

        Only inspects the buy button / purchase-bar element, not the whole
        page, to avoid false positives from other price tiers showing '缺货登记'.

        优先匹配 v8.x 的 btn_buy_view（有文案，可判缺货）；仅当其在整棵树中
        不存在时才回退到 ≥9.0.2x 的购买栏容器（issue #41）。注意 iter("node")
        是文档序（父先于子），容器很可能是 btn_buy_view 的祖先，不能在首个
        命中节点上早退，否则 v8.x 的缺货文本会被空文案容器短路漏检。
        容器为 Canvas 自绘（text/content-desc 均空）时返回 False，不误判缺货。
        """
        xml_root = self._dump_hierarchy_xml()
        if xml_root is None:
            return False
        container_node = None
        for node in xml_root.iter("node"):
            rid = node.get("resource-id", "")
            if rid in self._BUY_BUTTON_NODE_IDS:
                return self._node_shows_sold_out(node)
            if container_node is None and rid in self._BUY_BAR_CONTAINER_NODE_IDS:
                container_node = node
        if container_node is not None:
            return self._node_shows_sold_out(container_node)
        return False

    def _set_terminal_failure(self, reason):
        """Mark the current failure as non-retriable."""
        self._terminal_failure_reason = reason

    def _set_run_outcome(self, outcome):
        """Record the terminal outcome for the latest run attempt."""
        self._last_run_outcome = outcome

    def _report_pending_order_dialog(self):
        """Report an unpaid-order dialog observed before this run submits.

        Every caller reaches this helper before the exact submit boundary.  The
        dialog can therefore only be pre-existing or of unknown provenance; it
        must never be reported as a successful order from this run.
        """
        self._set_run_outcome("preexisting_pending_order")
        self._set_terminal_failure("preexisting_or_unknown_order")
        if self.config.probe_only:
            logger.info(
                "探测提醒：账号存在未支付订单（非本次安全探测产生），"
                "如需可自行前往订单页处理；本次探测未做任何提交"
            )
        else:
            logger.warning(
                "检测到未支付订单弹窗，但它出现在本轮提交前，"
                "无法归属本次运行；已停止自动操作，请人工核实订单列表"
            )
        return False

    def _execution_mode_key(self):
        """Return the current execution mode key."""
        if self.config.probe_only:
            return "probe"
        if not self.config.if_commit_order:
            return "validation"
        return "submit"

    def _execution_mode_label(self):
        """Return a short user-facing label for the current execution mode."""
        labels = {
            "probe": "安全探测",
            "validation": "开发验证",
            "submit": "正式抢票",
        }
        return labels[self._execution_mode_key()]

    def _execution_mode_description(self):
        """Return a user-facing description for the current execution mode."""
        descriptions = {
            "probe": "只检查目标演出页，不会点击“立即购票”",
            "validation": "会继续进入确认页并勾选观演人，但不会点击“立即提交”；这是开发调试路径",
            "submit": "会尝试提交订单",
        }
        return descriptions[self._execution_mode_key()]

    def _log_execution_mode(self):
        """Emit a clear log that tells the user what this run will actually do."""
        logger.info(
            f"开始执行{self._execution_mode_label()}：{self._execution_mode_description()}"
        )

    def _log_success_outcome(self, retry_prefix=""):
        """Emit a success log message that matches the actual run outcome."""
        prefix = f"{retry_prefix}" if retry_prefix else ""
        outcome_messages = {
            "probe_ready": "探测成功：已到目标演出页，购票控件已就绪",
            "validation_ready": "开发验证成功：已到订单确认页，未提交订单",
            "order_submitted": "抢票成功：已提交订单",
            "order_pending_payment": "抢票成功：检测到未支付订单，请立即前往支付完成下单",
            "preexisting_pending_order": "探测提醒：账号存在未支付订单（非本次探测产生），本次未做任何提交",
            "order_flow_completed": "抢票流程完成：已执行提交，等待后续结果确认",
        }
        logger.info(
            f"{prefix}{outcome_messages.get(self._last_run_outcome, '本轮执行成功')}"
        )

    def _finalize_submit_result(self, result, *, start_time=None):
        """_submit_order_fast 结果 → (返回值, outcome/terminal) 的唯一映射。

        主路径 run_ticket_grabbing 与快速重试 order_confirm_page 分支共用，
        保证两条提交路径对同一 verify 结果语义一致（U-10）——
        「成功」只能由 verify_order_result 确认，点击成功不算数。
        耗时信息仅在提供 start_time 时输出（主路径口径）。
        """
        elapsed_suffix = (
            "" if start_time is None else f"耗时: {_monotonic() - start_time:.2f}秒"
        )
        if result == "success":
            if not self._bound_submit_result_matches("success"):
                result = "unbound_success"
            else:
                self._set_run_outcome("order_submitted")
                logger.info(f"抢票成功！{elapsed_suffix}")
                return True
        if result == "existing_order":
            if not self._bound_submit_result_matches("existing_order"):
                result = "preexisting_order"
            else:
                self._set_run_outcome("order_pending_payment")
                logger.info(
                    f"检测到与本次提交连续绑定的未支付订单，请立即前往订单页支付。"
                    f"{elapsed_suffix}"
                )
                return True
        if result == "preexisting_order":
            self._set_run_outcome("preexisting_pending_order")
            self._set_terminal_failure("preexisting_or_unknown_order")
            logger.warning(
                "检测到未支付订单，但无法绑定到本次提交；"
                "已停止自动重试，请人工核实订单列表"
            )
            return False
        if result == "sold_out":
            self._set_run_outcome("sold_out")
            # 该结果来自提交点击后的只读核验。服务端回包与 UI 文案之间
            # 可能存在竞态，必须保留 active intent 并停止所有自动重试。
            self._set_terminal_failure("submit_intent_pending_review")
            logger.warning("提交后观察到售罄，已停止自动重试并等待人工核实订单")
            return False
        if result == "captcha":
            self._set_run_outcome("captcha")
            self._set_terminal_failure("manual_verification_required")
            logger.warning("提交后触发验证码/人机验证，已停止自动重试，请手动处理")
            return False
        if result == "device_error":
            self._set_run_outcome("device_error")
            self._device_reconnect_required = True
            logger.warning("提交前设备连接失效，本轮未点击提交；将重连设备后重试")
            return False
        if result == "intent_pending":
            self._set_run_outcome("submit_intent_pending_review")
            self._set_terminal_failure("submit_intent_pending_review")
            logger.error("存在未确认 submit intent，禁止再次自动提交")
            return False
        if result == "journal_error":
            self._set_run_outcome("submit_journal_error")
            self._set_terminal_failure("submit_journal_unavailable")
            logger.error("无法建立 durable submit intent，未点击提交")
            return False
        if result == "button_not_found":
            # No element was selected, no journal was armed, and no click was
            # dispatched.  A same-session refresh retry is therefore safe.
            self._set_run_outcome("submit_button_not_found")
            logger.warning("提交按钮尚未就绪，本轮未点击；允许安全刷新重试")
            return False
        if result == "target_unconfirmed":
            self._set_run_outcome("target_order_unconfirmed")
            self._set_terminal_failure("target_order_unconfirmed")
            logger.error("目标订单上下文未确认，未点击提交")
            return False
        # timeout / 未知值 — fail closed，避免虚假成功与重复提交
        self._set_terminal_failure("submit_unverified")
        logger.error(
            f"提交后未能确认成功状态（result={result}），"
            f"为避免重复下单已停止自动重试，请手动检查订单列表。{elapsed_suffix}"
        )
        return False

    @contextmanager
    def _timed_step(self, step_name, manual_baseline_seconds=None):
        """Record and log per-step latency for discovery hot path."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            faster_than_manual = (
                True
                if manual_baseline_seconds is None
                else elapsed <= manual_baseline_seconds
            )
            self._last_discovery_step_timings.append(
                {
                    "step": step_name,
                    "seconds": round(elapsed, 3),
                    "manual_baseline_seconds": manual_baseline_seconds,
                    "faster_than_manual": faster_than_manual,
                }
            )
            if manual_baseline_seconds is None:
                logger.info(f"步骤耗时[{step_name}] {elapsed:.2f}s")
            else:
                relation = "快于手动基线" if faster_than_manual else "慢于手动基线"
                log_fn = logger.info if faster_than_manual else logger.warning
                log_fn(
                    f"步骤耗时[{step_name}] {elapsed:.2f}s（手动基线 {manual_baseline_seconds:.2f}s，{relation}）"
                )

    def _prepare_runtime_config(self):
        """Pre-flight config checks before creating the driver session."""
        pass

    def _setup_driver(self):
        """初始化 uiautomator2 直连驱动。"""
        import uiautomator2 as u2

        serial = getattr(self.config, "serial", None) or None
        self.d = u2.connect(serial)
        self._invalidate_all_hot_cache(reason="driver_session_rebound")
        self._verified_price_selection = None
        if not self._ensure_device_awake():
            raise RuntimeError("device_screen_off")
        try:
            self.d.settings["wait_timeout"] = 0
            self.d.settings["operation_delay"] = (0, 0)
        except Exception:
            # 测试桩或精简驱动对象可能不支持 dict-style settings
            pass
        should_start_app = True
        try:
            current_app = self.d.app_current()
            if (
                isinstance(current_app, dict)
                and current_app.get("package") == self.config.app_package
            ):
                should_start_app = False
        except Exception:
            should_start_app = True

        if should_start_app:
            if self.config.probe_only:
                raise RuntimeError("probe_requires_target_app_foreground")
            self.d.app_start(
                self.config.app_package,
                activity=self.config.app_activity,
                stop=False,
            )
        self.driver = self.d
        self.wait = None
        self._device_reconnect_required = False
        if hasattr(self, "_attendee_sel"):
            self._bind_device_modules()
        if hasattr(self, "_submit_journal"):
            self._refresh_submit_journal_binding()

    def _ensure_device_awake(self):
        """Wake an attached device before probing or entering the hot path."""
        if self.d is None:
            return False
        was_off = None
        try:
            info = self.d.info
            if isinstance(info, dict):
                self._set_hot_cache_context(
                    geometry=info,
                    orientation=info.get(
                        "displayRotation",
                        info.get("rotation", info.get("orientation")),
                    ),
                )
            if isinstance(info, dict) and "screenOn" in info:
                was_off = info.get("screenOn") is False
        except Exception:
            pass

        screen_on = getattr(self.d, "screen_on", None)
        if not callable(screen_on):
            if was_off is True:
                logger.error("检测到设备息屏，但当前驱动无法唤醒；请手动解锁手机")
                return False
            return True

        # Avoid an extra device RPC on the known-awake hot path.  When state is
        # unavailable, screen_on is idempotent and safer than probing a black UI.
        if self.config.probe_only and was_off is True:
            logger.error("probe_only 检测到设备息屏；未唤醒设备，请手动亮屏并解锁")
            return False

        if was_off is not False and not self.config.probe_only:
            try:
                screen_on()
            except Exception as exc:
                logger.warning("设备唤醒失败，请确认手机已解锁: %s", exc)
                return was_off is not True
            if was_off is True:
                logger.info("检测到设备息屏，已在探测前唤醒")
                time.sleep(0.25)
        if self._is_device_keyguard_locked():
            logger.error("手机仍处于锁屏界面；请用指纹/密码手动解锁后重新启动")
            return False
        return True

    def _is_device_keyguard_locked(self):
        """Best-effort read-only Android keyguard check."""
        if self.d is None:
            return False
        try:
            result = self.d.shell(["dumpsys", "window", "policy"])
            output = getattr(result, "output", result)
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            if not isinstance(output, str):
                return False
        except Exception:
            return False
        # Some OEM builds leave KeyguardServiceDelegate's
        # ``showingAndNotOccluded`` flag set after a successful unlock.  Treat
        # direct keyguard/input-restriction signals as authoritative and only
        # use the delegate flag when Android exposes no explicit unlocked
        # state.  This keeps the check fail-closed without rejecting an
        # interactive, unrestricted device because of a stale vendor field.
        strong_locked_markers = (
            "isStatusBarKeyguard=true",
            "mShowingLockscreen=true",
            "inputRestricted=true",
            "mInputRestricted=true",
        )
        if any(marker in output for marker in strong_locked_markers):
            return True

        if "showingAndNotOccluded=true" not in output:
            return False

        explicit_unlocked_markers = (
            "isStatusBarKeyguard=false",
            "inputRestricted=false",
            "mInputRestricted=false",
        )
        return not any(marker in output for marker in explicit_unlocked_markers)

    # Core element operations, click operations, element inspection, and selector
    # utilities are inherited from UIPrimitives (mobile/ui_primitives.py).

    def wait_for_page_state(self, expected_states, timeout=5, poll_interval=0.2):
        """轮询等待页面进入指定状态，返回最后一次探测结果。"""
        deadline = _monotonic() + timeout
        last_probe = None

        while _monotonic() < deadline:
            last_probe = self.probe_current_page(fast=True)
            if last_probe["state"] in expected_states:
                return last_probe
            time.sleep(poll_interval)

        return last_probe if last_probe is not None else self.probe_current_page()

    def _probe_blocking_overlay(self):
        """Read critical overlays without trusting the underlying Activity."""
        page_probe = getattr(self, "_page_probe", None)
        if page_probe is None:
            return None
        overlay = page_probe.probe_blocking_overlay()
        self._last_blocking_overlay = overlay
        return overlay

    def _has_hot_blocking_overlay(self):
        page_probe = getattr(self, "_page_probe", None)
        return bool(
            page_probe is not None and page_probe.has_hot_blocking_overlay()
        )

    def _probe_hot_blocking_overlay(self):
        """One cheap signal check, with full classification only on a hit."""
        page_probe = getattr(self, "_page_probe", None)
        if page_probe is None:
            return None
        has_overlay = page_probe.has_hot_blocking_overlay()
        if page_probe._last_hot_probe_device_error:
            self._device_reconnect_required = True
            overlay = {
                "state": "device_error",
                "device_error": True,
            }
            self._last_blocking_overlay = overlay
            return overlay
        if not has_overlay:
            self._last_blocking_overlay = None
            return None
        return self._probe_blocking_overlay()

    def _wait_for_purchase_entry_result(
        self, timeout=1.2, poll_interval=0.04, fallback_probe_on_timeout=True
    ):
        """Wait for the detail-page CTA to open either sku or confirm page."""
        if self.config.rush_mode:
            # 极速模式：每次轮询只用 1 个 ID 选择器（~60ms/轮），替代 4 个选择器（~240ms/轮）。
            # 先检查 sku（更常见的转换目标），再检查确认页 checkbox。
            skip_reservation_check = not self.config.if_commit_order
            deadline = _monotonic() + timeout
            while _monotonic() < deadline:
                if self._has_element(By.ID, "cn.damai:id/checkbox"):
                    overlay = self._probe_hot_blocking_overlay()
                    if overlay is not None:
                        return overlay
                    return {"state": "order_confirm_page", "submit_button": True}
                if self._has_element(By.ID, "cn.damai:id/layout_sku"):
                    overlay = self._probe_hot_blocking_overlay()
                    if overlay is not None:
                        return overlay
                    return {
                        "state": "sku_page",
                        "price_container": True,
                        "reservation_mode": False
                        if skip_reservation_check
                        else self.is_reservation_sku_mode(),
                    }
                time.sleep(poll_interval)
            if fallback_probe_on_timeout:
                return self.probe_current_page()
            return None

        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]
        sku_selectors = [
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
            (By.ID, "cn.damai:id/sku_contanier"),
            (By.ID, "cn.damai:id/layout_price"),
            (By.ID, "cn.damai:id/tv_price_name"),
        ]

        deadline = _monotonic() + timeout
        while _monotonic() < deadline:
            if self._has_any_element(submit_selectors):
                overlay = self._probe_hot_blocking_overlay()
                if overlay is not None:
                    return overlay
                return {"state": "order_confirm_page", "submit_button": True}
            if self._has_any_element(sku_selectors):
                overlay = self._probe_hot_blocking_overlay()
                if overlay is not None:
                    return overlay
                return {
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": self.is_reservation_sku_mode(),
                }
            time.sleep(poll_interval)

        if fallback_probe_on_timeout:
            return self.probe_current_page()
        return None

    def _wait_for_submit_ready(self, timeout=1.6, poll_interval=0.04):
        """Wait until the confirm-page submit button appears."""
        self._last_blocking_overlay = None
        if self.config.rush_mode:
            # 极速模式：单 ID 选择器轮询（~60ms/轮 vs 3选择器 ~180ms/轮）。
            deadline = _monotonic() + timeout
            while _monotonic() < deadline:
                if self._has_element(By.ID, "cn.damai:id/checkbox"):
                    if self._probe_hot_blocking_overlay() is not None:
                        return False
                    return True
                time.sleep(poll_interval)
            return False

        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]

        deadline = _monotonic() + timeout
        while _monotonic() < deadline:
            if self._has_any_element(submit_selectors):
                if self._probe_hot_blocking_overlay() is not None:
                    return False
                return True
            time.sleep(poll_interval)

        return False

    def _click_sku_buy_button_element(self, burst_count=1):
        """Fallback to an element click for Damai's custom SKU buy button."""
        try:
            buy_button = self._find(By.ID, "cn.damai:id/btn_buy_view")
        except Exception:
            return False

        click_action = getattr(buy_button, "click", None)
        if not callable(click_action):
            return False

        for attempt in range(max(1, int(burst_count))):
            try:
                click_action()
            except Exception:
                if attempt == 0:
                    return False
            if attempt < burst_count - 1:
                time.sleep(0.03)
        return True

    # ------------------------------------------------------------------
    # Failure diagnostics (P1 #31)
    # ------------------------------------------------------------------

    def _save_price_failure_dump(
        self, reason: str = "", *, force: bool = False
    ) -> Path | None:
        """Persist a UI hierarchy XML (and best-effort screenshot) to tmp/.

        Called when the price selection path fails so post-mortem analysis
        does not need a live device. ``tmp/`` is git-ignored.
        Returns the dump path on success, or ``None`` if nothing could be
        captured (e.g. driver unavailable).
        """
        if (
            not force
            and getattr(self.config, "rush_mode", False)
            and getattr(self.config, "rush_skip_price_dump", False)
        ):
            logger.warning(
                "极速模式跳过同步 failure dump，避免阻塞下一轮快速重试%s",
                f"（reason={reason}）" if reason else "",
            )
            return None

        try:
            tmp_dir = Path("tmp")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            xml_path = tmp_dir / f"price_dump_{ts}.xml"

            xml_text = ""
            try:
                if self._using_u2():
                    xml_text = self.d.dump_hierarchy()
            except Exception as exc:  # pragma: no cover - logged only
                logger.warning("dump_hierarchy 失败: %s", exc)
                xml_text = ""

            if not xml_text:
                logger.warning("无法获取 UI hierarchy，跳过 dump 落盘")
                return None

            xml_path.write_text(xml_text, encoding="utf-8")
            suffix = f"（reason={reason}）" if reason else ""
            logger.error("failure dump saved to %s%s", xml_path, suffix)

            # best-effort screenshot — never block the dump on screenshot failure
            png_path = xml_path.with_suffix(".png")
            try:
                if self._using_u2() and hasattr(self.d, "screenshot"):
                    self.d.screenshot(str(png_path))
                    logger.error("failure screenshot saved to %s", png_path)
            except Exception as exc:  # pragma: no cover - logged only
                logger.debug("screenshot 不可用: %s", exc)

            return xml_path
        except Exception as exc:  # pragma: no cover - logged only
            logger.warning("保存 price dump 失败: %s", exc)
            return None

    def select_performance_date(self, timeout=1.0):
        """选择演出场次日期"""
        if not self.config.date:
            return True

        if self.config.if_commit_order:
            selected = self._select_formal_session_filter(
                "date",
                self.config.date,
            )
            if selected:
                self._mark_formal_session_filters_verified(date=True)
                logger.info(f"严格确认场次日期: {self.config.date}")
            return selected

        date_selector = f'new UiSelector().textContains("{self.config.date}")'
        if self.ultra_fast_click(ANDROID_UIAUTOMATOR, date_selector, timeout=timeout):
            logger.info(f"选择场次日期: {self.config.date}")
            return True
        logger.debug(f"未找到日期 '{self.config.date}'，不能确认目标场次")
        return False

    @staticmethod
    def _formal_filter_resource_matches(kind, resource_id):
        resource_id = (resource_id or "").casefold()
        if kind == "date":
            return resource_id.endswith("/tv_date")
        resource_name = resource_id.rsplit("/", 1)[-1]
        return resource_name in {
            "tv_city",
            "city_tv",
            "city_name",
            "tv_city_name",
            "city_text",
        }

    def _formal_session_filter_candidates(self, xml_root, kind, target):
        """Return unique clickable filter nodes from an existing hierarchy."""
        if xml_root is None or not hasattr(xml_root, "iter"):
            return []
        target_text = re.sub(r"\s+", "", str(target or "")).casefold()
        if not target_text:
            return []
        parent_map = {
            child: parent
            for parent in xml_root.iter("node")
            for child in parent
        }
        candidates = []
        seen_bounds = set()
        for node in xml_root.iter("node"):
            if node.get("visible-to-user") == "false":
                continue
            text = re.sub(
                r"\s+",
                "",
                f"{node.get('text') or ''}{node.get('content-desc') or ''}",
            ).casefold()
            if target_text not in text:
                continue
            if not self._formal_filter_resource_matches(
                kind, node.get("resource-id")
            ):
                continue
            clickable = node
            while (
                clickable is not None
                and clickable.get("clickable") != "true"
            ):
                clickable = parent_map.get(clickable)
            if clickable is None:
                continue
            bounds_text = clickable.get("bounds", "")
            bounds = self._parse_bounds(bounds_text)
            if not bounds or bounds_text in seen_bounds:
                continue
            seen_bounds.add(bounds_text)
            candidates.append((node, clickable, bounds))
        return candidates

    @staticmethod
    def _formal_session_filter_is_selected(node, clickable):
        for candidate in (node, clickable):
            if candidate is None:
                continue
            if (
                candidate.get("selected") == "true"
                or candidate.get("checked") == "true"
            ):
                return True
            if any(
                descendant.get("selected") == "true"
                or descendant.get("checked") == "true"
                for descendant in candidate.iter("node")
            ):
                return True
        return False

    def _verify_formal_session_filters_same_snapshot(self, xml_root=None):
        """Prove every configured session filter is selected in one hierarchy."""
        if not self.config.if_commit_order:
            return True
        self._invalidate_formal_session_proof()
        if not (
            isinstance(self.config.date, str)
            and self.config.date.strip()
            and isinstance(self.config.city, str)
            and self.config.city.strip()
        ):
            logger.error("正式模式缺少非空 date/city，无法证明目标场次")
            return False

        if xml_root is None:
            xml_root = self._dump_hierarchy_xml()
        for kind, target in (
            ("date", self.config.date),
            ("city", self.config.city),
        ):
            candidates = self._formal_session_filter_candidates(
                xml_root, kind, target
            )
            selected = [
                (node, clickable)
                for node, clickable, _ in candidates
                if self._formal_session_filter_is_selected(node, clickable)
            ]
            if len(candidates) != 1 or len(selected) != 1:
                logger.error(
                    "正式模式最终快照未唯一证明%s已选中: target=%s matches=%d selected=%d",
                    "日期" if kind == "date" else "城市",
                    target,
                    len(candidates),
                    len(selected),
                )
                return False

        self._mark_formal_session_filters_verified(
            date=True,
            city=True,
            joint=True,
        )
        context = self._formal_run_context
        if isinstance(context, dict):
            context["session_layout_digest"] = self._hot_layout_digest_from_xml(
                xml_root, "sku"
            )
            context["session_verified_at"] = _monotonic()
        return True

    def _formal_quantity_control_from_xml(self, xml_root):
        """Return one exact quantity value and plus coordinate from one SKU XML."""
        if xml_root is None or not hasattr(xml_root, "iter"):
            return None
        layouts = [
            node
            for node in xml_root.iter("node")
            if node.get("visible-to-user") != "false"
            and (node.get("resource-id") or "").rsplit("/", 1)[-1]
            == "layout_num"
        ]
        if len(layouts) != 1:
            logger.error(
                "正式模式未唯一定位数量区域: matches=%d",
                len(layouts),
            )
            return None
        layout = layouts[0]
        quantity_values = set()
        for node in layout.iter("node"):
            if node.get("visible-to-user") == "false":
                continue
            for attribute in ("text", "content-desc"):
                value = (node.get(attribute) or "").strip()
                match = re.fullmatch(
                    r"(?:数量\s*[:：]?\s*|共\s*)?(\d{1,2})(?:\s*[张票])?",
                    value,
                )
                if match:
                    quantity_values.add(int(match.group(1)))
        if len(quantity_values) != 1:
            logger.error(
                "正式模式数量区域未唯一给出当前数量: values=%s",
                sorted(quantity_values),
            )
            return None

        plus_candidates = []
        for node in layout.iter("node"):
            if (
                node.get("visible-to-user") == "false"
                or (node.get("resource-id") or "").rsplit("/", 1)[-1]
                != "img_jia"
            ):
                continue
            bounds = self._parse_bounds(node.get("bounds", ""))
            if bounds:
                plus_candidates.append(bounds)
        plus_coords = None
        if len(plus_candidates) == 1:
            left, top, right, bottom = plus_candidates[0]
            plus_coords = ((left + right) // 2, (top + bottom) // 2)
        return {
            "value": next(iter(quantity_values)),
            "plus_coords": plus_coords,
            "plus_matches": len(plus_candidates),
        }

    def _formal_sku_transition_snapshot(
        self,
        xml_root=None,
        *,
        expected_quantity,
    ):
        """Jointly prove the exact SKU state used for the one transition tap."""
        try:
            current = self.d.app_current()
        except Exception as exc:
            logger.warning("正式 SKU 边界无法读取前台应用: %s", exc)
            return None
        package = current.get("package") if isinstance(current, dict) else None
        activity = current.get("activity") if isinstance(current, dict) else None
        activity_text = activity if isinstance(activity, str) else ""
        if (
            package != self.config.app_package
            or not any(
                marker in activity_text.casefold()
                for marker in ("project", "detail", "sku")
            )
        ):
            logger.error(
                "正式 SKU 边界前台页面不可信: package=%s activity=%s",
                package,
                activity,
            )
            return None

        root = xml_root
        if root is None:
            root = self._dump_hierarchy_xml()
        if root is None or not hasattr(root, "iter"):
            logger.warning("正式 SKU 边界无法读取 hierarchy")
            return None

        try:
            from mobile.page_probe import (
                MANUAL_VERIFICATION_MARKERS,
                PENDING_ORDER_MARKERS,
                TRANSIENT_ERROR_MARKERS,
            )
        except ImportError:  # pragma: no cover
            from page_probe import (  # type: ignore[no-redef]
                MANUAL_VERIFICATION_MARKERS,
                PENDING_ORDER_MARKERS,
                TRANSIENT_ERROR_MARKERS,
            )
        visible_text = " ".join(
            value
            for node in root.iter("node")
            if node.get("visible-to-user") != "false"
            for value in (
                (node.get("text") or "").strip(),
                (node.get("content-desc") or "").strip(),
            )
            if value
        )
        if any(
            marker in visible_text
            for marker in (
                *TRANSIENT_ERROR_MARKERS,
                *MANUAL_VERIFICATION_MARKERS,
                *PENDING_ORDER_MARKERS,
            )
        ):
            logger.error("正式 SKU 边界同屏存在异常、验证或历史订单提示")
            return None

        if not self._verify_formal_session_filters_same_snapshot(root):
            logger.error("正式 SKU 边界未共同证明目标日期和城市")
            return None

        price_snapshot = self._price_sel._formal_price_card_snapshot(root)
        if not (
            isinstance(price_snapshot, dict)
            and price_snapshot.get("target_matches") is True
            and price_snapshot.get("unavailable") is False
            and price_snapshot.get("selected") is True
            and price_snapshot.get("coords")
        ):
            logger.error("正式 SKU 边界未共同证明目标票档已选中")
            return None

        quantity = self._formal_quantity_control_from_xml(root)
        if not (
            isinstance(quantity, dict)
            and quantity.get("value") == expected_quantity
        ):
            logger.error(
                "正式 SKU 边界数量不匹配: expected=%s observed=%s",
                expected_quantity,
                quantity.get("value") if isinstance(quantity, dict) else None,
            )
            return None

        buy_nodes = []
        for node in root.iter("node"):
            if (
                node.get("visible-to-user") == "false"
                or (node.get("resource-id") or "").rsplit("/", 1)[-1]
                != "btn_buy_view"
            ):
                continue
            bounds = self._parse_bounds(node.get("bounds", ""))
            if bounds:
                buy_nodes.append(bounds)
        if len(buy_nodes) != 1:
            logger.error(
                "正式 SKU 边界未唯一定位 btn_buy_view: matches=%d",
                len(buy_nodes),
            )
            return None
        left, top, right, bottom = buy_nodes[0]
        buy_coords = ((left + right) // 2, (top + bottom) // 2)

        layout_digest = self._hot_layout_digest_from_xml(root, "sku")
        self._set_hot_cache_context(
            scope="sku",
            page_state="sku_page",
            layout_digest=layout_digest,
        )
        self._put_hot_coord(
            "price",
            price_snapshot["coords"],
            scope="sku",
            page_state="sku_page",
            layout_digest=layout_digest,
        )
        self._put_hot_coord(
            "sku_buy",
            buy_coords,
            scope="sku",
            page_state="sku_page",
            layout_digest=layout_digest,
        )
        proof = self._verified_price_selection
        if not isinstance(proof, dict):
            logger.error("正式 SKU 边界缺少本轮票档 proof")
            return None
        proof.update(
            {
                "coords": price_snapshot["coords"],
                "selection_verified": True,
                "selection_source": "joint_sku_transition_snapshot",
                "selection_observed_price": price_snapshot["observed_price"],
                "selection_layout_digest": layout_digest,
                "stamp": self._cached_hot_path_coord_meta.get("price"),
            }
        )
        context = self._formal_run_context
        if isinstance(context, dict):
            context["quantity_verified"] = True
            context["quantity"] = expected_quantity
            context["quantity_layout_digest"] = layout_digest
            context["quantity_verified_at"] = _monotonic()
        return {
            "root": root,
            "quantity": quantity,
            "price_coords": price_snapshot["coords"],
            "buy_coords": buy_coords,
            "layout_digest": layout_digest,
            "activity": activity_text,
            "observed_at": _monotonic(),
            "run_generation": self._run_generation,
        }

    def _ensure_formal_quantity_and_transition_snapshot(self):
        """Select quantity one increment at a time, proving every redraw."""
        users = [
            value.strip()
            for value in (self.config.users or [])
            if isinstance(value, str) and value.strip()
        ]
        if not users or len(users) != len(set(users)):
            self._set_terminal_failure("quantity_selection_unconfirmed")
            logger.error("正式模式观演人配置为空或重名，无法绑定票数")
            return None
        target_quantity = len(users)
        root = self._dump_hierarchy_xml()
        quantity = self._formal_quantity_control_from_xml(root)
        if not isinstance(quantity, dict):
            self._set_terminal_failure("quantity_selection_unconfirmed")
            return None
        current_quantity = quantity["value"]
        if current_quantity > target_quantity:
            self._set_terminal_failure("quantity_selection_unconfirmed")
            logger.error(
                "当前票数 %d 大于目标票数 %d；正式模式不执行盲目减量",
                current_quantity,
                target_quantity,
            )
            return None

        while current_quantity < target_quantity:
            snapshot = self._formal_sku_transition_snapshot(
                root,
                expected_quantity=current_quantity,
            )
            plus_coords = (
                snapshot.get("quantity", {}).get("plus_coords")
                if isinstance(snapshot, dict)
                else None
            )
            if not plus_coords:
                self._set_terminal_failure("quantity_selection_unconfirmed")
                logger.error("正式模式未唯一定位数量加号，停止操作")
                return None
            try:
                self._click_coordinates(*plus_coords, duration=30)
            except Exception as exc:
                self._set_terminal_failure("quantity_selection_unknown")
                logger.error("数量加号点击结果不确定，停止确定购买: %s", exc)
                return None
            self._invalidate_hot_cache(
                scope="sku",
                reason="quantity_change_dispatched",
            )
            time.sleep(0.04)
            root = self._dump_hierarchy_xml()
            observed = self._formal_quantity_control_from_xml(root)
            if not (
                isinstance(observed, dict)
                and observed.get("value") == current_quantity + 1
            ):
                self._set_terminal_failure("quantity_selection_unconfirmed")
                logger.error(
                    "数量点击后未观察到精确递增: before=%d observed=%s",
                    current_quantity,
                    observed.get("value") if isinstance(observed, dict) else None,
                )
                return None
            current_quantity += 1

        snapshot = self._formal_sku_transition_snapshot(
            root,
            expected_quantity=target_quantity,
        )
        if not isinstance(snapshot, dict):
            self._set_terminal_failure("quantity_selection_unconfirmed")
            return None
        return snapshot

    def _select_formal_session_filter(self, kind, target):
        """Select one explicit filter and verify its post-click selected state."""
        before = self._dump_hierarchy_xml()
        candidates = self._formal_session_filter_candidates(
            before, kind, target
        )
        if len(candidates) != 1:
            logger.error(
                "正式模式无法唯一确认%s筛选控件: target=%s matches=%d",
                "日期" if kind == "date" else "城市",
                target,
                len(candidates),
            )
            return False
        node, clickable, (left, top, right, bottom) = candidates[0]
        if self._formal_session_filter_is_selected(node, clickable):
            logger.info(
                "正式模式目标%s已处于选中状态，跳过重复点击",
                "日期" if kind == "date" else "城市",
            )
            return True
        self._invalidate_formal_session_proof()
        try:
            self._click_coordinates(
                (left + right) // 2,
                (top + bottom) // 2,
                duration=30,
            )
        except Exception as exc:
            logger.warning("正式场次筛选点击结果不确定: %s", exc)
            return False
        time.sleep(0.08)
        after = self._dump_hierarchy_xml()
        post_candidates = self._formal_session_filter_candidates(
            after, kind, target
        )
        selected = [
            node
            for node, clickable, _ in post_candidates
            if self._formal_session_filter_is_selected(node, clickable)
        ]
        if len(selected) != 1:
            logger.error(
                "正式模式点击后未确认%s筛选已选中: target=%s",
                "日期" if kind == "date" else "城市",
                target,
            )
            return False
        return True

    def _handle_session_picker(self):
        """SESSION_PICKER 状态下按 config.date / config.city 选定场次。

        优先级与 :func:`mobile.event_navigator.select_session` 一致：
        date+city > date 唯一 > fallback_index(0)。多场次场景下永不跳过，
        即便 rush_skip_session=True 也强制选场（issue #25 根因防御）。
        """
        try:
            from mobile.date_utils import normalize_date
        except ImportError:  # pragma: no cover
            from date_utils import normalize_date  # type: ignore[no-redef]

        if getattr(self.config, "rush_skip_session", False):
            logger.warning(
                "rush_skip_session=True 但当前为多场次场次选择页 — 强制选场避免抢错（issue #25）"
            )

        normalised_date = normalize_date(self.config.date) if self.config.date else None
        try:
            # Invalidate before dispatch.  A device RPC can time out after the
            # tap has already reached Android, so waiting for a successful
            # response would leave old SKU/confirm coordinates in an
            # indeterminate post-click layout.
            self._invalidate_hot_cache(
                scope="sku",
                reason="session_selection_dispatched",
            )
            self._invalidate_hot_cache(
                scope="confirm",
                reason="session_selection_dispatched",
            )
            self._verified_price_selection = None
            chosen_idx = select_session(
                self.d,
                date=normalised_date,
                city=self.config.city,
                fallback_index=None if self.config.if_commit_order else 0,
                strict=self.config.if_commit_order,
            )
            logger.info(
                "多场次活动：已选定场次 idx=%d (date=%s, city=%s)",
                chosen_idx,
                normalised_date,
                self.config.city,
            )
            return True
        except SessionNotFoundError as exc:
            logger.error("多场次活动：select_session 失败 — %s", exc)
            self._set_terminal_failure(
                "session_filter_unconfirmed"
                if self.config.if_commit_order
                else "session_not_found"
            )
            return False

    def _ensure_target_session(self, page_probe):
        """Resolve a hidden multi-session picker before any price interaction.

        A fast Activity probe reports every ``NcovSku`` screen as ``sku_page``.
        The session picker uses that same Activity, so relying on the Activity
        alone can select a price from the default session.  This boundary check
        is intentionally performed once per SKU transition and records the
        result in the returned probe.
        """
        if not page_probe:
            return page_probe

        result = dict(page_probe)
        state = result.get("state")
        if state == "sku_page" and not result.get("session_checked"):
            page_probe_helper = getattr(self, "_page_probe", None)
            session_picker = (
                page_probe_helper.probe_session_picker_markers()
                if page_probe_helper is not None
                else False
            )
            if session_picker is None:
                self._device_reconnect_required = True
                logger.warning("场次选择器探测失败，停止选价并交给设备重连")
                return None
            if session_picker:
                result["state"] = PageState.SESSION_PICKER.value
                state = PageState.SESSION_PICKER.value
            else:
                result["session_checked"] = True

        if state != PageState.SESSION_PICKER.value:
            return result

        if not self._handle_session_picker():
            return None

        self._invalidate_page_probe_cache()
        result = self.probe_current_page()
        if result.get("state") != "sku_page":
            logger.error(
                "选择目标场次后未进入票档页（state=%s），停止以避免抢错场次",
                result.get("state"),
            )
            if not self._terminal_failure_reason:
                self._set_terminal_failure("session_filter_unconfirmed")
            return None

        result = dict(result)
        result["session_checked"] = True
        if self.config.if_commit_order:
            timeout = 0.35 if self.config.rush_mode else 1.0
            date_ready = self.select_performance_date(timeout=timeout)
            city_ready = self._select_city_from_detail_page(timeout=timeout)
            if not (
                date_ready
                and city_ready
                and self._verify_formal_session_filters_same_snapshot()
            ):
                self._set_terminal_failure("session_filter_unconfirmed")
                return None
        result["session_selection_confirmed"] = True
        return result

    def _reverify_formal_session_after_sku_recovery(self, page_probe):
        """Re-prove configured session filters after a SKU-page redraw."""
        if not self.config.if_commit_order:
            return page_probe

        candidate = dict(page_probe or {})
        candidate.pop("session_checked", None)
        candidate.pop("session_selection_confirmed", None)
        candidate = self._ensure_target_session(candidate)
        if not candidate or candidate.get("state") != "sku_page":
            return None
        if candidate.get("session_selection_confirmed"):
            return candidate

        timeout = 0.35 if self.config.rush_mode else 1.0
        date_ready = (
            self.select_performance_date(timeout=timeout)
            if self.config.date
            else True
        )
        city_ready = (
            self._select_city_from_detail_page(timeout=timeout)
            if self.config.city
            else True
        )
        if not (
            date_ready
            and city_ready
            and self._verify_formal_session_filters_same_snapshot()
        ):
            if not self._terminal_failure_reason:
                self._set_terminal_failure("session_filter_unconfirmed")
            logger.error(
                "票档页刷新后未能重新确认目标日期/城市，"
                "正式模式停止选价"
            )
            return None
        return candidate

    def _select_city_from_detail_page(self, timeout=1.0):
        """Select the configured city on the detail page."""
        if self.config.if_commit_order:
            selected = self._select_formal_session_filter(
                "city",
                self.config.city,
            )
            if selected:
                self._mark_formal_session_filters_verified(city=True)
            return selected
        city_selectors = [
            (ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
            (
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{self.config.city}")',
            ),
            (By.XPATH, f'//*[@text="{self.config.city}"]'),
        ]
        return self.smart_wait_and_click(
            *city_selectors[0], city_selectors[1:], timeout=timeout
        )

    def _prepare_detail_page_hot_path(self):
        """Preselect detail-page filters before the sale opens so launch-time work is minimized."""
        page_probe = self.probe_current_page()
        if page_probe["state"] != "detail_page":
            return False

        has_filters = bool(self.config.date or self.config.city)
        date_ready = (
            self.select_performance_date() if self.config.date else True
        )
        city_ready = True
        if self.config.city:
            city_ready = self._select_city_from_detail_page(timeout=0.6)
            if city_ready:
                logger.info(f"已预选城市: {self.config.city}")
            else:
                logger.warning("未确认目标城市，开售后将重新选择")

        if has_filters:
            self._invalidate_page_probe_cache()
        return has_filters and date_ready and city_ready

    def check_session_valid(self):
        """检查大麦 App 登录状态是否有效"""
        probe_hint = getattr(self, "_startup_probe_hint", None)
        hinted_activity = (
            probe_hint.get("activity") if isinstance(probe_hint, dict) else None
        )
        activity = (
            hinted_activity
            if isinstance(hinted_activity, str) and hinted_activity
            else self._get_current_activity()
        )
        if "LoginActivity" in activity or "SignActivity" in activity:
            logger.error("检测到登录页面，大麦 App 登录已过期，请重新登录")
            return False

        if (
            isinstance(probe_hint, dict)
            and probe_hint.get("snapshot_observed_at")
        ):
            if probe_hint.get("login_prompt"):
                logger.error("检测到登录提示，请重新登录大麦 App")
                return False
            # The same fresh hierarchy already proved that neither login marker
            # is visible, so avoid two more selector RPCs.
            return True

        login_prompt_selectors = [
            'new UiSelector().textContains("请先登录")',
            'new UiSelector().textContains("登录/注册")',
        ]
        for selector in login_prompt_selectors:
            if self._has_element(ANDROID_UIAUTOMATOR, selector):
                logger.error("检测到登录提示，请重新登录大麦 App")
                return False

        return True

    def verify_order_result(self, timeout=5):
        """Read one coherent result snapshot and attribute it fail-closed.

        Old payment pages and unpaid orders are common account state, not proof
        of this run.  A positive result is accepted only when this process still
        holds the fresh confirm-page boundary and the matching durable intent is
        exactly ``CLICK_DISPATCHED`` for the current config fingerprint.
        """
        timeout_value = max(0.0, float(timeout))
        started = _monotonic()
        deadline = started + timeout_value
        self._last_bound_submit_result = None
        context = self._active_submit_verification_context(
            observed_at=started,
            timeout=timeout_value,
        )

        payment_text_markers = (
            "立即支付",
            "去支付",
            "确认支付",
            "支付剩余时间",
            "收银台",
        )
        sold_out_markers = ("已售罄", "库存不足", "暂时无票")
        captcha_markers = ("滑块", "人机验证", "安全验证", "验证码")
        submit_resource_ids = {
            "cn.damai:id/btn_submit",
            "cn.damai:id/submit_btn",
        }
        unattributed_payment_logged = False

        while _monotonic() < deadline:
            activity = self._get_current_activity()
            root = self._dump_hierarchy_xml()
            values = []
            visible_resource_ids = set()
            if root is not None and hasattr(root, "iter"):
                parent_map = {
                    child: parent
                    for parent in root.iter()
                    for child in parent
                }

                def _is_visible(node):
                    cursor = node
                    while cursor is not None:
                        if cursor.get("visible-to-user") == "false":
                            return False
                        cursor = parent_map.get(cursor)
                    return True

                for node in root.iter("node"):
                    if not _is_visible(node):
                        continue
                    resource_id = node.get("resource-id") or ""
                    if resource_id:
                        visible_resource_ids.add(resource_id)
                    for attribute in ("text", "content-desc"):
                        value = (node.get(attribute) or "").strip()
                        if value:
                            values.append(value)

            visible_text = " ".join(values)
            confirm_still_visible = bool(
                submit_resource_ids & visible_resource_ids
                or "立即提交" in values
                or any("确认购买" in value for value in values)
            )
            activity_text = activity if isinstance(activity, str) else ""
            activity_key = activity_text.casefold()
            payment_activity = any(
                marker in activity_key
                for marker in (
                    "payactivity",
                    "cashier",
                    "alipayclient",
                    "paymentactivity",
                )
            )
            payment_text = any(
                marker in visible_text for marker in payment_text_markers
            )
            pending_order = "未支付" in visible_text

            if any(marker in visible_text for marker in sold_out_markers):
                logger.warning("提交后页面显示票已售罄")
                return "sold_out"
            if any(marker in visible_text for marker in captcha_markers):
                logger.warning("提交后触发验证码/人机验证")
                return "captcha"

            boundary = context.get("boundary") if isinstance(context, dict) else None
            boundary_activity = (
                boundary.get("activity") if isinstance(boundary, dict) else None
            )
            left_confirm_boundary = bool(
                root is not None
                and not confirm_still_visible
                and (
                    (activity_text and activity_text != boundary_activity)
                    or payment_text
                    or pending_order
                )
            )

            if pending_order:
                # A generic unpaid-order marker cannot identify which order it
                # belongs to, even immediately after a click.  Preserve the
                # intent and require human review rather than claiming success.
                logger.warning(
                    "检测到未支付订单，但页面未提供可安全归属本次提交的订单标识"
                )
                return "preexisting_order"

            if payment_activity or payment_text:
                if (
                    context is not None
                    and left_confirm_boundary
                    and self._remember_bound_submit_result(
                        "success",
                        context,
                        activity=activity_text,
                    )
                ):
                    logger.info("本次提交已连续跳转到支付页面")
                    return "success"
                if not unattributed_payment_logged:
                    logger.warning(
                        "检测到支付页面信号，但缺少本次点击的完整归属证明，"
                        "暂不判定成功"
                    )
                    unattributed_payment_logged = True

            time.sleep(0.3)

        logger.warning("订单验证超时")
        return "timeout"

    def dismiss_startup_popups(self):
        """处理首启的一次性系统/应用弹窗。"""
        dismissed = False
        probe_hint = getattr(self, "_startup_probe_hint", None)
        snapshot_available = bool(
            isinstance(probe_hint, dict)
            and probe_hint.get("snapshot_observed_at")
        )
        hinted_resource_ids = set(
            probe_hint.get("startup_popup_resource_ids") or ()
        ) if snapshot_available else set()
        hinted_texts = set(
            probe_hint.get("startup_popup_texts") or ()
        ) if snapshot_available else set()

        if self.config.probe_only:
            # ``--probe`` is an observation-only contract, not merely a
            # no-submit contract.  Even dismissing a harmless-looking startup
            # dialog mutates device state and can accidentally accept consent
            # or hide evidence needed to diagnose a blocked launch.  A full
            # probe already tells us whether a known popup is visible, so stop
            # and ask for manual handling without dispatching any click.
            if hinted_resource_ids or hinted_texts:
                self._set_terminal_failure("probe_blocking_popup_observed")
                logger.warning(
                    "probe_only 只读观察到启动弹窗；未点击任何弹窗控件，"
                    "请手动处理后重新探测"
                )
            return False

        popup_clicks = [
            (By.ID, "android:id/ok", "resource"),  # Android 全屏提示
            (By.ID, "cn.damai:id/id_boot_action_agree", "resource"),  # 大麦隐私协议
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn", "resource"),  # 开启消息通知
            (
                By.ID,
                "cn.damai:id/damai_theme_dialog_close_layout",
                "resource",
            ),  # 新版升级提示关闭按钮
            (By.ID, "cn.damai:id/damai_theme_dialog_close_btn", "resource"),  # 新版升级提示关闭图标
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().text("Cancel")',
                "Cancel",
            ),  # Add to home screen
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")', "下次再说"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")', "我知道了"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")', "知道了"),
        ]

        for by, value, hint in popup_clicks:
            if snapshot_available:
                present = (
                    value in hinted_resource_ids
                    if hint == "resource"
                    else hint in hinted_texts
                )
                if not present:
                    continue
                should_click = True
            else:
                should_click = self._has_element(by, value)
            if should_click and self.ultra_fast_click(by, value):
                dismissed = True
                time.sleep(0.3)

        return dismissed

    def _dismiss_fast_blocking_dialogs(self):
        """Dismiss lightweight blocking dialogs without the full startup scan."""
        dismissed = False
        dialog_clicks = [
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),
            (By.ID, "cn.damai:id/damai_theme_dialog_close_btn"),
            (By.ID, "cn.damai:id/damai_theme_dialog_close_layout"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
        ]

        for by, value in dialog_clicks:
            if not self._has_element(by, value):
                continue
            if self.ultra_fast_click(by, value, timeout=0.15):
                dismissed = True
                time.sleep(0.05)

        return dismissed

    def run_ticket_grabbing(self, initial_page_probe=None):
        """执行抢票主流程"""
        try:
            start_time = _monotonic()
            self._run_generation += 1
            self._verified_price_selection = None
            self._begin_formal_run_context()
            self._terminal_failure_reason = None
            self._last_run_outcome = None
            self._scheduled_entry_gate_clean = False
            self._scheduled_recovery_invalidated_preparation = False
            self._log_execution_mode()
            page_probe = initial_page_probe or self.probe_current_page(
                fast=not (
                    self.config.probe_only or self.config.if_commit_order
                )
            )
            if (
                self.config.probe_only
                and initial_page_probe is not None
                and page_probe.get("state") in {"detail_page", "sku_page"}
                and not (
                    page_probe.get("price_container")
                    and (
                        page_probe.get("state") == "sku_page"
                        or page_probe.get("purchase_button")
                    )
                )
            ):
                # A caller may supply an Activity-only probe.  Safe probe mode
                # needs the current readiness anchors, so refresh read-only
                # instead of treating omitted fields as real missing controls.
                self._invalidate_page_probe_cache()
                page_probe = self.probe_current_page(fast=False)
            fast_validation_hot_path = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and not self.config.probe_only
                and initial_page_probe is not None
                and page_probe["state"] in {"detail_page", "sku_page"}
                and self._current_page_matches_target(page_probe)
            )
            if fast_validation_hot_path:
                logger.info(
                    "开发验证极速路径：跳过启动弹窗与登录探测，直接执行抢票热路径"
                )
                overlay = self._probe_hot_blocking_overlay()
                if overlay is not None:
                    if overlay.get("state") == "pending_order_dialog":
                        return self._report_pending_order_dialog()
                    if overlay.get("state") in {
                        "manual_verification",
                        "transient_error",
                    }:
                        self._recover_transient_page_failure(
                            initial_probe=overlay
                        )
                    return False
                if page_probe["state"] == "detail_page":
                    if self._has_warm_pipeline_coords():
                        # Warm pipeline: blind shell clicks + concurrent polling.
                        pipeline_result = self._run_warm_validation_pipeline(start_time)
                    elif self._using_u2():
                        # Cold pipeline: XML dump → shell batch → concurrent polling.
                        pipeline_result = self._run_cold_validation_pipeline(start_time)
                    else:
                        pipeline_result = None
                    if pipeline_result is True:
                        return True
                    if pipeline_result is False:
                        return False
                    # pipeline_result is None → fall through to normal flow.
                    # Refresh page_probe since the pipeline may have advanced
                    # the app state (e.g. from detail_page to sku_page).
                    page_probe = self.probe_current_page(fast=True)
            else:
                self._startup_probe_hint = page_probe
                try:
                    dismissed = self.dismiss_startup_popups()
                    if self._terminal_failure_reason:
                        return False
                    if dismissed is True:
                        self._invalidate_page_probe_cache()
                        page_probe = self.probe_current_page(
                            fast=not (
                                self.config.probe_only
                                or self.config.if_commit_order
                            )
                        )
                        self._startup_probe_hint = page_probe
                    if not self.check_session_valid():
                        self._set_terminal_failure("session_invalid")
                        return False
                finally:
                    self._startup_probe_hint = None

            if page_probe["state"] == "pending_order_dialog":
                return self._report_pending_order_dialog()
            if page_probe["state"] in {
                "manual_verification",
                "transient_error",
            }:
                page_probe = (
                    self._recover_transient_page_failure(
                        initial_probe=page_probe
                    )
                    or page_probe
                )
                if self._terminal_failure_reason:
                    return False

            if page_probe["state"] not in {
                "detail_page",
                "sku_page",
                "order_confirm_page",
                PageState.SESSION_PICKER.value,
            } or (
                page_probe["state"]
                in {
                    "detail_page",
                    "sku_page",
                    PageState.SESSION_PICKER.value,
                }
                and not self._current_page_matches_target(page_probe)
            ):
                if self.config.auto_navigate and not self.config.probe_only:
                    logger.info("当前不在目标演出页，尝试自动导航")
                    if not self.navigate_to_target_event(page_probe):
                        return False
                    page_probe = self.probe_current_page()
                    if page_probe["state"] == "pending_order_dialog":
                        return self._report_pending_order_dialog()
                    if page_probe["state"] not in {
                        "detail_page",
                        "sku_page",
                        "order_confirm_page",
                        PageState.SESSION_PICKER.value,
                    } or (
                        page_probe["state"]
                        in {
                            "detail_page",
                            "sku_page",
                            PageState.SESSION_PICKER.value,
                        }
                        and not self._current_page_matches_target(page_probe)
                    ):
                        logger.error(
                            "自动导航后仍未确认目标演出页，停止本轮以避免抢错场"
                        )
                        return False
                else:
                    logger.warning(
                        "当前不在目标演出页，请先手动打开目标演出详情页"
                    )
                    if page_probe["state"] in {
                        PageState.HOMEPAGE.value,
                        PageState.SEARCH_PAGE.value,
                        PageState.DETAIL_PAGE.value,
                        PageState.SKU_PAGE.value,
                        PageState.SESSION_PICKER.value,
                    }:
                        # These are stable, user-navigable states. Retrying them
                        # cannot reach the configured target by itself when
                        # auto navigation is disabled, and local back recovery
                        # may even leave Damai. Unknown/transient states remain
                        # retryable so connection and refresh recovery still work.
                        self._set_terminal_failure("manual_navigation_required")
                    return False

            if (
                self.config.if_commit_order
                and page_probe["state"]
                in {
                    "detail_page",
                    "sku_page",
                    PageState.SESSION_PICKER.value,
                }
            ):
                self._mark_formal_event_verified()

            if self.config.probe_only:
                if page_probe["state"] == "order_confirm_page":
                    self._set_terminal_failure("probe_read_only_boundary")
                    logger.warning(
                        "probe_only 模式检测到订单确认页；"
                        "为保持只读，不勾选观演人、不点击任何订单控件"
                    )
                    return False
                detail_ready = (
                    page_probe["state"] == "detail_page"
                    and page_probe["purchase_button"]
                    and page_probe["price_container"]
                )
                sku_ready = (
                    page_probe["state"] == "sku_page" and page_probe["price_container"]
                )

                if detail_ready or sku_ready:
                    if (
                        detail_ready
                        and getattr(
                            self.config,
                            "presale_prewarm_enabled",
                            True,
                        )
                    ):
                        readiness = self._capture_presale_readiness(
                            max(
                                0,
                                int(
                                    getattr(
                                        self.config,
                                        "countdown_lead_ms",
                                        0,
                                    )
                                ),
                            ),
                            page_probe_hint=page_probe,
                        )
                        if (
                            readiness.get("device_error")
                            or readiness.get("overlay") is not None
                            or not readiness.get("ready")
                        ):
                            logger.warning(
                                "probe_only 模式: 开售前只读预热未就绪"
                            )
                            return False
                    self._set_run_outcome("probe_ready")
                    logger.info(
                        "probe_only 模式: 详情页关键控件已就绪，停止在购票点击前"
                    )
                    end_time = _monotonic()
                    logger.info(f"探测完成，耗时: {end_time - start_time:.2f}秒")
                    return True

                logger.warning("probe_only 模式: 详情页关键控件未就绪")
                return False

            prepared_detail_page = False
            should_prepare_detail_page = page_probe["state"] == "detail_page" and (
                self.config.sell_start_time is not None
                or self.config.wait_cta_ready_timeout_ms > 0
            )
            if should_prepare_detail_page:
                prepared_detail_page = self._prepare_detail_page_hot_path()
                page_probe = self.probe_current_page()

            if page_probe["state"] != "order_confirm_page":
                # Wait for sale start if configured
                if not self.wait_for_sale_start():
                    logger.warning("开售入口在配置的等待时间内未就绪，停止本轮")
                    return False
                # 极速模式保持已确认的页面与缓存坐标，避免开售后再做一次完整探测。
                if not self.config.rush_mode:
                    page_probe = self.probe_current_page()
                elif (
                    self.config.sell_start_time is None
                    and self.config.wait_cta_ready_timeout_ms > 0
                ):
                    # CTA 等待期间可能刚好从详情页恢复/跳转到了 SKU 页。
                    # 这里只做 Activity 级探测，防止按旧状态重复点击详情页入口。
                    self._invalidate_page_probe_cache()
                    page_probe = self.probe_current_page(fast=True)

            page_probe = self._ensure_target_session(page_probe)
            if page_probe is None:
                return False

            if page_probe["state"] == "order_confirm_page":
                return self._finish_order_confirm_flow(start_time=start_time)

            if page_probe["state"] == "detail_page":
                page_probe = self._enter_purchase_flow_from_detail_page(
                    prepared=prepared_detail_page
                )
                if page_probe is None:
                    return False
                if page_probe.get("state") == "pending_order_dialog":
                    return self._report_pending_order_dialog()
                if page_probe.get("state") == "order_confirm_page":
                    return self._finish_order_confirm_flow(start_time=start_time)
                page_probe = self._ensure_target_session(page_probe)
                if page_probe is None:
                    return False
                if (
                    self.config.if_commit_order
                    and page_probe.get("state") == "sku_page"
                    and not (
                        isinstance(self._formal_run_context, dict)
                        and self._formal_run_context.get("session_verified") is True
                    )
                ):
                    page_probe = self._reverify_formal_session_after_sku_recovery(
                        page_probe
                    )
                    if page_probe is None:
                        return False
                if page_probe.get("state") not in {
                    "sku_page",
                }:
                    logger.warning(
                        "进入购票流程后页面状态异常: %s",
                        page_probe.get("state"),
                    )
                    return False
            else:
                logger.info("当前已在票档选择页，跳过城市和预约按钮步骤")
                # 新版 SKU 页会先展示日期卡片，需在此再次选择场次后才会展开票档列表。
                session_selection_confirmed = page_probe.get(
                    "session_selection_confirmed", False
                )
                if session_selection_confirmed:
                    logger.info("多场次目标已确认，跳过重复的日期/城市点击")
                elif self.config.rush_mode and not self.config.if_commit_order:
                    logger.info("开发验证极速路径：已在票档页，跳过场次切换")
                else:
                    date_ready = self.select_performance_date(
                        timeout=0.35 if self.config.rush_mode else 1.0
                    )
                    city_ready = (
                        self._select_city_from_detail_page(
                            timeout=0.35 if self.config.rush_mode else 1.0
                        )
                        if self.config.city and self.config.if_commit_order
                        else True
                    )
                    joint_session_ready = (
                        self._verify_formal_session_filters_same_snapshot()
                        if self.config.if_commit_order
                        and date_ready
                        and city_ready
                        else not self.config.if_commit_order
                    )
                    if self.config.if_commit_order and not joint_session_ready:
                        self._set_terminal_failure("session_filter_unconfirmed")
                        logger.error(
                            "票档页未能确认目标日期/城市，正式下单模式已停止以避免抢错场次"
                        )
                        return False
                if self.config.rush_mode:
                    # 极速模式下避免一次完整重探测，减少热路径阻塞。
                    page_probe = dict(page_probe)
                    page_probe.setdefault("state", "sku_page")
                    if "reservation_mode" not in page_probe:
                        page_probe["reservation_mode"] = self.is_reservation_sku_mode()
                else:
                    page_probe = self.probe_current_page()

            if page_probe["state"] == "sku_page" and page_probe.get("reservation_mode"):
                logger.warning(
                    "检测到当前页面仍是“预售/抢票预约”流程，继续点击底部按钮只会提交预约，不会进入订单确认页"
                )
                self._set_terminal_failure("reservation_only")
                return False

            if page_probe["state"] == "sku_page":
                overlay = self._probe_hot_blocking_overlay()
                if overlay is not None:
                    overlay_state = overlay.get("state")
                    if overlay_state == "pending_order_dialog":
                        return self._report_pending_order_dialog()
                    if overlay_state in {
                        "manual_verification",
                        "transient_error",
                    }:
                        recovered = self._recover_transient_page_failure(
                            initial_probe=overlay
                        )
                        if recovered and recovered.get("state") == "pending_order_dialog":
                            return self._report_pending_order_dialog()
                        if recovered and recovered.get("state") == "order_confirm_page":
                            return self._finish_order_confirm_flow(
                                start_time=start_time
                            )
                        if (
                            not recovered
                            or recovered.get("state") != "sku_page"
                            or self._terminal_failure_reason
                        ):
                            return False
                        page_probe = dict(recovered)
                        if self.config.if_commit_order:
                            page_probe = (
                                self._reverify_formal_session_after_sku_recovery(
                                    page_probe
                                )
                            )
                            if page_probe is None:
                                return False
                    else:
                        logger.warning("票档操作前存在阻塞弹窗，停止本轮")
                        return False

            self._set_hot_cache_context(
                scope="sku",
                page_state="sku_page",
            )
            # Raw coordinates embedded by a caller are validation-only hints.
            # Formal mode requires coordinates rebuilt from the same XML that
            # proves the configured price text.
            price_coords = (
                page_probe.get("price_coords")
                if self.config.rush_mode and not self.config.if_commit_order
                else None
            )
            buy_button_coords = (
                page_probe.get("buy_button_coords")
                if self.config.rush_mode and not self.config.if_commit_order
                else None
            )
            # 热路径优先从 bot 级缓存读取坐标，避免重复 XML dump（热重试节省 ~0.5s）。
            if self.config.rush_mode and not self.config.if_commit_order:
                if price_coords is None:
                    price_coords = self._get_hot_coord(
                        "price", scope="sku", page_state="sku_page"
                    )
                if buy_button_coords is None:
                    buy_button_coords = self._get_hot_coord(
                        "sku_buy", scope="sku", page_state="sku_page"
                    )
            if (
                page_probe["state"] == "sku_page"
                and (
                    self.config.if_commit_order
                    or self.config.rush_mode
                )
            ):
                if (
                    self.config.if_commit_order
                    or price_coords is None
                    or buy_button_coords is None
                ):
                    # Single hierarchy dump shared by both coordinate captures (~0.5s vs 4s+).
                    if self.config.if_commit_order:
                        price_coords = None
                        buy_button_coords = None
                        self._verified_price_selection = None
                    _sku_xml = self._dump_hierarchy_xml()
                    sku_layout_digest = self._hot_layout_digest_from_xml(
                        _sku_xml, "sku"
                    )
                    self._set_hot_cache_context(
                        scope="sku",
                        page_state="sku_page",
                        layout_digest=sku_layout_digest,
                    )
                    if price_coords is None:
                        price_coords = (
                            self._get_price_option_coordinates_by_config_index(
                                xml_root=_sku_xml
                            )
                        )
                    if buy_button_coords is None:
                        buy_button_coords = self._get_buy_button_coordinates(
                            xml_root=_sku_xml
                        )
                    if self._terminal_failure_reason:
                        return False
            # 更新 bot 级缓存供后续热重试使用。
            if self.config.rush_mode or self.config.if_commit_order:
                sku_layout_digest = getattr(
                    self, "_hot_cache_layout_digests", {}
                ).get("sku", "")
                if price_coords is not None:
                    self._put_hot_coord(
                        "price",
                        price_coords,
                        scope="sku",
                        page_state="sku_page",
                        layout_digest=sku_layout_digest,
                    )
                    if self.config.if_commit_order:
                        proof = self._verified_price_selection
                        if (
                            isinstance(proof, dict)
                            and proof.get("coords") == price_coords
                        ):
                            proof["run_generation"] = self._run_generation
                            proof["stamp"] = self._cached_hot_path_coord_meta.get(
                                "price"
                            )
                if buy_button_coords is not None:
                    self._put_hot_coord(
                        "sku_buy",
                        buy_button_coords,
                        scope="sku",
                        page_state="sku_page",
                        layout_digest=sku_layout_digest,
                    )

            # 3. 票价选择 - 优化查找逻辑
            skip_price_selection = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and self._has_element(By.ID, "layout_num")
            )
            if skip_price_selection:
                logger.info("开发验证极速路径：检测到已处于可调数量状态，跳过票档点击")
            else:
                logger.info("选择票价...")
                try:
                    selected = self._select_price_option(cached_coords=price_coords)
                except SoldOutError as exc:
                    self._save_price_failure_dump(reason=f"sold_out: {exc}")
                    logger.error("票档已售罄，停止本轮抢票: %s", exc)
                    return False
                except PriceSelectorError as exc:
                    self._save_price_failure_dump(reason=str(exc))
                    logger.error("价格选择失败: %s", exc)
                    return False
                if not selected:
                    self._save_price_failure_dump(reason="select returned False")
                    return False
                if self.config.if_commit_order:
                    # Formal selection verification refreshes the SKU layout
                    # stamp and button coordinate from a post-click hierarchy.
                    price_coords = self._get_hot_coord(
                        "price", scope="sku", page_state="sku_page"
                    )
                    buy_button_coords = self._get_hot_coord(
                        "sku_buy", scope="sku", page_state="sku_page"
                    )
                    if price_coords is None:
                        self._set_terminal_failure(
                            "target_price_selection_unconfirmed"
                        )
                        return False

            # 4. 数量选择
            logger.info("选择数量...")
            if self.config.if_commit_order:
                transition_snapshot = (
                    self._ensure_formal_quantity_and_transition_snapshot()
                )
                if not isinstance(transition_snapshot, dict):
                    return False
                buy_button_coords = transition_snapshot["buy_coords"]
            elif len(self.config.users) > 1 and self._has_element(By.ID, "layout_num"):
                clicks_needed = len(self.config.users) - 1
                if clicks_needed > 0:
                    try:
                        plus_button = self._find(By.ID, "img_jia")
                        for i in range(clicks_needed):
                            rect = self._element_rect(plus_button)
                            x = rect["x"] + rect["width"] // 2
                            y = rect["y"] + rect["height"] // 2
                            self._click_coordinates(x, y, duration=50)
                            time.sleep(0.02)
                    except Exception as e:
                        logger.error(f"快速点击加号失败: {e}")

            # if self.driver.find_elements(by=By.ID, value='layout_num') and self.config.users is not None:
            #     for i in range(len(self.config.users) - 1):
            #         self.driver.find_element(by=By.ID, value='img_jia').click()

            # 5. 确定购买。先立即点击，再用短窗口确认；若票档状态尚未落稳，
            # 下一轮只在仍处于 SKU 页时重试，不再固定空等 500ms。
            logger.info("确定购买...")
            submit_ready = False
            confirm_timeout = (
                3.0
                if self.config.rush_mode and self.config.if_commit_order
                else 2.0
                if self.config.rush_mode
                else 1.8
            )
            confirm_deadline = _monotonic() + confirm_timeout
            confirm_attempt = 0
            click_limit_logged = False
            while _monotonic() < confirm_deadline and not submit_ready:
                should_click = confirm_attempt < _CONFIRM_CLICK_LIMIT
                if should_click:
                    confirm_attempt += 1
                elif not click_limit_logged:
                    logger.info(
                        "确定购买已达到 %d 次点击上限，剩余窗口仅观察页面",
                        _CONFIRM_CLICK_LIMIT,
                    )
                    click_limit_logged = True

                if (
                    should_click
                    and self.config.if_commit_order
                    and buy_button_coords
                ):
                    self._burst_click_coordinates(
                        *buy_button_coords,
                        count=1,
                        interval_ms=25,
                        duration=25,
                    )
                elif should_click and self.config.rush_mode and buy_button_coords:
                    if confirm_attempt == 1:
                        self._burst_click_coordinates(
                            *buy_button_coords,
                            count=1,
                            interval_ms=25,
                            duration=25,
                        )
                    else:
                        if not self._click_sku_buy_button_element(
                            burst_count=1
                        ):
                            self._burst_click_coordinates(
                                *buy_button_coords,
                                count=1,
                                interval_ms=25,
                                duration=25,
                            )
                elif should_click and self.config.rush_mode:
                    # Element click may not work on Damai's custom btn_buy_view —
                    # use coordinate click from XML bounds as primary method.
                    _buy_coords = self._get_buy_button_coordinates()
                    if _buy_coords:
                        self._burst_click_coordinates(
                            *_buy_coords, count=1, interval_ms=25, duration=25
                        )
                        buy_button_coords = _buy_coords  # cache for next retry
                    else:
                        if not self._click_sku_buy_button_element(
                            burst_count=1
                        ):
                            try:
                                buy_button = self._find(
                                    By.ID, "cn.damai:id/btn_buy_view"
                                )
                                self._burst_click_element_center(
                                    buy_button,
                                    count=1,
                                    interval_ms=25,
                                    duration=25,
                                )
                            except Exception:
                                self.ultra_fast_click(By.ID, "cn.damai:id/btn_buy_view")
                elif should_click:
                    if not self.ultra_fast_click(By.ID, "cn.damai:id/btn_buy_view"):
                        self.ultra_fast_click(
                            ANDROID_UIAUTOMATOR,
                            'new UiSelector().textMatches(".*确定.*|.*购买.*")',
                        )
                # First response window is intentionally short.  This removes
                # the old fixed 500ms delay and lets an error overlay enter
                # recovery in a few hundred milliseconds.
                remaining = confirm_deadline - _monotonic()
                response_window = (
                    0.22
                    if (
                        should_click
                        and self.config.rush_mode
                        and confirm_attempt == 1
                    )
                    else 0.45
                    if should_click and self.config.rush_mode
                    else 0.8
                    if should_click
                    else 0.2
                )
                check_timeout = min(response_window, max(0.05, remaining))
                submit_ready = self._wait_for_submit_ready(
                    timeout=check_timeout,
                    poll_interval=0.03 if self.config.rush_mode else 0.05,
                )
                blocking_overlay = getattr(
                    self, "_last_blocking_overlay", None
                )
                if not submit_ready and blocking_overlay is not None:
                    blocking_state = blocking_overlay.get("state")
                    if blocking_state == "pending_order_dialog":
                        return self._report_pending_order_dialog()
                    if blocking_state == "manual_verification":
                        self._set_terminal_failure("manual_verification_required")
                        logger.warning("检测到验证码/人机验证，停止确定购买重试")
                        return False
                    if blocking_state == "transient_error":
                        confirm_probe = (
                            self._recover_transient_page_failure(
                                initial_probe=blocking_overlay
                            )
                            or blocking_overlay
                        )
                        confirm_state = confirm_probe.get("state")
                        if confirm_state == "order_confirm_page":
                            submit_ready = True
                        elif confirm_state == "pending_order_dialog":
                            return self._report_pending_order_dialog()
                        elif self._terminal_failure_reason:
                            return False
                        else:
                            logger.warning("刷新恢复后未回到订单确认页，停止重复点击")
                            return False
                    else:
                        logger.warning("确定购买时检测到阻塞弹窗，停止重复点击")
                        return False

                if (
                    not submit_ready
                    and blocking_overlay is None
                    and self.config.rush_mode
                ):
                    self._invalidate_page_probe_cache()
                    confirm_probe = self.probe_current_page()
                    if confirm_probe.get("state") in {
                        "manual_verification",
                        "transient_error",
                        "unknown",
                    }:
                        confirm_probe = (
                            self._recover_transient_page_failure(
                                initial_probe=confirm_probe
                            )
                            or confirm_probe
                        )
                    confirm_state = confirm_probe.get("state")
                    if confirm_state == "order_confirm_page":
                        submit_ready = True
                    elif confirm_state == "pending_order_dialog":
                        return self._report_pending_order_dialog()
                    elif self._terminal_failure_reason:
                        return False
                    elif confirm_state not in {
                        "sku_page",
                        "manual_verification",
                        "unknown",
                        "transient_error",
                    }:
                        logger.warning(
                            "确定购买后页面跳转到非预期状态 %s，停止重复点击",
                            confirm_state,
                        )
                        return False
                if not submit_ready and should_click:
                    logger.debug(f"确定按钮第 {confirm_attempt} 次点击未生效，重试...")
                # Every 3rd attempt, check if buy button shows sold-out text.
                if (
                    not submit_ready
                    and should_click
                    and confirm_attempt % 3 == 0
                ):
                    if self._is_buy_button_sold_out():
                        logger.warning(
                            "购买按钮区域检测到缺货/售罄标识，该票档当前不可购买"
                        )
                        self._set_terminal_failure("sold_out")
                        return False
            if not submit_ready:
                # The one SKU purchase transition may already have reached the
                # device even when the confirmation controls are slow to
                # appear.  Do not click the old bottom coordinate again and do
                # not start attendee operations on an unproven page.
                self._set_terminal_failure("purchase_transition_unconfirmed")
                logger.warning(
                    "确定购买已发送一次但未确认进入订单页；"
                    "停止自动重试，请先人工核实当前页面"
                )
                return False

            return self._finish_order_confirm_flow(start_time=start_time)

        except Exception as e:
            logger.error(f"抢票过程发生错误: {e}")
            if not (
                self.config.rush_mode
                and getattr(self.config, "rush_skip_price_dump", False)
            ):
                try:
                    from .recovery_strategies import capture_failure_artifacts

                    capture_failure_artifacts(self, "run_ticket_grabbing", error=e)
                except Exception:  # pragma: no cover — capture must not crash
                    logger.debug("failure artifact capture failed", exc_info=True)
            else:
                logger.warning("极速模式跳过同步故障抓取，直接交给快速恢复")
            return False

    def run_with_retry(self, max_retries=3, initial_page_probe=None):
        """带重试机制的抢票"""
        self._attempts_made = 0
        recovery = getattr(self, "_recovery", None)
        reset_recovery_budget = getattr(
            recovery, "reset_transient_action_budget", None
        )
        if callable(reset_recovery_budget):
            reset_recovery_budget()
        guarded_result = self._observe_active_submit_intent()
        if guarded_result is not None:
            return guarded_result
        for attempt in range(max_retries):
            self._attempts_made += 1
            logger.info(f"第 {attempt + 1} 次尝试（{self._execution_mode_label()}）...")
            # Pass initial_page_probe only on the first attempt; retries must re-probe.
            probe = initial_page_probe if attempt == 0 else None
            if self.run_ticket_grabbing(initial_page_probe=probe):
                self._log_success_outcome()
                return True

            if self._terminal_failure_reason:
                logger.error(
                    f"检测到不可重试失败，停止后续重试: {self._terminal_failure_reason}"
                )
                break

            # Fast retry within same session
            for fast_attempt in range(
                0
                if self._device_reconnect_required
                else self.config.fast_retry_count
            ):
                self._attempts_made += 1
                logger.info(
                    f"快速重试 {fast_attempt + 1}/{self.config.fast_retry_count}"
                    f"（{self._execution_mode_label()}）..."
                )
                if fast_attempt > 0 and self.config.fast_retry_interval_ms > 0:
                    time.sleep(self.config.fast_retry_interval_ms / 1000)
                if self._fast_retry_from_current_state():
                    self._log_success_outcome("快速重试成功：")
                    return True
                if self._terminal_failure_reason:
                    logger.error(
                        f"快速重试遇到不可重试失败，停止后续重试: {self._terminal_failure_reason}"
                    )
                    break
                if self._device_reconnect_required:
                    logger.warning("检测到设备连接失效，跳过同会话快速重试")
                    break

            if self._terminal_failure_reason:
                break

            # Full driver recreation
            logger.warning(f"第 {attempt + 1} 次尝试及快速重试均失败")
            if attempt < max_retries - 1:
                if (
                    not self.config.auto_navigate
                    and not self._device_reconnect_required
                ):
                    logger.info("手动起跑模式，保留当前会话并继续本地重试")
                    continue
                logger.info(
                    "设备连接恢复后重试..."
                    if self._device_reconnect_required
                    else "重建驱动后重试..."
                )
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self._setup_driver()

        logger.error("所有尝试均失败")
        return False
