# -*- coding: UTF-8 -*-
"""
PageProbe — fast page-state detection for Damai mobile automation.

Provides two probe modes:
- **Fast probe** (~100ms): checks only the Android Activity name.
- **Full probe** (~1.5s): checks element resource IDs for comprehensive state.

Results are cached with a configurable TTL to avoid redundant device queries.
"""

import os
import re
import time
import xml.etree.ElementTree as ET
from enum import Enum
from typing import Any, Dict, Optional, Set, Union

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

# A full detail snapshot may remove duplicate read-only prewarm RPCs only
# while it is effectively the same observation.  Keep this hard cap below the
# normal PageProbe cache TTL and never extend it at a call site.
_DETAIL_SNAPSHOT_REUSE_MAX_AGE_S = 0.250


# ---------------------------------------------------------------------------
# Page state enum (P1 #25, Step 1)
# ---------------------------------------------------------------------------
# Inherits ``str`` so existing string-based comparisons (``state == "sku_page"``)
# keep working without churn while new code can use ``PageState.SESSION_PICKER``.
class PageState(str, Enum):
    HOMEPAGE = "homepage"
    SEARCH_PAGE = "search_page"
    DETAIL_PAGE = "detail_page"
    SKU_PAGE = "sku_page"
    SESSION_PICKER = "session_picker"
    ORDER_CONFIRM_PAGE = "order_confirm_page"
    CONSENT_DIALOG = "consent_dialog"
    PENDING_ORDER_DIALOG = "pending_order_dialog"
    MANUAL_VERIFICATION = "manual_verification"
    TRANSIENT_ERROR = "transient_error"
    UNKNOWN = "unknown"


# Markers used to recognise the multi-session date picker on the SKU page.
_SESSION_PICKER_RESOURCE_IDS = ("cn.damai:id/sku_panel_dates",)
_SESSION_PICKER_TEXTS = (
    "请选择场次",
    "选择日期",
    "请选择日期",
    "请选择演出日期",
)
_SESSION_PICKER_TEXT_PATTERN = r".*(请选择场次|选择日期|请选择日期|请选择演出日期).*"


# ---------------------------------------------------------------------------
# Price panel state detection (P1 #31, Step 3)
# ---------------------------------------------------------------------------

# Resource IDs / text patterns checked when classifying the price panel state.
_PRICE_LOADING_INDICATORS = (
    "cn.damai:id/loading_progress",
    "cn.damai:id/sku_loading",
    "cn.damai:id/loading_view",
)
_PRICE_READY_CONTAINERS = (
    "cn.damai:id/project_detail_perform_price_flowlayout",
    "cn.damai:id/layout_price",
)
_PRICE_SOLD_OUT_TEXTS = (
    "已售罄",
    "全部售罄",
    "无票",
    "暂无可售票档",
    "缺货登记",
)


def _selector_exists(selector: Any) -> bool:
    """Read uiautomator2 ``exists`` across property and callable variants."""
    exists = getattr(selector, "exists", False)
    if callable(exists):
        try:
            return bool(exists(timeout=0))
        except TypeError:
            return bool(exists())
    return bool(exists)


def _safe_exists(driver: Any, **selector: Any) -> bool:
    """Return selector existence while swallowing a device/backend error."""
    try:
        return _selector_exists(driver(**selector))
    except Exception:
        return False


def detect_price_panel_state(driver: Any) -> str:
    """Classify the visible price panel state.

    Returns one of: ``"loading"``, ``"ready"``, ``"sold_out"``, ``"unknown"``.

    The order is intentional:
      1. ``loading`` — a loading spinner is present, caller should wait briefly.
      2. ``sold_out`` — visible text matches a sold-out marker.
      3. ``ready`` — a known price container resource-id exists.
      4. ``unknown`` — none of the above; caller should fall back gracefully.
    """
    # 1. loading spinner takes priority — the panel may render later
    for resource_id in _PRICE_LOADING_INDICATORS:
        if _safe_exists(driver, resourceId=resource_id):
            return "loading"

    # 2. sold-out markers (text/textContains) — never try to click empty cards
    for text in _PRICE_SOLD_OUT_TEXTS:
        if _safe_exists(driver, text=text) or _safe_exists(driver, textContains=text):
            return "sold_out"

    # 3. ready when a known price container is present
    for container_id in _PRICE_READY_CONTAINERS:
        if _safe_exists(driver, resourceId=container_id):
            return "ready"

    return "unknown"


# Activity substrings → state mapping (used by fast probe)
_ACTIVITY_STATE_MAP = (
    ("ProjectDetail", "detail_page"),
    ("NcovSku", "sku_page"),
    ("MainActivity", "homepage"),
    ("SearchActivity", "search_page"),
)

