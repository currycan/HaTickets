"""Unit tests for mobile.page_probe.detect_price_panel_state (P1 #31)."""

from unittest.mock import MagicMock

from mobile.page_probe import detect_price_panel_state


def _driver_with_existence(matchers: dict):
    """Mock driver where ``driver(**selector).exists`` is True iff
    one of the (kwarg, value) pairs in ``matchers`` matches.
    """
    driver = MagicMock()

    def _select(**kwargs):
        for k, v in kwargs.items():
            if (k, v) in matchers:
                el = MagicMock()
                el.exists = True
                return el
        el = MagicMock()
        el.exists = False
        return el

    driver.side_effect = _select
    return driver


class TestDetectPricePanelState:
    def test_loading_when_spinner_present(self):
        driver = _driver_with_existence(
            {("resourceId", "cn.damai:id/sku_loading"): True}
        )
        assert detect_price_panel_state(driver) == "loading"

    def test_loading_when_progress_present(self):
        driver = _driver_with_existence(
            {("resourceId", "cn.damai:id/loading_progress"): True}
        )
        assert detect_price_panel_state(driver) == "loading"

    def test_loading_when_view_present(self):
        driver = _driver_with_existence(
            {("resourceId", "cn.damai:id/loading_view"): True}
        )
        assert detect_price_panel_state(driver) == "loading"

    def test_sold_out_text(self):
        driver = _driver_with_existence({("text", "已售罄"): True})
        assert detect_price_panel_state(driver) == "sold_out"

    def test_sold_out_text_contains(self):
        driver = _driver_with_existence({("textContains", "全部售罄"): True})
        assert detect_price_panel_state(driver) == "sold_out"

    def test_sold_out_no_ticket(self):
        driver = _driver_with_existence({("text", "无票"): True})
        assert detect_price_panel_state(driver) == "sold_out"

    def test_ready_primary_container(self):
        driver = _driver_with_existence(
            {
                (
                    "resourceId",
                    "cn.damai:id/project_detail_perform_price_flowlayout",
                ): True
            }
        )
        assert detect_price_panel_state(driver) == "ready"

    def test_ready_layout_price(self):
        driver = _driver_with_existence(
            {("resourceId", "cn.damai:id/layout_price"): True}
        )
        assert detect_price_panel_state(driver) == "ready"

    def test_unknown_when_nothing_matches(self):
        driver = _driver_with_existence({})
        assert detect_price_panel_state(driver) == "unknown"

    def test_loading_takes_priority_over_sold_out(self):
        driver = _driver_with_existence(
            {
                ("resourceId", "cn.damai:id/loading_progress"): True,
                ("text", "已售罄"): True,
            }
        )
        assert detect_price_panel_state(driver) == "loading"

    def test_sold_out_takes_priority_over_ready(self):
        driver = _driver_with_existence(
            {
                ("text", "已售罄"): True,
                ("resourceId", "cn.damai:id/layout_price"): True,
            }
        )
        assert detect_price_panel_state(driver) == "sold_out"

    def test_device_exception_is_swallowed(self):
        driver = MagicMock(side_effect=RuntimeError("device gone"))
        assert detect_price_panel_state(driver) == "unknown"
