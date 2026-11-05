# -*- coding: UTF-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from mobile.damai_app.coords_cache import (
    CoordsCacheMixin,
    HOT_CACHE_KEY_SCOPES,
    HotCoordStamp,
)


class CacheHost(CoordsCacheMixin):
    def __init__(self):
        self.config = SimpleNamespace(
            app_package="cn.damai",
            keyword="目标演出",
            target_title="目标演出",
            target_venue="目标场馆",
            city="上海",
            date="07.20",
            price="680元",
            price_index=2,
            users=["甲", "乙"],
        )
        self.item_detail = SimpleNamespace(item_id="ITEM-1")
        self._cached_hot_path_coords = {}
        self._cached_hot_path_no_match = set()
        self._set_hot_cache_context(
            app_package="cn.damai",
            app_version="9.0.27",
            geometry=(1080, 2244),
            orientation=0,
        )
        self._set_hot_cache_context(
            scope="detail",
            page_state="detail_page",
            activity="ProjectDetailActivity",
            layout_digest="detail-layout-v1",
        )
        self._set_hot_cache_context(
            scope="sku",
            page_state="sku_page",
            activity="NcovSkuActivity",
            layout_digest="sku-layout-v1",
        )
        self._set_hot_cache_context(
            scope="confirm",
            page_state="order_confirm_page",
            activity="OrderConfirmActivity",
            layout_digest="confirm-layout-v1",
        )


@pytest.fixture
def host():
    return CacheHost()


def _populate_warm_bundle(host):
    host._put_hot_coord("detail_buy", (540, 1900))
    host._put_hot_coord("price", (300, 1300))
    host._put_hot_coord("sku_buy", (540, 2050))
    host._put_hot_coord("attendee_checkboxes", [(100, 900), (100, 1000)])


def test_key_scopes_cover_all_pipeline_entries():
    assert HOT_CACHE_KEY_SCOPES == {
        "date": "detail",
        "city": "detail",
        "detail_buy": "detail",
        "price": "sku",
        "sku_buy": "sku",
        "attendee_checkboxes": "confirm",
    }


def test_put_and_get_coord_uses_full_local_stamp(host):
    coords = host._put_hot_coord("detail_buy", (540, 1900))

    assert coords == (540, 1900)
    assert host._get_hot_coord("detail_buy") == (540, 1900)
    stamp = host._cached_hot_path_coord_meta["detail_buy"]
    assert stamp == HotCoordStamp(
        app_package="cn.damai",
        app_version="9.0.27",
        screen_width=1080,
        screen_height=2244,
        orientation="0",
        scope="detail",
        scope_generation=0,
        target_digest=host._hot_cache_target_digest("detail"),
        page_state="detail_page",
        activity="ProjectDetailActivity",
        layout_digest="detail-layout-v1",
    )


def test_unstamped_coordinate_is_a_miss_and_is_purged(host):
    host._cached_hot_path_coords["detail_buy"] = (1, 2)

    assert host._get_hot_coord("detail_buy") is None
    assert "detail_buy" not in host._cached_hot_path_coords


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("app_package", "other.app"),
        ("app_version", "10.0.0"),
        ("geometry", (1440, 3120)),
        ("orientation", 1),
    ],
)
def test_static_identity_change_invalidates_on_read(host, field, new_value):
    host._put_hot_coord("detail_buy", (1, 2))
    host._set_hot_cache_context(**{field: new_value})

    assert host._get_hot_coord("detail_buy") is None


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("page_state", "unknown"),
        ("activity", "OtherActivity"),
        ("layout_digest", "detail-layout-v2"),
    ],
)
def test_page_identity_change_invalidates_on_read(host, field, new_value):
    host._put_hot_coord("detail_buy", (1, 2))
    host._set_hot_cache_context(scope="detail", **{field: new_value})

    assert host._get_hot_coord("detail_buy") is None


def test_generation_change_invalidates_even_if_entry_was_not_cleared(host):
    host._put_hot_coord("detail_buy", (1, 2))
    host._hot_cache_scope_generations["detail"] += 1

    assert host._get_hot_coord("detail_buy") is None


def test_target_change_invalidates_only_dependent_scope_identity(host):
    host._put_hot_coord("price", (1, 2))
    host.config.price_index = 3

    assert host._get_hot_coord("price") is None


def test_no_match_is_stamped_and_stale_negative_is_removed(host):
    host._put_hot_no_match("city")
    assert host._has_hot_no_match("city") is True

    host.config.city = "北京"
    assert host._has_hot_no_match("city") is False
    assert "city" not in host._cached_hot_path_no_match


def test_put_coord_and_no_match_are_mutually_exclusive(host):
    host._put_hot_no_match("city")
    host._put_hot_coord("city", (100, 200))
    assert host._has_hot_no_match("city") is False

    host._put_hot_no_match("city")
    assert host._get_hot_coord("city") is None


def test_scope_invalidation_preserves_other_scopes(host):
    _populate_warm_bundle(host)
    before = dict(host._hot_cache_scope_generations)

    host._invalidate_hot_cache(scope="sku", reason="session_changed")

    assert host._get_hot_coord("detail_buy") == (540, 1900)
    assert host._get_hot_coord("price") is None
    assert host._get_hot_coord("sku_buy") is None
    assert host._get_hot_coord("attendee_checkboxes") == [
        (100, 900),
        (100, 1000),
    ]
    assert host._hot_cache_scope_generations["sku"] == before["sku"] + 1
    assert host._hot_cache_scope_generations["detail"] == before["detail"]
    assert host._last_hot_cache_invalidation_reason == "session_changed"


