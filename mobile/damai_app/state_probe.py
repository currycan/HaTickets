# -*- coding: UTF-8 -*-
"""Page-state inspection helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Hosts the page-state probe entrypoints, the SKU-page reservation
detector, and the prompt-mode inspection helpers that summarise visible
date / price options for the user.
"""

from __future__ import annotations

from . import logger

try:
    from mobile.ui_primitives import ANDROID_UIAUTOMATOR
except ImportError:  # pragma: no cover
    from ui_primitives import ANDROID_UIAUTOMATOR  # type: ignore[no-redef]

try:
    from selenium.webdriver.common.by import By
except ModuleNotFoundError:  # pragma: no cover
    raise


class StateProbeMixin:
    """Mixin contributing page-state and SKU inspection methods to ``DamaiBot``."""

    def _get_detail_title_text(self, xml_root=None):
        """Read title text from detail/sku pages."""
        if xml_root is not None and self._using_u2():
            title = self._xml_find_text_by_resource_id(xml_root, "cn.damai:id/title_tv")
            if title:
                return title
            parts = [
                self._xml_find_text_by_resource_id(xml_root, rid)
                for rid in (
                    "cn.damai:id/project_title_tv1",
                    "cn.damai:id/project_title_tv2",
                )
            ]
            return "".join(p.strip() for p in parts if p).strip()

        title = ""
        try:
            title = self._safe_element_text(self.driver, By.ID, "cn.damai:id/title_tv")
        except Exception:
            title = ""

        if title:
            return title

        title_parts = []
        for resource_id in (
            "cn.damai:id/project_title_tv1",
            "cn.damai:id/project_title_tv2",
        ):
            part = self._safe_element_text(self.driver, By.ID, resource_id)
            if part:
                title_parts.append(part.strip())

        return "".join(title_parts).strip()

    def is_reservation_sku_mode(self):
        """识别当前 SKU 页是否仍处于抢票预约流，而非正式下单流。"""
        reservation_indicators = [
            (By.ID, "cn.damai:id/btn_cancel_reservation"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看场次")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看票档")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("提交抢票预约")'),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("已预约")'),
        ]

        return any(self._has_element(by, value) for by, value in reservation_indicators)

    def get_visible_date_options(self, xml_root=None):
        """Return visible date options on the current page."""
        if xml_root is not None and self._using_u2():
            dates = []
            seen = set()
            for node in xml_root.iter("node"):
                if node.get("resource-id") == "cn.damai:id/tv_date":
                    text = (node.get("text") or "").strip()
                    if text and text not in seen:
                        dates.append(text)
                        seen.add(text)
            return dates

        dates = []
        seen = set()
        for element in self._find_all(By.ID, "cn.damai:id/tv_date"):
            text = self._read_element_text(element).strip()
            if not text or text in seen:
                continue
            dates.append(text)
            seen.add(text)
        return dates

    def _get_detail_venue_text(self, xml_root=None):
        """Read venue text from the detail page if present."""
        if xml_root is not None and self._using_u2():
            for resource_id in (
                "cn.damai:id/venue_name_0",
                "cn.damai:id/tv_project_venueName",
            ):
                value = self._xml_find_text_by_resource_id(xml_root, resource_id)
                if value:
                    return value.strip()
            return ""

        for resource_id in (
            "cn.damai:id/venue_name_0",
            "cn.damai:id/tv_project_venueName",
        ):
            value = self._safe_element_text(self.driver, By.ID, resource_id)
            if value:
                return value.strip()
        return ""

    def ensure_sku_page_for_inspection(self, page_probe=None):
        """Safely enter the sku page so prompt-based flows can inspect dates and prices."""
        page_probe = page_probe or self.probe_current_page()
        if page_probe["state"] == "sku_page":
            return page_probe

        if page_probe["state"] != "detail_page":
            return page_probe

        book_selectors = [
            (
                By.ID,
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ),
            (
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")',
            ),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
        ]
        if not self.smart_wait_and_click(
            *book_selectors[0], book_selectors[1:], timeout=0.5
        ):
            return self.probe_current_page()

        return self._wait_for_purchase_entry_result(timeout=5, poll_interval=0.04)

    def inspect_current_target_event(self, page_probe=None):
        """Summarize the currently opened event for prompt-based confirmation."""
        page_probe = page_probe or self.probe_current_page()

        xml_root = None
        sku_probe = page_probe

        if page_probe["state"] == "detail_page":
            # Click buy immediately so sku_page starts loading before we do anything else.
            book_selectors = [
                (
                    By.ID,
                    "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
                ),
                (
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")',
                ),
                (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
            ]
            clicked = self.smart_wait_and_click(
                *book_selectors[0], book_selectors[1:], timeout=0.5
            )
            # Dump detail_page hierarchy while sku_page loads (~1.5s parallel time).
            xml_root = self._dump_hierarchy_xml()
            if clicked:
                sku_probe = self._wait_for_purchase_entry_result(
                    timeout=4.0, poll_interval=0.04
                )
            else:
                sku_probe = self.probe_current_page()
        elif page_probe["state"] != "sku_page":
            sku_probe = self.ensure_sku_page_for_inspection(page_probe)

        summary = {
            "state": sku_probe["state"],
            "title": self._get_detail_title_text(xml_root=xml_root),
            "venue": self._get_detail_venue_text(xml_root=xml_root),
            "dates": [],
            "price_options": [],
            "reservation_mode": sku_probe.get("reservation_mode", False),
        }

        if sku_probe["state"] == "sku_page":
            # Re-dump for sku_page content (different screen from detail_page).
            xml_root = self._dump_hierarchy_xml()
            if not summary["title"]:
                summary["title"] = self._get_detail_title_text(xml_root=xml_root)
            if not summary["venue"]:
                summary["venue"] = self._get_detail_venue_text(xml_root=xml_root)
            summary["reservation_mode"] = sku_probe.get("reservation_mode", False)
            summary["dates"] = self.get_visible_date_options(xml_root=xml_root)
            summary["price_options"] = self.get_visible_price_options(xml_root=xml_root)

        return summary

    def probe_current_page(self, fast=False):
        """探测当前页面状态和关键控件可见性。"""
        # Delegate to PageProbe when available (u2 backend)
        if hasattr(self, "_page_probe"):
            result = self._page_probe.probe_current_page(fast=fast)
            if result["state"] != "unknown" or fast:
                logger.info(f"当前页面状态: {result['state']}")
                return result

        # Fallback: element-based probe using _has_element
        return self._probe_current_page_element_based()

    def _probe_current_page_element_based(self):
        """Full probe using _has_element calls (fallback when PageProbe unavailable)."""
        state = "unknown"
        current_activity = self._get_current_activity()
        purchase_button = self._has_element(
            By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
        )
        detail_price_summary = self._has_element(
            By.ID, "cn.damai:id/project_detail_price_layout"
        )
        sku_price_container = (
            self._has_element(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            )
            or self._has_element(By.ID, "cn.damai:id/layout_price")
            or self._has_element(By.ID, "cn.damai:id/tv_price_name")
        )
        quantity_picker = self._has_element(By.ID, "layout_num")
        submit_button = self._has_element(By.ID, "cn.damai:id/checkbox")
        pending_order_dialog = self._has_element(
            By.ID, "cn.damai:id/damai_theme_dialog_confirm_btn"
        )
        reservation_mode = False

        if self._has_element(By.ID, "cn.damai:id/id_boot_action_agree"):
            state = "consent_dialog"
        elif pending_order_dialog:
            state = "pending_order_dialog"
        elif (
            "MainActivity" in current_activity
            or self._has_element(By.ID, "cn.damai:id/homepage_header_search")
            or self._has_element(
                By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"
            )
        ):
            state = "homepage"
        elif "SearchActivity" in current_activity or self._has_element(
            By.ID, "cn.damai:id/header_search_v2_input"
        ):
            state = "search_page"
        elif submit_button:
            state = "order_confirm_page"
        elif (
            "NcovSkuActivity" in current_activity
            or self._has_element(By.ID, "cn.damai:id/layout_sku")
            or self._has_element(By.ID, "cn.damai:id/sku_contanier")
        ):
            state = "sku_page"
        elif (
            "ProjectDetailActivity" in current_activity
            or purchase_button
            or detail_price_summary
            or self._has_element(By.ID, "cn.damai:id/title_tv")
        ):
            state = "detail_page"

        if state == "sku_page":
            reservation_mode = self.is_reservation_sku_mode()

        result = {
            "state": state,
            "purchase_button": purchase_button,
            "price_container": sku_price_container or detail_price_summary,
            "quantity_picker": quantity_picker,
            "submit_button": submit_button,
            "reservation_mode": reservation_mode,
            "pending_order_dialog": pending_order_dialog,
        }

        logger.info(f"当前页面状态: {result['state']}")
        if current_activity:
            logger.debug(f"当前 Activity: {current_activity}")
        logger.debug(
            "探测结果: "
            f"purchase_button={result['purchase_button']}, "
            f"price_container={result['price_container']}, "
            f"quantity_picker={result['quantity_picker']}, "
            f"submit_button={result['submit_button']}, "
            f"reservation_mode={result['reservation_mode']}"
        )

        return result
