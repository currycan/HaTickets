"""AttendeeSelector — confirm page attendee checkbox automation."""
from __future__ import annotations

import re
import time
from typing import List, Optional

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By

from mobile.logger import get_logger

logger = get_logger(__name__)
_CHECKBOX_ID = "cn.damai:id/checkbox"


class AttendeeSelector:
    def __init__(self, device, config) -> None:
        self._d = device
        self._config = config
        self._bot = None  # DamaiBot reference for UIPrimitives delegation

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference (breaks circular init dependency)."""
        self._bot = bot

    # ------------------------------------------------------------------
    # Migrated method bodies from DamaiBot
    # ------------------------------------------------------------------

    def _attendee_required_count_on_confirm_page(self):
        """Infer how many attendees must be selected on the confirm page."""
        hint_text = self._bot._safe_element_text(
            self._bot.driver,
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("仅需选择")',
        )
        match = re.search(r"仅需选择\s*(\d+)\s*位", hint_text or "")
        if match:
            return max(1, int(match.group(1)))
        return max(1, len(self._config.users or []))

    def _attendee_checkbox_elements(self):
        try:
            return self._bot._find_all(By.ID, _CHECKBOX_ID)
        except Exception:
            return []

    @staticmethod
    def _is_checkbox_selected(checkbox):
        from mobile.ui_primitives import UIPrimitives
        return UIPrimitives._is_checked(checkbox)

    def _attendee_selected_count(self, checkbox_elements=None, use_source_fallback=True):
        """Count selected attendee checkboxes, with XML fallback for flaky checked attrs."""
        if checkbox_elements is not None:
            elements = checkbox_elements
        elif self._bot is not None:
            elements = self._bot._attendee_checkbox_elements()
        else:
            elements = self._attendee_checkbox_elements()
        selected_count = sum(1 for checkbox in elements if self._is_checkbox_selected(checkbox))
        if selected_count > 0:
            return selected_count
        if (self._config.rush_mode and not self._config.if_commit_order) and not use_source_fallback:
            return selected_count

        try:
            if not self._bot._using_u2():
                source = self._bot.driver.page_source or ""
            else:
                source = self._d.dump_hierarchy() or ""
        except Exception:
            return selected_count
        if not isinstance(source, str):
            return selected_count

        states = re.findall(
            r'resource-id="cn\.damai:id/checkbox"[^>]*checked="(true|false)"',
            source,
        )
        if not states:
            return selected_count
        return sum(1 for state in states if state == "true")

    def _click_attendee_checkbox(self, checkbox):
        """Try multiple click paths and verify checkbox becomes selected."""
        bot = self._bot
        use_fallback = not (self._config.rush_mode and not self._config.if_commit_order)
        before_selected = bot._attendee_selected_count(use_source_fallback=use_fallback)
        click_actions = [
            lambda: bot._click_element_center(checkbox, duration=35),
            lambda: checkbox.click(),
            lambda: bot._burst_click_element_center(checkbox, count=2, interval_ms=30, duration=30),
        ]

        for action in click_actions:
            try:
                action()
            except Exception:
                continue
            time.sleep(0.05)
            if bot._is_checkbox_selected(checkbox):
                return True
            if bot._attendee_selected_count(use_source_fallback=use_fallback) > before_selected:
                return True
        return False

    def _click_attendee_checkbox_fast(self, checkbox):
        """Low-latency checkbox click path for rush-mode validation."""
        bot = self._bot
        click_actions = [
            lambda: checkbox.click(),
            lambda: bot._click_element_center(checkbox, duration=28),
        ]
        for action in click_actions:
            try:
                action()
                time.sleep(0.01)
                return True
            except Exception:
                continue
        return False

    def _select_attendee_checkbox_by_name(self, user_name):
        bot = self._bot
        checkbox_xpaths = [
            (
                f'//*[@resource-id="cn.damai:id/text_name" and normalize-space(@text)="{user_name}"]'
                '/ancestor::*[.//*[@resource-id="cn.damai:id/checkbox"]][1]'
                '//*[@resource-id="cn.damai:id/checkbox"]'
            ),
            (
                f'//*[@resource-id="cn.damai:id/text_name" and contains(normalize-space(@text), "{user_name}")]'
                '/ancestor::*[.//*[@resource-id="cn.damai:id/checkbox"]][1]'
                '//*[@resource-id="cn.damai:id/checkbox"]'
            ),
        ]

        for checkbox_xpath in checkbox_xpaths:
            try:
                checkboxes = bot._find_all(By.XPATH, checkbox_xpath)
            except Exception:
                checkboxes = []

            for checkbox in checkboxes:
                if bot._is_checkbox_selected(checkbox):
                    return True
                if bot._click_attendee_checkbox(checkbox):
                    return True
        return False

    def _ensure_attendees_selected_on_confirm_page(self, require_attendee_section=False):
        """Make sure required attendee checkboxes are selected before submit.

        NOTE: internal calls go through ``self._bot`` so that test-level patches
        on DamaiBot delegate methods (e.g. ``bot._attendee_checkbox_elements``)
        are honoured.
        """
        bot = self._bot
        required_count = max(1, len(self._config.users or []))

        if self._config.rush_mode and not self._config.if_commit_order:
            cached_coords = bot._cached_hot_path_coords.get("attendee_checkboxes")
            if cached_coords:
                logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）")
                logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
                for coords in cached_coords[:required_count]:
                    bot._click_coordinates(*coords)
                return True

            checkbox_elements = bot._attendee_checkbox_elements()
            if not checkbox_elements:
                return not require_attendee_section

            _coords = []
            for el in checkbox_elements:
                try:
                    bt = getattr(el, "bounds", None)
                    if isinstance(bt, (list, tuple)) and len(bt) == 4:
                        left, top, right, bottom = [int(v) for v in bt]
                        _coords.append(((left + right) // 2, (top + bottom) // 2))
                except Exception:
                    pass
            if _coords:
                bot._cached_hot_path_coords["attendee_checkboxes"] = _coords

            selected_count = bot._attendee_selected_count(checkbox_elements, use_source_fallback=False)
            if selected_count >= required_count:
                return True

            logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 {selected_count}/{required_count}）")
            logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
            clicked_count = 0
            for checkbox in checkbox_elements[:required_count]:
                if bot._click_attendee_checkbox_fast(checkbox):
                    clicked_count += 1
            if clicked_count < required_count:
                logger.warning(f"观演人选择不足（需要 {required_count} 位，当前 {clicked_count} 位）")
                return False
            return True

        checkbox_elements = bot._attendee_checkbox_elements()

        if self._config.rush_mode:
            if not checkbox_elements:
                return not require_attendee_section
        else:
            attendee_section_visible = bot._has_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("实名观演人")',
            )
            if not attendee_section_visible:
                return not require_attendee_section
            if not checkbox_elements:
                logger.warning("确认页存在观演人区域，但未找到可勾选观演人，请手动检查")
                return False
            required_count = bot._attendee_required_count_on_confirm_page()

        selected_count = bot._attendee_selected_count(
            checkbox_elements,
            use_source_fallback=not self._config.rush_mode,
        )
        if selected_count >= required_count:
            return True

        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 {selected_count}/{required_count}）")

        unmatched_users = []
        for user_name in self._config.users or []:
            if selected_count >= required_count:
                break
            if bot._select_attendee_checkbox_by_name(user_name):
                selected_count = bot._attendee_selected_count()
            else:
                unmatched_users.append(user_name)

        if unmatched_users and selected_count < required_count:
            logger.warning(f"未能按姓名定位观演人: {'、'.join(unmatched_users)}，将尝试按勾选框兜底")

        if selected_count < required_count:
            for checkbox in bot._attendee_checkbox_elements():
                if selected_count >= required_count:
                    break
                if bot._is_checkbox_selected(checkbox):
                    continue
                if not bot._click_attendee_checkbox(checkbox):
                    continue
                selected_count = bot._attendee_selected_count()

        if selected_count < required_count:
            logger.warning(f"观演人选择不足（需要 {required_count} 位，当前 {selected_count} 位）")
            return False
        return True

    # ------------------------------------------------------------------
    # Legacy interface (kept for backward compat)
    # ------------------------------------------------------------------

    def ensure_selected(self) -> None:
        """Ensure the correct number of attendees are checked."""
        if self._bot is not None:
            self._ensure_attendees_selected_on_confirm_page()
            return
        # Fallback: simple path without bot reference
        required = max(1, len(self._config.users or []))
        checkboxes = self._find_checkboxes()
        if not checkboxes:
            logger.warning("未找到观演人勾选框")
            return
        for cb in checkboxes[:required]:
            self._click_checkbox(cb)
        logger.info(f"已勾选 {min(required, len(checkboxes))}/{required} 位观演人")

    def _find_checkboxes(self) -> List:
        try:
            elements = self._d(resourceId=_CHECKBOX_ID)
            return list(elements) if elements.exists else []
        except Exception:
            return []

    def _click_checkbox(self, element) -> None:
        try:
            element.click()
        except Exception:
            pass
