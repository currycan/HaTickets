# -*- coding: UTF-8 -*-
"""Local-only coordinate-cache identity helpers for :class:`DamaiBot`.

The hot path intentionally stores raw coordinate values in
``self._cached_hot_path_coords`` for compatibility with the existing pipeline.
This module keeps a sidecar :class:`HotCoordStamp` for every coordinate and
negative lookup.  A cache hit is accepted only when the current, locally held
runtime context produces the same stamp; no helper in this module queries the
device.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Optional


HOT_CACHE_SCOPES = frozenset({"detail", "sku", "confirm"})

HOT_CACHE_KEY_SCOPES = {
    "date": "detail",
    "city": "detail",
    "detail_buy": "detail",
    "price": "sku",
    "sku_buy": "sku",
    "attendee_checkboxes": "confirm",
}

WARM_PIPELINE_REQUIRED_KEYS = frozenset(
    {
        "detail_buy",
        "price",
        "sku_buy",
        "attendee_checkboxes",
    }
)

_PAGE_STATE_SCOPES = {
    "detail_page": "detail",
    "sku_page": "sku",
    "session_picker": "sku",
    "order_confirm_page": "confirm",
}
_UNSET = object()
_HOT_LAYOUT_RESOURCE_IDS = {
    "detail": frozenset(
        {
            "cn.damai:id/title_tv",
            "cn.damai:id/project_title_tv1",
            "cn.damai:id/project_title_tv2",
            "cn.damai:id/info_v2_price_layout",
            "cn.damai:id/project_detail_price_layout",
            "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            "cn.damai:id/btn_buy_view",
            "cn.damai:id/tv_date",
        }
    ),
    "sku": frozenset(
        {
            "cn.damai:id/layout_sku",
            "cn.damai:id/sku_contanier",
            "cn.damai:id/sku_panel_dates",
            "cn.damai:id/project_detail_perform_price_flowlayout",
            "cn.damai:id/layout_price",
            "cn.damai:id/btn_buy_view",
            "cn.damai:id/layout_num",
            "cn.damai:id/tv_date",
        }
    ),
    "confirm": frozenset(
        {
            "cn.damai:id/checkbox",
            "cn.damai:id/btn_submit",
            "cn.damai:id/submit_btn",
        }
    ),
}


@dataclass(frozen=True)
class HotCoordStamp:
    """Identity attached to one cached coordinate or negative lookup.

    All fields are immutable, serialisable local values.  Empty strings and
    ``None`` geometry are valid best-effort observations, but they still
    participate in equality so a later known value invalidates an older stamp.
    """

    app_package: str
    app_version: str
    screen_width: Optional[int]
    screen_height: Optional[int]
    orientation: str
    scope: str
    scope_generation: int
    target_digest: str
    page_state: str
    activity: str
    layout_digest: str


def _normalise_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", value).casefold()


def _normalise_optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _normalise_geometry(value: Any) -> tuple[Optional[int], Optional[int]]:
    if isinstance(value, Mapping):
        width = value.get("width", value.get("displayWidth"))
        height = value.get("height", value.get("displayHeight"))
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        width, height = value
    else:
        width, height = None, None
    return _normalise_optional_int(width), _normalise_optional_int(height)


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


class CoordsCacheMixin:
    """Mixin contributing stamped hot-path coordinate-cache helpers."""

    def _ensure_hot_cache_sidecars(self) -> None:
        """Lazily initialise sidecars so existing lightweight hosts still work."""
        if not isinstance(getattr(self, "_cached_hot_path_coords", None), dict):
            self._cached_hot_path_coords = {}
        if not isinstance(getattr(self, "_cached_hot_path_no_match", None), set):
            self._cached_hot_path_no_match = set()
        if not isinstance(getattr(self, "_cached_hot_path_coord_meta", None), dict):
            self._cached_hot_path_coord_meta = {}
        if not isinstance(
            getattr(self, "_cached_hot_path_no_match_meta", None), dict
        ):
            self._cached_hot_path_no_match_meta = {}
        if not isinstance(getattr(self, "_hot_cache_scope_generations", None), dict):
            self._hot_cache_scope_generations = {
                scope: 0 for scope in HOT_CACHE_SCOPES
            }
        else:
            for scope in HOT_CACHE_SCOPES:
                self._hot_cache_scope_generations.setdefault(scope, 0)
        if not isinstance(getattr(self, "_hot_cache_page_states", None), dict):
            self._hot_cache_page_states = {}
        if not isinstance(getattr(self, "_hot_cache_activities", None), dict):
            self._hot_cache_activities = {}
        if not isinstance(getattr(self, "_hot_cache_layout_digests", None), dict):
            self._hot_cache_layout_digests = {}

    @staticmethod
    def _validate_hot_cache_scope(scope: str) -> str:
        if scope not in HOT_CACHE_SCOPES:
            raise ValueError(
                f"unknown hot-cache scope {scope!r}; "
                f"expected one of {sorted(HOT_CACHE_SCOPES)}"
            )
        return scope

    def _hot_cache_scope_for_key(
        self, cache_key: str, explicit_scope: Optional[str] = None
    ) -> str:
        known_scope = HOT_CACHE_KEY_SCOPES.get(cache_key)
        if explicit_scope is not None:
            explicit_scope = self._validate_hot_cache_scope(explicit_scope)
            if known_scope is not None and known_scope != explicit_scope:
                raise ValueError(
                    f"hot-cache key {cache_key!r} belongs to scope "
                    f"{known_scope!r}, not {explicit_scope!r}"
                )
            return explicit_scope
        if known_scope is not None:
            return known_scope
        current_state = getattr(self, "_hot_cache_current_page_state", "")
        inferred_scope = _PAGE_STATE_SCOPES.get(current_state)
        if inferred_scope is None:
            raise ValueError(
                f"hot-cache key {cache_key!r} has no declared scope; "
                "pass scope='detail', 'sku', or 'confirm'"
            )
        return inferred_scope

    def _set_hot_cache_context(
        self,
        *,
        app_package: Any = _UNSET,
        app_version: Any = _UNSET,
        geometry: Any = _UNSET,
        orientation: Any = _UNSET,
        scope: Optional[str] = None,
        page_state: Any = _UNSET,
        activity: Any = _UNSET,
        layout_digest: Any = _UNSET,
    ) -> None:
        """Update already-observed context without performing device I/O.

        Callers feed this method values obtained by their existing ``app_current``,
        device-info, or hierarchy reads.  Scope-specific page information is kept
        separately so a warm bundle can span detail, SKU, and confirm Activities.
        """
        self._ensure_hot_cache_sidecars()
        if app_package is not _UNSET:
            self._hot_cache_app_package = (
                app_package.strip() if isinstance(app_package, str) else ""
            )
        if app_version is not _UNSET:
            self._hot_cache_app_version = (
                app_version.strip() if isinstance(app_version, str) else ""
            )
        if geometry is not _UNSET:
            self._hot_cache_geometry = _normalise_geometry(geometry)
        if orientation is not _UNSET:
            self._hot_cache_orientation = (
                "" if orientation is None else str(orientation)
            )

        if scope is None:
            if page_state is not _UNSET:
                state_value = page_state if isinstance(page_state, str) else ""
                self._hot_cache_current_page_state = state_value
                scope = _PAGE_STATE_SCOPES.get(state_value)
            if activity is not _UNSET:
                self._hot_cache_current_activity = (
                    activity if isinstance(activity, str) else ""
                )
        else:
            scope = self._validate_hot_cache_scope(scope)

        if scope is None:
            return
        if page_state is not _UNSET:
            state_value = page_state if isinstance(page_state, str) else ""
            self._hot_cache_page_states[scope] = state_value
            self._hot_cache_current_page_state = state_value
        if activity is not _UNSET:
            activity_value = activity if isinstance(activity, str) else ""
            self._hot_cache_activities[scope] = activity_value
            self._hot_cache_current_activity = activity_value
        if layout_digest is not _UNSET:
            self._hot_cache_layout_digests[scope] = (
                layout_digest if isinstance(layout_digest, str) else ""
            )

    def _hot_cache_target_digest(self, scope: str) -> str:
        """Return a conservative, scope-specific digest of the configured target."""
        scope = self._validate_hot_cache_scope(scope)
        config = getattr(self, "config", None)
        item_detail = getattr(self, "item_detail", None)

        def config_value(name: str) -> Any:
            return getattr(config, name, None) if config is not None else None

        item_id = getattr(item_detail, "item_id", None)
        if not isinstance(item_id, (str, int)):
            item_id = None
        target = config_value("target_title") or config_value("keyword")
        event_payload = {
            "item_id": str(item_id) if item_id is not None else "",
            "target": _normalise_text(target),
            "venue": _normalise_text(config_value("target_venue")),
            "city": _normalise_text(config_value("city")),
            "date": _normalise_text(config_value("date")),
        }
        if scope in {"sku", "confirm"}:
            price_index = config_value("price_index")
            event_payload.update(
                {
                    "price": _normalise_text(config_value("price")),
                    "price_index": (
                        price_index
                        if isinstance(price_index, int)
                        and not isinstance(price_index, bool)
                        else None
                    ),
                }
            )
        if scope == "confirm":
            users = config_value("users")
            if not isinstance(users, (list, tuple)):
                users = []
            event_payload.update(
                {
                    "users": [_normalise_text(user) for user in users],
                    "attendee_count": len(users),
                }
            )
        return _stable_digest(event_payload)

    def _hot_layout_digest_from_xml(self, xml_root: Any, scope: str) -> str:
        """Hash stable, scope-relevant structure from an existing hierarchy.

        The caller already owns ``xml_root``; this method performs local parsing
        only.  Dynamic countdown/marketing copy is excluded.  Target date,
        city, price and attendee labels are included only when they match the
        configured target, so a meaningful card/row move changes the digest
        without advertisements invalidating the cache.
        """
        scope = self._validate_hot_cache_scope(scope)
        if xml_root is None or not hasattr(xml_root, "iter"):
            return ""
        config = getattr(self, "config", None)
        target_texts = {
            _normalise_text(value)
            for value in (
                getattr(config, "date", None),
                getattr(config, "city", None),
                getattr(config, "price", None),
                *(getattr(config, "users", None) or []),
            )
            if _normalise_text(value)
        }
        rows = []
        relevant_ids = _HOT_LAYOUT_RESOURCE_IDS[scope]
        for node in xml_root.iter("node"):
            if node.get("visible-to-user") == "false":
                continue
            resource_id = node.get("resource-id") or ""
            text = _normalise_text(node.get("text") or "")
            target_text = (
                text
                if text
                and any(
                    target in text or text in target
                    for target in target_texts
                )
                else ""
            )
            status_text = (
                text
                if text
                and re.search(
                    r"(售罄|无票|缺货|不可售|不可选|可售|可预约|预售)",
                    text,
                )
                else ""
            )
            include = (
                resource_id in relevant_ids
                or bool(target_text)
                or (
                    node.get("clickable") == "true"
                    and resource_id
                    and scope != "confirm"
                )
            )
            if not include:
                continue
            rows.append(
                (
                    resource_id,
                    node.get("class") or "",
                    node.get("bounds") or "",
                    target_text,
                    status_text,
                    node.get("selected") or "",
                    node.get("checked") or "",
                    node.get("enabled") or "",
                    node.get("clickable") or "",
                )
            )
        payload = {
            "scope": scope,
            "rotation": (
                xml_root.get("rotation", "")
                if hasattr(xml_root, "get")
                else ""
            ),
            "nodes": rows,
        }
        return _stable_digest(payload)

    def _build_hot_cache_stamp(
        self,
        scope: str,
        *,
        target_digest: Optional[str] = None,
        page_state: Optional[str] = None,
        activity: Optional[str] = None,
        layout_digest: Optional[str] = None,
    ) -> HotCoordStamp:
        """Build a stamp exclusively from local attributes and explicit values."""
        self._ensure_hot_cache_sidecars()
        scope = self._validate_hot_cache_scope(scope)

        config = getattr(self, "config", None)
        configured_package = getattr(config, "app_package", "")
        package = getattr(self, "_hot_cache_app_package", _UNSET)
        if package is _UNSET:
            package = configured_package if isinstance(configured_package, str) else ""

        version = getattr(self, "_hot_cache_app_version", _UNSET)
        if version is _UNSET:
            version = getattr(self, "_damai_app_version", "unknown")
        if not isinstance(version, str):
            version = "unknown"

        geometry = getattr(self, "_hot_cache_geometry", (None, None))
        width, height = _normalise_geometry(geometry)
        orientation_value = getattr(self, "_hot_cache_orientation", "")

        if page_state is None:
            page_state = self._hot_cache_page_states.get(scope, "")
        if activity is None:
            activity = self._hot_cache_activities.get(scope, "")
        if layout_digest is None:
            layout_digest = self._hot_cache_layout_digests.get(scope, "")
        if target_digest is None:
            target_digest = self._hot_cache_target_digest(scope)

        return HotCoordStamp(
            app_package=package.strip() if isinstance(package, str) else "",
            app_version=version.strip(),
            screen_width=width,
            screen_height=height,
            orientation=(
                "" if orientation_value is None else str(orientation_value)
            ),
            scope=scope,
            scope_generation=int(self._hot_cache_scope_generations[scope]),
            target_digest=target_digest if isinstance(target_digest, str) else "",
            page_state=page_state if isinstance(page_state, str) else "",
            activity=activity if isinstance(activity, str) else "",
            layout_digest=layout_digest if isinstance(layout_digest, str) else "",
        )

    def _put_hot_coord(
        self,
        cache_key: str,
        value: Any,
        *,
        scope: Optional[str] = None,
        stamp: Optional[HotCoordStamp] = None,
        target_digest: Optional[str] = None,
        page_state: Optional[str] = None,
        activity: Optional[str] = None,
        layout_digest: Optional[str] = None,
    ) -> Any:
        """Store a coordinate value and its sidecar stamp."""
        self._ensure_hot_cache_sidecars()
        resolved_scope = self._hot_cache_scope_for_key(cache_key, scope)
        resolved_stamp = stamp or self._build_hot_cache_stamp(
            resolved_scope,
            target_digest=target_digest,
            page_state=page_state,
            activity=activity,
            layout_digest=layout_digest,
        )
        if not isinstance(resolved_stamp, HotCoordStamp):
            raise TypeError("stamp must be a HotCoordStamp")
        if resolved_stamp.scope != resolved_scope:
            raise ValueError(
                f"stamp scope {resolved_stamp.scope!r} does not match "
                f"cache key scope {resolved_scope!r}"
            )

        self._cached_hot_path_coords[cache_key] = value
        self._cached_hot_path_coord_meta[cache_key] = resolved_stamp
        self._cached_hot_path_no_match.discard(cache_key)
        self._cached_hot_path_no_match_meta.pop(cache_key, None)
        return value

    def _get_hot_coord(
        self,
        cache_key: str,
        *,
        scope: Optional[str] = None,
        current_stamp: Optional[HotCoordStamp] = None,
        target_digest: Optional[str] = None,
        page_state: Optional[str] = None,
        activity: Optional[str] = None,
        layout_digest: Optional[str] = None,
    ) -> Any:
        """Return a stamped coordinate, or ``None`` after purging a stale entry."""
        self._ensure_hot_cache_sidecars()
        if cache_key not in self._cached_hot_path_coords:
            self._cached_hot_path_coord_meta.pop(cache_key, None)
            return None
        resolved_scope = self._hot_cache_scope_for_key(cache_key, scope)
        stored_stamp = self._cached_hot_path_coord_meta.get(cache_key)
        expected_stamp = current_stamp or self._build_hot_cache_stamp(
            resolved_scope,
            target_digest=target_digest,
            page_state=page_state,
            activity=activity,
            layout_digest=layout_digest,
        )
        if (
            not isinstance(stored_stamp, HotCoordStamp)
            or not isinstance(expected_stamp, HotCoordStamp)
            or stored_stamp.scope != resolved_scope
            or stored_stamp != expected_stamp
        ):
            self._cached_hot_path_coords.pop(cache_key, None)
            self._cached_hot_path_coord_meta.pop(cache_key, None)
            return None
        return self._cached_hot_path_coords[cache_key]

    def _put_hot_no_match(
        self,
        cache_key: str,
        *,
        scope: Optional[str] = None,
        stamp: Optional[HotCoordStamp] = None,
        target_digest: Optional[str] = None,
        page_state: Optional[str] = None,
        activity: Optional[str] = None,
        layout_digest: Optional[str] = None,
    ) -> None:
        """Remember a negative lookup only for the stamped layout and target."""
        self._ensure_hot_cache_sidecars()
        resolved_scope = self._hot_cache_scope_for_key(cache_key, scope)
        resolved_stamp = stamp or self._build_hot_cache_stamp(
            resolved_scope,
            target_digest=target_digest,
            page_state=page_state,
            activity=activity,
            layout_digest=layout_digest,
        )
        if not isinstance(resolved_stamp, HotCoordStamp):
            raise TypeError("stamp must be a HotCoordStamp")
        if resolved_stamp.scope != resolved_scope:
            raise ValueError(
                f"stamp scope {resolved_stamp.scope!r} does not match "
                f"cache key scope {resolved_scope!r}"
            )
        self._cached_hot_path_coords.pop(cache_key, None)
        self._cached_hot_path_coord_meta.pop(cache_key, None)
        self._cached_hot_path_no_match.add(cache_key)
        self._cached_hot_path_no_match_meta[cache_key] = resolved_stamp

    def _has_hot_no_match(
        self,
        cache_key: str,
        *,
        scope: Optional[str] = None,
        current_stamp: Optional[HotCoordStamp] = None,
        target_digest: Optional[str] = None,
        page_state: Optional[str] = None,
        activity: Optional[str] = None,
        layout_digest: Optional[str] = None,
    ) -> bool:
        """Return whether a negative lookup is valid for the current context."""
        self._ensure_hot_cache_sidecars()
        if cache_key not in self._cached_hot_path_no_match:
            self._cached_hot_path_no_match_meta.pop(cache_key, None)
            return False
        resolved_scope = self._hot_cache_scope_for_key(cache_key, scope)
        stored_stamp = self._cached_hot_path_no_match_meta.get(cache_key)
        expected_stamp = current_stamp or self._build_hot_cache_stamp(
            resolved_scope,
            target_digest=target_digest,
            page_state=page_state,
            activity=activity,
            layout_digest=layout_digest,
        )
        if (
            not isinstance(stored_stamp, HotCoordStamp)
            or not isinstance(expected_stamp, HotCoordStamp)
            or stored_stamp.scope != resolved_scope
            or stored_stamp != expected_stamp
        ):
            self._cached_hot_path_no_match.discard(cache_key)
            self._cached_hot_path_no_match_meta.pop(cache_key, None)
            return False
        return True

    def _invalidate_hot_cache(
        self,
        *,
        scope: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Invalidate one page scope, or every scope when ``scope`` is ``None``."""
        self._ensure_hot_cache_sidecars()
        scopes: Iterable[str]
        if scope is None:
            scopes = HOT_CACHE_SCOPES
        else:
            scopes = (self._validate_hot_cache_scope(scope),)

        invalidated = set(scopes)
        for current_scope in invalidated:
            self._hot_cache_scope_generations[current_scope] += 1
            self._hot_cache_layout_digests.pop(current_scope, None)

        keys = set(HOT_CACHE_KEY_SCOPES)
        keys.update(self._cached_hot_path_coord_meta)
        keys.update(self._cached_hot_path_no_match_meta)
        for cache_key in keys:
            key_scope = HOT_CACHE_KEY_SCOPES.get(cache_key)
            coord_stamp = self._cached_hot_path_coord_meta.get(cache_key)
            no_match_stamp = self._cached_hot_path_no_match_meta.get(cache_key)
            if key_scope is None and isinstance(coord_stamp, HotCoordStamp):
                key_scope = coord_stamp.scope
            if key_scope is None and isinstance(no_match_stamp, HotCoordStamp):
                key_scope = no_match_stamp.scope
            if key_scope not in invalidated:
                continue
            self._cached_hot_path_coords.pop(cache_key, None)
            self._cached_hot_path_coord_meta.pop(cache_key, None)
            self._cached_hot_path_no_match.discard(cache_key)
            self._cached_hot_path_no_match_meta.pop(cache_key, None)

        self._last_hot_cache_invalidation_reason = reason

    def _invalidate_all_hot_cache(self, reason: Optional[str] = None) -> None:
        """Explicit all-scope convenience wrapper."""
        self._invalidate_hot_cache(reason=reason)
        invalidate_event_proof = getattr(
            self, "_invalidate_formal_event_proof", None
        )
        if callable(invalidate_event_proof):
            invalidate_event_proof()

    def _validate_hot_cache_bundle(
        self,
        required_keys: Optional[Iterable[str]] = None,
    ) -> bool:
        """Validate a multi-page coordinate bundle without device access."""
        keys = tuple(required_keys or WARM_PIPELINE_REQUIRED_KEYS)
        return all(self._get_hot_coord(cache_key) is not None for cache_key in keys)

    def _has_warm_pipeline_coords(self):
        """Check whether the fully stamped warm-pipeline bundle is valid."""
        return self._validate_hot_cache_bundle(WARM_PIPELINE_REQUIRED_KEYS)

    def _get_buy_button_coordinates(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_buy_button_coordinates(xml_root)
        return None

    def _get_price_option_coordinates_by_config_index(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_price_option_coordinates_by_config_index(
                xml_root
            )
        return None
