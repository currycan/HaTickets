# -*- coding: UTF-8 -*-
"""Purchase-flow helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Hosts the detail→purchase entry path and the fast submit retry loop.
"""

from __future__ import annotations

import logging
import re
import time

from selenium.common.exceptions import TimeoutException

from . import (
    _SALE_READY_TEXT_REGEX_OR,
    logger,
    normalize_text,
)

try:
    from mobile.logger import log_event
except ImportError:  # pragma: no cover
    from logger import log_event  # type: ignore[no-redef]

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:  # pragma: no cover
    from ui_primitives import ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:  # pragma: no cover
    raise

_monotonic = time.monotonic


class PurchaseFlowMixin:
    """Mixin contributing detail→purchase entry and submit logic to ``DamaiBot``."""

    def _guard_formal_order_context(self, *, stage):
        """Fail closed when a money-moving path has lost its run proof."""
        if (
            not self.config.if_commit_order
            or self._formal_order_context_is_verified()
        ):
            return True
        self._set_terminal_failure("target_order_unconfirmed")
        logger.error("%s缺少本轮演出、场次和票档的完整验证链，已停止", stage)
        return False

    def _formal_submit_boundary_snapshot(self):
        """Atomically prove one exact, current order-confirm hierarchy.

        This proof deliberately does not trust the previously selected SKU.
        The confirm page itself must restate the configured event, session,
        unit price, attendee set and ticket quantity in the same fresh XML
        snapshot as the one exact submit resource.
        """
        self._last_formal_submit_boundary = None
        try:
            current = self.d.app_current()
        except Exception as exc:
            logger.warning("提交边界无法读取前台应用: %s", exc)
            return None
        package = current.get("package") if isinstance(current, dict) else None
        activity = current.get("activity") if isinstance(current, dict) else None
        activity_text = activity if isinstance(activity, str) else ""
        if (
            package != self.config.app_package
            or not any(
                marker in activity_text.casefold()
                for marker in ("order", "confirm")
            )
        ):
            logger.error(
                "提交边界前台页面不可信: package=%s activity=%s",
                package,
                activity,
            )
            return None

        root = self._dump_hierarchy_xml()
        if root is None or not hasattr(root, "iter"):
            logger.warning("提交边界无法读取确认页 hierarchy")
            return None

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

        visible_nodes = [
            node for node in root.iter("node") if _is_visible(node)
        ]
        visible_text = " ".join(
            value
            for node in visible_nodes
            for value in (
                (node.get("text") or "").strip(),
                (node.get("content-desc") or "").strip(),
            )
            if value
        )
        if (
            any(marker in visible_text for marker in TRANSIENT_ERROR_MARKERS)
            or any(
                marker in visible_text
                for marker in MANUAL_VERIFICATION_MARKERS
            )
            or any(marker in visible_text for marker in PENDING_ORDER_MARKERS)
            or any(
                node.get("resource-id")
                == "cn.damai:id/id_boot_action_agree"
                for node in visible_nodes
            )
        ):
            logger.warning("提交边界同屏存在阻塞/验证/异常状态")
            return None

        submit_resource_ids = {
            "cn.damai:id/btn_submit",
            "cn.damai:id/submit_btn",
        }
        submit_nodes = []
        for node in visible_nodes:
            resource_id = node.get("resource-id") or ""
            if resource_id not in submit_resource_ids:
                continue
            subtree_values = {
                (descendant.get(attribute) or "").strip()
                for descendant in node.iter("node")
                for attribute in ("text", "content-desc")
            }
            if "立即提交" in subtree_values:
                submit_nodes.append(node)
        if len(submit_nodes) != 1:
            logger.error(
                "提交边界未唯一定位精确“立即提交”资源: matches=%d",
                len(submit_nodes),
            )
            return None

        if "实名观演人" not in visible_text:
            logger.error("提交边界未确认实名观演人区域")
            return None

        field_specs = {
            "target_title": {
                "expected": getattr(self.config, "target_title", None),
                "resources": {
                    "project_name",
                    "project_title",
                    "tv_project_name",
                    "tv_project_title",
                    "item_name",
                    "item_title",
                    "order_project_name",
                    "order_project_title",
                },
                "labels": ("演出", "项目", "项目名称"),
            },
            "target_venue": {
                "expected": getattr(self.config, "target_venue", None),
                "resources": {
                    "venue_name",
                    "tv_venue",
                    "tv_venue_name",
                    "project_venue",
                    "order_venue",
                },
                "labels": ("场馆", "演出场馆"),
                "optional": True,
            },
            "date": {
                "expected": getattr(self.config, "date", None),
                "resources": {
                    "perform_date",
                    "show_date",
                    "tv_date",
                    "order_date",
                    "perform_time",
                    "show_time",
                },
                "labels": ("日期", "演出日期", "场次"),
            },
            "city": {
                "expected": getattr(self.config, "city", None),
                "resources": {
                    "city_name",
                    "tv_city",
                    "tv_city_name",
                    "order_city",
                    "perform_city",
                },
                "labels": ("城市", "演出城市"),
            },
            "price": {
                "expected": getattr(self.config, "price", None),
                "resources": {
                    "ticket_price",
                    "unit_price",
                    "tv_price",
                    "order_ticket_price",
                    "perform_price",
                    "project_price",
                },
                "labels": ("票价", "单价"),
            },
        }

        def _strip_explicit_label(raw_value, labels):
            for label in labels:
                match = re.fullmatch(
                    rf"\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*",
                    raw_value,
                )
                if match:
                    return match.group(1)
            return None

        def _date_matches(expected, observed):
            expected_parts = [
                int(part) for part in re.findall(r"\d+", expected)
            ]
            observed_parts = [
                int(part) for part in re.findall(r"\d+", observed)
            ]
            if len(expected_parts) >= 2:
                width = len(expected_parts)
                return any(
                    observed_parts[index : index + width] == expected_parts
                    for index in range(len(observed_parts) - width + 1)
                )
            return normalize_text(expected) == normalize_text(observed)

        def _field_matches(field_name, expected, observed):
            if field_name == "date":
                return _date_matches(expected, observed)
            if field_name == "city":
                expected_city = normalize_text(expected).removesuffix("市")
                observed_city = normalize_text(observed).removesuffix("市")
                return bool(expected_city and observed_city == expected_city)
            if field_name == "price":
                matcher = getattr(
                    getattr(self, "_price_sel", None),
                    "_price_text_matches_target",
                    None,
                )
                return bool(callable(matcher) and matcher(observed))
            return normalize_text(observed) == normalize_text(expected)

        field_proof = []
        for field_name, spec in field_specs.items():
            expected = spec.get("expected")
            expected = (
                expected.strip()
                if isinstance(expected, str) and expected.strip()
                else None
            )
            if expected is None:
                if spec.get("optional"):
                    field_proof.append((field_name, None, ()))
                    continue
                logger.error("提交边界配置缺少必需字段: %s", field_name)
                return None

            candidates = []
            for node in visible_nodes:
                resource_name = (
                    (node.get("resource-id") or "")
                    .rsplit("/", 1)[-1]
                    .casefold()
                )
                semantic_resource = resource_name in spec["resources"]
                for attribute in ("text", "content-desc"):
                    raw_value = (node.get(attribute) or "").strip()
                    if not raw_value:
                        continue
                    labelled_value = _strip_explicit_label(
                        raw_value, spec["labels"]
                    )
                    if not semantic_resource and labelled_value is None:
                        continue
                    candidates.append(
                        labelled_value
                        if labelled_value is not None
                        else raw_value
                    )
            if not candidates or any(
                not _field_matches(field_name, expected, candidate)
                for candidate in candidates
            ):
                logger.error(
                    "提交边界订单摘要字段不匹配: field=%s expected=%s observed=%s",
                    field_name,
                    expected,
                    candidates,
                )
                return None
            field_proof.append(
                (
                    field_name,
                    normalize_text(expected),
                    tuple(
                        sorted(
                            {
                                normalize_text(candidate)
                                for candidate in candidates
                            }
                        )
                    ),
                )
            )

        users = [
            value.strip()
            for value in (self.config.users or [])
            if isinstance(value, str) and value.strip()
        ]
        if not users or len(users) != len(set(users)):
            logger.error("提交边界观演人配置为空或重名")
            return None

        quantity_values = set()
        for node in visible_nodes:
            resource_name = (
                (node.get("resource-id") or "").rsplit("/", 1)[-1].casefold()
            )
            semantic_quantity = resource_name in {
                "quantity_summary",
                "ticket_count",
                "ticket_num",
                "buy_num",
                "tv_count",
                "tv_num",
                "order_quantity",
            }
            for attribute in ("text", "content-desc"):
                raw_value = (node.get(attribute) or "").strip()
                if not raw_value:
                    continue
                match = re.fullmatch(
                    r"\s*(?:票数|数量|共)\s*[:：]?\s*(\d{1,2})"
                    r"\s*(?:张|票)?\s*",
                    raw_value,
                )
                if match is None and semantic_quantity:
                    match = re.fullmatch(
                        r"\s*(\d{1,2})\s*(?:张|票)?\s*",
                        raw_value,
                    )
                if match is not None:
                    quantity_values.add(int(match.group(1)))
        target_quantity = len(users)
        if quantity_values != {target_quantity}:
            logger.error(
                "提交边界票数摘要不匹配: expected=%d observed=%s",
                target_quantity,
                sorted(quantity_values),
            )
            return None

        row_records = []
        for name_node in root.iter("node"):
            if name_node.get("resource-id") != "cn.damai:id/text_name":
                continue
            name = (name_node.get("text") or "").strip()
            if not name:
                continue
            cursor = parent_map.get(name_node)
            checkboxes = []
            while cursor is not None:
                checkboxes = [
                    node
                    for node in cursor.iter("node")
                    if node.get("resource-id") == "cn.damai:id/checkbox"
                ]
                if checkboxes:
                    break
                cursor = parent_map.get(cursor)
            if len(checkboxes) != 1:
                logger.error(
                    "提交边界观演人行无法唯一映射: name=%s checkboxes=%d",
                    name,
                    len(checkboxes),
                )
                return None
            checkbox = checkboxes[0]
            row_records.append(
                {
                    "name": name,
                    "selected": (
                        checkbox.get("checked") == "true"
                        or checkbox.get("selected") == "true"
                    ),
                    "visible": (
                        _is_visible(name_node) and _is_visible(checkbox)
                    ),
                }
            )

        selected_rows = [
            record for record in row_records if record["selected"]
        ]
        selected_names = {
            record["name"] for record in selected_rows
        }
        configured_names = set(users)
        visible_configured_rows = {
            name: [
                record
                for record in row_records
                if record["name"] == name and record["visible"]
            ]
            for name in users
        }
        if any(
            len(records) != 1 or records[0]["selected"] is not True
            for records in visible_configured_rows.values()
        ) or (
            selected_names != configured_names
            or len(selected_rows) != target_quantity
        ):
            logger.error(
                "提交边界观演人选中集合不匹配: configured=%s selected=%s "
                "selected_rows=%d quantity=%d",
                sorted(configured_names),
                sorted(selected_names),
                len(selected_rows),
                target_quantity,
            )
            return None

        boundary_resource_names = {
            resource_name
            for spec in field_specs.values()
            for resource_name in spec["resources"]
        } | {
            "quantity_summary",
            "ticket_count",
            "ticket_num",
            "buy_num",
            "tv_count",
            "tv_num",
            "order_quantity",
            "text_name",
            "checkbox",
            "btn_submit",
            "submit_btn",
        }
        hierarchy_token = tuple(
            (
                node.get("resource-id") or "",
                node.get("class") or "",
                node.get("text") or "",
                node.get("content-desc") or "",
                node.get("bounds") or "",
                node.get("visible-to-user") or "",
                node.get("checked") or "",
                node.get("selected") or "",
                node.get("enabled") or "",
                node.get("clickable") or "",
            )
            for node in root.iter("node")
            if (
                (node.get("resource-id") or "").rsplit("/", 1)[-1].casefold()
                in boundary_resource_names
                or (node.get("text") or "").strip()
                in {"实名观演人", "立即提交"}
                or (node.get("content-desc") or "").strip()
                in {"实名观演人", "立即提交"}
            )
        )
        layout_digest = self._hot_layout_digest_from_xml(root, "confirm")
        proof = {
            "resource_id": submit_nodes[0].get("resource-id"),
            "package": package,
            "activity": activity_text,
            "layout_digest": layout_digest,
            "token": hierarchy_token,
            "fields": tuple(field_proof),
            "selected_names": tuple(sorted(selected_names)),
            "selected_row_count": len(selected_rows),
            "quantity": target_quantity,
            "observed_at": _monotonic(),
            "run_generation": self._run_generation,
        }
        self._last_formal_submit_boundary = proof
        return proof

    def _wait_for_purchase_entry_with_recovery(
        self,
        *,
        soft_timeout=0.35,
        hard_timeout=2.0,
        poll_interval=0.03,
    ):
        """Wait for detail→SKU transition and recover transient errors early."""
        next_probe = self._wait_for_purchase_entry_result(
            timeout=soft_timeout,
            poll_interval=poll_interval,
            fallback_probe_on_timeout=True,
        )
        terminal_transition_states = {
            "sku_page",
            "order_confirm_page",
            "pending_order_dialog",
        }
        if next_probe and next_probe.get("state") in terminal_transition_states:
            return next_probe

        if next_probe and next_probe.get("state") in {
            "manual_verification",
            "transient_error",
            "unknown",
        }:
            recovered = self._recover_transient_page_failure(
                initial_probe=next_probe
            )
            if recovered and recovered.get("state") in {
                "sku_page",
                "order_confirm_page",
                "pending_order_dialog",
                "manual_verification",
            }:
                return recovered
            # A same-page retry can restore detail_page immediately.  Return it
            # so the caller can re-use its cached CTA coordinate without a
            # cold navigation or a six-second timeout.
            if recovered and recovered.get("state") == "detail_page":
                return recovered
            if (
                getattr(self, "_terminal_failure_reason", None)
                or getattr(self, "_device_reconnect_required", False)
                or (
                    recovered
                    and (
                        recovered.get("manual_verification")
                        or recovered.get("device_error")
                    )
                )
            ):
                return recovered or next_probe

        remaining = max(0.0, hard_timeout - soft_timeout)
        if remaining <= 0:
            return next_probe
        final_probe = self._wait_for_purchase_entry_result(
            timeout=remaining,
            poll_interval=poll_interval,
            fallback_probe_on_timeout=True,
        )
        if final_probe and final_probe.get("state") in {
            "manual_verification",
            "transient_error",
            "unknown",
        }:
            return (
                self._recover_transient_page_failure(
                    initial_probe=final_probe
                )
                or final_probe
            )
        return final_probe

    def _formal_detail_purchase_snapshot(self):
        """Prove one exact detail-page CTA from a fresh, joint snapshot.

        Formal runs must never reuse a pre-filter coordinate or a broad
        purchase-text selector here.  Selecting a date/city can redraw the
        bottom bar, so the final CTA coordinate is derived only after the same
        hierarchy has re-proved the event and both session filters.
        """
        try:
            current = self.d.app_current()
        except Exception as exc:
            logger.warning("正式购票入口无法读取前台应用: %s", exc)
            return None
        package = current.get("package") if isinstance(current, dict) else None
        activity = current.get("activity") if isinstance(current, dict) else None
        activity_text = activity if isinstance(activity, str) else ""
        if (
            package != self.config.app_package
            or not any(
                marker in activity_text.casefold()
                for marker in ("project", "detail")
            )
        ):
            logger.error(
                "正式购票入口前台页面不可信: package=%s activity=%s",
                package,
                activity,
            )
            return None

        root = self._dump_hierarchy_xml()
        if root is None or not hasattr(root, "iter"):
            logger.warning("正式购票入口无法读取详情页 hierarchy")
            return None

        observed_title = self._get_detail_title_text(xml_root=root)
        observed_venue = self._get_detail_venue_text(xml_root=root)
        if not self._current_page_matches_target(
            {
                "state": "detail_page",
                "observed_title": observed_title,
                "observed_venue": observed_venue,
            }
        ):
            self._set_terminal_failure("target_event_unconfirmed")
            logger.error("正式购票入口同一快照未能证明精确目标演出")
            return None
        self._mark_formal_event_verified()

        if not self._verify_formal_session_filters_same_snapshot(root):
            self._set_terminal_failure("session_filter_unconfirmed")
            logger.error("正式购票入口同一快照未能证明目标日期和城市")
            return None

        resource_ids = {
            "trade_project_detail_purchase_status_bar_container_fl",
            "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
        }
        candidates = []
        for node in root.iter("node"):
            if (
                node.get("visible-to-user") == "false"
                or node.get("resource-id") not in resource_ids
            ):
                continue
            bounds = self._parse_bounds(node.get("bounds", ""))
            if bounds:
                candidates.append((node, bounds))
        if len(candidates) != 1:
            logger.error(
                "正式购票入口未唯一定位精确 CTA 资源: matches=%d",
                len(candidates),
            )
            return None

        _, (left, top, right, bottom) = candidates[0]
        return {
            "coords": ((left + right) // 2, (top + bottom) // 2),
            "layout_digest": self._hot_layout_digest_from_xml(root, "detail"),
            "activity": activity_text,
            "observed_at": _monotonic(),
            "run_generation": self._run_generation,
        }

    def _click_formal_detail_purchase_entry(self):
        """Dispatch exactly one CTA click after a fresh formal proof."""
        snapshot = self._formal_detail_purchase_snapshot()
        if not isinstance(snapshot, dict):
            if not getattr(self, "_terminal_failure_reason", None):
                self._set_terminal_failure("purchase_entry_unconfirmed")
            return False
        try:
            self._click_coordinates(*snapshot["coords"], duration=25)
        except Exception as exc:
            logger.warning("正式购票入口点击结果不确定: %s", exc)
            self._set_terminal_failure("purchase_entry_dispatch_unknown")
            return False
        self._invalidate_page_probe_cache()
        return True

    def _enter_purchase_flow_from_detail_page(self, prepared=False):
        """Open the purchase panel from the detail page with a low-latency hot path."""
        if getattr(
            self, "_scheduled_recovery_invalidated_preparation", False
        ):
            prepared = False
            self._scheduled_recovery_invalidated_preparation = False
        scheduled_gate_clean = bool(
            self.config.rush_mode
            and getattr(self, "_scheduled_entry_gate_clean", False)
        )
        self._scheduled_entry_gate_clean = False
        if not scheduled_gate_clean:
            overlay = self._probe_hot_blocking_overlay()
            if overlay is not None:
                overlay_state = overlay.get("state")
                if overlay_state == "pending_order_dialog":
                    return overlay
                if overlay_state in {"manual_verification", "transient_error"}:
                    recovered = self._recover_transient_page_failure(
                        initial_probe=overlay
                    )
                    if recovered is not None:
                        return recovered
                return overlay
        if not prepared:
            if self.config.rush_mode and not self.config.if_commit_order:
                # 极速模式冷路径：单次 XML dump 提取所有坐标（~0.3s），替代多次 _cached_tap（~3-4s）。
                # 热路径（有缓存）用 _cached_tap 直接点击缓存坐标（1次 HTTP/元素）。
                if self._using_u2() and not self._get_hot_coord("detail_buy"):
                    # Cold path: single XML dump for all detail page elements.
                    if self._rush_preselect_and_buy_via_xml(
                        dialogs_checked=True
                    ):
                        next_probe = self._wait_for_purchase_entry_with_recovery(
                            soft_timeout=0.35,
                            hard_timeout=2.0,
                            poll_interval=0.03,
                        )
                        if next_probe and next_probe.get("state") in {
                            "sku_page",
                            "order_confirm_page",
                            "pending_order_dialog",
                            "manual_verification",
                        }:
                            return next_probe
                    preselect_failure = getattr(
                        getattr(self, "_pipeline", None),
                        "_last_preselect_failure",
                        None,
                    )
                    if preselect_failure == "transient_error":
                        recovered = self._recover_transient_page_failure()
                        if recovered is not None:
                            return recovered
                        return None
                    if preselect_failure == "device_error":
                        self._device_reconnect_required = True
                        logger.warning(
                            "极速预选无法读取页面结构，停止点击并交给设备重连"
                        )
                        return None
                    if self._terminal_failure_reason:
                        return None
                else:
                    # Warm path: cached coords for date/city/buy.
                    if (
                        self.config.date
                        and not self._has_hot_no_match("date")
                    ):
                        _date_found = self._cached_tap(
                            "date",
                            ANDROID_UIAUTOMATOR,
                            f'new UiSelector().textContains("{self.config.date}")',
                            timeout=0.1,
                        )
                        if _date_found:
                            logger.info(f"极速模式预选日期: {self.config.date}")
                        elif self._get_hot_coord("date") is None:
                            self._put_hot_no_match("date")
                            if self.config.if_commit_order:
                                self._set_terminal_failure(
                                    "session_filter_unconfirmed"
                                )
                                logger.error(
                                    "未确认目标日期，正式下单模式已停止以避免抢错场次"
                                )
                                return None
                    if (
                        self.config.city
                        and not self._has_hot_no_match("city")
                    ):
                        _city_found = self._cached_tap(
                            "city",
                            ANDROID_UIAUTOMATOR,
                            f'new UiSelector().text("{self.config.city}")',
                            timeout=0.2,
                        )
                        if not _city_found:
                            _city_found = self._cached_tap(
                                "city",
                                ANDROID_UIAUTOMATOR,
                                f'new UiSelector().textContains("{self.config.city}")',
                                timeout=0.15,
                            )
                        if _city_found:
                            logger.info(f"极速模式预选城市: {self.config.city}")
                        elif self._get_hot_coord("city") is None:
                            self._put_hot_no_match("city")
                            if self.config.if_commit_order:
                                self._set_terminal_failure(
                                    "session_filter_unconfirmed"
                                )
                                logger.error(
                                    "未确认目标城市，正式下单模式已停止以避免抢错场次"
                                )
                                return None
                            logger.debug("开发验证未命中城市选择，继续检查购票入口")
            else:
                date_ready = self.select_performance_date()
                if self.config.if_commit_order and not date_ready:
                    self._set_terminal_failure("session_filter_unconfirmed")
                    logger.error(
                        "未确认目标日期，正式下单模式已停止以避免抢错场次"
                    )
                    return None
                logger.info("选择城市...")
                if not self._select_city_from_detail_page(timeout=1.0):
                    logger.warning("城市选择失败")
                    return None

        if not self._get_hot_coord("detail_buy"):
            logger.info("点击购票按钮...")
        if self.config.if_commit_order:
            if not self._click_formal_detail_purchase_entry():
                return None
            next_probe = self._wait_for_purchase_entry_with_recovery(
                soft_timeout=0.35 if self.config.rush_mode else 0.8,
                hard_timeout=2.0 if self.config.rush_mode else 5.0,
                poll_interval=0.03 if self.config.rush_mode else 0.08,
            )
            if next_probe and next_probe.get("state") in {
                "sku_page",
                "order_confirm_page",
                "pending_order_dialog",
                "manual_verification",
            }:
                return next_probe
            return None
        if self.config.rush_mode:
            # 极速模式：_cached_tap 冷路径查找并缓存购票按钮坐标，热路径直接点击（1次HTTP）。
            # 点击一次后等足够长时间，避免重复点击重置 sku_page 加载。
            _buy_clicked = self._cached_tap(
                "detail_buy",
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                timeout=0.2,
            )
            if not _buy_clicked:
                # 文案集合源 SALE_READY_TEXTS（issue #29）+ 旧文案兜底
                _buy_clicked = self._cached_tap(
                    "detail_buy",
                    ANDROID_UIAUTOMATOR,
                    f'new UiSelector().textMatches("{_SALE_READY_TEXT_REGEX_OR}|.*购票.*|.*抢票.*|.*购买.*")',
                    timeout=0.25,
                )
            if _buy_clicked:
                next_probe = self._wait_for_purchase_entry_with_recovery(
                    soft_timeout=0.35,
                    hard_timeout=2.0,
                    poll_interval=0.03,
                )
                if next_probe and next_probe.get("state") in {
                    "sku_page",
                    "order_confirm_page",
                    "pending_order_dialog",
                    "manual_verification",
                }:
                    return next_probe

        # 文案集合源 SALE_READY_TEXTS（issue #29）+ 旧"预约/购买"兜底
        book_selectors = [
            (
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ),
            (
                ANDROID_UIAUTOMATOR,
                f'new UiSelector().textMatches("{_SALE_READY_TEXT_REGEX_OR}|.*预约.*|.*购买.*")',
            ),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
        ]
        if not self.smart_wait_and_click(
            *book_selectors[0], book_selectors[1:], timeout=0.8
        ):
            logger.warning("购票按钮点击失败")
            return None
        next_probe = self._wait_for_purchase_entry_with_recovery(
            soft_timeout=0.8,
            hard_timeout=2.0 if self.config.rush_mode else 5.0,
            poll_interval=0.03 if self.config.rush_mode else 0.08,
        )
        if next_probe and next_probe.get("state") in {
            "sku_page",
            "order_confirm_page",
            "pending_order_dialog",
            "manual_verification",
        }:
            return next_probe
        return None

    def _finish_order_confirm_flow(self, *, start_time):
        """Validate attendees and optionally submit from an already-open confirm page."""
        if not self._guard_formal_order_context(stage="订单确认页"):
            return False

        overlay = self._probe_hot_blocking_overlay()
        if overlay is not None:
            overlay_state = overlay.get("state")
            if overlay_state == "pending_order_dialog":
                return self._report_pending_order_dialog()
            if overlay_state == "manual_verification":
                self._set_terminal_failure("manual_verification_required")
                logger.warning("观演人操作前检测到验证码/人机验证，已停止")
                return False
            if overlay_state == "transient_error":
                recovered = self._recover_transient_page_failure(
                    initial_probe=overlay
                )
                if recovered and recovered.get("state") == "pending_order_dialog":
                    return self._report_pending_order_dialog()
                if (
                    not recovered
                    or recovered.get("state") != "order_confirm_page"
                    or self._terminal_failure_reason
                ):
                    logger.warning("订单确认页刷新恢复失败，停止观演人操作")
                    return False
                if not self._guard_formal_order_context(
                    stage="订单确认页刷新恢复后"
                ):
                    return False
            else:
                logger.warning("订单确认页存在阻塞弹窗，停止观演人操作")
                return False

        submit_selectors = [
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textMatches(".*提交.*|.*确认.*")',
            ),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]
        if not self._guard_formal_order_context(stage="观演人操作前"):
            return False
        if not self._ensure_attendees_selected_on_confirm_page(
            require_attendee_section=bool(
                self.config.if_commit_order
                or (
                    self.config.rush_mode
                    and not self.config.if_commit_order
                )
            )
        ):
            self._set_terminal_failure("attendee_unselected")
            logger.error("订单提交前观演人未选择完整，已停止自动提交")
            return False

        if not self.config.if_commit_order:
            self._set_run_outcome("validation_ready")
            elapsed = _monotonic() - start_time
            logger.info("if_commit_order=False，已完成观演人勾选，停止在“立即提交”前")
            logger.info(
                f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {elapsed:.2f}秒"
            )
            return True

        logger.info("提交订单...")
        result = self._submit_order_fast(submit_selectors)
        return self._finalize_submit_result(result, start_time=start_time)

    def _submit_order_fast(self, submit_selectors):
        """Submit once, then verify without issuing a duplicate order click.

        UI automation can confirm that a tap was sent, but it cannot prove the
        server rejected it.  Re-clicking while the result page is slow risks a
        duplicate order, so selector fallbacks are allowed only until the first
        successful click.  After that, verification is read-only and fail-closed.
        """
        from .submit_journal import ActiveSubmitIntentError, SubmitJournalError

        if not self._guard_formal_order_context(stage="提交边界"):
            return "target_unconfirmed"

        t0 = time.monotonic()
        overlay = self._probe_hot_blocking_overlay()
        if overlay is not None:
            overlay_state = overlay.get("state")
            if overlay.get("device_error"):
                logger.warning("提交前设备连接异常，不再点击提交")
                return "device_error"
            if overlay_state == "pending_order_dialog":
                logger.warning(
                    "提交前检测到未支付订单弹窗；无法归属本次运行，不再点击提交"
                )
                return "preexisting_order"
            if overlay_state == "manual_verification":
                logger.warning("提交前检测到验证码/人机验证，不再点击提交")
                return "captcha"
            if overlay_state == "transient_error":
                recovered = self._recover_transient_page_failure(
                    initial_probe=overlay
                )
                if recovered and recovered.get("device_error"):
                    return "device_error"
                if getattr(self, "_terminal_failure_reason", None):
                    return "captcha"
                if not recovered or recovered.get("state") != "order_confirm_page":
                    logger.warning("提交前页面刷新恢复未回到确认页，不再点击提交")
                    return "timeout"
                if not self._guard_formal_order_context(
                    stage="提交前页面刷新恢复后"
                ):
                    return "target_unconfirmed"
                overlay = self._probe_hot_blocking_overlay()
                if overlay is not None:
                    if overlay.get("device_error"):
                        return "device_error"
                    logger.warning("提交前仍有阻塞遮罩，不再点击提交")
                    return "timeout"
            elif overlay_state == "consent_dialog":
                logger.warning("提交前仍有阻塞弹窗，不再点击提交")
                return "timeout"

        if not self._guard_formal_order_context(stage="提交前页面恢复后"):
            return "target_unconfirmed"

        page_probe = getattr(self, "_page_probe", None)
        probe_consent = getattr(page_probe, "probe_consent_dialog", None)
        if callable(probe_consent):
            consent_visible = probe_consent()
            if consent_visible is None:
                logger.warning("提交前无法确认授权弹窗状态，不再点击提交")
                return "device_error"
            if consent_visible:
                logger.warning("提交前检测到授权弹窗，不再点击提交")
                return "timeout"

        # Crash/restart guard (also protects direct callers and confirm-page
        # fast retry paths that bypass run_with_retry's startup check).
        try:
            active = self._submit_journal.load_active()
        except SubmitJournalError as exc:
            logger.error("提交前无法安全读取 submit journal，未点击提交: %s", exc)
            return "journal_error"
        if active is not None:
            self._remember_submit_intent(active)
            logger.error("设备已有未确认 submit intent，禁止再次点击提交")
            return "intent_pending"

        # One fresh hierarchy must prove the exact foreground confirm page,
        # exact selected attendee set, and an exact submit resource together.
        # Broad "确认/提交" selector fallbacks are deliberately forbidden here.
        boundary = self._formal_submit_boundary_snapshot()
        if not isinstance(boundary, dict):
            self._set_terminal_failure("submit_boundary_unconfirmed")
            logger.warning("提交边界无法完整确认，未查找或点击任何提交控件")
            return "target_unconfirmed"

        try:
            submit_element = self._wait_for_element(
                By.ID,
                boundary["resource_id"],
                timeout=0.15,
            )
        except TimeoutException:
            logger.warning("精确提交控件在边界确认后消失，未点击")
            log_event(
                logger,
                "order_submitted",
                level=logging.WARNING,
                success=False,
                result="button_not_found",
                attempts=0,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            return "target_unconfirmed"

        boundary_keys = (
            "resource_id",
            "package",
            "activity",
            "layout_digest",
            "token",
            "fields",
            "selected_names",
            "selected_row_count",
            "quantity",
            "run_generation",
        )

        def _same_boundary(candidate):
            return isinstance(candidate, dict) and all(
                candidate.get(key) == boundary.get(key)
                for key in boundary_keys
            )

        # The selector lookup is a device RPC and therefore a race boundary.
        # Re-read the entire page before arming and require the exact same
        # hierarchy token, layout and order-summary fields.
        pre_arm_boundary = self._formal_submit_boundary_snapshot()
        if not _same_boundary(pre_arm_boundary):
            self._set_terminal_failure("submit_boundary_changed_before_arm")
            logger.error(
                "精确提交控件定位后页面边界发生变化，未 arm、未点击"
            )
            return "target_unconfirmed"

        if not self._guard_formal_order_context(stage="提交按钮点击前"):
            return "target_unconfirmed"

        # Money-moving boundary: durable ARMED must be fsynced before the
        # selected element receives exactly one click.
        try:
            intent = self._submit_journal.arm(config=self.config)
            self._remember_submit_intent(intent)
        except ActiveSubmitIntentError as exc:
            logger.error("提交前检测到并发/历史 submit intent: %s", exc)
            return "intent_pending"
        except SubmitJournalError as exc:
            logger.error("无法持久化 submit intent，未点击提交: %s", exc)
            return "journal_error"

        # A durable ARMED record now exists.  Take one final fresh snapshot:
        # if anything changed, retain ARMED so later runs remain blocked, but
        # never dispatch the click into the changed page.
        post_arm_boundary = self._formal_submit_boundary_snapshot()
        if not _same_boundary(post_arm_boundary):
            self._set_terminal_failure("submit_boundary_changed_after_arm")
            logger.error(
                "submit intent 已 ARMED，但页面边界变化；保留 ARMED 且未点击"
            )
            return "intent_pending"

        try:
            exact_click = getattr(submit_element, "click", None)
            if not callable(exact_click):
                raise RuntimeError("exact_submit_element_has_no_click")
            exact_click()
        except KeyboardInterrupt:
            # ARMED is deliberately retained: interruption may have arrived
            # after the device accepted the RPC.
            raise
        except Exception as exc:
            logger.error("提交点击 RPC 结果不明，保留 active intent: %s", exc)
            try:
                intent = self._submit_journal.record_observation(
                    intent["intent_id"], "timeout"
                )
                self._remember_submit_intent(intent)
            except SubmitJournalError:
                pass
            return "timeout"

        try:
            intent = self._submit_journal.mark_click_dispatched(intent["intent_id"])
            self._remember_submit_intent(intent)
            if not self._remember_submit_click_dispatch(
                intent,
                post_arm_boundary,
            ):
                logger.error(
                    "点击已发出但无法冻结本地提交边界；结果将保持未知并锁定"
                )
        except SubmitJournalError as exc:
            # ARMED already exists durably and therefore still blocks restarts.
            logger.error(
                "点击已发出但 journal 状态更新失败；ARMED 仍保持安全阻塞: %s",
                exc,
            )

        try:
            result = self.verify_order_result(timeout=4)
        except Exception as exc:
            logger.warning("提交后只读验证异常，按结果未知处理: %s", exc)
            result = "timeout"
        result = self._sanitize_submit_observation_attribution(result)

        try:
            intent = self._submit_journal.record_observation(
                intent["intent_id"], result
            )
            self._remember_submit_intent(intent)
        except SubmitJournalError as exc:
            logger.error("提交结果无法写入 journal；active 记录仍保留: %s", exc)

        if result == "timeout":
            logger.warning(
                "提交点击已发出但结果暂未确认；为避免重复下单，不会再次点击提交"
            )

        log_event(
            logger,
            "order_submitted",
            level=(
                logging.INFO
                if result in {"success", "existing_order"}
                else logging.WARNING
            ),
            success=result in {"success", "existing_order"},
            result=result,
            attempts=1,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        return result
