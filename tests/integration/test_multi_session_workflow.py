# -*- coding: UTF-8 -*-
"""Integration tests for the multi-session SKU flow (P1 #25).

Covers two end-to-end paths:

1. ``page_probe`` classifies a multi-session SKU panel as ``session_picker``.
2. ``select_session`` then resolves a unique candidate by ``date + city`` and
   issues a click via the mocked uiautomator2 driver.

The tests rely on the shared ``conftest.py`` fixture that injects a u2 mock
into ``sys.modules`` so no real device is required.
"""

from unittest.mock import MagicMock

import pytest

from mobile.event_navigator import (
    SessionNotFoundError,
    _enumerate_sessions_from_xml,
    select_session,
)
from mobile.page_probe import PageProbe, PageState


def _build_session_picker_xml(*sessions):
    """Hierarchy stub: ``cn.damai:id/sku_panel_dates`` parent + N session cards."""
    cards = ""
    for date_text, city_text, bounds in sessions:
        cards += f"""
            <node clickable="true" bounds="{bounds}">
              <node resource-id="cn.damai:id/tv_date" text="{date_text}" bounds="{bounds}"/>
              <node resource-id="cn.damai:id/tv_venue" text="{city_text}" bounds="{bounds}"/>
            </node>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy>
          <node resource-id="cn.damai:id/sku_panel_dates" bounds="[0,0][1080,400]">
            {cards}
          </node>
        </hierarchy>"""


@pytest.fixture
def multi_session_device():
    device = MagicMock()
    device.app_current.return_value = {"activity": "com.damai.NcovSkuActivity"}

    def element_factory(**kwargs):
        el = MagicMock()
        el.exists = (
            kwargs.get("resourceId") == "cn.damai:id/sku_panel_dates"
            or kwargs.get("text") == "请选择场次"
        )
        return el

    device.side_effect = element_factory
    device.dump_hierarchy.return_value = _build_session_picker_xml(
        ("04.06", "上海", "[0,0][540,200]"),
        ("04.13", "北京", "[540,0][1080,200]"),
        ("04.20", "广州", "[0,200][540,400]"),
    )
    return device


class TestPageProbeIntoSelectSession:
    """End-to-end: probe → select_session → click resolves a unique session."""

    def test_probe_classifies_as_session_picker(self, multi_session_device):
        probe = PageProbe(multi_session_device, cache_ttl_s=0)
        result = probe.classify(fast=False)
        assert result["state"] == PageState.SESSION_PICKER.value

    def test_select_session_picks_by_date_city(self, multi_session_device):
        idx = select_session(multi_session_device, date="04.13", city="北京")
        assert idx == 1
        # Center of [540,0][1080,200] = (810, 100)
        multi_session_device.click.assert_called_once_with(810, 100)

    def test_full_workflow_probe_then_select(self, multi_session_device):
        """Probe identifies SESSION_PICKER, then caller invokes select_session."""
        probe = PageProbe(multi_session_device, cache_ttl_s=0)
        state = probe.classify(fast=False)["state"]

        if state == PageState.SESSION_PICKER.value:
            chosen = select_session(multi_session_device, date="04.06", city="上海")
        else:
            pytest.fail(f"unexpected state: {state}")

        assert chosen == 0
        multi_session_device.click.assert_called_once_with(270, 100)

    def test_full_workflow_falls_through_when_date_missing(self, multi_session_device):
        """When configured date does not match any card, select_session
        with fallback_index=0 still picks deterministically — guarding
        against silent hangs on production multi-session events."""
        idx = select_session(multi_session_device, date="07.01", fallback_index=0)
        assert idx == 0


class TestSessionPickerEdgeCases:
    def test_empty_panel_raises_session_not_found(self):
        device = MagicMock()
        device.dump_hierarchy.return_value = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hierarchy><node resource-id="cn.damai:id/sku_panel_dates"/></hierarchy>'
        )
        with pytest.raises(SessionNotFoundError, match="未发现可选场次"):
            select_session(device, date="04.06")

    def test_enumerate_skips_panels_outside_dates_container(self):
        """A tv_date node OUTSIDE sku_panel_dates is ignored (regression guard)."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy>
          <node resource-id="cn.damai:id/some_other_panel">
            <node resource-id="cn.damai:id/tv_date" text="04.06" bounds="[0,0][100,100]"/>
          </node>
          <node resource-id="cn.damai:id/sku_panel_dates" bounds="[0,200][1080,400]">
            <node clickable="true" bounds="[0,200][540,400]">
              <node resource-id="cn.damai:id/tv_date" text="04.13" bounds="[0,200][540,400]"/>
              <node resource-id="cn.damai:id/tv_venue" text="北京" bounds="[0,200][540,400]"/>
            </node>
          </node>
        </hierarchy>"""
        cards = _enumerate_sessions_from_xml(xml)
        assert len(cards) == 1
        assert cards[0]["date"] == "04.13"

    def test_dump_hierarchy_failure_does_not_crash_workflow(self):
        device = MagicMock()
        device.dump_hierarchy.side_effect = RuntimeError("ADB closed")
        with pytest.raises(SessionNotFoundError, match="dump_hierarchy 失败"):
            select_session(device, date="04.06")