# 详情页价格区容器候选（多版本兼容）：大麦 9.0.2x 详情页已移除
# project_detail_price_layout，价格区改为 info_v2_price_layout（2026-07-11
# 真机 dump 证实）。新 ID 在前，现网主流版本一次命中少一次元素查询。
_DETAIL_PRICE_LAYOUT_IDS = (
    "cn.damai:id/info_v2_price_layout",
    "cn.damai:id/project_detail_price_layout",
)
_DETAIL_CTA_RESOURCE_IDS = (
    "cn.damai:id/btn_buy_view",
    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
)
_DETAIL_TITLE_RESOURCE_IDS = (
    "cn.damai:id/title_tv",
    "cn.damai:id/project_title_tv1",
    "cn.damai:id/project_title_tv2",
)
_DETAIL_VENUE_RESOURCE_IDS = (
    "cn.damai:id/venue_name_0",
    "cn.damai:id/tv_project_venueName",
)
_STARTUP_POPUP_RESOURCE_IDS = frozenset(
    {
        "android:id/ok",
        "cn.damai:id/id_boot_action_agree",
        "cn.damai:id/damai_theme_dialog_cancel_btn",
        "cn.damai:id/damai_theme_dialog_close_layout",
        "cn.damai:id/damai_theme_dialog_close_btn",
    }
)
_STARTUP_POPUP_TEXTS = frozenset(
    {"Cancel", "下次再说", "我知道了", "知道了"}
)
_LOGIN_PROMPT_MARKERS = ("请先登录", "登录/注册")
_RESERVATION_RESOURCE_IDS = frozenset(
    {"cn.damai:id/btn_cancel_reservation"}
)
_RESERVATION_TEXT_MARKERS = (
    "预约想看场次",
    "预约想看票档",
    "提交抢票预约",
    "已预约",
)
_RETRY_ACTION_TEXTS = frozenset(
    {"重试", "重新加载", "重新刷新", "刷新", "点击重试"}
)
_RETRY_ACTION_PHRASES = (
    "重新加载",
    "重新刷新",
    "点击重试",
    "点击重新加载",
    "请重试",
    "重试一下",
)
_LOCAL_ERROR_CONTAINER_TOKENS = (
    "dialog",
    "empty",
    "error",
    "exception",
    "fail",
    "network",
    "reload",
    "retry",
)
_BOUNDS_PATTERN = re.compile(
    r"^\[(?P<left>-?\d+),(?P<top>-?\d+)\]"
    r"\[(?P<right>-?\d+),(?P<bottom>-?\d+)\]$"
)


