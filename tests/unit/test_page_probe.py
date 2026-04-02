# -*- coding: UTF-8 -*-
"""Unit tests for mobile/page_probe.py — PageProbe class."""

import time as _time_module

import pytest
from unittest.mock import Mock, patch, PropertyMock

from mobile.page_probe import PageProbe, _DEFAULT_RESULT


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


# ---------------------------------------------------------------------------
# Full probe tests
# ---------------------------------------------------------------------------

class TestFullProbe:
    """Tests for the full probe mode (element-based detection)."""

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

    def test_sku_page_detected_by_layout(self):
        """Full probe detects sku_page by layout_sku resource ID."""
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
        assert result["quantity_picker"] is True

    def test_homepage_detected_by_search_header(self):
        """Full probe detects homepage by search header resource ID."""
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
        """Full probe detects detail_page by purchase status bar resource ID."""
        device = _make_device("com.damai.SomeActivity")

        def element_factory(**kwargs):
            el = Mock()
            rid = kwargs.get("resourceId", "")
            if rid == "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl":
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