def test_all_scope_invalidation_clears_coords_no_match_and_bumps_generations(host):
    _populate_warm_bundle(host)
    host._put_hot_no_match("city")
    before = dict(host._hot_cache_scope_generations)

    host._invalidate_all_hot_cache(reason="driver_reconnect")

    assert host._cached_hot_path_coords == {}
    assert host._cached_hot_path_coord_meta == {}
    assert host._cached_hot_path_no_match == set()
    assert host._cached_hot_path_no_match_meta == {}
    assert all(
        host._hot_cache_scope_generations[scope] == generation + 1
        for scope, generation in before.items()
    )


def test_warm_bundle_validation_accepts_cross_scope_stamps(host):
    _populate_warm_bundle(host)

    assert host._validate_hot_cache_bundle() is True
    assert host._has_warm_pipeline_coords() is True


def test_warm_bundle_validation_rejects_unstamped_or_stale_member(host):
    _populate_warm_bundle(host)
    host._cached_hot_path_coord_meta.pop("price")

    assert host._validate_hot_cache_bundle() is False
    assert "price" not in host._cached_hot_path_coords


def test_unknown_key_requires_explicit_or_inferred_scope(host):
    host._hot_cache_current_page_state = "unknown"
    with pytest.raises(ValueError, match="has no declared scope"):
        host._put_hot_coord("custom", (1, 2))

    host._set_hot_cache_context(page_state="detail_page")
    host._put_hot_coord("custom", (1, 2))
    assert host._get_hot_coord("custom", scope="detail") == (1, 2)


def test_known_key_rejects_wrong_explicit_scope(host):
    with pytest.raises(ValueError, match="belongs to scope 'detail'"):
        host._put_hot_coord("detail_buy", (1, 2), scope="sku")


def test_helpers_do_not_touch_device_objects(host):
    trap = Mock()
    host.d = trap
    host.driver = trap

    host._put_hot_coord("detail_buy", (1, 2))
    assert host._get_hot_coord("detail_buy") == (1, 2)
    host._put_hot_no_match("city")
    assert host._has_hot_no_match("city") is True
    assert host._validate_hot_cache_bundle(["detail_buy"]) is True

    assert trap.mock_calls == []


def test_confirm_target_digest_tracks_ordered_users(host):
    detail_digest = host._hot_cache_target_digest("detail")
    confirm_digest = host._hot_cache_target_digest("confirm")
    host.config.users = list(reversed(host.config.users))

    assert host._hot_cache_target_digest("detail") == detail_digest
    assert host._hot_cache_target_digest("confirm") != confirm_digest


def test_layout_digest_uses_existing_stable_structure_only(host):
    first = ET.fromstring(
        """
        <hierarchy rotation="0">
          <node resource-id="cn.damai:id/title_tv"
                class="android.widget.TextView" bounds="[0,100][1080,180]"
                text="动态倒计时 03:21"/>
          <node resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                class="android.widget.FrameLayout" bounds="[0,1900][1080,2244]"/>
        </hierarchy>
        """
    )
    dynamic_text_changed = ET.fromstring(
        """
        <hierarchy rotation="0">
          <node resource-id="cn.damai:id/title_tv"
                class="android.widget.TextView" bounds="[0,100][1080,180]"
                text="动态倒计时 00:01"/>
          <node resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                class="android.widget.FrameLayout" bounds="[0,1900][1080,2244]"/>
        </hierarchy>
        """
    )
    layout_changed = ET.fromstring(
        """
        <hierarchy rotation="0">
          <node resource-id="cn.damai:id/title_tv"
                class="android.widget.TextView" bounds="[0,100][1080,180]"
                text="动态倒计时 00:01"/>
          <node resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                class="android.widget.FrameLayout" bounds="[0,1800][1080,2100]"/>
        </hierarchy>
        """
    )

    digest = host._hot_layout_digest_from_xml(first, "detail")
    assert (
        host._hot_layout_digest_from_xml(dynamic_text_changed, "detail")
        == digest
    )
    assert host._hot_layout_digest_from_xml(layout_changed, "detail") != digest


def test_sku_layout_digest_tracks_selection_and_availability(host):
    available = ET.fromstring(
        """
        <hierarchy>
          <node resource-id="cn.damai:id/layout_price"
                class="android.widget.FrameLayout" bounds="[0,400][1080,900]"
                selected="false" enabled="true" clickable="true">
            <node text="799元" bounds="[20,450][220,520]"/>
          </node>
        </hierarchy>
        """
    )
    selected = ET.fromstring(
        """
        <hierarchy>
          <node resource-id="cn.damai:id/layout_price"
                class="android.widget.FrameLayout" bounds="[0,400][1080,900]"
                selected="true" enabled="true" clickable="true">
            <node text="799元" bounds="[20,450][220,520]"/>
          </node>
        </hierarchy>
        """
    )
    sold_out = ET.fromstring(
        """
        <hierarchy>
          <node resource-id="cn.damai:id/layout_price"
                class="android.widget.FrameLayout" bounds="[0,400][1080,900]"
                selected="false" enabled="false" clickable="true">
            <node text="799元 已售罄" bounds="[20,450][320,520]"/>
          </node>
        </hierarchy>
        """
    )

    digest = host._hot_layout_digest_from_xml(available, "sku")
    assert host._hot_layout_digest_from_xml(selected, "sku") != digest
    assert host._hot_layout_digest_from_xml(sold_out, "sku") != digest
