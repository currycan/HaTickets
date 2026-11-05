# -*- coding: UTF-8 -*-
"""Unit tests for mobile/page_probe.py — PageProbe class."""

import time as _time_module
from types import SimpleNamespace

from unittest.mock import Mock, patch

from mobile.page_probe import (  # noqa: F401
    MANUAL_VERIFICATION_MARKERS,
    PENDING_ORDER_MARKERS,
    PageProbe,
    PageState,
    _DEFAULT_RESULT,
    _MANUAL_VERIFICATION_TEXT_PATTERN,
    _PENDING_ORDER_TEXT_PATTERN,
    _TRANSIENT_ERROR_TEXT_PATTERN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device(activity: str = "") -> Mock:
    """Create a mock u2 device that returns the given activity name."""
    device = Mock()
    device.app_current.return_value = {"activity": activity}
    # Default: all element lookups return non-existing elements
    mock_element = Mock()
    mock_element.exists = False
    device.return_value = mock_element
    return device


# ---------------------------------------------------------------------------
# Fast probe tests
# ---------------------------------------------------------------------------


class TestFastProbe:
    """Tests for fast probe mode (Activity-based detection)."""

    def test_detail_page_by_activity(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=True)

        assert result["state"] == "detail_page"
        device.app_current.assert_called_once()

    def test_sku_page_by_activity(self):
        device = _make_device("com.damai.NcovSkuActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=True)

        assert result["state"] == "sku_page"

    def test_homepage_by_activity(self):
        device = _make_device("com.damai.MainActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=True)

        assert result["state"] == "homepage"

    def test_same_activity_name_from_other_package_is_not_damai_homepage(self):
        device = _make_device("com.example.MainActivity")
        device.app_current.return_value = {
            "package": "com.example.other",
            "activity": "com.example.MainActivity",
        }
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0,
        )

        result = probe.probe_current_page(fast=True)

        assert result["state"] == "unknown"
        assert device.app_current.call_count == 1

    def test_search_page_by_activity(self):
        device = _make_device("com.damai.SearchActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=True)

        assert result["state"] == "search_page"

    def test_unknown_activity_falls_through_to_full_probe(self):
        """When fast probe cannot identify the activity, it falls through to full probe."""
        device = _make_device("com.damai.SomeUnknownActivity")
        # Full probe will also find nothing → state=unknown
        mock_element = Mock()
        mock_element.exists = False
        device.return_value = mock_element

        probe = PageProbe(device, cache_ttl_s=0)
        result = probe.probe_current_page(fast=True)

        assert result["state"] == "unknown"

    def test_fast_probe_result_has_all_keys(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=True)

        for key in _DEFAULT_RESULT:
            assert key in result


# ---------------------------------------------------------------------------
# TTL cache tests
# ---------------------------------------------------------------------------


class TestTTLCache:
    """Tests for the TTL-based result cache."""

    def test_cached_result_returned_within_ttl(self):
        """Second call within TTL returns the cached result, even if device state changes."""
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=10.0)  # long TTL

        result1 = probe.probe_current_page(fast=True)
        assert result1["state"] == "detail_page"

        # Change what the device would return
        device.app_current.return_value = {"activity": "com.damai.MainActivity"}

        result2 = probe.probe_current_page(fast=True)
        assert result2["state"] == "detail_page"  # still cached
        # app_current should have been called only once (first call)
        assert device.app_current.call_count == 1

    def test_cache_expires_after_ttl(self):
        """After the TTL expires, a new probe is performed."""
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=0.05)  # 50ms TTL

        result1 = probe.probe_current_page(fast=True)
        assert result1["state"] == "detail_page"

        # Wait past TTL
        _time_module.sleep(0.06)

        # Change device state
        device.app_current.return_value = {"activity": "com.damai.MainActivity"}

        result2 = probe.probe_current_page(fast=True)
        assert result2["state"] == "homepage"

    def test_invalidate_cache_clears_cached_result(self):
        """invalidate_cache() forces the next call to re-query the device."""
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=10.0)

        result1 = probe.probe_current_page(fast=True)
        assert result1["state"] == "detail_page"

        # Change device, then invalidate
        device.app_current.return_value = {"activity": "com.damai.NcovSkuActivity"}
        probe.invalidate_cache()

        result2 = probe.probe_current_page(fast=True)
        assert result2["state"] == "sku_page"

    def test_full_probe_does_not_reuse_fast_only_cache(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device, cache_ttl_s=10.0)

        assert probe.probe_current_page(fast=True)["state"] == "detail_page"

        def element_factory(**kwargs):
            el = Mock()
            el.exists = (
                kwargs.get("textMatches") == _PENDING_ORDER_TEXT_PATTERN
            )
            return el

        device.side_effect = element_factory
        result = probe.probe_current_page(fast=False)

        assert result["state"] == "pending_order_dialog"


