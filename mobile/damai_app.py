# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

import time
import subprocess
import xml.etree.ElementTree as ET
from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

try:
    from mobile.config import Config
except ImportError:
    from config import Config


class DamaiBot:
    def __init__(self):
        self.config = Config.load_config()
        self.driver = None
        self.wait = None
        self.last_error = ""
        self._setup_driver()

    def _setup_driver(self):
        """初始化驱动配置"""
        capabilities = {
            "platformName": "Android",  # 操作系统
            "platformVersion": self.config.platform_version,  # 系统版本
            "deviceName": self.config.device_name,  # 设备名称
            "appPackage": self.config.app_package,  # app 包名
            "appActivity": self.config.app_activity,  # app 启动 Activity
            "unicodeKeyboard": True,  # 支持 Unicode 输入
            "resetKeyboard": True,  # 隐藏键盘
            "noReset": True,  # 不重置 app
            "newCommandTimeout": 6000,  # 超时时间
            "automationName": self.config.automation_name,  # 使用 uiautomator2
            "skipServerInstallation": False,  # 跳过服务器安装
            "ignoreHiddenApiPolicyError": True,  # 忽略隐藏 API 策略错误
            "disableWindowAnimation": True,  # 禁用窗口动画
            # 优化性能配置
            "mjpegServerFramerate": 1,  # 降低截图帧率
            "shouldTerminateApp": False,
            "adbExecTimeout": 20000,
            "uiautomator2ServerInstallTimeout": 60000,
            "uiautomator2ServerLaunchTimeout": 60000,
        }
        if self.config.udid:
            capabilities["udid"] = self.config.udid

        device_app_info = AppiumOptions()
        device_app_info.load_capabilities(capabilities)
        client_timeout = 12
        client_config = ClientConfig(remote_server_addr=self.config.server_url, timeout=client_timeout)
        command_executor = RemoteConnection(self.config.server_url, client_config=client_config)

        for attempt in range(2):
            try:
                self.driver = webdriver.Remote(command_executor=command_executor, options=device_app_info)
                break
            except Exception as e:
                if attempt == 0 and self.config.udid:
                    # 重启 uiautomator2 server 后再试一次
                    try:
                        subprocess.run(
                            ["adb", "-s", self.config.udid, "shell", "am", "force-stop", "io.appium.uiautomator2.server"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        subprocess.run(
                            ["adb", "-s", self.config.udid, "shell", "am", "force-stop", "io.appium.uiautomator2.server.test"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        time.sleep(0.5)
                        continue
                    except Exception:
                        pass
                raise e

        # 稳定优先的设置（避免 UIA2 崩溃）
        base_settings = {
            "waitForIdleTimeout": 200,
            "actionAcknowledgmentTimeout": 100,
            "keyInjectionDelay": 0,
            "waitForSelectorTimeout": 600,
            "ignoreUnimportantViews": False,
            "allowInvisibleElements": False,
            "enableNotificationListener": False,
        }
        if not self.config.fast_mode:
            base_settings.update({
                "waitForIdleTimeout": 1000,
                "actionAcknowledgmentTimeout": 200,
                "waitForSelectorTimeout": 1000,
            })
        self.driver.update_settings(base_settings)

        # 极短的显式等待，抢票场景下速度优先
        wait_seconds = 1.2 if self.config.fast_mode else 3
        self.wait = WebDriverWait(self.driver, wait_seconds)

    def ultra_fast_click(self, by, value, timeout=1.0):
        """超快速点击 - 适合抢票场景"""
        try:
            for _ in range(3):
                try:
                    # 直接查找并点击，不等待可点击状态
                    el = WebDriverWait(self.driver, timeout).until(
                        EC.presence_of_element_located((by, value))
                    )
                    if self._click_element(el):
                        return True
                except StaleElementReferenceException:
                    time.sleep(0.05)
                    continue
            return False
        except TimeoutException:
            return False

    def batch_click(self, elements_info, delay=0.1):
        """批量点击操作"""
        for by, value in elements_info:
            if self.ultra_fast_click(by, value):
                if delay > 0:
                    time.sleep(delay)
            else:
                print(f"点击失败: {value}")

    def ultra_batch_click(self, elements_info, timeout=2):
        """超快批量点击 - 带等待机制"""
        coordinates = []
        # 批量收集坐标，带超时等待
        for by, value in elements_info:
            try:
                elements = self.driver.find_elements(by, value)
                if not elements:
                    print(f"未找到用户: {value}")
                    continue
                el = elements[0]
                rect = el.rect
                x = rect['x'] + rect['width'] // 2
                y = rect['y'] + rect['height'] // 2
                coordinates.append((x, y, value))
            except Exception as e:
                print(f"查找用户失败 {value}: {e}")
        print(f"成功找到 {len(coordinates)} 个用户")
        # 快速连续点击
        for i, (x, y, value) in enumerate(coordinates):
            self.driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 30
            })
            if i < len(coordinates) - 1:
                time.sleep(0.01)
            print(f"点击用户: {value}")

    def smart_wait_and_click(self, by, value, backup_selectors=None, timeout=1.0):
        """智能等待和点击 - 支持备用选择器"""
        selectors = [(by, value)]
        if backup_selectors:
            selectors.extend(backup_selectors)

        for selector_by, selector_value in selectors:
            for _ in range(3):
                try:
                    el = WebDriverWait(self.driver, timeout).until(
                        EC.presence_of_element_located((selector_by, selector_value))
                    )
                    if self._click_element(el):
                        return True
                except StaleElementReferenceException:
                    time.sleep(0.05)
                    continue
                except TimeoutException:
                    break
        return False

    def _click_element(self, el, duration=50):
        """点击元素，处理 StaleElement"""
        try:
            rect = el.rect
            x = rect['x'] + rect['width'] // 2
            y = rect['y'] + rect['height'] // 2
            self.driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": duration
            })
            return True
        except (StaleElementReferenceException, WebDriverException):
            return False

    def _build_date_tokens(self, date_text):
        """从配置的场次文本构建可匹配的 token 列表（偏宽松，仅按日期匹配）"""
        if not date_text:
            return []
        raw = date_text.strip()
        tokens = []

        # 提取时间
        import re
        # 宽松策略：仅按日期匹配，不强制时间
        m_time = re.search(r"(\\d{1,2}:\\d{2})", raw)

        # 提取日期
        m_full = re.search(r"(\\d{4}[-./]\\d{1,2}[-./]\\d{1,2})", raw)
        if m_full:
            full = m_full.group(1).replace(".", "-").replace("/", "-")
            y, m, d = full.split("-")
            tokens.extend([
                f"{y}-{m.zfill(2)}-{d.zfill(2)}",
                f"{y}.{m.zfill(2)}.{d.zfill(2)}",
                f"{int(m)}-{int(d)}",
                f"{m.zfill(2)}-{d.zfill(2)}",
                f"{int(m)}.{int(d)}",
                f"{m.zfill(2)}.{d.zfill(2)}",
                f"{int(m)}月{int(d)}日",
                f"{int(m)}月{int(d)}",
            ])
            # 兼容“5月2日-3日”类范围展示
            tokens.extend([
                f"{int(m)}月{int(d)}日-",
                f"{int(m)}月{int(d)}-",
                f"{m.zfill(2)}-{d.zfill(2)}-",
            ])
        else:
            m_md = re.search(r"(\\d{1,2})[-./](\\d{1,2})", raw)
            if m_md:
                m, d = m_md.group(1), m_md.group(2)
                tokens.extend([
                    f"{int(m)}-{int(d)}",
                    f"{m.zfill(2)}-{d.zfill(2)}",
                    f"{int(m)}.{int(d)}",
                    f"{m.zfill(2)}.{d.zfill(2)}",
                    f"{int(m)}月{int(d)}日",
                    f"{int(m)}月{int(d)}",
                ])
                tokens.extend([
                    f"{int(m)}月{int(d)}日-",
                    f"{int(m)}月{int(d)}-",
                    f"{m.zfill(2)}-{d.zfill(2)}-",
                ])

        # 兜底：如果没有日期解析出来，再尝试原始文本或时间
        if not tokens:
            tokens.append(raw)
            if m_time:
                tokens.append(m_time.group(1))

        # 去重并保持顺序
        seen = set()
        ordered = []
        for t in tokens:
            if t and t not in seen:
                seen.add(t)
                ordered.append(t)
        return ordered

    def _try_click_by_text_tokens(self, tokens, timeout=1.0):
        """使用文本 token 尝试点击元素（不滚动）"""
        for token in tokens:
            try:
                # UIAutomator contains
                elements = self.driver.find_elements(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().textContains("{token}")'
                )
                for el in elements[:3]:
                    if self._click_element(el):
                        return True

                # content-desc contains
                elements = self.driver.find_elements(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().descriptionContains("{token}")'
                )
                for el in elements[:3]:
                    if self._click_element(el):
                        return True

                # XPath contains
                elements = self.driver.find_elements(By.XPATH, f'//*[contains(@text,"{token}") or contains(@content-desc,"{token}")]')
                for el in elements[:3]:
                    if self._click_element(el):
                        return True
            except WebDriverException:
                continue
        return False

    def _try_scroll_and_click(self, tokens):
        """滚动查找文本 token 并点击"""
        if self.config.fast_mode:
            return False
        try:
            if not self.driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().scrollable(true)'
            ):
                return False
        except WebDriverException:
            return False
        for token in tokens:
            try:
                el = self.driver.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiScrollable(new UiSelector().scrollable(true))'
                    f'.scrollIntoView(new UiSelector().textContains("{token}"))'
                )
                if self._click_element(el):
                    return True
            except Exception:
                continue
        return False

    def _try_open_time_panel(self):
        """尝试打开场次/时间选择区域"""
        panel_tokens = ["场次", "时间", "日期", "演出时间", "选择场次", "选择时间"]
        return self._try_click_by_text_tokens(panel_tokens, timeout=1.0)

    def _try_select_date_by_index(self):
        """在场次容器内按索引选择场次"""
        try:
            items = self.driver.find_elements(
                By.XPATH,
                "//*[@resource-id='cn.damai:id/project_detail_perform_flowlayout']//android.view.ViewGroup[@clickable='true']"
            )
            if not items and not self.config.fast_mode:
                try:
                    WebDriverWait(self.driver, 1).until(
                        EC.presence_of_element_located((By.ID, "cn.damai:id/project_detail_perform_flowlayout"))
                    )
                    items = self.driver.find_elements(
                        By.XPATH,
                        "//*[@resource-id='cn.damai:id/project_detail_perform_flowlayout']//android.view.ViewGroup[@clickable='true']"
                    )
                except Exception:
                    pass
            if not items:
                containers = self.driver.find_elements(By.ID, "cn.damai:id/project_detail_perform_flowlayout")
                if not containers:
                    containers = self.driver.find_elements(
                        AppiumBy.ANDROID_UIAUTOMATOR,
                        'new UiSelector().resourceId("cn.damai:id/project_detail_perform_flowlayout")'
                    )
                if containers:
                    vg = containers[0].find_elements(By.CLASS_NAME, "android.view.ViewGroup")
                    items = [el for el in vg if el.get_attribute("clickable") == "true"]
                if not items:
                    if not self.config.fast_mode:
                        print("  场次容器内未找到可点击项")
                    return False
            idx = int(self.config.date_index) if self.config.date_index is not None else 0
            if idx < 0 or idx >= len(items):
                if self.config.date_strict:
                    return False
                idx = 0
            ok = self._click_element(items[idx])
            if not self.config.fast_mode:
                print(f"  场次可点击项: {len(items)}, 选择索引: {idx}, 成功: {ok}")
            return ok
        except Exception:
            return False

    def _scan_date_texts(self, max_items=20):
        """打印部分可能的场次文本，便于调试"""
        try:
            candidates = self.driver.find_elements(
                By.XPATH,
                '//*[contains(@text,"月") or contains(@text,"日") or contains(@text,":") or contains(@text,"-") or contains(@text,".") or contains(@content-desc,"月") or contains(@content-desc,"日") or contains(@content-desc,":") or contains(@content-desc,"-") or contains(@content-desc,".")]'
            )
            print(f"  可能的场次文本元素: {len(candidates)}")
            seen = set()
            for elem in candidates[:max_items]:
                try:
                    text = (elem.get_attribute("text") or "").strip()
                    desc = (elem.get_attribute("content-desc") or "").strip()
                    text = text or desc
                    text = text.strip()
                    if text and text not in seen and len(text) < 100:
                        print(f"  - {text}")
                        seen.add(text)
                except Exception:
                    continue
        except WebDriverException:
            return

    def _get_webview_context(self):
        try:
            contexts = self.driver.contexts
            for ctx in contexts:
                if "WEBVIEW" in ctx:
                    return ctx
        except Exception:
            return None
        return None

    def _with_context(self, ctx, fn):
        """临时切换上下文执行"""
        original = None
        try:
            original = self.driver.current_context
        except Exception:
            original = None
        try:
            if ctx and ctx != original:
                self.driver.switch_to.context(ctx)
            return fn()
        finally:
            try:
                if original and ctx != original:
                    self.driver.switch_to.context(original)
            except Exception:
                pass

    def _try_click_by_text_tokens_webview(self, tokens):
        """在 WEBVIEW 上下文尝试点击"""
        def _run():
            for token in tokens:
                try:
                    elements = self.driver.find_elements(
                        By.XPATH,
                        f'//*[contains(normalize-space(.), "{token}")]'
                    )
                    for el in elements[:3]:
                        try:
                            el.click()
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
            return False

        webview = self._get_webview_context()
        if not webview:
            return False
        return self._with_context(webview, _run)

    def _tap_bottom_area(self):
        """兜底点击底部购票区域"""
        try:
            size = self.driver.get_window_size()
            x = int(size["width"] * 0.75)
            y = int(size["height"] * 0.92)
            self.driver.execute_script("mobile: clickGesture", {
                "x": x,
                "y": y,
                "duration": 80
            })
            return True
        except Exception:
            return False

    def _ensure_sku_panel(self):
        """确保 SKU 面板已打开"""
        max_tries = 1 if self.config.fast_mode else 3
        for _ in range(max_tries):
            try:
                if self.driver.find_elements(By.ID, "cn.damai:id/layout_sku") or \
                   self.driver.find_elements(By.ID, "cn.damai:id/sku_contanier"):
                    return True
            except Exception:
                pass
            self._tap_bottom_area()
            time.sleep(0.2 if self.config.fast_mode else 0.6)
            self._swipe_up_small()
            time.sleep(0.2 if self.config.fast_mode else 0.6)
        return False

    def _try_select_city_by_index(self):
        """按索引选择城市（从 dump 中点击）"""
        if self.config.city_index is None:
            return False
        # 尝试导出当前结构
        self._dump_page_source(path="/tmp/damai_city.xml", force=True)
        return self._tap_from_dump(
            "cn.damai:id/tour_list",
            index=int(self.config.city_index),
            class_name="android.view.ViewGroup",
        )

    def _adb_tap(self, x, y):
        if not self.config.udid:
            return False

        try:
            subprocess.run(
                ["adb", "-s", self.config.udid, "shell", "input", "tap", str(int(x)), str(int(y))],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def _adb_screen_size(self):
        if not self.config.udid:
            return None
        try:
            result = subprocess.run(
                ["adb", "-s", self.config.udid, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                check=False,
            )
            # output: Physical size: 1080x2346
            for line in result.stdout.splitlines():
                if "Physical size" in line:
                    size = line.split(":")[-1].strip()
                    w, h = size.split("x")
                    return {"width": int(w), "height": int(h)}
        except Exception:
            return None
        return None

    def _tap_right_bottom(self):
        size = self._adb_screen_size()
        if size:
            return self._adb_tap(size["width"] * 0.85, size["height"] * 0.94)
        return False

    def _tap_bounds(self, bounds):
        try:
            # bounds format: [x1,y1][x2,y2]
            parts = bounds.replace("[", "").split("]")
            x1, y1 = map(int, parts[0].split(","))
            x2, y2 = map(int, parts[1].split(","))
            return self._adb_tap((x1 + x2) / 2, (y1 + y2) / 2)
        except Exception:
            return False

    def _tap_from_dump(self, resource_id, index=0, class_name=None):
        """从本地 dump 文件中按索引点击"""
        dump_path = "/tmp/damai_after_date.xml"
        try:
            tree = ET.parse(dump_path)
            root = tree.getroot()
        except Exception:
            return False

        candidates = []
        for node in root.iter():
            if node.attrib.get("resource-id") == resource_id:
                for child in node.iter():
                    if class_name and child.attrib.get("class") != class_name:
                        continue
                    if child.attrib.get("clickable") == "true":
                        bounds = child.attrib.get("bounds")
                        if bounds:
                            candidates.append(bounds)
        if not candidates:
            return False
        idx = max(0, min(index, len(candidates) - 1))
        return self._tap_bounds(candidates[idx])

    def _tap_text_from_dump(self, dump_path, text, exact=False):
        """从 dump 文件中按文本点击"""
        try:
            tree = ET.parse(dump_path)
            root = tree.getroot()
        except Exception:
            return False
        for node in root.iter():
            t = (node.attrib.get("text") or "").strip()
            d = (node.attrib.get("content-desc") or "").strip()
            cand = t or d
            if not cand:
                continue
            if exact:
                ok = cand == text
            else:
                ok = text in cand
            if ok and node.attrib.get("bounds"):
                return self._tap_bounds(node.attrib.get("bounds"))
        return False

    def _swipe_up_small(self):
        """轻微上滑，展开底部面板"""
        try:
            size = self.driver.get_window_size()
            x = int(size["width"] * 0.5)
            y_start = int(size["height"] * 0.78)
            y_end = int(size["height"] * 0.52)
            self.driver.execute_script("mobile: swipeGesture", {
                "left": x - 10,
                "top": y_end,
                "width": 20,
                "height": y_start - y_end,
                "direction": "up",
                "percent": 0.7
            })
            return True
        except Exception:
            return False

    def _scan_textviews(self, max_items=30):
        try:
            elements = self.driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().className("android.widget.TextView")'
            )
            print(f"  TextView 数量: {len(elements)}")
            seen = set()
            for elem in elements[:max_items]:
                try:
                    text = (elem.get_attribute("text") or "").strip()
                    desc = (elem.get_attribute("content-desc") or "").strip()
                    text = text or desc
                    if text and text not in seen and len(text) < 100:
                        print(f"  - {text}")
                        seen.add(text)
                except Exception:
                    continue
        except Exception:
            return

    def _dump_page_source(self, path="/tmp/damai_page_source.xml", force=False):
        if self.config.fast_mode and not force:
            return
        try:
            src = self.driver.page_source
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            print(f"  已导出页面结构: {path}")
        except Exception:
            pass

    def _try_select_any_price(self):
        """尝试选择任意可见票价"""
        try:
            candidates = self.driver.find_elements(
                By.XPATH,
                '//*[contains(@text,"¥") or contains(@text,"元") or contains(@content-desc,"¥") or contains(@content-desc,"元")]'
            )
            for elem in candidates[:10]:
                if self._click_element(elem):
                    return True
            return False
        except WebDriverException:
            return False

    def _try_select_price_by_resource(self):
        """根据资源 id 关键词选择票价"""
        try:
            elements = self.driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().resourceIdMatches(".*price.*|.*ticket.*|.*sku.*").clickable(true)'
            )
            for el in elements[:5]:
                if self._click_element(el):
                    return True
            return False
        except WebDriverException:
            return False

    def _try_select_price_by_layout(self):
        """在 SKU 容器内按位置选择票价"""
        try:
            sku = self.driver.find_elements(By.ID, "cn.damai:id/layout_sku")
            if not sku:
                return False
            sku = sku[0]
            items = sku.find_elements(By.CLASS_NAME, "android.view.ViewGroup")
            candidates = []
            for el in items:
                try:
                    if el.get_attribute("clickable") != "true":
                        continue
                    rect = el.rect
                    # 过滤掉顶部区域（场次区域）
                    if rect.get("y", 0) < 850:
                        continue
                    candidates.append(el)
                except Exception:
                    continue
            if not candidates:
                return False
            idx = int(self.config.price_index) if self.config.price_index is not None else 0
            idx = max(0, min(idx, len(candidates) - 1))
            ok = self._click_element(candidates[idx])
            if not self.config.fast_mode:
                print(f"  票价可点击项: {len(candidates)}, 选择索引: {idx}, 成功: {ok}")
            return ok
        except Exception:
            return False

    def _try_select_price_in_flowlayout(self):
        """在票档 flowlayout 内按索引选择"""
        try:
            items = self.driver.find_elements(
                By.XPATH,
                "//*[@resource-id='cn.damai:id/project_detail_perform_price_flowlayout']//android.widget.FrameLayout[@clickable='true']"
            )
            if not items and not self.config.fast_mode:
                try:
                    WebDriverWait(self.driver, 1).until(
                        EC.presence_of_element_located((By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"))
                    )
                    items = self.driver.find_elements(
                        By.XPATH,
                        "//*[@resource-id='cn.damai:id/project_detail_perform_price_flowlayout']//android.widget.FrameLayout[@clickable='true']"
                    )
                except Exception:
                    pass
            if not items:
                return False
            idx = int(self.config.price_index) if self.config.price_index is not None else 0
            idx = max(0, min(idx, len(items) - 1))
            ok = self._click_element(items[idx])
            if not self.config.fast_mode:
                print(f"  票档可点击项: {len(items)}, 选择索引: {idx}, 成功: {ok}")
            return ok
        except Exception:
            return False

    def run_ticket_grabbing(self):
        """执行抢票主流程"""
        try:
            print("开始抢票流程...")
            start_time = time.time()
            self.last_error = ""

            # 1. 城市选择 - 准备多个备选方案
            print("选择城市...")
            if self.config.city:
                city_tokens = [self.config.city, f"{self.config.city}站"]
                city_selectors = [
                    (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
                    (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{self.config.city}")'),
                    (By.XPATH, f'//*[@text="{self.config.city}"]')
                ]
                if not self.smart_wait_and_click(*city_selectors[0], city_selectors[1:]):
                    if not self._try_click_by_text_tokens(city_tokens, timeout=1.0):
                        if not self._try_select_city_by_index():
                            print("城市未选中，继续下一步")

            # 2. 点击预约按钮 - 多种可能的按钮文本
            print("点击预约按钮...")
            book_selectors = [
                (By.ID, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*预约.*|.*购买.*|.*立即.*|.*购票.*|.*抢票.*|.*预售.*|.*开抢.*|.*开售.*")'),
                (By.XPATH, '//*[contains(@text,"预约") or contains(@text,"购买") or contains(@text,"购票") or contains(@text,"抢票") or contains(@text,"开售") or contains(@text,"预售")]'),
                (By.XPATH, '//*[contains(@content-desc,"预约") or contains(@content-desc,"购买") or contains(@content-desc,"购票") or contains(@content-desc,"抢票")]'),
                (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceIdMatches(".*purchase.*|.*buy.*|.*ticket.*")'),
            ]
            if not self.smart_wait_and_click(*book_selectors[0], book_selectors[1:]):
                # 兜底：按文本 token 找按钮
                if not self._try_click_by_text_tokens(["预约", "购买", "购票", "抢票", "开售", "预售", "立即"]):
                    if not self._tap_bottom_area():
                        print("预约按钮点击失败")
                        return False
            # 尝试展开场次/票价面板
            self._swipe_up_small()
            time.sleep(0.2 if self.config.fast_mode else 0.8)
            if self._ensure_sku_panel():
                if not self.config.fast_mode:
                    print("已检测到票档面板")
            else:
                print("未检测到票档面板，继续尝试选择")

            # 3. 场次选择（时间/日期）
            if self.config.date:
                print("选择场次...")
                self._try_open_time_panel()
                time.sleep(0.2 if self.config.fast_mode else 0.6)
                if self.config.date_index is not None:
                    if self._try_select_date_by_index():
                        print(f"  已按索引选择场次: {self.config.date_index}")
                    else:
                        if self._tap_from_dump(
                            "cn.damai:id/project_detail_perform_flowlayout",
                            index=int(self.config.date_index),
                            class_name="android.view.ViewGroup",
                        ):
                            print(f"  已通过ADB选择场次: {self.config.date_index}")
                        else:
                            print(f"  按索引选择场次失败: {self.config.date_index}")
                else:
                    tokens = self._build_date_tokens(self.config.date)
                    if not self._try_click_by_text_tokens(tokens, timeout=1.2):
                        if self._try_click_by_text_tokens_webview(tokens):
                            pass
                        else:
                            if not self.config.fast_mode:
                                if not self._try_scroll_and_click(tokens):
                                    print("场次选择失败")
                                try:
                                    print(f"  当前上下文: {self.driver.current_context}")
                                    print(f"  可用上下文: {self.driver.contexts}")
                                except Exception:
                                    pass
                                self._scan_date_texts()
                                self._scan_textviews()
                                self._dump_page_source()
                            if self.config.date_strict:
                                return False
                if not self.config.fast_mode:
                    time.sleep(0.6)
                    self._dump_page_source(path="/tmp/damai_after_date.xml")

            # 4. 票价选择 - 优化查找逻辑
            print("选择票价...")
            try:
                price_selected = False
                if not self.config.fast_mode:
                    self._dump_page_source(path="/tmp/damai_after_date.xml", force=True)
                # 优先按文本匹配
                if self._tap_from_dump(
                    "cn.damai:id/project_detail_perform_price_flowlayout",
                    index=int(self.config.price_index) if self.config.price_index is not None else 0,
                    class_name="android.widget.FrameLayout",
                ):
                    price_selected = True
                if self.config.price_index is not None:
                    if not price_selected:
                        if self._try_select_price_in_flowlayout():
                            price_selected = True
                        else:
                            if self._tap_from_dump(
                                "cn.damai:id/project_detail_perform_price_flowlayout",
                                index=int(self.config.price_index),
                                class_name="android.widget.FrameLayout",
                            ):
                                price_selected = True
                            else:
                                if self.config.fast_mode:
                                    print("票档未命中索引，判定抢票失败，准备重试")
                                    return False
                                else:
                                    raise NoSuchElementException("price index not found")
                if self.config.price:
                    price_token = str(self.config.price)
                    if not price_selected:
                        if self._try_click_by_text_tokens([price_token, f"{price_token}元", f"¥{price_token}"]):
                            price_selected = True
                        elif self._try_click_by_text_tokens_webview([price_token, f"{price_token}元", f"¥{price_token}"]):
                            price_selected = True
                        elif self._try_select_price_in_flowlayout():
                            price_selected = True
                        elif self._try_select_price_by_layout():
                            price_selected = True
                        else:
                            raise NoSuchElementException("price text not found")
                else:
                    if self._try_select_any_price():
                        pass
                    else:
                        print("未能自动选择票价，继续下一步")
                        # 不抛错，可能只有单一票价或默认已选
            except Exception as e:
                print(f"票价选择失败，启动备用方案: {e}")
                # 备用方案
                if self._try_select_price_in_flowlayout():
                    pass
                else:
                    container_ids = [
                        'cn.damai:id/project_detail_perform_price_flowlayout',
                        'cn.damai:id/project_detail_perform_price_list',
                        'cn.damai:id/project_detail_perform_price_recycler',
                    ]
                    price_container = None
                    for cid in container_ids:
                        try:
                            price_container = self.wait.until(
                                EC.presence_of_element_located((By.ID, cid))
                            )
                            if price_container:
                                break
                        except TimeoutException:
                            continue

                    if price_container:
                        target_price = price_container.find_element(
                            AppiumBy.ANDROID_UIAUTOMATOR,
                            f'new UiSelector().className("android.widget.FrameLayout").index({self.config.price_index}).clickable(true)'
                        )
                        self._click_element(target_price)
                    else:
                        if not self._try_select_any_price():
                            if not self._try_select_price_by_resource():
                                if not self._try_select_price_by_layout():
                                    print("未找到票价容器，继续下一步")

                # if not self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR,
                #                              'new UiSelector().textMatches(".*799.*|.*\\d+元.*")'):
                #     return False

            # 4.5 票档确认（右下角确定/购买）
            print("确认票档...")
            # 直接点击右下角按钮，避免误点左下角价格
            self._tap_right_bottom()

            # 5. 数量选择
            print("选择数量...")
            if len(self.config.users) <= 1:
                pass
            elif self.driver.find_elements(by=By.ID, value='layout_num'):
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
                        print(f"快速点击加号失败: {e}")

            # if self.driver.find_elements(by=By.ID, value='layout_num') and self.config.users is not None:
            #     for i in range(len(self.config.users) - 1):
            #         self.driver.find_element(by=By.ID, value='img_jia').click()

            # 6. 确定购买
            print("确定购买...")
            if self.config.fast_mode:
                if not self._tap_from_dump(
                    "cn.damai:id/btn_buy_view",
                    index=0,
                    class_name="android.widget.LinearLayout",
                ):
                    # 底部右侧按钮区域兜底点击
                    size = self._adb_screen_size()
                    if size:
                        self._adb_tap(size["width"] * 0.85, size["height"] * 0.94)
            else:
                if not self._tap_from_dump(
                    "cn.damai:id/btn_buy_view",
                    index=0,
                    class_name="android.widget.LinearLayout",
                ):
                    if not self.ultra_fast_click(By.ID, "btn_buy_view"):
                        # 备用按钮文本
                        self.ultra_fast_click(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")')

            # 7. 批量选择用户
            print("选择用户...")
            time.sleep(0.3 if self.config.fast_mode else 1.0)
            self._dump_page_source(path="/tmp/damai_confirm.xml", force=True)
            for user in self.config.users:
                if not self._tap_text_from_dump("/tmp/damai_confirm.xml", user, exact=True):
                    if not self._tap_text_from_dump("/tmp/damai_confirm.xml", user, exact=False):
                        if not self.config.fast_mode:
                            user_clicks = [(AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")')]
                            self.ultra_batch_click(user_clicks)

            # 8. 提交订单
            if self.config.if_commit_order:
                print("提交订单...")
                if self.config.fast_mode:
                    if not self._tap_text_from_dump("/tmp/damai_confirm.xml", "立即提交"):
                        if not self._tap_text_from_dump("/tmp/damai_confirm.xml", "提交"):
                            size = self._adb_screen_size()
                            if size:
                                self._adb_tap(size["width"] * 0.85, size["height"] * 0.94)
                else:
                    submit_selectors = [
                        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                        (By.XPATH, '//*[contains(@text,"提交")]')
                    ]
                    submit_success = self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:])
                    if not submit_success:
                        print("⚠ 提交订单按钮未找到，请手动确认订单状态")
            else:
                print("已配置不提交订单，停止在确认页")

            end_time = time.time()
            print(f"抢票流程完成，耗时: {end_time - start_time:.2f}秒")
            return True

        except Exception as e:
            self.last_error = str(e)
            print(f"抢票过程发生错误: {e}")
            return False
        finally:
            time.sleep(1)  # 给最后的操作一点时间

    def run_with_retry(self, max_retries=3):
        """带重试机制的抢票"""
        for attempt in range(max_retries):
            print(f"第 {attempt + 1} 次尝试...")
            if self.run_ticket_grabbing():
                print("抢票成功！")
                return True
            else:
                print(f"第 {attempt + 1} 次尝试失败")
                # 保持快速模式，不切换到慢速调试
                if attempt < max_retries - 1:
                    retry_sleep = 0.2 if self.config.fast_mode else 2
                    print(f"{retry_sleep}秒后重试...")
                    time.sleep(retry_sleep)
                    # 重新初始化驱动
                    try:
                        self.driver.quit()
                    except Exception:
                        pass
                    self._setup_driver()

        print("所有尝试均失败")
        return False


# 使用示例
if __name__ == "__main__":
    bot = DamaiBot()
    try:
        bot.run_with_retry(max_retries=3)
    finally:
        try:
            bot.driver.quit()
        except Exception:
            pass
