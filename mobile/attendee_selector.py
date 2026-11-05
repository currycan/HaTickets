"""AttendeeSelector — confirm page attendee checkbox automation."""

from __future__ import annotations

import re
import time
from typing import List

from selenium.webdriver.common.by import By

from mobile.ui_primitives import ANDROID_UIAUTOMATOR

from mobile.logger import get_logger

logger = get_logger(__name__)
_CHECKBOX_ID = "cn.damai:id/checkbox"
_monotonic = time.monotonic


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
            ANDROID_UIAUTOMATOR,
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

    def _attendee_selected_count(
        self, checkbox_elements=None, use_source_fallback=True
    ):
        """Count selected attendee checkboxes, with XML fallback for flaky checked attrs."""
        if checkbox_elements is not None:
            elements = checkbox_elements
        elif self._bot is not None:
            elements = self._bot._attendee_checkbox_elements()
        else:
            elements = self._attendee_checkbox_elements()
        selected_count = sum(
            1 for checkbox in elements if self._is_checkbox_selected(checkbox)
        )
        if selected_count > 0:
            return selected_count
        if (
            self._config.rush_mode and not self._config.if_commit_order
        ) and not use_source_fallback:
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
            lambda: bot._burst_click_element_center(
                checkbox, count=2, interval_ms=30, duration=30
            ),
        ]

        for action in click_actions:
            try:
                action()
            except Exception:
                continue
            time.sleep(0.05)
            if bot._is_checkbox_selected(checkbox):
                return True
            if (
                bot._attendee_selected_count(use_source_fallback=use_fallback)
                > before_selected
            ):
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

    @staticmethod
    def _xpath_string_literal(value):
        """Return an XPath string literal without broadening an exact name match."""
        value = str(value)
        if '"' not in value:
            return f'"{value}"'
        if "'" not in value:
            return f"'{value}'"
        parts = value.split('"')
        joined = ", '\"', ".join(f'"{part}"' for part in parts)
        return f"concat({joined})"

    def _formal_named_attendee_checkboxes(self, user_name):
        """Return checkboxes belonging to the exact configured attendee row."""
        name = user_name.strip() if isinstance(user_name, str) else ""
        if not name:
            return []
        literal = self._xpath_string_literal(name)
        checkbox_xpath = (
            '//*[@resource-id="cn.damai:id/text_name" '
            f"and normalize-space(@text)={literal}]"
            '/ancestor::*[.//*[@resource-id="cn.damai:id/checkbox"]][1]'
            '//*[@resource-id="cn.damai:id/checkbox"]'
        )
        try:
            return self._bot._find_all(By.XPATH, checkbox_xpath)
        except Exception:
            return []

    def _verify_formal_named_attendee(self, user_name):
        """Require one uniquely mapped, selected checkbox for ``user_name``."""
        checkboxes = self._formal_named_attendee_checkboxes(user_name)
        if len(checkboxes) != 1:
            logger.error(
                "正式模式无法唯一定位观演人同行勾选框: name=%s matches=%d",
                user_name,
                len(checkboxes),
            )
            return False
        return bool(self._bot._is_checkbox_selected(checkboxes[0]))

    def _ensure_formal_attendees_selected(self):
        """Fail closed unless every configured name is selected in its own row."""
        bot = self._bot
        users = [
            user.strip()
            for user in (self._config.users or [])
            if isinstance(user, str) and user.strip()
        ]
        if len(users) != len(self._config.users or []) or not users:
            logger.error("正式模式缺少有效观演人姓名，已停止自动提交")
            return False
        if len(set(users)) != len(users):
            logger.error("正式模式观演人姓名重复，无法唯一映射勾选框")
            return False

        attendee_section_visible = bot._has_element(
            ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("实名观演人")',
        )
        if not attendee_section_visible:
            logger.error("正式模式未确认实名观演人区域，已停止自动提交")
            return False

        for user_name in users:
            checkboxes = self._formal_named_attendee_checkboxes(user_name)
            if len(checkboxes) != 1:
                logger.error(
                    "正式模式无法唯一定位观演人同行勾选框: name=%s matches=%d",
                    user_name,
                    len(checkboxes),
                )
                return False
            checkbox = checkboxes[0]
            if bot._is_checkbox_selected(checkbox):
                continue

            # Exactly one click is allowed here.  If the RPC result or the
            # resulting checked state is uncertain, fail closed instead of
            # issuing a fallback click that could toggle an already-selected row.
            try:
                bot._click_element_center(checkbox, duration=35)
            except Exception as exc:
                logger.warning(
                    "正式模式观演人点击结果不确定，停止自动提交: name=%s err=%s",
                    user_name,
                    exc,
                )
                return False

            verify_deadline = _monotonic() + 0.35
            while True:
                refreshed = self._formal_named_attendee_checkboxes(user_name)
                if len(refreshed) > 1:
                    logger.error(
                        "正式模式点击后观演人映射变得不唯一: name=%s matches=%d",
                        user_name,
                        len(refreshed),
                    )
                    return False
                if (
                    len(refreshed) == 1
                    and bot._is_checkbox_selected(refreshed[0])
                ):
                    break
                if _monotonic() >= verify_deadline:
                    logger.error(
                        "正式模式点击后未确认观演人已勾选: name=%s",
                        user_name,
                    )
                    return False
                time.sleep(0.04)

        # Re-read every named row after all clicks.  A total selected count is
        # never sufficient proof because it may belong to different attendees.
        for user_name in users:
            if not self._verify_formal_named_attendee(user_name):
                logger.error(
                    "正式模式最终观演人校验失败: name=%s",
                    user_name,
                )
                return False
        return True

    def _ensure_attendees_selected_on_confirm_page(
        self, require_attendee_section=False
    ):
        """Make sure required attendee checkboxes are selected before submit.

        NOTE: internal calls go through ``self._bot`` so that test-level patches
        on DamaiBot delegate methods (e.g. ``bot._attendee_checkbox_elements``)
        are honoured.
        """
        bot = self._bot
        required_count = max(1, len(self._config.users or []))

        if self._config.if_commit_order:
            return self._ensure_formal_attendees_selected()

        if self._config.rush_mode and not self._config.if_commit_order:
            cached_coords = bot._get_hot_coord("attendee_checkboxes")
            if cached_coords:
                logger.info(
                    f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）"
                )
                logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
                for coords in cached_coords[:required_count]:
                    bot._click_coordinates(*coords)
                return True

            # Poll for checkbox elements — confirm page may still be rendering.
            checkbox_elements = []
            poll_deadline = _monotonic() + 3.0
            while _monotonic() < poll_deadline:
                checkbox_elements = bot._attendee_checkbox_elements()
                if checkbox_elements:
                    break
                time.sleep(0.05)
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
                layout_digest = getattr(
                    bot, "_hot_cache_layout_digests", {}
                ).get("confirm") or f"confirm-checkboxes:{_coords!r}"
                bot._set_hot_cache_context(
                    scope="confirm",
                    page_state="order_confirm_page",
                    layout_digest=layout_digest,
                )
                bot._put_hot_coord(
                    "attendee_checkboxes",
                    _coords,
                    scope="confirm",
                    page_state="order_confirm_page",
                    layout_digest=layout_digest,
                )

            selected_count = bot._attendee_selected_count(
                checkbox_elements, use_source_fallback=False
            )
            if selected_count >= required_count:
                return True

            logger.info(
                f"检测到观演人未选择完成，尝试自动补选（已选 {selected_count}/{required_count}）"
            )
            logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
            clicked_count = 0
            for checkbox in checkbox_elements[:required_count]:
                if bot._click_attendee_checkbox_fast(checkbox):
                    clicked_count += 1
            if clicked_count < required_count:
                logger.warning(
                    f"观演人选择不足（需要 {required_count} 位，当前 {clicked_count} 位）"
                )
                return False
            return True

        checkbox_elements = bot._attendee_checkbox_elements()

        if self._config.rush_mode:
            if not checkbox_elements:
                return not require_attendee_section
        else:
            attendee_section_visible = bot._has_element(
                ANDROID_UIAUTOMATOR,
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

        logger.info(
            f"检测到观演人未选择完成，尝试自动补选（已选 {selected_count}/{required_count}）"
        )

        unmatched_users = []
        for user_name in self._config.users or []:
            if selected_count >= required_count:
                break
            if bot._select_attendee_checkbox_by_name(user_name):
                selected_count = bot._attendee_selected_count()
            else:
                unmatched_users.append(user_name)

        if unmatched_users and selected_count < required_count:
            logger.warning(
                f"未能按姓名定位观演人: {'、'.join(unmatched_users)}，将尝试按勾选框兜底"
            )

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
            logger.warning(
                f"观演人选择不足（需要 {required_count} 位，当前 {selected_count} 位）"
            )
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
