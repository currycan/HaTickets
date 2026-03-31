# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

import re
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    from mobile.config import Config
except ImportError:
    from config import Config

try:
    from mobile.item_resolver import (
        DamaiItemResolver,
        DamaiItemResolveError,
        city_keyword,
        extract_item_id,
        normalize_text,
    )
except ImportError:
    from item_resolver import (
        DamaiItemResolver,
        DamaiItemResolveError,
        city_keyword,
        extract_item_id,
        normalize_text,
    )

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

_MAGICK_BIN = shutil.which("magick")
_TESSERACT_BIN = shutil.which("tesseract")
_PRICE_UNAVAILABLE_TAGS = {"无票", "缺货", "缺货登记", "售罄", "已售罄", "不可选", "暂不可售"}
_CTA_READY_KEYWORDS = (
    "立即购买", "立即抢票", "立即预定", "选座购买", "购买", "抢票", "预定", "提交订单", "去结算", "确定"
)
_CTA_BLOCKED_KEYWORDS = ("预约", "预售", "即将开抢", "待开售", "未开售", "倒计时", "无票", "售罄", "缺货")


class DamaiBot:
    def __init__(self, config=None, setup_driver=True):
        self.config = config or Config.load_config()
        self.item_detail = None
        self.driver = None
        self.wait = None
        self._terminal_failure_reason = None
        self._last_run_outcome = None
        self._prepare_runtime_config()
        if setup_driver:
            self._setup_driver()

    def _set_terminal_failure(self, reason):
        """Mark the current failure as non-retriable."""
        self._terminal_failure_reason = reason

    def _set_run_outcome(self, outcome):
        """Record the terminal outcome for the latest run attempt."""
        self._last_run_outcome = outcome

    def _execution_mode_key(self):
        """Return the current execution mode key."""
        if self.config.probe_only:
            return "probe"
        if not self.config.if_commit_order:
            return "validation"
        return "submit"

    def _execution_mode_label(self):
        """Return a short user-facing label for the current execution mode."""
        labels = {
            "probe": "安全探测",
            "validation": "开发验证",
            "submit": "正式抢票",
        }
        return labels[self._execution_mode_key()]

    def _execution_mode_description(self):
        """Return a user-facing description for the current execution mode."""
        descriptions = {
            "probe": "只检查目标演出页，不会点击“立即购票”",
            "validation": "会继续进入确认页并勾选观演人，但不会点击“立即提交”；这是开发调试路径",
            "submit": "会尝试提交订单",
        }
        return descriptions[self._execution_mode_key()]

    def _log_execution_mode(self):
        """Emit a clear log that tells the user what this run will actually do."""
        logger.info(
            f"开始执行{self._execution_mode_label()}：{self._execution_mode_description()}"
        )

    def _log_success_outcome(self, retry_prefix=""):
        """Emit a success log message that matches the actual run outcome."""
        prefix = f"{retry_prefix}" if retry_prefix else ""
        outcome_messages = {
            "probe_ready": "探测成功：已到目标演出页，购票控件已就绪",
            "validation_ready": "开发验证成功：已到订单确认页，未提交订单",
            "order_submitted": "抢票成功：已提交订单",
            "order_pending_payment": "抢票成功：检测到未支付订单，请立即前往支付完成下单",
            "order_flow_completed": "抢票流程完成：已执行提交，等待后续结果确认",
        }
        logger.info(f"{prefix}{outcome_messages.get(self._last_run_outcome, '本轮执行成功')}")

    def _prepare_runtime_config(self):
        """Resolve item metadata before creating the Appium session."""
        if self.config.item_url and not self.config.item_id:
            self.config.item_id = extract_item_id(self.config.item_url)

        if not (self.config.item_url or self.config.item_id):
            return

        try:
            self.item_detail = DamaiItemResolver().fetch_item_detail(
                item_url=self.config.item_url,
                item_id=self.config.item_id,
            )
        except (DamaiItemResolveError, ValueError) as exc:
            if self.config.keyword:
                logger.warning(f"解析 item_url/item_id 失败，继续使用现有 keyword: {exc}")
                return
            raise

        self.config.item_id = self.item_detail.item_id
        if not self.config.keyword:
            self.config.keyword = self.item_detail.search_keyword
            logger.info(f"已根据 item 链接自动生成搜索关键词: {self.config.keyword}")

        resolved_city = self.item_detail.city_keyword or city_keyword(self.item_detail.venue_city_name)
        config_city = city_keyword(self.config.city)
        if resolved_city and config_city and normalize_text(resolved_city) != normalize_text(config_city):
            raise ValueError(
                f"配置 city={self.config.city!r} 与 item_url 指向城市={self.item_detail.city_name!r} 不一致"
            )

        logger.info(
            f"已解析 itemId={self.item_detail.item_id}，演出={self.item_detail.item_name}，"
            f"城市={self.item_detail.city_name}，时间={self.item_detail.show_time}，"
            f"票价范围={self.item_detail.price_range}"
        )

    def _build_capabilities(self):
        """根据配置构造 Appium capabilities。"""
        capabilities = {
            "platformName": "Android",  # 操作系统
            "deviceName": self.config.device_name,  # 模拟器或真机名称
            "appPackage": self.config.app_package,  # app 包名
            "appActivity": self.config.app_activity,  # app 启动 Activity
            "unicodeKeyboard": True,  # 支持 Unicode 输入
            "resetKeyboard": True,  # 隐藏键盘
            "noReset": True,  # 不重置 app
            "newCommandTimeout": 6000,  # 超时时间
            "automationName": "UiAutomator2",  # 使用 uiautomator2
            "skipServerInstallation": False,  # 跳过服务器安装
            "ignoreHiddenApiPolicyError": True,  # 忽略隐藏 API 策略错误
            "disableWindowAnimation": True,  # 禁用窗口动画
            # 优化性能配置
            "mjpegServerFramerate": 1,  # 降低截图帧率
            "shouldTerminateApp": False,
            "adbExecTimeout": 20000,
        }

        if self.config.udid:
            capabilities["udid"] = self.config.udid

        if self.config.platform_version:
            capabilities["platformVersion"] = self.config.platform_version

        return capabilities

    def _list_connected_device_ids(self):
        """Return adb-connected Android device ids, or None when adb is unavailable."""
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

        device_ids = []
        for line in result.stdout.splitlines():
            if "\tdevice" not in line:
                continue
            device_ids.append(line.split("\t", 1)[0].strip())
        return device_ids

    def _read_device_android_version(self, udid):
        """Return the Android version reported by adb for the target device."""
        try:
            result = subprocess.run(
                ["adb", "-s", udid, "shell", "getprop", "ro.build.version.release"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

        return result.stdout.strip() or None

    def _preflight_validate_device_target(self):
        """Validate the configured udid/platform version before creating Appium session."""
        if not self.config.udid:
            return

        connected_devices = self._list_connected_device_ids()
        if connected_devices is None:
            return

        if self.config.udid not in connected_devices:
            connected_text = "、".join(connected_devices) if connected_devices else "无"
            raise ValueError(
                f"当前配置 udid={self.config.udid!r} 不在已连接设备列表中（当前已连接: {connected_text}）。"
                "请先执行 adb devices，并把 mobile/config.jsonc 中的 udid 改成当前真机序列号。"
            )

        if not self.config.platform_version:
            return

        actual_version = self._read_device_android_version(self.config.udid)
        if actual_version and actual_version != self.config.platform_version:
            raise ValueError(
                f"当前配置 platform_version={self.config.platform_version!r} 与设备实际版本 {actual_version!r} 不一致。"
                "请更新 mobile/config.jsonc 中的 platform_version。"
            )

    def _setup_driver(self):
        """初始化驱动配置"""
        self._preflight_validate_device_target()
        device_app_info = AppiumOptions()
        device_app_info.load_capabilities(self._build_capabilities())
        self.driver = webdriver.Remote(self.config.server_url, options=device_app_info)

        # 更激进的性能优化设置
        self.driver.update_settings({
            "waitForIdleTimeout": 0,  # 空闲时间，0 表示不等待，让 UIAutomator2 不等页面“空闲”再返回
            "actionAcknowledgmentTimeout": 0,  # 禁止等待动作确认
            "keyInjectionDelay": 0,  # 禁止输入延迟
            "waitForSelectorTimeout": 300,  # 从500减少到300ms
            "ignoreUnimportantViews": False,  # 保持false避免元素丢失
            "allowInvisibleElements": True,
            "enableNotificationListener": False,  # 禁用通知监听
        })

        # 极短的显式等待，抢票场景下速度优先
        self.wait = WebDriverWait(self.driver, 2)  # 从5秒减少到2秒

    def ultra_fast_click(self, by, value, timeout=1.5):
        """超快速点击 - 适合抢票场景"""
        try:
            # 直接查找并点击，不等待可点击状态
            el = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            # 使用坐标点击更快
            rect = el.rect
            x = rect['x'] + rect['width'] // 2
            y = rect['y'] + rect['height'] // 2
            self.driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 50  # 极短点击时间
            })
            return True
        except TimeoutException:
            return False

    def batch_click(self, elements_info, delay=0.1):
        """批量点击操作"""
        for by, value in elements_info:
            if self.ultra_fast_click(by, value):
                if delay > 0:
                    time.sleep(delay)
            else:
                logger.warning(f"点击失败: {value}")

    def ultra_batch_click(self, elements_info, timeout=2):
        """超快批量点击 - 带等待机制"""
        coordinates = []
        # 批量收集坐标，带超时等待
        for by, value in elements_info:
            try:
                # 等待元素出现
                el = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                rect = el.rect
                x = rect['x'] + rect['width'] // 2
                y = rect['y'] + rect['height'] // 2
                coordinates.append((x, y, value))
            except TimeoutException:
                logger.warning(f"超时未找到用户: {value}")
            except Exception as e:
                logger.error(f"查找用户失败 {value}: {e}")
        logger.info(f"成功找到 {len(coordinates)} 个用户")
        # 快速连续点击
        for i, (x, y, value) in enumerate(coordinates):
            self.driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 30
            })
            if i < len(coordinates) - 1:
                time.sleep(0.01)
            logger.debug(f"点击用户: {value}")
        return len(coordinates)

    def smart_wait_and_click(self, by, value, backup_selectors=None, timeout=1.5):
        """智能等待和点击 - 支持备用选择器"""
        selectors = [(by, value)]
        if backup_selectors:
            selectors.extend(backup_selectors)

        for selector_by, selector_value in selectors:
            try:
                el = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((selector_by, selector_value))
                )
                rect = el.rect
                x = rect['x'] + rect['width'] // 2
                y = rect['y'] + rect['height'] // 2
                self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y, "duration": 50})
                return True
            except TimeoutException:
                continue
        return False

    def smart_wait_for_element(self, by, value, backup_selectors=None, timeout=1.5):
        """智能等待元素出现 - 支持备用选择器，但不执行点击。"""
        selectors = [(by, value)]
        if backup_selectors:
            selectors.extend(backup_selectors)

        for selector_by, selector_value in selectors:
            try:
                WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((selector_by, selector_value))
                )
                return True
            except TimeoutException:
                continue
        return False

    def wait_for_page_state(self, expected_states, timeout=5, poll_interval=0.2):
        """轮询等待页面进入指定状态，返回最后一次探测结果。"""
        deadline = time.time() + timeout
        last_probe = None

        while time.time() < deadline:
            last_probe = self.probe_current_page()
            if last_probe["state"] in expected_states:
                return last_probe
            time.sleep(poll_interval)

        return last_probe if last_probe is not None else self.probe_current_page()

    def _has_any_element(self, selectors):
        """Return True if any selector matches immediately."""
        for by, value in selectors:
            if self._has_element(by, value):
                return True
        return False

    def _wait_for_purchase_entry_result(self, timeout=1.2, poll_interval=0.04):
        """Wait for the detail-page CTA to open either sku or confirm page."""
        submit_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
            (By.XPATH, '//*[contains(@text,"提交")]'),
        ]
        sku_selectors = [
            (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"),
            (By.ID, "cn.damai:id/layout_sku"),
            (By.ID, "cn.damai:id/sku_contanier"),
            (By.ID, "cn.damai:id/layout_price"),
            (By.ID, "cn.damai:id/tv_price_name"),
        ]

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._has_any_element(submit_selectors):
                return {"state": "order_confirm_page", "submit_button": True}
            if self._has_any_element(sku_selectors):
                return {
                    "state": "sku_page",
                    "price_container": True,
                    "reservation_mode": self.is_reservation_sku_mode(),
                }
            time.sleep(poll_interval)

        return self.probe_current_page()

    def _wait_for_submit_ready(self, timeout=1.6, poll_interval=0.04):
        """Wait until the confirm-page submit button appears."""
        if self.config.rush_mode:
            # 极速模式下避免高成本 XPath 轮询，优先轻量选择器。
            submit_selectors = [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("确认购买")'),
                (By.ID, "cn.damai:id/checkbox"),
            ]
        else:
            submit_selectors = [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                (By.XPATH, '//*[contains(@text,"提交")]'),
            ]

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._has_any_element(submit_selectors):
                return True
            time.sleep(poll_interval)

        return False

    def _attendee_required_count_on_confirm_page(self):
        """Infer how many attendees must be selected on the confirm page."""
        hint_text = self._safe_element_text(
            self.driver,
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("仅需选择")',
        )
        match = re.search(r"仅需选择\s*(\d+)\s*位", hint_text or "")
        if match:
            return max(1, int(match.group(1)))
        return max(1, len(self.config.users or []))

    def _attendee_checkbox_elements(self):
        try:
            return self.driver.find_elements(By.ID, "cn.damai:id/checkbox")
        except Exception:
            return []

    @staticmethod
    def _is_checkbox_selected(checkbox):
        try:
            return str(checkbox.get_attribute("checked")).lower() == "true"
        except Exception:
            return False

    def _attendee_selected_count(self, checkbox_elements=None, use_source_fallback=True):
        """Count selected attendee checkboxes, with XML fallback for flaky checked attrs."""
        elements = checkbox_elements if checkbox_elements is not None else self._attendee_checkbox_elements()
        selected_count = sum(1 for checkbox in elements if self._is_checkbox_selected(checkbox))
        if selected_count > 0:
            return selected_count
        if (self.config.rush_mode and not self.config.if_commit_order) and not use_source_fallback:
            # 开发验证极速模式下避免 page_source 级别扫描带来的高延迟。
            return selected_count

        try:
            source = self.driver.page_source or ""
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
        use_fallback = not (self.config.rush_mode and not self.config.if_commit_order)
        before_selected = self._attendee_selected_count(use_source_fallback=use_fallback)
        click_actions = [
            lambda: self._click_element_center(checkbox, duration=35),
            lambda: checkbox.click(),
            lambda: self._burst_click_element_center(checkbox, count=2, interval_ms=30, duration=30),
        ]

        for action in click_actions:
            try:
                action()
            except Exception:
                continue
            time.sleep(0.05)
            if self._is_checkbox_selected(checkbox):
                return True
            if self._attendee_selected_count(use_source_fallback=use_fallback) > before_selected:
                return True
        return False

    def _click_attendee_checkbox_fast(self, checkbox):
        """Low-latency checkbox click path for rush-mode validation."""
        click_actions = [
            lambda: checkbox.click(),
            lambda: self._click_element_center(checkbox, duration=28),
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
                checkboxes = self.driver.find_elements(By.XPATH, checkbox_xpath)
            except Exception:
                checkboxes = []

            for checkbox in checkboxes:
                if self._is_checkbox_selected(checkbox):
                    return True
                if self._click_attendee_checkbox(checkbox):
                    return True
        return False

    def _ensure_attendees_selected_on_confirm_page(self, require_attendee_section=False):
        """Make sure required attendee checkboxes are selected before submit."""
        attendee_section_visible = self._has_element(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("实名观演人")',
        )
        checkbox_elements = self._attendee_checkbox_elements()

        if not attendee_section_visible:
            return not require_attendee_section
        if not checkbox_elements:
            logger.warning("确认页存在观演人区域，但未找到可勾选观演人，请手动检查")
            return False

        required_count = self._attendee_required_count_on_confirm_page()
        selected_count = self._attendee_selected_count(checkbox_elements)
        if selected_count >= required_count:
            return True

        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 {selected_count}/{required_count}）")

        if self.config.rush_mode and not self.config.if_commit_order:
            logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
            provisional_selected = selected_count
            for round_index in range(3):
                current_checkboxes = checkbox_elements if round_index == 0 else self._attendee_checkbox_elements()
                for checkbox in current_checkboxes:
                    if selected_count >= required_count:
                        break
                    if self._is_checkbox_selected(checkbox):
                        continue
                    clicked = self._click_attendee_checkbox_fast(checkbox)
                    if not clicked:
                        continue
                    provisional_selected += 1
                    selected_count = max(selected_count, provisional_selected)
                if selected_count >= required_count:
                    break
                time.sleep(0.08)
            if selected_count < required_count:
                logger.warning(f"观演人选择不足（需要 {required_count} 位，当前 {selected_count} 位）")
                return False
            return True

        unmatched_users = []
        for user_name in self.config.users or []:
            if selected_count >= required_count:
                break
            if self._select_attendee_checkbox_by_name(user_name):
                selected_count = self._attendee_selected_count()
            else:
                unmatched_users.append(user_name)

        if unmatched_users and selected_count < required_count:
            logger.warning(f"未能按姓名定位观演人: {'、'.join(unmatched_users)}，将尝试按勾选框兜底")

        if selected_count < required_count:
            for checkbox in self._attendee_checkbox_elements():
                if selected_count >= required_count:
                    break
                if self._is_checkbox_selected(checkbox):
                    continue
                if not self._click_attendee_checkbox(checkbox):
                    continue
                selected_count = self._attendee_selected_count()

        if selected_count < required_count:
            logger.warning(f"观演人选择不足（需要 {required_count} 位，当前 {selected_count} 位）")
            return False
        return True

    def _has_element(self, by, value):
        """快速判断元素是否存在，不等待点击状态。"""
        try:
            return len(self.driver.find_elements(by=by, value=value)) > 0
        except Exception:
            return False

    def _get_current_activity(self):
        """获取当前 Activity，失败时返回空字符串。"""
        try:
            return self.driver.current_activity or ""
        except Exception:
            return ""

    def _click_element_center(self, element, duration=50):
        """Click the center point of an element via gesture."""
        rect = element.rect
        x = rect["x"] + rect["width"] // 2
        y = rect["y"] + rect["height"] // 2
        self._click_coordinates(x, y, duration=duration)

    def _click_coordinates(self, x, y, duration=50):
        """Click a fixed screen coordinate via gesture."""
        self.driver.execute_script(
            "mobile: clickGesture",
            {"x": x, "y": y, "duration": duration},
        )

    def _press_keycode_safe(self, keycode, context=""):
        """Press an Android keycode with error handling to avoid hard crashes."""
        try:
            self.driver.press_keycode(keycode)
            return True
        except Exception as exc:
            suffix = f"（{context}）" if context else ""
            logger.warning(f"按键事件失败{suffix}: keycode={keycode}, err={exc}")
            return False

    def _burst_click_element_center(self, element, count=2, interval_ms=35, duration=30):
        """Click an element center repeatedly for low-latency race-mode actions."""
        for attempt in range(count):
            self._click_element_center(element, duration=duration)
            if attempt < count - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000)

    def _burst_click_coordinates(self, x, y, count=2, interval_ms=35, duration=30):
        """Click a fixed coordinate repeatedly."""
        for attempt in range(count):
            self._click_coordinates(x, y, duration=duration)
            if attempt < count - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000)

    def _get_buy_button_coordinates(self):
        """Capture the current buy/confirm button coordinates before the hot path needs them."""
        selectors = [
            (By.ID, "btn_buy_view"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")'),
        ]
        for by, value in selectors:
            try:
                elements = self.driver.find_elements(by=by, value=value)
            except Exception:
                continue
            if not elements:
                continue
            rect = elements[0].rect
            return (
                rect["x"] + rect["width"] // 2,
                rect["y"] + rect["height"] // 2,
            )
        return None

    def _get_price_option_coordinates_by_config_index(self):
        """Capture the configured price card center so rush mode can tap by coordinate."""
        try:
            price_container = self.driver.find_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
        except Exception:
            return None

        try:
            target_card = price_container.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                (
                    'new UiSelector().className("android.widget.FrameLayout")'
                    f'.clickable(true).instance({self.config.price_index})'
                ),
            )
            rect = target_card.rect
            return (
                rect["x"] + rect["width"] // 2,
                rect["y"] + rect["height"] // 2,
            )
        except Exception:
            pass

        try:
            cards = price_container.find_elements(By.CLASS_NAME, "android.widget.FrameLayout")
        except Exception:
            return None

        if not isinstance(cards, (list, tuple)):
            return None

        clickable_cards = [card for card in cards if str(card.get_attribute("clickable")).lower() == "true"]
        if not (0 <= self.config.price_index < len(clickable_cards)):
            return None

        rect = clickable_cards[self.config.price_index].rect
        return (
            rect["x"] + rect["width"] // 2,
            rect["y"] + rect["height"] // 2,
        )

    def _safe_element_text(self, container, by, value):
        """Read the first child text if present."""
        try:
            elements = container.find_elements(by=by, value=value)
        except Exception:
            return ""

        for element in elements:
            text = self._normalize_element_text(getattr(element, "text", ""))
            if text:
                return text
        return ""

    def _safe_element_texts(self, container, by, value):
        """Read all non-empty child texts if present."""
        try:
            elements = container.find_elements(by=by, value=value)
        except Exception:
            return []

        texts = []
        seen = set()
        for element in elements:
            text = self._normalize_element_text(getattr(element, "text", ""))
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    def _collect_descendant_texts(self, container):
        """Collect all visible descendant texts under a container."""
        texts = []
        seen = set()
        try:
            descendants = container.find_elements(By.XPATH, ".//*")
        except Exception:
            descendants = []

        for element in descendants:
            try:
                text = self._normalize_element_text(getattr(element, "text", ""))
            except Exception:
                text = ""
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    @staticmethod
    def _normalize_element_text(value):
        """Normalize UI text values; ignore non-string placeholders from mocked elements."""
        if isinstance(value, str):
            return value.strip()
        return ""

    def _build_compound_price_text(self, container):
        """Build a human-readable price string from split price fields."""
        prefix_ids = (
            "cn.damai:id/bricks_dm_common_price_prefix",
            "cn.damai:id/project_price_char",
        )
        value_ids = (
            "cn.damai:id/bricks_dm_common_price_des",
            "cn.damai:id/project_price_pre",
            "cn.damai:id/project_price_suffix",
        )
        suffix_ids = (
            "cn.damai:id/bricks_dm_common_price_suffix",
        )

        prefix = ""
        value_parts = []
        suffix = ""

        for resource_id in prefix_ids:
            prefix = prefix or self._safe_element_text(container, By.ID, resource_id)
        for resource_id in value_ids:
            value_parts.extend(self._safe_element_texts(container, By.ID, resource_id))
        for resource_id in suffix_ids:
            suffix = suffix or self._safe_element_text(container, By.ID, resource_id)

        value = "".join(value_parts).strip()
        compound = f"{prefix}{value}{suffix}".strip()
        if compound == "¥":
            compound = ""
        if compound and prefix == "¥" and suffix == "起":
            return compound
        if value and value.replace(".", "", 1).isdigit() and not suffix:
            return f"{value}元"
        if compound and compound.startswith("¥"):
            return compound.replace("¥", "¥", 1)
        return compound

    def _price_option_text_from_descendants(self, texts):
        """Collapse descendant texts into a price label."""
        if not texts:
            return ""

        filtered = []
        ignored = {"可预约", "预售", "无票", "已预约", "缺货", "惠", "荐", "热", "售罄"}
        for text in texts:
            value = text.strip()
            if not value or value in ignored:
                continue
            filtered.append(value)

        if not filtered:
            return ""

        merged = "".join(filtered)
        if merged.isdigit():
            return f"{merged}元"
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]+[0-9]{2,5}", merged):
            return f"{merged}元"
        if re.fullmatch(r"[0-9]{2,5}[A-Za-z\u4e00-\u9fff]+", merged):
            return merged
        return merged

    def _normalize_ocr_price_text(self, ocr_output):
        """Extract the leading ticket price from noisy OCR output."""
        normalized_text = ocr_output if isinstance(ocr_output, str) else ""
        digits = "".join(re.findall(r"\d", normalized_text))
        if len(digits) >= 4:
            leading_four = int(digits[:4])
            if 1000 <= leading_four <= 1999:
                return f"{leading_four}元"
        if len(digits) >= 3:
            leading_three = int(digits[:3])
            if 100 <= leading_three <= 999:
                return f"{leading_three}元"
        return ""

    def _ocr_price_text_from_card(self, screenshot_path, rect):
        """OCR the price number from a price-card crop as a last-resort fallback."""
        if not (_MAGICK_BIN and _TESSERACT_BIN and screenshot_path and rect):
            return ""

        crop_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                crop_path = tmp_file.name

            subprocess.run(
                [
                    _MAGICK_BIN,
                    screenshot_path,
                    "-crop", f"{rect['width']}x{rect['height']}+{rect['x']}+{rect['y']}",
                    "-resize", "300%",
                    crop_path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [_TESSERACT_BIN, crop_path, "stdout", "-l", "eng+snum", "--psm", "6"],
                check=True,
                capture_output=True,
                text=True,
            )
            return self._normalize_ocr_price_text(result.stdout)
        except Exception:
            return ""
        finally:
            if crop_path and os.path.exists(crop_path):
                try:
                    os.unlink(crop_path)
                except OSError:
                    pass

    def _extract_price_digits(self, text):
        """Extract the numeric portion of a ticket price label."""
        normalized_text = text if isinstance(text, str) else ""
        match = re.search(r"([1-9]\d{1,4})", normalized_text)
        if match:
            return int(match.group(1))
        return None

    def _price_text_matches_target(self, text):
        """Check whether a visible price label matches the configured price."""
        normalized_target = normalize_text(self.config.price)
        normalized_text = normalize_text(text)
        if normalized_target and normalized_text:
            if normalized_target in normalized_text or normalized_text in normalized_target:
                return True

        target_digits = self._extract_price_digits(self.config.price)
        text_digits = self._extract_price_digits(text)
        return target_digits is not None and target_digits == text_digits

    def _is_price_option_available(self, option):
        """Return whether a visible price option is actually selectable."""
        tag = (option.get("tag") or "").strip()
        return tag not in _PRICE_UNAVAILABLE_TAGS

    def _click_visible_price_option(self, card_index):
        """Click a visible price card by its clickable-card index."""
        try:
            price_container = self.driver.find_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
            cards = price_container.find_elements(By.CLASS_NAME, "android.widget.FrameLayout")
        except Exception:
            return False

        if not isinstance(cards, (list, tuple)):
            return False
        clickable_cards = [card for card in cards if str(card.get_attribute("clickable")).lower() == "true"]
        if 0 <= card_index < len(clickable_cards):
            self._click_element_center(clickable_cards[card_index], duration=30)
            return True
        return False

    def _click_price_option_by_config_index(self, burst=False, coords=None):
        """Click the configured price card index directly without reading ticket texts."""
        target_coords = coords or self._get_price_option_coordinates_by_config_index()
        if not target_coords:
            return False
        if burst:
            self._burst_click_coordinates(*target_coords, count=2, interval_ms=25, duration=25)
        else:
            self._click_coordinates(*target_coords, duration=30)
        logger.info(f"通过配置索引直接选择票价: price_index={self.config.price_index}")
        return True

    def _select_price_option_fast(self, cached_coords=None):
        """Use config-driven, low-latency ticket selection before OCR-heavy fallbacks."""
        if self.config.rush_mode and self._click_price_option_by_config_index(burst=True, coords=cached_coords):
            return True

        visible_options = self.get_visible_price_options(allow_ocr=False)

        if visible_options:
            indexed_option = next((option for option in visible_options if option["index"] == self.config.price_index), None)
            if indexed_option:
                if not self._is_price_option_available(indexed_option):
                    logger.warning(
                        f"配置索引对应票档当前不可选: {indexed_option.get('text') or '(未识别)'} "
                        f"[{indexed_option.get('tag') or '不可售'}]"
                    )
                    return False
                if not indexed_option.get("text") or self._price_text_matches_target(indexed_option.get("text") or ""):
                    if self._click_visible_price_option(indexed_option["index"]):
                        logger.info(
                            f"通过配置索引快速选择票价: {indexed_option.get('text') or self.config.price} "
                            f"(price_index={self.config.price_index})"
                        )
                        return True
            elif self._click_price_option_by_config_index():
                return True

            matched_options = [
                option for option in visible_options
                if self._price_text_matches_target(option.get("text") or "")
            ]
            for option in matched_options:
                if not self._is_price_option_available(option):
                    logger.warning(
                        f"目标票档当前不可选: {option.get('text') or '(未识别)'} [{option.get('tag') or '不可售'}]"
                    )
                    return False
                if self._click_visible_price_option(option["index"]):
                    logger.info(
                        f"通过可见票档快速匹配选择票价: {option.get('text') or self.config.price} "
                        f"(index={option['index']})"
                    )
                    return True

        price_text_selector = f'new UiSelector().textContains("{self.config.price}")'
        if self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.35):
            logger.info(f"通过文本快速匹配选择票价: {self.config.price}")
            return True

        if self._click_price_option_by_config_index():
            return True

        return None

    def _select_price_option(self, cached_coords=None):
        """Select the configured price using fast config-driven logic first, then OCR-heavy fallbacks."""
        fast_result = self._select_price_option_fast(cached_coords=cached_coords)
        if fast_result is not None:
            return fast_result

        visible_options = self.get_visible_price_options()
        matched_options = [option for option in visible_options if self._price_text_matches_target(option.get("text") or "")]

        for option in matched_options:
            if not self._is_price_option_available(option):
                logger.warning(
                    f"目标票档当前不可选: {option.get('text') or '(未识别)'} [{option.get('tag') or '不可售'}]"
                )
                return False
            if self._click_visible_price_option(option["index"]):
                logger.info(
                    f"通过可见票档匹配选择票价: {option.get('text') or self.config.price} "
                    f"(index={option['index']}, source={option.get('source', 'ui')})"
                )
                return True

        if visible_options:
            indexed_option = next((option for option in visible_options if option["index"] == self.config.price_index), None)
            if indexed_option and self._is_price_option_available(indexed_option):
                if self._click_visible_price_option(indexed_option["index"]):
                    logger.info(
                        f"文本匹配未命中，使用当前可见票档索引选择: {indexed_option.get('text') or self.config.price} "
                        f"(price_index={self.config.price_index})"
                    )
                    return True
            elif indexed_option and not self._is_price_option_available(indexed_option):
                logger.warning(
                    f"配置索引对应票档当前不可选: {indexed_option.get('text') or '(未识别)'} "
                    f"[{indexed_option.get('tag') or '不可售'}]"
                )
                return False

        price_text_selector = f'new UiSelector().textContains("{self.config.price}")'
        if self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, price_text_selector, timeout=0.8):
            logger.info(f"通过文本匹配选择票价: {self.config.price}")
            return True

        logger.info(f"文本匹配失败，使用索引选择票价: price_index={self.config.price_index}")
        try:
            price_container = self.driver.find_element(By.ID, 'cn.damai:id/project_detail_perform_price_flowlayout')
            target_price = price_container.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().className("android.widget.FrameLayout").index({self.config.price_index}).clickable(true)'
            )
            self.driver.execute_script('mobile: clickGesture', {'elementId': target_price.id})
            return True
        except Exception as e:
            logger.warning(f"票价选择失败，启动备用方案: {e}")
            try:
                price_container = self.wait.until(
                    EC.presence_of_element_located((By.ID, 'cn.damai:id/project_detail_perform_price_flowlayout')))
                target_price = price_container.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().className("android.widget.FrameLayout").index({self.config.price_index}).clickable(true)'
                )
                self.driver.execute_script('mobile: clickGesture', {'elementId': target_price.id})
                return True
            except Exception as backup_error:
                logger.warning(f"备用票价选择也失败: {backup_error}")
                return False

    def _keyword_tokens(self):
        """Split the configured keyword into reusable fuzzy-match tokens."""
        keyword = self.config.keyword or ""
        tokens = []
        for raw in re.split(r"[\s,，、|/]+", keyword):
            token = normalize_text(raw)
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
        return tokens

    def _get_detail_title_text(self):
        """Read title text from detail/sku pages."""
        title = ""
        try:
            title = self._safe_element_text(self.driver, By.ID, "cn.damai:id/title_tv")
        except Exception:
            title = ""

        if title:
            return title

        title_parts = []
        for resource_id in ("cn.damai:id/project_title_tv1", "cn.damai:id/project_title_tv2"):
            part = self._safe_element_text(self.driver, By.ID, resource_id)
            if part:
                title_parts.append(part.strip())

        return "".join(title_parts).strip()

    def _title_matches_target(self, title_text):
        """Check whether a page or search result title matches the configured target."""
        normalized_title = normalize_text(title_text)
        if not normalized_title:
            return False

        candidates = []
        if self.item_detail:
            candidates.extend([self.item_detail.item_name, self.item_detail.item_name_display])
        if self.config.target_title:
            candidates.append(self.config.target_title)
        if self.config.keyword:
            candidates.append(self.config.keyword)

        for candidate in candidates:
            normalized_candidate = normalize_text(candidate)
            if not normalized_candidate:
                continue
            if normalized_candidate in normalized_title or normalized_title in normalized_candidate:
                return True

        keyword_tokens = self._keyword_tokens()
        if keyword_tokens and all(token in normalized_title for token in keyword_tokens):
            return True

        return False

    def _current_page_matches_target(self, page_probe):
        """Check if the current detail/sku page already points at the expected event."""
        if page_probe["state"] not in {"detail_page", "sku_page"}:
            return False

        if not self.item_detail and not self.config.target_title and not self.config.keyword:
            return True

        return self._title_matches_target(self._get_detail_title_text())

    def _exit_non_target_event_context(self, page_probe, max_back_steps=4, back_delay=0.5):
        """Back out from a non-target detail/sku page until search/homepage is reachable."""
        current_probe = page_probe

        for _ in range(max_back_steps):
            if current_probe["state"] not in {"detail_page", "sku_page"}:
                return current_probe
            if self._current_page_matches_target(current_probe):
                return current_probe

            if not self._press_keycode_safe(4, context="退出非目标演出页"):
                break
            time.sleep(back_delay)
            self.dismiss_startup_popups()
            current_probe = self.probe_current_page()

        return current_probe

    def _recover_to_navigation_start(self, page_probe, max_back_steps=3):
        """Recover to a navigable page such as homepage or search page."""
        navigable_states = {"homepage", "search_page", "detail_page", "sku_page"}
        current_probe = page_probe
        if current_probe["state"] in navigable_states:
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="恢复导航起点"):
                break
            time.sleep(0.4)
            current_probe = self.probe_current_page()
            if current_probe["state"] in navigable_states:
                return current_probe

        try:
            self.driver.activate_app(self.config.app_package)
            time.sleep(1)
        except Exception:
            pass

        return self.probe_current_page()

    def _recover_to_detail_page_for_local_retry(self, initial_probe=None, max_back_steps=4, back_delay=0.2):
        """Recover locally to the current event detail/sku page without rebuilding the Appium session."""
        current_probe = initial_probe or self.probe_current_page()
        retryable_states = {"detail_page", "sku_page"}

        if current_probe["state"] in retryable_states and (
                not self.item_detail or self._current_page_matches_target(current_probe)):
            return current_probe

        self.dismiss_startup_popups()
        current_probe = self.probe_current_page()
        if current_probe["state"] in retryable_states and (
                not self.item_detail or self._current_page_matches_target(current_probe)):
            return current_probe

        for _ in range(max_back_steps):
            if not self._press_keycode_safe(4, context="本地快速回退"):
                break
            time.sleep(back_delay)
            self.dismiss_startup_popups()
            current_probe = self.probe_current_page()
            if current_probe["state"] in retryable_states and (
                    not self.item_detail or self._current_page_matches_target(current_probe)):
                return current_probe

        return current_probe

    def _open_search_from_homepage(self):
        """Enter the homepage search flow."""
        search_selectors = [
            (By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"),
            (By.ID, "cn.damai:id/homepage_header_search"),
            (By.ID, "cn.damai:id/homepage_header_search_layout"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("搜索")'),
        ]

        for by, value in search_selectors:
            if self.ultra_fast_click(by, value, timeout=0.8):
                search_probe = self.wait_for_page_state({"search_page"}, timeout=2.5, poll_interval=0.15)
                if search_probe["state"] == "search_page":
                    return True

        search_probe = self.probe_current_page()
        if search_probe["state"] == "search_page":
            return True

        logger.warning("未能从首页打开搜索页")
        return False

    def _submit_search_keyword(self):
        """Fill the configured keyword into the Damai search box and submit."""
        if not self.config.keyword:
            logger.warning("缺少 keyword，无法执行自动搜索")
            return False

        try:
            search_input = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.ID, "cn.damai:id/header_search_v2_input"))
            )
        except TimeoutException:
            logger.warning("未找到搜索输入框")
            return False

        self._click_element_center(search_input)
        time.sleep(0.2)

        current_text = (search_input.text or "").strip()
        if current_text and current_text != self.config.keyword:
            if self._has_element(By.ID, "cn.damai:id/header_search_v2_input_delete"):
                self.ultra_fast_click(By.ID, "cn.damai:id/header_search_v2_input_delete", timeout=0.8)
                time.sleep(0.1)
            else:
                try:
                    search_input.clear()
                except Exception:
                    pass

        if (search_input.text or "").strip() != self.config.keyword:
            search_input.send_keys(self.config.keyword)

        if not self._press_keycode_safe(66, context="提交搜索关键词"):
            return False
        try:
            WebDriverWait(self.driver, 5).until(
                lambda drv: len(drv.find_elements(By.ID, "cn.damai:id/ll_search_item")) > 0
            )
        except TimeoutException:
            logger.warning("搜索结果加载超时")
            return False

        if self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("演出")'):
            self.smart_wait_and_click(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().text("演出")',
                timeout=0.8,
            )
            time.sleep(0.2)

        return True

    def _score_search_result(self, title_text, venue_text):
        """Score a search result against the configured target."""
        normalized_title = normalize_text(title_text)
        normalized_venue = normalize_text(venue_text)
        if not normalized_title:
            return -1

        score = 0
        if self._title_matches_target(title_text):
            score += 100

        normalized_keyword = normalize_text(self.config.keyword)
        if normalized_keyword:
            if normalized_keyword == normalized_title:
                score += 80
            elif normalized_keyword in normalized_title:
                score += 50

        keyword_tokens = self._keyword_tokens()
        if keyword_tokens:
            token_hits = sum(1 for token in keyword_tokens if token in normalized_title)
            score += token_hits * 20
            if token_hits == len(keyword_tokens) and len(keyword_tokens) >= 2:
                score += 30

        normalized_city = normalize_text(city_keyword(self.config.city))
        if normalized_city and normalized_city in normalized_title:
            score += 20

        if self.item_detail:
            expected_venue = normalize_text(self.item_detail.venue_name)
            if expected_venue and expected_venue in normalized_venue:
                score += 20

            expected_city = normalize_text(self.item_detail.city_keyword)
            if expected_city and expected_city in normalized_title:
                score += 10

        if self.config.target_venue:
            expected_venue = normalize_text(self.config.target_venue)
            if expected_venue and expected_venue in normalized_venue:
                score += 30

        return score

    def _scroll_search_results(self):
        """Scroll the search result list upward."""
        self.driver.execute_script(
            "mobile: swipeGesture",
            {
                "left": 96,
                "top": 520,
                "width": 1088,
                "height": 1500,
                "direction": "up",
                "percent": 0.55,
                "speed": 5000,
            },
        )

    def _open_target_from_search_results(self, max_scrolls=2):
        """Open the best-matching event from search results."""
        seen_titles = set()

        for _ in range(max_scrolls + 1):
            result_cards = self.driver.find_elements(By.ID, "cn.damai:id/ll_search_item")
            best_match = None
            best_score = -1

            for card in result_cards:
                title_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_name")
                venue_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_venueName")
                score = self._score_search_result(title_text, venue_text)
                if title_text:
                    seen_titles.add(title_text)
                if score > best_score:
                    best_score = score
                    best_match = card

            if best_match is not None and best_score >= 60:
                self._click_element_center(best_match)
                detail_probe = self.wait_for_page_state({"detail_page", "sku_page"}, timeout=8)
                if detail_probe["state"] in {"detail_page", "sku_page"} and self._current_page_matches_target(detail_probe):
                    return True

                logger.warning("已进入详情页，但标题与目标演出不一致，返回搜索结果继续尝试")
                if not self._press_keycode_safe(4, context="返回搜索列表"):
                    break
                time.sleep(0.5)
            else:
                logger.info(f"本屏搜索结果未找到明确匹配项，已扫描: {len(seen_titles)} 条")

            if _ < max_scrolls:
                self._scroll_search_results()
                time.sleep(0.4)

        logger.warning("自动搜索后未找到目标演出")
        return False

    def collect_search_results(self, max_scrolls=0, max_results=5):
        """Collect search result summaries without opening them."""
        seen = set()
        collected = []

        for scroll_index in range(max_scrolls + 1):
            result_cards = self.driver.find_elements(By.ID, "cn.damai:id/ll_search_item")
            for card in result_cards:
                title_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_name")
                if not title_text:
                    continue

                normalized_title = normalize_text(title_text)
                if normalized_title in seen:
                    continue

                venue_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_venueName")
                city_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_city").replace("|", "").strip()
                time_text = self._safe_element_text(card, By.ID, "cn.damai:id/tv_project_time")
                price_text = self._build_compound_price_text(card)
                score = self._score_search_result(title_text, venue_text)

                collected.append({
                    "title": title_text,
                    "venue": venue_text,
                    "city": city_text,
                    "time": time_text,
                    "price": price_text,
                    "score": score,
                })
                seen.add(normalized_title)

            if len(collected) >= max_results:
                break

            if scroll_index < max_scrolls:
                self._scroll_search_results()
                time.sleep(0.4)

        collected.sort(key=lambda item: item["score"], reverse=True)
        return collected[:max_results]

    def navigate_to_target_event(self, initial_probe=None):
        """Auto-navigate from homepage/search to the target event detail page."""
        if not self.config.auto_navigate:
            return False

        page_probe = initial_probe or self.probe_current_page()
        page_probe = self._recover_to_navigation_start(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and self._current_page_matches_target(page_probe):
            return True

        if page_probe["state"] in {"detail_page", "sku_page"} and not self._current_page_matches_target(page_probe):
            page_probe = self._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and self._current_page_matches_target(page_probe):
            return True

        if page_probe["state"] == "homepage":
            logger.info("当前位于首页，开始自动搜索目标演出")
            if not self._open_search_from_homepage():
                return False
            page_probe = self.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning(f"当前页面不适合自动搜索: {page_probe['state']}")
            return False

        if not self._submit_search_keyword():
            return False

        return self._open_target_from_search_results()

    def discover_target_event(self, keyword_candidates, initial_probe=None, search_scrolls=1, result_limit=5):
        """Try multiple keywords, collect candidate summaries, and open the best match."""
        page_probe = initial_probe or self.probe_current_page()
        page_probe = self._recover_to_navigation_start(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and self._current_page_matches_target(page_probe):
            return {
                "used_keyword": self.config.keyword,
                "search_results": [],
                "page_probe": page_probe,
            }

        if page_probe["state"] in {"detail_page", "sku_page"} and not self._current_page_matches_target(page_probe):
            page_probe = self._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and self._current_page_matches_target(page_probe):
            return {
                "used_keyword": self.config.keyword,
                "search_results": [],
                "page_probe": page_probe,
            }

        if page_probe["state"] == "homepage":
            if not self._open_search_from_homepage():
                return None
            page_probe = self.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning(f"当前页面不适合执行提示词检索: {page_probe['state']}")
            return None

        tried = set()
        for keyword in keyword_candidates:
            normalized_keyword = normalize_text(keyword)
            if not normalized_keyword or normalized_keyword in tried:
                continue

            self.config.keyword = keyword
            logger.info(f"尝试搜索关键词: {keyword}")
            if not self._submit_search_keyword():
                tried.add(normalized_keyword)
                continue

            search_results = self.collect_search_results(max_scrolls=search_scrolls, max_results=result_limit)
            if search_results:
                logger.info(f"搜索到 {len(search_results)} 条候选结果，最高分 {search_results[0]['score']}")
            if search_results and search_results[0]["score"] >= 40 and self._open_target_from_search_results(max_scrolls=search_scrolls):
                page_probe = self.probe_current_page()
                return {
                    "used_keyword": keyword,
                    "search_results": search_results,
                    "page_probe": page_probe,
                }

            tried.add(normalized_keyword)

        logger.warning("根据提示词尝试多个搜索关键词后，仍未打开目标演出")
        return None

    def select_performance_date(self, timeout=1.0):
        """选择演出场次日期"""
        if not self.config.date:
            return

        date_selector = f'new UiSelector().textContains("{self.config.date}")'
        if self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, date_selector, timeout=timeout):
            logger.info(f"选择场次日期: {self.config.date}")
        else:
            logger.debug(f"未找到日期 '{self.config.date}'，使用默认场次")

    def _select_city_from_detail_page(self, timeout=1.0):
        """Select the configured city on the detail page."""
        city_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{self.config.city}")'),
            (By.XPATH, f'//*[@text="{self.config.city}"]')
        ]
        return self.smart_wait_and_click(*city_selectors[0], city_selectors[1:], timeout=timeout)

    def _prepare_detail_page_hot_path(self):
        """Preselect detail-page filters before the sale opens so launch-time work is minimized."""
        page_probe = self.probe_current_page()
        if page_probe["state"] != "detail_page":
            return False

        prepared = False
        if self.config.date:
            self.select_performance_date()
            prepared = True

        if self.config.city and self._select_city_from_detail_page(timeout=0.6):
            logger.info(f"已预选城市: {self.config.city}")
            prepared = True

        return prepared

    def _enter_purchase_flow_from_detail_page(self, prepared=False):
        """Open the purchase panel from the detail page with a low-latency hot path."""
        if not prepared:
            if self.config.rush_mode:
                # 极速模式下先做轻量预选，失败不阻塞抢占购票入口。
                self.select_performance_date(timeout=0.35)
                if self.config.city and self._select_city_from_detail_page(timeout=0.35):
                    logger.info(f"极速模式预选城市: {self.config.city}")
                elif self.config.city:
                    logger.debug("极速模式未命中城市选择，继续抢占购票入口")
            else:
                self.select_performance_date()
                logger.info("选择城市...")
                if not self._select_city_from_detail_page(timeout=1.0):
                    logger.warning("城市选择失败")
                    return None

        logger.info("点击购票按钮...")
        if self.config.rush_mode:
            if self.ultra_fast_click(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl", timeout=0.25):
                next_probe = self._wait_for_purchase_entry_result(timeout=0.7, poll_interval=0.03)
                if next_probe["state"] in {"sku_page", "order_confirm_page"}:
                    return next_probe
            if self.ultra_fast_click(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl", timeout=0.2):
                next_probe = self._wait_for_purchase_entry_result(timeout=0.6, poll_interval=0.03)
                if next_probe["state"] in {"sku_page", "order_confirm_page"}:
                    return next_probe

        hot_attempts = [
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*购票.*|.*抢票.*|.*购买.*|.*立即.*")'),
        ]
        for by, value in hot_attempts:
            if self.ultra_fast_click(by, value, timeout=0.35):
                next_probe = self._wait_for_purchase_entry_result(timeout=0.9, poll_interval=0.05)
                if next_probe["state"] in {"sku_page", "order_confirm_page"}:
                    return next_probe

        book_selectors = [
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")'),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]')
        ]
        if not self.smart_wait_and_click(*book_selectors[0], book_selectors[1:], timeout=0.8):
            logger.warning("购票按钮点击失败")
            return None
        return self._wait_for_purchase_entry_result(timeout=5, poll_interval=0.08)

    def check_session_valid(self):
        """检查大麦 App 登录状态是否有效"""
        activity = self._get_current_activity()
        if "LoginActivity" in activity or "SignActivity" in activity:
            logger.error("检测到登录页面，大麦 App 登录已过期，请重新登录")
            return False

        login_prompt_selectors = [
            'new UiSelector().textContains("请先登录")',
            'new UiSelector().textContains("登录/注册")',
        ]
        for selector in login_prompt_selectors:
            if self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, selector):
                logger.error("检测到登录提示，请重新登录大麦 App")
                return False

        return True

    def _purchase_bar_text_ready(self):
        """Inspect the detail-page CTA text and decide whether sale has opened."""
        try:
            purchase_bar = self.driver.find_element(
                By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
            )
        except Exception:
            return False

        texts = [text.strip() for text in self._collect_descendant_texts(purchase_bar) if text.strip()]
        merged = normalize_text("".join(texts))
        if not merged:
            return False
        if any(normalize_text(keyword) in merged for keyword in _CTA_BLOCKED_KEYWORDS):
            return False
        return any(normalize_text(keyword) in merged for keyword in _CTA_READY_KEYWORDS)

    def _is_sale_ready(self):
        """Check whether the current UI state is actionable for purchase."""
        ready_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("立即购买")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("立即抢票")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("选座购买")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("立即提交")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("提交订单")'),
        ]
        for by, value in ready_selectors:
            if self._has_element(by, value):
                return True

        if self._has_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"):
            return not self.is_reservation_sku_mode()

        if self._has_element(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"):
            return self._purchase_bar_text_ready()

        return False

    def wait_for_sale_start(self):
        """等待开售时间，在开售前 countdown_lead_ms 毫秒开始轮询。"""
        if self.config.sell_start_time is None:
            if self.config.wait_cta_ready_timeout_ms > 0:
                logger.info("未配置 sell_start_time，已跳过 CTA 等待，直接开始执行")
            return

        _tz_shanghai = timezone(timedelta(hours=8))
        sell_time = datetime.fromisoformat(self.config.sell_start_time)
        # Ensure timezone-aware
        if sell_time.tzinfo is None:
            sell_time = sell_time.replace(tzinfo=_tz_shanghai)

        now = datetime.now(tz=_tz_shanghai)
        if now >= sell_time:
            logger.info("开售时间已过，跳过等待")
            return

        lead_delta = timedelta(milliseconds=self.config.countdown_lead_ms)
        poll_start = sell_time - lead_delta
        sleep_seconds = (poll_start - now).total_seconds()

        if sleep_seconds > 0:
            logger.info(
                f"等待开售，将在 {self.config.sell_start_time} 前 "
                f"{self.config.countdown_lead_ms}ms 开始轮询"
            )
            time.sleep(sleep_seconds)

        # Tight polling loop with multiple purchase signals until the page becomes actionable.
        deadline = sell_time + timedelta(seconds=8)
        while datetime.now(tz=_tz_shanghai) < deadline:
            if self._is_sale_ready():
                logger.info("检测到可购买按钮，开售已开始")
                return
            time.sleep(0.08)

        logger.warning("等待开售超时，继续执行")

    def verify_order_result(self, timeout=5):
        """验证订单提交结果"""
        start = time.time()
        payment_text_selectors = [
            'new UiSelector().textContains("立即支付")',
            'new UiSelector().textContains("去支付")',
            'new UiSelector().textContains("确认支付")',
            'new UiSelector().textContains("支付剩余时间")',
            'new UiSelector().textContains("收银台")',
        ]

        while time.time() - start < timeout:
            activity = self._get_current_activity()

            # Success: payment page
            if any(kw in activity for kw in ("Pay", "Cashier", "AlipayClient")):
                logger.info("订单提交成功，已跳转支付页面")
                return "success"

            # Check page text for various outcomes
            if self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("未支付")'):
                logger.warning("已有未支付订单")
                return "existing_order"
            if self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("已售罄")') or \
               self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("库存不足")') or \
               self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("暂时无票")'):
                logger.warning("票已售罄")
                return "sold_out"
            if self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("滑块")') or \
               self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("验证")'):
                logger.warning("触发验证码")
                return "captcha"
            if any(self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, selector) for selector in payment_text_selectors):
                submit_still_visible = self._has_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().text("立即提交")',
                )
                confirm_title_visible = self._has_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().textContains("确认购买")',
                )
                if submit_still_visible or confirm_title_visible:
                    logger.warning("检测到支付相关文本，但仍在确认购买页，暂不判定提交成功")
                else:
                    logger.info("订单提交成功，检测到支付页关键控件")
                    return "success"

            time.sleep(0.3)

        logger.warning("订单验证超时")
        return "timeout"

    def _submit_order_fast(self, submit_selectors):
        """Attempt submit quickly and retry within the confirm page before falling back."""
        attempt_count = 3
        has_submitted_once = False
        for attempt in range(attempt_count):
            submit_success = False
            if self.ultra_fast_click(*submit_selectors[0], timeout=0.35):
                submit_success = True
            elif self.ultra_fast_click(*submit_selectors[1], timeout=0.35):
                submit_success = True
            elif self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:], timeout=0.6):
                submit_success = True

            if not submit_success:
                logger.warning("提交订单按钮未找到，请手动确认订单状态")
                if has_submitted_once:
                    followup_result = self.verify_order_result(timeout=2)
                    if followup_result != "timeout":
                        return followup_result
                return "timeout"

            has_submitted_once = True
            verify_timeout = 1.2 if attempt < attempt_count - 1 else 3
            result = self.verify_order_result(timeout=verify_timeout)
            if result != "timeout":
                return result
            logger.warning(f"提交后暂未确认结果，快速重试提交 {attempt + 2}/{attempt_count}")

        return "timeout"

    def _fast_retry_from_current_state(self):
        """根据当前页面状态进行快速重试。"""
        page_probe = self.probe_current_page()
        state = page_probe["state"]

        if state in ("detail_page", "sku_page"):
            if self.item_detail and not self._current_page_matches_target(page_probe):
                if not self.config.auto_navigate:
                    logger.warning("当前详情页不是目标演出，手动起跑模式下停止本地快速重试")
                    return False
                logger.info("当前详情页不是目标演出，转为自动导航")
                return self.navigate_to_target_event(page_probe) and self.run_ticket_grabbing()
            return self.run_ticket_grabbing()
        elif state == "order_confirm_page":
            if not self.config.if_commit_order:
                if not self._ensure_attendees_selected_on_confirm_page():
                    self._set_terminal_failure("attendee_unselected")
                    logger.error("开发验证模式下观演人未选择完整，已停止")
                    return False
                submit_selectors = [
                    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                    (By.XPATH, '//*[contains(@text,"提交")]')
                ]
                return self.smart_wait_for_element(*submit_selectors[0], submit_selectors[1:])
            if not self._ensure_attendees_selected_on_confirm_page():
                self._set_terminal_failure("attendee_unselected")
                return False
            submit_selectors = [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                (By.XPATH, '//*[contains(@text,"提交")]')
            ]
            return self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:])
        elif state == "pending_order_dialog":
            self._set_run_outcome("order_pending_payment")
            logger.info("检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付")
            return True
        else:
            if self.config.auto_navigate:
                return self.navigate_to_target_event(page_probe) and self.run_ticket_grabbing()
            recovered_probe = self._recover_to_detail_page_for_local_retry(page_probe)
            if recovered_probe["state"] not in {"detail_page", "sku_page"}:
                logger.warning(f"本地快速回退后仍未回到演出页，当前状态: {recovered_probe['state']}")
                return False
            return self.run_ticket_grabbing()

    def dismiss_startup_popups(self):
        """处理首启的一次性系统/应用弹窗。"""
        dismissed = False

        popup_clicks = [
            (By.ID, "android:id/ok"),  # Android 全屏提示
            (By.ID, "cn.damai:id/id_boot_action_agree"),  # 大麦隐私协议
            (By.ID, "cn.damai:id/damai_theme_dialog_cancel_btn"),  # 开启消息通知
            (By.ID, "cn.damai:id/damai_theme_dialog_close_layout"),  # 新版升级提示关闭按钮
            (By.ID, "cn.damai:id/damai_theme_dialog_close_btn"),  # 新版升级提示关闭图标
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'),  # Add to home screen
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
        ]

        for by, value in popup_clicks:
            if self._has_element(by, value):
                if self.ultra_fast_click(by, value):
                    dismissed = True
                    time.sleep(0.3)

        return dismissed

    def is_reservation_sku_mode(self):
        """识别当前 SKU 页是否仍处于抢票预约流，而非正式下单流。"""
        reservation_indicators = [
            (By.ID, "cn.damai:id/btn_cancel_reservation"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看场次")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("预约想看票档")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("提交抢票预约")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("已预约")'),
        ]

        return any(self._has_element(by, value) for by, value in reservation_indicators)

    def get_visible_date_options(self):
        """Return visible date options on the current page."""
        dates = []
        seen = set()
        for element in self.driver.find_elements(By.ID, "cn.damai:id/tv_date"):
            text = (element.text or "").strip()
            if not text or text in seen:
                continue
            dates.append(text)
            seen.add(text)
        return dates

    def get_visible_price_options(self, allow_ocr=True):
        """Return visible price options from the current sku page."""
        try:
            price_container = self.driver.find_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
        except Exception:
            return []

        options = []
        try:
            cards = price_container.find_elements(By.CLASS_NAME, "android.widget.FrameLayout")
        except Exception:
            cards = []

        if not isinstance(cards, (list, tuple)):
            return []
        cards = [card for card in cards if str(card.get_attribute("clickable")).lower() == "true"]
        screenshot_path = None
        if allow_ocr and cards and _MAGICK_BIN and _TESSERACT_BIN:
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                    screenshot_path = tmp_file.name
                self.driver.get_screenshot_as_file(screenshot_path)
            except Exception:
                screenshot_path = None

        for index, card in enumerate(cards):
            texts = self._collect_descendant_texts(card)
            text = self._price_option_text_from_descendants(texts)
            source = "ui" if text else ""
            tag = ""
            for candidate in texts:
                if candidate in {"可预约", "预售", "无票", "已预约", "缺货", "售罄", "已售罄", "可选"}:
                    tag = candidate
                    break

            if not text and screenshot_path:
                text = self._ocr_price_text_from_card(screenshot_path, card.rect)
                if text:
                    source = "ocr"

            if not text and not tag:
                continue

            options.append({
                "index": index,
                "text": text,
                "tag": tag,
                "raw_texts": texts,
                "source": source or "ui",
            })

        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.unlink(screenshot_path)
            except OSError:
                pass

        return options

    def _get_detail_venue_text(self):
        """Read venue text from the detail page if present."""
        for resource_id in ("cn.damai:id/venue_name_0", "cn.damai:id/tv_project_venueName"):
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

        if self.config.date:
            self.select_performance_date()

        city_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{self.config.city}")'),
            (By.XPATH, f'//*[@text="{self.config.city}"]'),
        ]
        self.smart_wait_and_click(*city_selectors[0], city_selectors[1:], timeout=0.8)

        book_selectors = [
            (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*")'),
            (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买")]'),
        ]
        if not self.smart_wait_and_click(*book_selectors[0], book_selectors[1:], timeout=1.0):
            return self.probe_current_page()

        return self.wait_for_page_state({"sku_page", "order_confirm_page"}, timeout=5)

    def inspect_current_target_event(self, page_probe=None):
        """Summarize the currently opened event for prompt-based confirmation."""
        page_probe = page_probe or self.probe_current_page()
        summary = {
            "state": page_probe["state"],
            "title": self._get_detail_title_text(),
            "venue": self._get_detail_venue_text(),
            "dates": [],
            "price_options": [],
            "reservation_mode": page_probe.get("reservation_mode", False),
        }

        sku_probe = self.ensure_sku_page_for_inspection(page_probe)
        summary["state"] = sku_probe["state"]
        if not summary["title"]:
            summary["title"] = self._get_detail_title_text()
        if not summary["venue"]:
            summary["venue"] = self._get_detail_venue_text()

        if sku_probe["state"] == "sku_page":
            summary["reservation_mode"] = sku_probe.get("reservation_mode", False)
            summary["dates"] = self.get_visible_date_options()
            summary["price_options"] = self.get_visible_price_options()

        return summary

    def probe_current_page(self):
        """探测当前页面状态和关键控件可见性。"""
        state = "unknown"
        current_activity = self._get_current_activity()
        purchase_button = self._has_element(By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl")
        detail_price_summary = self._has_element(By.ID, "cn.damai:id/project_detail_price_layout")
        sku_price_container = self._has_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout") or \
            self._has_element(By.ID, "cn.damai:id/layout_price") or \
            self._has_element(By.ID, "cn.damai:id/tv_price_name")
        quantity_picker = self._has_element(By.ID, "layout_num")
        submit_button = self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")')
        pending_order_dialog = self._has_element(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("未支付订单")',
        ) or (
            self._has_element(By.ID, "cn.damai:id/damai_theme_dialog_confirm_btn")
            and self._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("查看订单")')
        )
        reservation_mode = False

        if self._has_element(By.ID, "cn.damai:id/id_boot_action_agree"):
            state = "consent_dialog"
        elif pending_order_dialog:
            state = "pending_order_dialog"
        elif "MainActivity" in current_activity or \
                self._has_element(By.ID, "cn.damai:id/homepage_header_search") or \
                self._has_element(By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"):
            state = "homepage"
        elif "SearchActivity" in current_activity or self._has_element(By.ID, "cn.damai:id/header_search_v2_input"):
            state = "search_page"
        elif submit_button:
            state = "order_confirm_page"
        elif "NcovSkuActivity" in current_activity or \
                self._has_element(By.ID, "cn.damai:id/layout_sku") or \
                self._has_element(By.ID, "cn.damai:id/sku_contanier"):
            state = "sku_page"
        elif "ProjectDetailActivity" in current_activity or purchase_button or detail_price_summary or \
                self._has_element(By.ID, "cn.damai:id/title_tv"):
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

    def run_ticket_grabbing(self, initial_page_probe=None):
        """执行抢票主流程"""
        try:
            self._terminal_failure_reason = None
            self._last_run_outcome = None
            self._log_execution_mode()
            start_time = time.time()
            page_probe = initial_page_probe or self.probe_current_page()
            fast_validation_hot_path = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and initial_page_probe is not None
                and page_probe["state"] in {"detail_page", "sku_page"}
            )
            if fast_validation_hot_path:
                logger.info("开发验证极速路径：跳过启动弹窗与登录探测，直接执行抢票热路径")
            else:
                self.dismiss_startup_popups()
                if not self.check_session_valid():
                    self._set_terminal_failure("session_invalid")
                    return False

            if page_probe["state"] == "pending_order_dialog":
                self._set_run_outcome("order_pending_payment")
                logger.info("检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付")
                return True

            if page_probe["state"] not in {"detail_page", "sku_page"} or \
                    (self.item_detail and not self._current_page_matches_target(page_probe)):
                if self.config.auto_navigate:
                    logger.info("当前不在目标演出页，尝试自动导航")
                    if not self.navigate_to_target_event(page_probe):
                        return False
                    page_probe = self.probe_current_page()
                    if page_probe["state"] == "pending_order_dialog":
                        self._set_run_outcome("order_pending_payment")
                        logger.info("检测到未支付订单弹窗（已占单待支付），请立即前往订单页完成支付")
                        return True
                else:
                    logger.warning("当前不在演出详情页，请先手动打开目标演出详情页")
                    return False

            if self.config.probe_only:
                detail_ready = page_probe["state"] == "detail_page" and page_probe["purchase_button"] and page_probe["price_container"]
                sku_ready = page_probe["state"] == "sku_page" and page_probe["price_container"]

                if detail_ready or sku_ready:
                    self._set_run_outcome("probe_ready")
                    logger.info("probe_only 模式: 详情页关键控件已就绪，停止在购票点击前")
                    end_time = time.time()
                    logger.info(f"探测完成，耗时: {end_time - start_time:.2f}秒")
                    return True

                logger.warning("probe_only 模式: 详情页关键控件未就绪")
                return False

            prepared_detail_page = False
            should_prepare_detail_page = (
                page_probe["state"] == "detail_page"
                and (
                    self.config.sell_start_time is not None
                    or (self.config.wait_cta_ready_timeout_ms > 0 and not self.config.rush_mode)
                )
            )
            if should_prepare_detail_page:
                prepared_detail_page = self._prepare_detail_page_hot_path()
                page_probe = self.probe_current_page()

            # Wait for sale start if configured
            self.wait_for_sale_start()
            # 极速模式 + 未配置开售时间时，wait_for_sale_start 为即时返回，无需再次探测页面状态。
            if self.config.sell_start_time is not None or not self.config.rush_mode:
                page_probe = self.probe_current_page()

            if page_probe["state"] == "detail_page":
                page_probe = self._enter_purchase_flow_from_detail_page(prepared=prepared_detail_page)
                if page_probe is None:
                    return False
            else:
                logger.info("当前已在票档选择页，跳过城市和预约按钮步骤")
                # 新版 SKU 页会先展示日期卡片，需在此再次选择场次后才会展开票档列表。
                if self.config.rush_mode and not self.config.if_commit_order:
                    logger.info("开发验证极速路径：已在票档页，跳过场次切换")
                else:
                    self.select_performance_date(timeout=0.35 if self.config.rush_mode else 1.0)
                if self.config.rush_mode:
                    # 极速模式下避免一次完整重探测，减少热路径阻塞。
                    page_probe = dict(page_probe)
                    page_probe.setdefault("state", "sku_page")
                    if "reservation_mode" not in page_probe:
                        page_probe["reservation_mode"] = self.is_reservation_sku_mode()
                else:
                    page_probe = self.probe_current_page()

            if page_probe["state"] == "sku_page" and page_probe.get("reservation_mode"):
                logger.warning(
                    "检测到当前页面仍是“预售/抢票预约”流程，继续点击底部按钮只会提交预约，不会进入订单确认页"
                )
                self._set_terminal_failure("reservation_only")
                return False

            price_coords = page_probe.get("price_coords") if self.config.rush_mode else None
            buy_button_coords = page_probe.get("buy_button_coords") if self.config.rush_mode else None
            if self.config.rush_mode and page_probe["state"] == "sku_page":
                if price_coords is None:
                    price_coords = self._get_price_option_coordinates_by_config_index()
                if buy_button_coords is None:
                    buy_button_coords = self._get_buy_button_coordinates()

            # 3. 票价选择 - 优化查找逻辑
            skip_price_selection = (
                self.config.rush_mode
                and not self.config.if_commit_order
                and self._has_element(By.ID, "layout_num")
            )
            if skip_price_selection:
                logger.info("开发验证极速路径：检测到已处于可调数量状态，跳过票档点击")
            else:
                logger.info("选择票价...")
                if not self._select_price_option(cached_coords=price_coords):
                    return False

            # 4. 数量选择
            logger.info("选择数量...")
            if self.driver.find_elements(by=By.ID, value='layout_num'):
                clicks_needed = len(self.config.users) - 1
                if clicks_needed > 0:
                    try:
                        plus_button = self.driver.find_element(By.ID, 'img_jia')
                        for i in range(clicks_needed):
                            rect = plus_button.rect
                            x = rect['x'] + rect['width'] // 2
                            y = rect['y'] + rect['height'] // 2
                            self.driver.execute_script("mobile: clickGesture", {
                                "x": x,
                                "y": y,
                                "duration": 50
                            })
                            time.sleep(0.02)
                    except Exception as e:
                        logger.error(f"快速点击加号失败: {e}")

            # if self.driver.find_elements(by=By.ID, value='layout_num') and self.config.users is not None:
            #     for i in range(len(self.config.users) - 1):
            #         self.driver.find_element(by=By.ID, value='img_jia').click()

            # 5. 确定购买
            logger.info("确定购买...")
            if self.config.rush_mode and buy_button_coords:
                burst_count = 1 if not self.config.if_commit_order else 2
                self._burst_click_coordinates(*buy_button_coords, count=burst_count, interval_ms=25, duration=25)
                buy_clicked = True
            elif self.config.rush_mode:
                try:
                    buy_button = self.driver.find_element(By.ID, "btn_buy_view")
                    burst_count = 1 if not self.config.if_commit_order else 2
                    self._burst_click_element_center(buy_button, count=burst_count, interval_ms=25, duration=25)
                    buy_clicked = True
                except Exception:
                    buy_clicked = False
            else:
                buy_clicked = False

            if not buy_clicked and not self.ultra_fast_click(By.ID, "btn_buy_view"):
                # 备用按钮文本
                self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")')

            submit_ready = self._wait_for_submit_ready(
                timeout=1.2 if self.config.rush_mode else 1.8,
                poll_interval=0.03 if self.config.rush_mode else 0.05,
            )
            if not submit_ready:
                if self.config.rush_mode and not self.config.if_commit_order:
                    logger.info("开发验证极速路径：确认页未完全就绪，跳过预选用户兜底，直接校验观演人区域")
                else:
                    # 6. 批量选择用户
                    logger.info("选择用户...")
                    user_clicks = [(AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")') for user in
                                   self.config.users]
                    user_timeout = 0.35 if self.config.rush_mode else 1.0
                    clicked_users = self.ultra_batch_click(user_clicks, timeout=user_timeout)
                    if clicked_users:
                        submit_ready = self._wait_for_submit_ready(
                            timeout=0.9 if self.config.rush_mode else 1.5,
                            poll_interval=0.03 if self.config.rush_mode else 0.05,
                        )

            if not submit_ready and not (self.config.rush_mode and not self.config.if_commit_order):
                logger.warning("未进入订单确认页，请检查票档可用性或观演人配置")
                return False

            submit_selectors = [
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                (By.XPATH, '//*[contains(@text,"提交")]')
            ]
            if not self._ensure_attendees_selected_on_confirm_page(
                require_attendee_section=self.config.rush_mode and not self.config.if_commit_order
            ):
                self._set_terminal_failure("attendee_unselected")
                logger.error("订单提交前观演人未选择完整，已停止自动提交")
                return False

            if not self.config.if_commit_order:
                self._set_run_outcome("validation_ready")
                end_time = time.time()
                logger.info("if_commit_order=False，已完成观演人勾选，停止在“立即提交”前")
                logger.info(f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {end_time - start_time:.2f}秒")
                return True

            # 7. 提交订单
            logger.info("提交订单...")
            result = self._submit_order_fast(submit_selectors)
            if result == "success":
                self._set_run_outcome("order_submitted")
                end_time = time.time()
                logger.info(f"抢票成功！耗时: {end_time - start_time:.2f}秒")
                return True
            if result == "existing_order":
                self._set_run_outcome("order_pending_payment")
                end_time = time.time()
                logger.info(
                    f"检测到未支付订单（已占单待支付），请立即前往订单页支付。耗时: {end_time - start_time:.2f}秒"
                )
                return True
            elif result in ("sold_out", "captcha"):
                return False
            # timeout/unknown — fail closed to avoid false positives and duplicate submissions
            self._set_terminal_failure("submit_unverified")
            end_time = time.time()
            logger.error(
                f"提交后未能确认成功状态（result={result}），"
                f"为避免重复下单已停止自动重试，请手动检查订单列表。耗时: {end_time - start_time:.2f}秒"
            )
            return False

        except Exception as e:
            logger.error(f"抢票过程发生错误: {e}")
            return False
        finally:
            time.sleep(0.05)

    def run_with_retry(self, max_retries=3):
        """带重试机制的抢票"""
        for attempt in range(max_retries):
            logger.info(f"第 {attempt + 1} 次尝试（{self._execution_mode_label()}）...")
            if self.run_ticket_grabbing():
                self._log_success_outcome()
                return True

            if self._terminal_failure_reason:
                logger.error(f"检测到不可重试失败，停止后续重试: {self._terminal_failure_reason}")
                break

            # Fast retry within same session
            for fast_attempt in range(self.config.fast_retry_count):
                logger.info(
                    f"快速重试 {fast_attempt + 1}/{self.config.fast_retry_count}"
                    f"（{self._execution_mode_label()}）..."
                )
                if fast_attempt > 0 and self.config.fast_retry_interval_ms > 0:
                    time.sleep(self.config.fast_retry_interval_ms / 1000)
                if self._fast_retry_from_current_state():
                    self._log_success_outcome("快速重试成功：")
                    return True
                if self._terminal_failure_reason:
                    logger.error(f"快速重试遇到不可重试失败，停止后续重试: {self._terminal_failure_reason}")
                    break

            if self._terminal_failure_reason:
                break

            # Full driver recreation
            logger.warning(f"第 {attempt + 1} 次尝试及快速重试均失败")
            if attempt < max_retries - 1:
                if not self.config.auto_navigate:
                    logger.info("手动起跑模式，保留当前会话并继续本地重试")
                    continue
                logger.info("重建驱动后重试...")
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self._setup_driver()

        logger.error("所有尝试均失败")
        return False


# 使用示例
if __name__ == "__main__":
    bot = None
    try:
        bot = DamaiBot()
        bot.run_with_retry(max_retries=3)
    except (ValueError, RuntimeError) as exc:
        logger.error(str(exc))
    finally:
        try:
            if bot and bot.driver:
                bot.driver.quit()
        except Exception:
            pass