# ---------------------------------------------------------------------------
# Full probe tests
# ---------------------------------------------------------------------------


class TestFullProbe:
    """Tests for the full probe mode (Activity + element detection)."""

    # --- Activity-based fast path within full probe ---

    def test_detail_snapshot_reuses_title_venue_and_startup_state(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.app_current.return_value = {
            "package": "cn.damai",
            "activity": "com.damai.ProjectDetailActivity",
        }
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/title_tv" text="苏州演唱会" />
          <node resource-id="cn.damai:id/venue_name_0" text="苏州奥体中心" />
          <node resource-id="cn.damai:id/info_v2_price_layout" />
          <node
            resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
          />
        </hierarchy>
        """
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0,
        )

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["observed_title"] == "苏州演唱会"
        assert result["observed_venue"] == "苏州奥体中心"
        assert result["login_prompt"] is False
        assert result["startup_popup_resource_ids"] == ()
        assert result["startup_popup_texts"] == ()
        assert result["snapshot_observed_at"] > 0
        device.assert_not_called()

    def test_borrows_fresh_full_detail_snapshot_with_exact_generation(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.app_current.return_value = {
            "package": "cn.damai",
            "activity": "com.damai.ProjectDetailActivity",
        }
        device.dump_hierarchy.return_value = (
            "<hierarchy>"
            '<node resource-id="cn.damai:id/title_tv" text="目标演出" />'
            '<node resource-id="cn.damai:id/'
            'trade_project_detail_purchase_status_bar_container_fl" '
            'bounds="[100,1200][700,1400]" />'
            '<node resource-id="cn.damai:id/info_v2_price_layout" />'
            "</hierarchy>"
        )
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0.5,
        )

        result = probe.probe_current_page(fast=False)
        borrowed = probe.borrow_fresh_detail_snapshot(result)

        assert borrowed is not None
        assert borrowed["snapshot_generation"] == result["snapshot_generation"]
        assert borrowed["foreground_package"] == "cn.damai"
        assert borrowed["activity"] == "com.damai.ProjectDetailActivity"
        assert borrowed["overlay"] is None
        assert borrowed["cta_inspection"]["coords"] == (400, 1300)

    def test_fresh_detail_snapshot_rejects_copy_expiry_and_invalidation(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.app_current.return_value = {
            "package": "cn.damai",
            "activity": "com.damai.ProjectDetailActivity",
        }
        device.dump_hierarchy.return_value = (
            "<hierarchy>"
            '<node resource-id="cn.damai:id/'
            'trade_project_detail_purchase_status_bar_container_fl" '
            'bounds="[100,1200][700,1400]" />'
            '<node resource-id="cn.damai:id/info_v2_price_layout" />'
            "</hierarchy>"
        )
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0.5,
        )
        result = probe.probe_current_page(fast=False)

        assert probe.borrow_fresh_detail_snapshot(dict(result)) is None
        with patch(
            "mobile.page_probe.time.monotonic",
            return_value=result["snapshot_observed_at"] + 0.251,
        ):
            assert probe.borrow_fresh_detail_snapshot(result) is None

        probe.invalidate_cache()
        assert probe.borrow_fresh_detail_snapshot(result) is None

    def test_fresh_detail_snapshot_rejects_package_activity_and_cta_drift(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.app_current.return_value = {
            "package": "cn.damai",
            "activity": "com.damai.ProjectDetailActivity",
        }
        device.dump_hierarchy.return_value = (
            "<hierarchy>"
            '<node resource-id="cn.damai:id/'
            'trade_project_detail_purchase_status_bar_container_fl" '
            'bounds="[100,1200][700,1400]" />'
            '<node resource-id="cn.damai:id/info_v2_price_layout" />'
            "</hierarchy>"
        )
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0.5,
        )
        result = probe.probe_current_page(fast=False)

        probe._last_foreground_package = "other.app"
        assert probe.borrow_fresh_detail_snapshot(result) is None
        probe._last_foreground_package = "cn.damai"
        probe._last_activity = "com.damai.MainActivity"
        assert probe.borrow_fresh_detail_snapshot(result) is None
        probe._last_activity = result["activity"]
        probe._last_snapshot_cta_inspection["resource_id"] = "other:id/button"
        assert probe.borrow_fresh_detail_snapshot(result) is None

    def test_new_hierarchy_generation_invalidates_prior_borrow_proof(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.app_current.return_value = {
            "package": "cn.damai",
            "activity": "com.damai.ProjectDetailActivity",
        }
        device.dump_hierarchy.return_value = (
            "<hierarchy>"
            '<node resource-id="cn.damai:id/'
            'trade_project_detail_purchase_status_bar_container_fl" '
            'bounds="[100,1200][700,1400]" />'
            '<node resource-id="cn.damai:id/info_v2_price_layout" />'
            "</hierarchy>"
        )
        probe = PageProbe(
            device,
            config=SimpleNamespace(app_package="cn.damai"),
            cache_ttl_s=0.5,
        )
        result = probe.probe_current_page(fast=False)
        assert probe.borrow_fresh_detail_snapshot(result) is not None

        assert probe.probe_blocking_overlay() is None

        assert probe.borrow_fresh_detail_snapshot(result) is None

    def test_detail_page_by_activity_fast_path(self):
        """Full probe uses Activity shortcut for ProjectDetail, confirms with purchase bar."""
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            el = Mock()
            rid = kwargs.get("resourceId", "")
            if (
                rid
                == "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
            ):
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["purchase_button"] is True

    def test_detail_page_fast_path_price_container_new_layout(self):
        """9.0.2x 详情页价格区为 info_v2_price_layout，price_container 须为 True。

        回归锁（issue #41 probe 侧面）：ProjectDetail 快路径曾不填 price_container，
        导致 probe_only 就绪判定在 detail_page 上恒为「未就绪」。
        """
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = kwargs.get("resourceId", "") in (
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                "cn.damai:id/info_v2_price_layout",
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["purchase_button"] is True
        assert result["price_container"] is True

    def test_detail_page_fast_path_price_container_legacy_layout(self):
        """v8.x 详情页仍用 project_detail_price_layout，多 ID 候选保持兼容。"""
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = kwargs.get("resourceId", "") in (
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                "cn.damai:id/project_detail_price_layout",
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["price_container"] is True

    def test_detail_page_reuses_hierarchy_snapshot_without_selector_rpcs(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.dump_hierarchy.return_value = (
            "<hierarchy>"
            '<node resource-id="cn.damai:id/'
            'trade_project_detail_purchase_status_bar_container_fl" '
            'bounds="[100,1200][700,1400]" />'
            '<node resource-id="cn.damai:id/project_detail_price_layout" />'
            "</hierarchy>"
        )
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["purchase_button"] is True
        assert result["price_container"] is True
        assert probe._last_snapshot_cta_inspection == {
            "resource_id": (
                "cn.damai:id/"
                "trade_project_detail_purchase_status_bar_container_fl"
            ),
            "text": None,
            "coords": (400, 1300),
            "kind": "canvas",
            "device_error": False,
            "source": "hierarchy_snapshot",
        }
        device.assert_not_called()

    def test_detail_page_fast_path_price_container_absent(self):
        """无任何价格区锚点时 price_container 保持 False（probe_only 判未就绪）。"""
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = (
                kwargs.get("resourceId", "")
                == "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["price_container"] is False

    def test_sku_page_by_activity_fast_path(self):
        """NcovSku identifies page type but does not invent missing controls."""
        device = _make_device("com.damai.NcovSkuActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "sku_page"
        assert result["price_container"] is False
        assert result["quantity_picker"] is False

    def test_sku_activity_reports_only_observed_ready_controls(self):
        device = _make_device("com.damai.NcovSkuActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/layout_price" />
          <node resource-id="cn.damai:id/layout_num" />
        </hierarchy>
        """
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "sku_page"
        assert result["price_container"] is True
        assert result["quantity_picker"] is True

    def test_sku_activity_loading_snapshot_is_not_actionable(self):
        device = _make_device("com.damai.NcovSkuActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/sku_loading" />
        </hierarchy>
        """
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "sku_page"
        assert result["price_container"] is False
        assert result["quantity_picker"] is False

    def test_homepage_by_activity_fast_path(self):
        """Full probe uses Activity shortcut for MainActivity."""
        device = _make_device("com.damai.MainActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "homepage"

    def test_search_page_by_activity_fast_path(self):
        """Full probe uses Activity shortcut for SearchActivity."""
        device = _make_device("com.damai.SearchActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "search_page"

    def test_activity_fast_path_only_checks_priority_overlays(self):
        """Known activities still check dialogs/errors that can cover the page."""
        device = _make_device("com.damai.MainActivity")
        mock_element = Mock()
        mock_element.exists = False
        device.return_value = mock_element

        probe = PageProbe(device, cache_ttl_s=0)
        result = probe.probe_current_page(fast=False)

        assert result["state"] == "homepage"
        assert device.call_count == 4
        device.assert_any_call(resourceId="cn.damai:id/id_boot_action_agree")
        device.assert_any_call(textMatches=_PENDING_ORDER_TEXT_PATTERN)
        device.assert_any_call(textMatches=_MANUAL_VERIFICATION_TEXT_PATTERN)
        device.assert_any_call(textMatches=_TRANSIENT_ERROR_TEXT_PATTERN)

    # --- Element-based slow path (ambiguous Activity) ---

    def test_order_confirm_page_detected_by_submit_button(self):
        """Full probe detects order_confirm_page when '立即提交' text element exists."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            if kwargs.get("text") == "立即提交":
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "order_confirm_page"
        assert result["submit_button"] is True

    def test_consent_dialog_detected(self):
        """Full probe detects consent dialog by resource ID."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            if kwargs.get("resourceId") == "cn.damai:id/id_boot_action_agree":
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "consent_dialog"

    def test_pending_order_dialog_overrides_detail_activity(self):
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = (
                kwargs.get("textMatches") == _PENDING_ORDER_TEXT_PATTERN
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "pending_order_dialog"
        assert result["pending_order_dialog"] is True

    def test_transient_error_overrides_sku_activity(self):
        device = _make_device("com.damai.NcovSkuActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = (
                kwargs.get("textMatches") == _TRANSIENT_ERROR_TEXT_PATTERN
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "transient_error"
        assert result["transient_error"] is True

    def test_transient_snapshot_carries_exact_retry_coordinates(self):
        device = _make_device("com.damai.NcovSkuActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/app_root">
            <node resource-id="cn.damai:id/network_error_panel">
              <node text="网络开小差，请稍后重试" />
              <node clickable="true" bounds="[100,200][300,300]">
                <node text="重新加载" />
              </node>
            </node>
          </node>
        </hierarchy>
        """
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "transient_error"
        assert result["transient_retry_coords"] == (200, 250)
        assert result["snapshot_observed_at"] > 0

    def test_transient_snapshot_rejects_retry_from_unrelated_region(self):
        device = _make_device("com.damai.NcovSkuActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/app_root">
            <node resource-id="cn.damai:id/page_content">
              <node text="网络异常" bounds="[0,0][300,100]" />
              <node text="刷新" clickable="true"
                    bounds="[800,0][1000,100]" />
            </node>
          </node>
        </hierarchy>
        """
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "transient_error"
        assert result["transient_retry_coords"] is None

    def test_transient_snapshot_never_pairs_through_hierarchy_root(self):
        device = _make_device("com.damai.NcovSkuActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="网络异常" bounds="[0,0][300,100]" />
          <node text="重试" bounds="[100,200][300,300]" />
        </hierarchy>
        """
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "transient_error"
        assert result["transient_retry_coords"] is None

    def test_manual_verification_overrides_detail_activity(self):
        device = _make_device("com.damai.ProjectDetailActivity")

        def element_factory(**kwargs):
            element = Mock()
            element.exists = (
                kwargs.get("textMatches") == _MANUAL_VERIFICATION_TEXT_PATTERN
            )
            return element

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "manual_verification"
        assert result["manual_verification"] is True

    def test_all_manual_verification_markers_override_page(self):
        for marker in MANUAL_VERIFICATION_MARKERS:
            device = _make_device("com.damai.ProjectDetailActivity")
            device.dump_hierarchy.return_value = (
                f'<hierarchy><node text="{marker}" /></hierarchy>'
            )

            result = PageProbe(device, cache_ttl_s=0).probe_blocking_overlay()

            assert result is not None, marker
            assert result["state"] == "manual_verification", marker
            assert result["manual_verification"] is True, marker

    def test_all_pending_order_markers_override_page(self):
        for marker in PENDING_ORDER_MARKERS:
            device = _make_device("com.damai.ProjectDetailActivity")
            device.dump_hierarchy.return_value = (
                f'<hierarchy><node text="{marker}" /></hierarchy>'
            )

            result = PageProbe(device, cache_ttl_s=0).probe_blocking_overlay()

            assert result is not None, marker
            assert result["state"] == "pending_order_dialog", marker
            assert result["pending_order_dialog"] is True, marker

    def test_generic_order_payment_copy_is_not_pending_dialog(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="订单信息" />
          <node text="支付方式" />
        </hierarchy>
        """

        result = PageProbe(device, cache_ttl_s=0).probe_blocking_overlay()

        assert result is None

    def test_hidden_error_marker_does_not_override_visible_page(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="网络异常" visible-to-user="false" />
          <node resource-id="cn.damai:id/title_tv"
                visible-to-user="true" />
        </hierarchy>
        """

        result = PageProbe(device, cache_ttl_s=0).probe_blocking_overlay()

        assert result is None

    def test_sku_page_detected_by_layout(self):
        """Full probe detects sku_page by layout_sku resource ID (element fallback)."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            if kwargs.get("resourceId") == "cn.damai:id/layout_sku":
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "sku_page"
        assert result["quantity_picker"] is False

    def test_homepage_detected_by_search_header(self):
        """Full probe detects homepage by search header resource ID (element fallback)."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            if kwargs.get("resourceId") == "cn.damai:id/homepage_header_search":
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "homepage"

    def test_detail_page_detected_by_purchase_bar(self):
        """Full probe detects detail_page by purchase status bar (element fallback)."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            rid = kwargs.get("resourceId", "")
            if (
                rid
                == "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
            ):
                el.exists = True
            else:
                el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == "detail_page"
        assert result["purchase_button"] is True

    def test_unknown_when_no_elements_found(self):
        """Full probe returns unknown when no elements match."""
        device = _make_device("com.damai.SomeActivity")
        mock_element = Mock()
        mock_element.exists = False
        device.return_value = mock_element

        probe = PageProbe(device, cache_ttl_s=0)
        result = probe.probe_current_page(fast=False)

        assert result["state"] == "unknown"


# ---------------------------------------------------------------------------
# get_current_activity tests
# ---------------------------------------------------------------------------


class TestGetCurrentActivity:
    def test_returns_activity_string(self):
        device = _make_device("com.damai.ProjectDetailActivity")
        probe = PageProbe(device)

        assert probe.get_current_activity() == "com.damai.ProjectDetailActivity"

    def test_returns_empty_on_error(self):
        device = Mock()
        device.app_current.side_effect = RuntimeError("device disconnected")
        probe = PageProbe(device)

        assert probe.get_current_activity() == ""


# ---------------------------------------------------------------------------
# Multi-session date picker (P1 #25)
# ---------------------------------------------------------------------------


class TestClassifySessionPicker:
    """SESSION_PICKER must be detected before sku_page so the navigator can
    branch into select_session() instead of trying to click prices on a panel
    that hasn't loaded the price flow-layout yet."""

    def test_classify_session_picker_by_resource_id(self):
        device = _make_device("com.damai.NcovSkuActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = kwargs.get("resourceId") == "cn.damai:id/sku_panel_dates"
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.classify(fast=False)

        assert result["state"] == PageState.SESSION_PICKER.value
        assert result["state"] == "session_picker"
        assert result["price_container"] is False

    def test_classify_session_picker_by_text_请选择场次(self):
        device = _make_device("com.damai.NcovSkuActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = kwargs.get("text") == "请选择场次"
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.classify(fast=False)

        assert result["state"] == PageState.SESSION_PICKER.value

    def test_classify_session_picker_fallback_on_unknown_activity(self):
        """When Activity name does not match but layout_sku + dates panel exist,
        full probe still returns SESSION_PICKER (not sku_page)."""
        device = _make_device("com.damai.SomeUnknownActivity")

        def element_factory(**kwargs):
            el = Mock()
            rid = kwargs.get("resourceId", "")
            txt_contains = kwargs.get("textContains", "")
            el.exists = (
                rid
                in {
                    "cn.damai:id/layout_sku",
                    "cn.damai:id/sku_panel_dates",
                }
                or txt_contains == "选择日期"
            )
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.probe_current_page(fast=False)

        assert result["state"] == PageState.SESSION_PICKER.value

    def test_sku_page_when_no_session_picker_markers(self):
        """Regression guard: bare NcovSku page without dates panel still
        classifies as sku_page so the existing rush hot path keeps working."""
        device = _make_device("com.damai.NcovSkuActivity")

        def element_factory(**kwargs):
            el = Mock()
            el.exists = False
            return el

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        result = probe.classify(fast=False)

        assert result["state"] == "sku_page"
        assert result["state"] != PageState.SESSION_PICKER.value

    def test_fast_session_probe_reports_device_error_instead_of_absent(self):
        device = _make_device("com.damai.NcovSkuActivity")
        calls = {"count": 0}

        def element_factory(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                el = Mock()
                el.exists = False
                return el
            raise RuntimeError("device disconnected")

        device.side_effect = element_factory
        probe = PageProbe(device, cache_ttl_s=0)

        assert probe.probe_session_picker_markers() is None
        assert probe._last_session_probe_device_error is True


class TestPageStateEnum:
    def test_session_picker_value(self):
        assert PageState.SESSION_PICKER.value == "session_picker"

    def test_str_inheritance_keeps_string_compat(self):
        # Existing string-based equality must keep working.
        assert PageState.SKU_PAGE == "sku_page"
        assert PageState.SESSION_PICKER == "session_picker"


# ---------------------------------------------------------------------------
# Unknown threshold + force_state (P2 #24)
# ---------------------------------------------------------------------------


import os  # noqa: E402


class TestUnknownThreshold:
    def test_unknown_state_threshold_alerts(self, tmp_path):
        # Empty activity + no element matches → state="unknown"
        device = _make_device(activity="")
        device.dump_hierarchy.return_value = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hierarchy><node text="无法识别页面"/></hierarchy>'
        )
        probe = PageProbe(
            device,
            cache_ttl_s=0,
            unknown_threshold=2,
            dump_dir=str(tmp_path),
        )

        # First unknown — counter only, no dump yet.
        r1 = probe.probe_current_page()
        assert r1["state"] == "unknown"
        assert probe.dumped_xml_path is None

        # Second unknown — threshold reached.
        r2 = probe.probe_current_page()
        assert r2["state"] == "unknown"
        assert probe.dumped_xml_path is not None
        assert os.path.exists(probe.dumped_xml_path)
        # Dump filename matches the agreed-upon convention.
        assert os.path.basename(probe.dumped_xml_path).startswith("page_probe_unknown_")
        # Hierarchy content was written.
        with open(probe.dumped_xml_path, encoding="utf-8") as fh:
            assert "无法识别页面" in fh.read()

    def test_unknown_counter_resets_on_known_state(self, tmp_path):
        device = _make_device(activity="")
        device.dump_hierarchy.return_value = "<hierarchy/>"
        probe = PageProbe(
            device,
            cache_ttl_s=0,
            unknown_threshold=2,
            dump_dir=str(tmp_path),
        )

        # First unknown.
        assert probe.probe_current_page()["state"] == "unknown"
        assert probe.dumped_xml_path is None

        # Switch device to a known activity (homepage) to reset the counter.
        device.app_current.return_value = {"activity": "cn.damai.homepage.MainActivity"}
        assert probe.probe_current_page()["state"] == "homepage"
        assert probe.dumped_xml_path is None

        # Back to unknown — only one consecutive, still no dump.
        device.app_current.return_value = {"activity": ""}
        assert probe.probe_current_page()["state"] == "unknown"
        assert probe.dumped_xml_path is None

        # Second consecutive unknown — threshold (2) reached.
        assert probe.probe_current_page()["state"] == "unknown"
        assert probe.dumped_xml_path is not None
        assert os.path.exists(probe.dumped_xml_path)

    def test_unknown_threshold_zero_disables_dump(self, tmp_path):
        device = _make_device(activity="")
        device.dump_hierarchy.return_value = "<hierarchy/>"
        probe = PageProbe(
            device,
            cache_ttl_s=0,
            unknown_threshold=0,
            dump_dir=str(tmp_path),
        )
        probe.probe_current_page()
        assert probe.dumped_xml_path is None


class TestForceState:
    def test_force_state_overrides(self):
        # Device would normally classify as unknown.
        device = _make_device(activity="")
        probe = PageProbe(device, cache_ttl_s=0)

        probe.force_state(PageState.HOMEPAGE)

        result = probe.probe_current_page()
        assert result["state"] == "homepage"
        # Forced state must not touch the device.
        device.app_current.assert_not_called()

    def test_force_state_accepts_string(self):
        device = _make_device(activity="")
        probe = PageProbe(device, cache_ttl_s=0)

        probe.force_state("sku_page")
        assert probe.probe_current_page()["state"] == "sku_page"

    def test_force_state_clear_returns_to_probe(self):
        device = _make_device(activity="cn.damai.homepage.MainActivity")
        probe = PageProbe(device, cache_ttl_s=0)

        probe.force_state(PageState.SKU_PAGE)
        assert probe.probe_current_page()["state"] == "sku_page"

        probe.force_state(None)
        # Real probing resumes after override is cleared.
        assert probe.probe_current_page()["state"] == "homepage"

    def test_force_state_resets_unknown_counter(self, tmp_path):
        device = _make_device(activity="")
        device.dump_hierarchy.return_value = "<hierarchy/>"
        probe = PageProbe(
            device,
            cache_ttl_s=0,
            unknown_threshold=2,
            dump_dir=str(tmp_path),
        )
        # Trip one unknown.
        probe.probe_current_page()
        assert probe.dumped_xml_path is None

        # Force a known state — counter resets.
        probe.force_state(PageState.HOMEPAGE)
        probe.probe_current_page()
        probe.force_state(None)

        # One more unknown — only 1 consecutive after reset, no dump.
        probe.probe_current_page()
        assert probe.dumped_xml_path is None


# ---------------------------------------------------------------------------
# unknown_threshold=0 — qa W3-03 边界 (Task C2)
# ---------------------------------------------------------------------------


class TestUnknownThresholdZeroBoundary:
    """W3-03 qa-added 边界用例：把 unknown_threshold=0 的语义 lock-in。"""

    def test_unknown_threshold_zero_disables_alert(self, tmp_path):
        """传 unknown_threshold=0 时，无论多少次 unknown 都不应触发告警/dump。

        0 表示禁用 unknown 告警与 dump。
        """
        device = _make_device(activity="")
        device.dump_hierarchy.return_value = "<hierarchy/>"
        probe = PageProbe(
            device,
            cache_ttl_s=0,
            unknown_threshold=0,
            dump_dir=str(tmp_path),
        )
        # 即便连续 5 次 unknown，也不应触发任何 dump（按"0=disabled"语义）。
        for _ in range(5):
            assert probe.probe_current_page()["state"] == "unknown"
        assert probe.dumped_xml_path is None, (
            "expected no dump when unknown_threshold=0 (disabled), "
            "but probe.dumped_xml_path is set"
        )