def _center_from_bounds_text(bounds: str) -> Optional[tuple[int, int]]:
    """Return a positive-area centre from an Android hierarchy bounds string."""
    match = _BOUNDS_PATTERN.match(bounds or "")
    if match is None:
        return None
    left, top, right, bottom = (
        int(match.group(name))
        for name in ("left", "top", "right", "bottom")
    )
    if right <= left or bottom <= top:
        return None
    return ((left + right) // 2, (top + bottom) // 2)

# Shared blocking-overlay markers.  Keep these phrases specific: matching
# generic fragments such as "验证", "待支付", or "未支付" can stop the rush
# path on ordinary instructional/order-copy text.
MANUAL_VERIFICATION_MARKERS = (
    "验证码",
    "滑动验证",
    "安全验证",
    "人机验证",
    "滑块",
    "行为验证",
    "风险验证",
    "风控验证",
    "请完成验证",
)
PENDING_ORDER_MARKERS = (
    "未支付订单",
    "待支付订单",
    "待付款订单",
    "已有未支付订单",
    "已有订单待支付",
)
TRANSIENT_ERROR_MARKERS = (
    "加载失败",
    "网络异常",
    "网络错误",
    "网络不给力",
    "网络开小差",
    "页面出错",
    "请求失败",
    "服务异常",
    "刷新失败",
    "连接失败",
    "暂无网络",
    "无网络连接",
)


def _node_visible_text(node: ET.Element) -> str:
    """Return the visible text payload used for recovery classification."""
    return " ".join(
        part.strip()
        for part in (
            node.get("text", ""),
            node.get("content-desc", ""),
        )
        if part and part.strip()
    )


def _nearest_retry_click_target(
    node: ET.Element,
    *,
    node_text: str,
    parent_map: Dict[ET.Element, ET.Element],
    root: ET.Element,
) -> Optional[ET.Element]:
    """Resolve a retry control without climbing into a broad page container."""
    if node.get("clickable") == "true":
        return node
    parent = parent_map.get(node)
    if (
        parent is not None
        and parent is not root
        and parent.get("visible-to-user") != "false"
        and parent.get("clickable") == "true"
    ):
        return parent

    # Some Damai retry labels are directly tappable even when uiautomator does
    # not expose ``clickable=true``.  Keep this narrow to exact action copy (or
    # an explicit "点击重试" instruction), matching the previous safe fallback.
    if node_text in _RETRY_ACTION_TEXTS or "点击重试" in node_text:
        return node
    return None


def _is_reasonable_error_container(node: ET.Element) -> bool:
    """Return whether a nested node carries local error-container semantics."""
    own_text = _node_visible_text(node)
    if any(marker in own_text for marker in TRANSIENT_ERROR_MARKERS):
        return True
    identity = " ".join(
        (
            node.get("resource-id", ""),
            node.get("class", ""),
            node.get("content-desc", ""),
        )
    ).lower()
    return any(token in identity for token in _LOCAL_ERROR_CONTAINER_TOKENS)


def _nearest_common_ancestor(
    first: ET.Element,
    second: ET.Element,
    parent_map: Dict[ET.Element, ET.Element],
) -> tuple[Optional[ET.Element], int, int]:
    """Return the nearest common ancestor and each node's edge distance."""
    first_distances: Dict[ET.Element, int] = {}
    cursor: Optional[ET.Element] = first
    distance = 0
    while cursor is not None:
        first_distances[cursor] = distance
        cursor = parent_map.get(cursor)
        distance += 1

    cursor = second
    second_distance = 0
    while cursor is not None:
        if cursor in first_distances:
            return cursor, first_distances[cursor], second_distance
        cursor = parent_map.get(cursor)
        second_distance += 1
    return None, 0, 0


def _is_local_transient_retry_pair(
    marker_node: ET.Element,
    action_node: ET.Element,
    click_target: ET.Element,
    *,
    parent_map: Dict[ET.Element, ET.Element],
    root: ET.Element,
) -> bool:
    """Prove that a retry control belongs to the nearby transient error.

    The hierarchy document itself is never accepted as a shared container.
    Apart from combined marker/action copy on one node, both nodes must meet
    within one edge of a nested XML ``node``.  This intentionally rejects a
    page-level error marker paired with an unrelated toolbar/content refresh.
    """
    if marker_node is action_node and click_target is action_node:
        return True

    common, marker_distance, target_distance = _nearest_common_ancestor(
        marker_node,
        click_target,
        parent_map,
    )
    if common is None or common is root or common.tag != "node":
        return False
    if parent_map.get(common) is root:
        # A top-level app shell is structurally equivalent to the hierarchy
        # root and is too broad to prove that the action belongs to the error.
        return False
    return (
        marker_distance <= 1
        and target_distance <= 1
        and _is_reasonable_error_container(common)
    )


def _find_local_transient_retry_coords(
    root: ET.Element,
) -> Optional[tuple[int, int]]:
    """Return retry coordinates only after local error/action pairing.

    Transient markers may still classify the page as an error when no pairing
    can be proved; in that case callers receive ``None`` and must not tap.
    """
    parent_map = {
        child: parent
        for parent in root.iter()
        for child in parent
    }
    visible_nodes = [
        node
        for node in root.iter("node")
        if node.get("visible-to-user") != "false"
    ]
    marker_nodes = [
        node
        for node in visible_nodes
        if any(
            marker in _node_visible_text(node)
            for marker in TRANSIENT_ERROR_MARKERS
        )
    ]
    if not marker_nodes:
        return None

    for action_node in visible_nodes:
        node_text = _node_visible_text(action_node)
        if not node_text:
            continue
        exact_action = node_text in _RETRY_ACTION_TEXTS
        phrase_action = any(
            phrase in node_text for phrase in _RETRY_ACTION_PHRASES
        )
        if not (exact_action or phrase_action):
            continue
        click_target = _nearest_retry_click_target(
            action_node,
            node_text=node_text,
            parent_map=parent_map,
            root=root,
        )
        if click_target is None:
            continue
        coords = _center_from_bounds_text(click_target.get("bounds") or "")
        if coords is None:
            continue
        if any(
            _is_local_transient_retry_pair(
                marker_node,
                action_node,
                click_target,
                parent_map=parent_map,
                root=root,
            )
            for marker_node in marker_nodes
        ):
            return coords
    return None


def _marker_text_pattern(markers) -> str:
    """Build one Android ``textMatches`` pattern for exact marker phrases."""
    alternatives = "|".join(re.escape(marker) for marker in markers)
    return rf".*({alternatives}).*"


_TRANSIENT_ERROR_TEXT_PATTERN = _marker_text_pattern(
    TRANSIENT_ERROR_MARKERS
)
_MANUAL_VERIFICATION_TEXT_PATTERN = _marker_text_pattern(
    MANUAL_VERIFICATION_MARKERS
)
_PENDING_ORDER_TEXT_PATTERN = _marker_text_pattern(PENDING_ORDER_MARKERS)
_HOT_BLOCKING_TEXT_PATTERN = (
    rf".*({'|'.join(re.escape(marker) for marker in PENDING_ORDER_MARKERS)}|"
    rf"{'|'.join(re.escape(marker) for marker in MANUAL_VERIFICATION_MARKERS)}|"
    rf"{'|'.join(re.escape(marker) for marker in TRANSIENT_ERROR_MARKERS)}).*"
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
    "manual_verification": False,
    "transient_error": False,
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

    def __init__(
        self,
        device: Any,
        config: Any = None,
        cache_ttl_s: float = 0.5,
        *,
        unknown_threshold: int = 3,
        dump_dir: str = "tmp",
    ) -> None:
        self._device = device
        self._config = config
        self._bot = None
        self._cache_ttl_s = cache_ttl_s
        self._cached_result: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0
        self._cached_mode: Optional[str] = None
        # P2 #24 — consecutive-unknown alerting
        self._unknown_threshold = max(0, int(unknown_threshold))
        self._consecutive_unknown_count = 0
        self._dump_dir = dump_dir
        self.dumped_xml_path: Optional[str] = None
        self._last_snapshot_resource_ids: Set[str] = set()
        self._last_snapshot_text = ""
        self._last_snapshot_valid = False
        self._last_snapshot_cta_inspection: Optional[Dict[str, Any]] = None
        self._last_snapshot_observed_at = 0.0
        self._last_snapshot_title = ""
        self._last_snapshot_venue = ""
        self._last_snapshot_login_prompt = False
        self._last_snapshot_popup_resource_ids: Set[str] = set()
        self._last_snapshot_popup_texts: Set[str] = set()
        self._last_snapshot_reservation_mode = False
        self._last_snapshot_retry_coords: Optional[tuple[int, int]] = None
        self._snapshot_generation = 0
        self._last_snapshot_generation = 0
        self._snapshot_reuse_invalidated = True
        self._last_hot_probe_device_error = False
        self._last_session_probe_device_error = False
        self._last_consent_probe_device_error = False
        self._last_foreground_package: Optional[str] = None
        self._last_activity: str = ""
        # P2 #24 — recovery override
        self._forced_state: Optional[str] = None

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference for delegation (e.g. reservation_mode check)."""
        self._bot = bot

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
        # P2 #24 — recovery short-circuit: if a known state has been forced,
        # return it without touching the device.
        if self._forced_state is not None:
            result = _make_result(state=self._forced_state)
            self._cached_result = result
            self._cached_at = time.monotonic()
            self._cached_mode = "full"
            self._publish_hot_cache_context(result)
            return result

        now = time.monotonic()
        requested_mode = "fast" if fast else "full"
        if (
            self._cached_result is not None
            and (now - self._cached_at) < self._cache_ttl_s
            and (fast or self._cached_mode == "full")
        ):
            logger.debug(
                "page probe: returning cached result (age=%.3fs)", now - self._cached_at
            )
            self._publish_hot_cache_context(self._cached_result)
            return self._cached_result

        if fast:
            result = self._probe_fast()
        else:
            result = self._probe_full()

        self._cached_result = result
        self._cached_at = time.monotonic()
        self._cached_mode = requested_mode
        self._track_unknown(result)
        self._publish_hot_cache_context(result)
        return result

    def _publish_hot_cache_context(self, result: Dict[str, Any]) -> None:
        """Forward already-observed page identity to the local cache sidecar."""
        bot = self._bot
        setter = getattr(bot, "_set_hot_cache_context", None)
        if not callable(setter):
            return
        setter(
            app_package=self._last_foreground_package,
            page_state=(result or {}).get("state", ""),
            activity=self._last_activity,
        )

    def _snapshot_result_fields(self) -> Dict[str, Any]:
        """Return structured values extracted from the latest full hierarchy."""
        if not self._last_snapshot_valid:
            return {}
        return {
            "activity": self._last_activity,
            "snapshot_observed_at": self._last_snapshot_observed_at,
            "snapshot_generation": self._last_snapshot_generation,
            "observed_title": self._last_snapshot_title,
            "observed_venue": self._last_snapshot_venue,
            "login_prompt": self._last_snapshot_login_prompt,
            "startup_popup_resource_ids": tuple(
                sorted(self._last_snapshot_popup_resource_ids)
            ),
            "startup_popup_texts": tuple(
                sorted(self._last_snapshot_popup_texts)
            ),
            "snapshot_reservation_mode": self._last_snapshot_reservation_mode,
            "transient_retry_coords": self._last_snapshot_retry_coords,
        }

    def borrow_fresh_detail_snapshot(
        self,
        probe_result: Any,
        *,
        max_age_s: float = _DETAIL_SNAPSHOT_REUSE_MAX_AGE_S,
    ) -> Optional[Dict[str, Any]]:
        """Return a bounded proof for reusing one full detail snapshot.

        This is deliberately stricter than the general page cache.  It only
        supports an exact result object produced by the latest successful full
        hierarchy probe, before any explicit cache invalidation.  Callers may
        reuse the already-read Activity, clean overlay result and CTA anchor,
        but must still run a fresh final gate before any click.
        """
        try:
            requested_max_age = max(0.0, float(max_age_s))
        except (TypeError, ValueError):
            return None
        effective_max_age = min(
            requested_max_age,
            _DETAIL_SNAPSHOT_REUSE_MAX_AGE_S,
        )
        if (
            not isinstance(probe_result, dict)
            or probe_result is not self._cached_result
            or self._cached_mode != "full"
            or self._snapshot_reuse_invalidated
            or not self._last_snapshot_valid
            or probe_result.get("state") != PageState.DETAIL_PAGE.value
            or probe_result.get("purchase_button") is not True
            or probe_result.get("price_container") is not True
        ):
            return None

        generation = probe_result.get("snapshot_generation")
        if (
            not isinstance(generation, int)
            or generation <= 0
            or generation != self._last_snapshot_generation
            or generation != self._snapshot_generation
        ):
            return None

        observed_at = probe_result.get("snapshot_observed_at")
        if (
            not isinstance(observed_at, (int, float))
            or observed_at <= 0
            or observed_at != self._last_snapshot_observed_at
        ):
            return None
        age_s = time.monotonic() - float(observed_at)
        if age_s < 0 or age_s > effective_max_age:
            return None

        expected_package = getattr(self._config, "app_package", None)
        activity = probe_result.get("activity")
        if (
            not isinstance(expected_package, str)
            or not expected_package
            or self._last_foreground_package != expected_package
            or not isinstance(activity, str)
            or "ProjectDetail" not in activity
            or activity != self._last_activity
        ):
            return None

        inspection = self._last_snapshot_cta_inspection
        if not (
            isinstance(inspection, dict)
            and inspection.get("resource_id") in _DETAIL_CTA_RESOURCE_IDS
            and inspection.get("source") == "hierarchy_snapshot"
            and inspection.get("kind") in {"text", "canvas"}
            and inspection.get("device_error") is False
        ):
            return None

        return {
            "snapshot_generation": generation,
            "snapshot_observed_at": float(observed_at),
            "foreground_package": self._last_foreground_package,
            "activity": activity,
            "state": PageState.DETAIL_PAGE.value,
            "overlay": None,
            "cta_inspection": dict(inspection),
        }

    def probe_blocking_overlay(self) -> Optional[Dict[str, Any]]:
        """Return a critical overlay that must win over the underlying page.

        This deliberately bypasses the normal page cache.  SKU/confirm
        controls can remain in the hierarchy underneath a dialog, so callers
        use this gate immediately before a state transition or submit click.
        One hierarchy snapshot replaces four serial selector RPCs on-device.
        """
        self._last_snapshot_valid = False
        self._last_snapshot_cta_inspection = None
        self._last_snapshot_observed_at = 0.0
        self._last_snapshot_title = ""
        self._last_snapshot_venue = ""
        self._last_snapshot_login_prompt = False
        self._last_snapshot_popup_resource_ids = set()
        self._last_snapshot_popup_texts = set()
        self._last_snapshot_reservation_mode = False
        self._last_snapshot_retry_coords = None
        self._snapshot_generation += 1
        self._snapshot_reuse_invalidated = True
        try:
            xml = self._device.dump_hierarchy() or ""
            if isinstance(xml, str) and xml.strip():
                root = ET.fromstring(xml)
                texts = []
                resource_ids = set()
                texts_by_resource_id: Dict[str, list[str]] = {}
                cta_inspections: Dict[str, Dict[str, Any]] = {}
                consent = False
                for node in root.iter("node"):
                    if node.get("visible-to-user") == "false":
                        continue
                    resource_id = node.get("resource-id") or ""
                    if resource_id:
                        resource_ids.add(resource_id)
                        if resource_id in _STARTUP_POPUP_RESOURCE_IDS:
                            self._last_snapshot_popup_resource_ids.add(
                                resource_id
                            )
                    if (
                        resource_id in _DETAIL_CTA_RESOURCE_IDS
                        and resource_id not in cta_inspections
                    ):
                        text = (
                            (node.get("text") or "").strip()
                            or (node.get("content-desc") or "").strip()
                            or None
                        )
                        coords = _center_from_bounds_text(
                            node.get("bounds") or ""
                        )
                        cta_inspections[resource_id] = {
                            "resource_id": resource_id,
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
                            "source": "hierarchy_snapshot",
                        }
                    if resource_id == "cn.damai:id/id_boot_action_agree":
                        consent = True
                    node_values = []
                    for attr in ("text", "content-desc"):
                        value = (node.get(attr) or "").strip()
                        if value:
                            node_values.append(value)
                            texts.append(value)
                            if resource_id:
                                texts_by_resource_id.setdefault(
                                    resource_id, []
                                ).append(value)
                            if value in _STARTUP_POPUP_TEXTS:
                                self._last_snapshot_popup_texts.add(value)
                self._last_snapshot_retry_coords = (
                    _find_local_transient_retry_coords(root)
                )
                merged = " ".join(texts)
                self._last_snapshot_resource_ids = resource_ids
                self._last_snapshot_text = merged
                self._last_snapshot_valid = True
                self._last_snapshot_observed_at = time.monotonic()
                self._last_snapshot_generation = self._snapshot_generation
                self._snapshot_reuse_invalidated = False
                self._last_snapshot_cta_inspection = next(
                    (
                        cta_inspections[resource_id]
                        for resource_id in _DETAIL_CTA_RESOURCE_IDS
                        if resource_id in cta_inspections
                    ),
                    None,
                )
                title_parts = texts_by_resource_id.get(
                    "cn.damai:id/title_tv", []
                )
                if title_parts:
                    self._last_snapshot_title = title_parts[0].strip()
                else:
                    self._last_snapshot_title = "".join(
                        (
                            texts_by_resource_id.get(
                                "cn.damai:id/project_title_tv1", [""]
                            )[0],
                            texts_by_resource_id.get(
                                "cn.damai:id/project_title_tv2", [""]
                            )[0],
                        )
                    ).strip()
                self._last_snapshot_venue = next(
                    (
                        texts_by_resource_id[resource_id][0].strip()
                        for resource_id in _DETAIL_VENUE_RESOURCE_IDS
                        if texts_by_resource_id.get(resource_id)
                    ),
                    "",
                )
                self._last_snapshot_login_prompt = any(
                    marker in merged for marker in _LOGIN_PROMPT_MARKERS
                )
                self._last_snapshot_reservation_mode = bool(
                    _RESERVATION_RESOURCE_IDS.intersection(resource_ids)
                    or any(
                        marker in merged
                        for marker in _RESERVATION_TEXT_MARKERS
                    )
                )
                activity_scope = (
                    "detail"
                    if "ProjectDetail" in self._last_activity
                    else "sku"
                    if "NcovSku" in self._last_activity
                    else "confirm"
                    if (
                        "Order" in self._last_activity
                        or "Confirm" in self._last_activity
                    )
                    else None
                )
                digest_builder = getattr(
                    self._bot, "_hot_layout_digest_from_xml", None
                )
                context_setter = getattr(
                    self._bot, "_set_hot_cache_context", None
                )
                if (
                    activity_scope is not None
                    and callable(digest_builder)
                    and callable(context_setter)
                ):
                    context_setter(
                        scope=activity_scope,
                        activity=self._last_activity,
                        layout_digest=digest_builder(root, activity_scope),
                    )
                if consent:
                    return _make_result(
                        state=PageState.CONSENT_DIALOG.value,
                        **self._snapshot_result_fields(),
                    )
                if any(marker in merged for marker in PENDING_ORDER_MARKERS):
                    return _make_result(
                        state=PageState.PENDING_ORDER_DIALOG.value,
                        pending_order_dialog=True,
                        **self._snapshot_result_fields(),
                    )
                if any(
                    marker in merged for marker in MANUAL_VERIFICATION_MARKERS
                ):
                    return _make_result(
                        state=PageState.MANUAL_VERIFICATION.value,
                        manual_verification=True,
                        **self._snapshot_result_fields(),
                    )
                if any(marker in merged for marker in TRANSIENT_ERROR_MARKERS):
                    return _make_result(
                        state=PageState.TRANSIENT_ERROR.value,
                        transient_error=True,
                        **self._snapshot_result_fields(),
                    )
                return None
        except Exception:
            # Older/mocked backends may not expose hierarchy dumps here.
            # Preserve the selector fallback for compatibility.
            pass

        if self._exists_by_resource_id("cn.damai:id/id_boot_action_agree"):
            return _make_result(state=PageState.CONSENT_DIALOG.value)

        if self._exists_by_text_matches(_PENDING_ORDER_TEXT_PATTERN):
            return _make_result(
                state=PageState.PENDING_ORDER_DIALOG.value,
                pending_order_dialog=True,
            )

        if self._exists_by_text_matches(_MANUAL_VERIFICATION_TEXT_PATTERN):
            return _make_result(
                state=PageState.MANUAL_VERIFICATION.value,
                manual_verification=True,
            )

        if self._exists_by_text_matches(_TRANSIENT_ERROR_TEXT_PATTERN):
            return _make_result(
                state=PageState.TRANSIENT_ERROR.value,
                transient_error=True,
            )

        return None

    def has_hot_blocking_overlay(self) -> bool:
        """One-RPC pre-click guard for pending/manual/transient overlays."""
        self._last_hot_probe_device_error = False
        try:
            return _selector_exists(
                self._device(textMatches=_HOT_BLOCKING_TEXT_PATTERN)
            )
        except Exception:
            self._last_hot_probe_device_error = True
            return False

    def probe_consent_dialog(self) -> Optional[bool]:
        """Check the consent control only at the final submit boundary.

        Returns True/False, or None when the device query itself failed.
        Keeping this out of the general hot-overlay selector avoids adding a
        serial RPC to every presale poll.
        """
        self._last_consent_probe_device_error = False
        try:
            return _selector_exists(
                self._device(
                    resourceId="cn.damai:id/id_boot_action_agree"
                )
            )
        except Exception:
            self._last_consent_probe_device_error = True
            return None

    def force_state(self, state: Optional[Union["PageState", str]]) -> None:
        """Force the probe to return a known state on subsequent calls.

        Used by recovery modules that have external knowledge of the current
        page (e.g. just dismissed a dialog and know we are back on detail_page)
        to bypass the slower element-based classification.

        Pass ``None`` to clear the override and resume normal probing.
        """
        if state is None:
            self._forced_state = None
            self.invalidate_cache()
            logger.info("PageProbe.force_state: cleared override")
            return
        state_value = state.value if isinstance(state, PageState) else str(state)
        self._forced_state = state_value
        self._consecutive_unknown_count = 0
        self.invalidate_cache()
        logger.info("PageProbe.force_state: %s", state_value)

    def _track_unknown(self, result: Dict[str, Any]) -> None:
        """Count consecutive unknown classifications and dump UI on threshold."""
        if (result or {}).get("state") != PageState.UNKNOWN.value:
            self._consecutive_unknown_count = 0
            return
        if self._unknown_threshold == 0:
            return
        self._consecutive_unknown_count += 1
        if self._consecutive_unknown_count == self._unknown_threshold:
            logger.warning(
                "UNKNOWN_THRESHOLD reached after %d classifications",
                self._consecutive_unknown_count,
            )
            self.dumped_xml_path = self._dump_unknown_xml()

    def _dump_unknown_xml(self) -> Optional[str]:
        """Persist the current UI hierarchy to ``<dump_dir>/page_probe_unknown_<ts>.xml``.

        Returns the file path on success, or ``None`` if dumping failed.
        Failures never raise — alerting is best-effort.
        """
        try:
            os.makedirs(self._dump_dir, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("page probe dump dir create failed: %s", exc)
            return None
        try:
            xml = self._device.dump_hierarchy() or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("page probe dump_hierarchy failed: %s", exc)
            xml = ""
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._dump_dir, f"page_probe_unknown_{ts}.xml")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(xml)
        except Exception as exc:  # noqa: BLE001
            logger.debug("page probe dump write failed: %s", exc)
            return None
        return path

    def classify(self, fast: bool = False) -> Dict[str, Any]:
        """Public alias of :meth:`probe_current_page`.

        Existing call sites use ``probe_current_page``; new code (P1 #25
        multi-session flow) reads more naturally as ``probe.classify()``.
        """
        return self.probe_current_page(fast=fast)

    def get_current_activity(self) -> str:
        """Return the current foreground Activity name."""
        try:
            info = self._device.app_current()
            self._last_foreground_package = info.get("package") or None
            self._last_activity = info.get("activity", "")
            setter = getattr(self._bot, "_set_hot_cache_context", None)
            if callable(setter):
                setter(app_package=self._last_foreground_package)
            return self._last_activity
        except Exception:
            self._last_foreground_package = None
            self._last_activity = ""
            logger.warning("page probe: failed to get current activity")
            return ""

    def _foreground_package_mismatch(self) -> bool:
        expected = getattr(self._config, "app_package", None)
        return bool(
            expected
            and self._last_foreground_package
            and self._last_foreground_package != expected
        )

    def invalidate_cache(self) -> None:
        """Clear the cached probe result so the next call queries the device."""
        self._cached_result = None
        self._cached_at = 0.0
        self._cached_mode = None
        self._snapshot_generation += 1
        self._snapshot_reuse_invalidated = True

    # ------------------------------------------------------------------
    # Internal: fast probe
    # ------------------------------------------------------------------

    def _probe_fast(self) -> Dict[str, Any]:
        """Probe using only the Activity name (~100ms)."""
        activity = self.get_current_activity()
        logger.debug("page probe (fast): activity=%s", activity)
        if self._foreground_package_mismatch():
            logger.warning("page probe: foreground package is not target app")
            return _make_result()

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
        """Probe using Activity + element lookups for comprehensive state detection.

        Strategy: inspect the Activity plus a small priority-overlay set.
        Consent, pending-order and transient-error overlays must win over the
        Activity underneath them; all other element checks are deferred to
        pages without a distinct Activity.
        """
        activity = self.get_current_activity()
        logger.debug("page probe (full): activity=%s", activity)
        if self._foreground_package_mismatch():
            logger.warning("page probe: foreground package is not target app")
            return _make_result()

        # Blocking overlays take precedence over their underlying Activity.
        # This is intentionally limited to the full probe so the normal
        # Activity-only hot path remains cheap.
        overlay = self.probe_blocking_overlay()
        if overlay is not None:
            return overlay
        snapshot_fields = self._snapshot_result_fields()

        if self._last_snapshot_valid and (
            "cn.damai:id/checkbox" in self._last_snapshot_resource_ids
            or "立即提交" in self._last_snapshot_text
        ):
            return _make_result(
                state=PageState.ORDER_CONFIRM_PAGE.value,
                submit_button="立即提交" in self._last_snapshot_text,
                **snapshot_fields,
            )

        # ------------------------------------------------------------------
        # Fast path: Activity-based detection with minimal confirmation
        # ------------------------------------------------------------------
        if "ProjectDetail" in activity:
            if self._last_snapshot_valid:
                purchase_bar = (
                    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                    in self._last_snapshot_resource_ids
                )
                price_layout = any(
                    rid in self._last_snapshot_resource_ids
                    for rid in _DETAIL_PRICE_LAYOUT_IDS
                )
            else:
                purchase_bar = self._exists_by_resource_id(
                    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                )
                price_layout = any(
                    self._exists_by_resource_id(rid)
                    for rid in _DETAIL_PRICE_LAYOUT_IDS
                )
            # price_container 不能停留在默认 False：probe_only 就绪判定
            # （orchestrator）依赖它，否则安全探测在 detail_page 上永远「未就绪」。
            # 优先复用上面的 hierarchy，避免再发 2–3 个设备 RPC。
            return _make_result(
                state="detail_page",
                purchase_button=purchase_bar,
                price_container=price_layout,
                **snapshot_fields,
            )

        if "NcovSku" in activity:
            if self._last_snapshot_valid:
                has_session_picker = bool(
                    any(
                        resource_id in self._last_snapshot_resource_ids
                        for resource_id in _SESSION_PICKER_RESOURCE_IDS
                    )
                    or any(
                        text in self._last_snapshot_text
                        for text in _SESSION_PICKER_TEXTS
                    )
                )
            else:
                has_session_picker = self._has_session_picker_markers()
            if has_session_picker:
                return _make_result(
                    state=PageState.SESSION_PICKER.value,
                    price_container=False,
                    **snapshot_fields,
                )
            reservation = (
                self._last_snapshot_reservation_mode
                if self._last_snapshot_valid
                else self._check_reservation_mode()
            )
            if self._last_snapshot_valid:
                price_container = any(
                    resource_id in self._last_snapshot_resource_ids
                    for resource_id in _PRICE_READY_CONTAINERS
                )
                quantity_picker = (
                    "cn.damai:id/layout_num"
                    in self._last_snapshot_resource_ids
                )
            else:
                price_container = any(
                    self._exists_by_resource_id(resource_id)
                    for resource_id in _PRICE_READY_CONTAINERS
                )
                quantity_picker = self._exists_by_resource_id(
                    "cn.damai:id/layout_num"
                )
            return _make_result(
                state="sku_page",
                price_container=price_container,
                quantity_picker=quantity_picker,
                reservation_mode=reservation,
                **snapshot_fields,
            )

        if "MainActivity" in activity:
            return _make_result(state="homepage", **snapshot_fields)

        if "SearchActivity" in activity:
            return _make_result(state="search_page", **snapshot_fields)

        # ------------------------------------------------------------------
        # Slow path: element-based detection for ambiguous Activities
        # ------------------------------------------------------------------
        logger.debug("page probe (full): activity not matched, starting element checks")

        # Check order confirm page (submit button)
        submit = self._exists_by_text("立即提交")
        checkbox = self._exists_by_resource_id("cn.damai:id/checkbox")
        if submit or checkbox:
            return _make_result(
                state="order_confirm_page",
                submit_button=submit,
            )

        # Check SKU page (fallback for when Activity didn't match)
        sku_layout = self._exists_by_resource_id("cn.damai:id/layout_sku")
        sku_container = self._exists_by_resource_id("cn.damai:id/sku_contanier")
        if sku_layout or sku_container:
            if self._has_session_picker_markers():
                return _make_result(
                    state=PageState.SESSION_PICKER.value,
                )
            reservation = self._check_reservation_mode()
            return _make_result(
                state="sku_page",
                price_container=any(
                    self._exists_by_resource_id(resource_id)
                    for resource_id in _PRICE_READY_CONTAINERS
                ),
                quantity_picker=self._exists_by_resource_id(
                    "cn.damai:id/layout_num"
                ),
                reservation_mode=reservation,
            )

        # Check detail page (fallback)
        purchase_bar = self._exists_by_resource_id(
            "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        )
        price_layout = any(
            self._exists_by_resource_id(rid) for rid in _DETAIL_PRICE_LAYOUT_IDS
        )
        title_tv = self._exists_by_resource_id("cn.damai:id/title_tv")
        if purchase_bar or price_layout or title_tv:
            return _make_result(
                state="detail_page",
                purchase_button=purchase_bar,
                price_container=price_layout,
            )

        # Check homepage (fallback)
        search_header = self._exists_by_resource_id(
            "cn.damai:id/homepage_header_search"
        )
        search_btn = self._exists_by_resource_id(
            "cn.damai:id/pioneer_homepage_header_search_btn"
        )
        if search_header or search_btn:
            return _make_result(state="homepage")

        # Check search page (fallback)
        search_input = self._exists_by_resource_id("cn.damai:id/header_search_v2_input")
        if search_input:
            return _make_result(state="search_page")

        return _make_result()

    # ------------------------------------------------------------------
    # Element existence helpers
    # ------------------------------------------------------------------

    def _exists_by_resource_id(self, resource_id: str) -> bool:
        try:
            el = self._device(resourceId=resource_id)
            return _selector_exists(el)
        except Exception:
            return False

    def _exists_by_text(self, text: str) -> bool:
        try:
            el = self._device(text=text)
            return _selector_exists(el)
        except Exception:
            return False

    def _exists_by_text_contains(self, text: str) -> bool:
        try:
            el = self._device(textContains=text)
            return _selector_exists(el)
        except Exception:
            return False

    def _exists_by_text_matches(self, pattern: str) -> bool:
        try:
            el = self._device(textMatches=pattern)
            return _selector_exists(el)
        except Exception:
            return False

    def has_session_picker_markers(self) -> bool:
        """Fast pre-price check using at most two selector RPCs."""
        return bool(self.probe_session_picker_markers())

    def probe_session_picker_markers(self) -> Optional[bool]:
        """Return True/False, or None when the device query itself failed."""
        self._last_session_probe_device_error = False
        try:
            for resource_id in _SESSION_PICKER_RESOURCE_IDS:
                if _selector_exists(self._device(resourceId=resource_id)):
                    return True
            return _selector_exists(
                self._device(textMatches=_SESSION_PICKER_TEXT_PATTERN)
            )
        except Exception:
            self._last_session_probe_device_error = True
            return None

    def _has_session_picker_markers(self) -> bool:
        """Return True when the multi-session date picker UI is visible.

        Detects the SKU panel state where the user must pick a session/date
        before the price flow-layout becomes interactive. Markers checked:

        - resource-id ``cn.damai:id/sku_panel_dates``
        - text "请选择场次" / "选择日期" (and minor wording variants)
        """
        for resource_id in _SESSION_PICKER_RESOURCE_IDS:
            if self._exists_by_resource_id(resource_id):
                return True
        for text in _SESSION_PICKER_TEXTS:
            if self._exists_by_text(text) or self._exists_by_text_contains(text):
                return True
        return False

    def _check_reservation_mode(self) -> bool:
        """Check if the SKU page is in reservation (not purchase) mode."""
        if self._bot is not None:
            try:
                return self._bot.is_reservation_sku_mode()
            except Exception:
                pass
        return False
