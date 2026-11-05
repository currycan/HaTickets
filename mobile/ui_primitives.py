# -*- coding: UTF-8 -*-
"""
Low-level UI interaction primitives extracted from DamaiBot.

This mixin class provides core element finding, clicking, text reading,
and coordinate utilities that every sub-module depends on.  DamaiBot
inherits from this class so all ``self._find()`` / ``self._click_coordinates()``
etc. calls continue to work transparently via inheritance.
"""

import re
import time
import xml.etree.ElementTree as ET

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)


# Constant replacing the former AppiumBy.ANDROID_UIAUTOMATOR selector type.
# Used as a routing key in _appium_selector_to_u2 / _parse_uiselector.
ANDROID_UIAUTOMATOR = "android_uiautomator"


class UIPrimitives:
    """Mixin providing low-level UI interaction methods.

    Expects the following attributes on *self* (set by the concrete subclass):
    - ``self.d``   — uiautomator2 device handle
    - ``self.driver`` — u2 driver (same as ``self.d``)
    - ``self.config`` — Config object with at least ``app_package``
    - ``self._cached_hot_path_coords`` — ``dict`` of ``{key: (x, y)}``
    """

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _uiautomator_by_values():
        return {"android uiautomator", "android_uiautomator", ANDROID_UIAUTOMATOR}

    def _using_u2(self):
        return True

    # ------------------------------------------------------------------
    # Core element operations
    # ------------------------------------------------------------------

    def _find(self, by, value):
        """统一查找入口，返回 u2 selector。"""
        if not self._using_u2():
            return self.driver.find_element(by, value)
        return self._appium_selector_to_u2(by, value)

    @staticmethod
    def _xpath_literal(value):
        escaped = str(value).replace('"', '\\"')
        return f'"{escaped}"'

    def _find_all(self, by, value):
        """统一查找列表，返回 u2 element list。"""
        if not self._using_u2():
            elements = self.driver.find_elements(by=by, value=value)
            if isinstance(elements, (list, tuple)):
                return list(elements)
            try:
                return list(elements)
            except TypeError:
                return []

        if by in (By.ID, By.CLASS_NAME):
            key = "resourceId" if by == By.ID else "className"
            attr = "resource-id" if by == By.ID else "class"
            if by == By.ID:
                value = self._qualify_resource_id(value)
            try:
                xpath_query = f"//*[@{attr}={self._xpath_literal(value)}]"
                matches = self.d.xpath(xpath_query).all()
                if matches:
                    return list(matches)
                return []
            except Exception:
                # 回退到 instance 扫描，兼容测试桩
                results = []
                for index in range(24):
                    selector = self.d(**{key: value, "instance": index})
                    if not self._selector_exists(selector):
                        break
                    try:
                        info = getattr(selector, "info", {}) or {}
                    except Exception:
                        info = {}
                    actual = info.get("resourceId" if by == By.ID else "className")
                    if actual and actual != value:
                        if index == 0:
                            return []
                        break
                    results.append(selector)
                return results

        selector = self._appium_selector_to_u2(by, value)
        if hasattr(selector, "all"):
            return list(selector.all())
        try:
            return list(selector)
        except TypeError:
            return [selector] if self._selector_exists(selector) else []

    def _has_element(self, by, value):
        """快速判断元素是否存在，不等待点击状态。"""
        try:
            if not self._using_u2():
                return len(self.driver.find_elements(by=by, value=value)) > 0
            return self._selector_exists(self._find(by, value))
        except Exception:
            return False

    def _wait_for_element(self, by, value, timeout=1.5):
        """等待元素出现并返回元素对象。"""
        if not self._using_u2():
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )

        selector = self._find(by, value)
        if hasattr(selector, "wait") and selector.wait(timeout=timeout):
            if hasattr(selector, "get"):
                try:
                    return selector.get()
                except Exception:
                    pass
            return selector
        raise TimeoutException(f"timeout waiting element: by={by}, value={value}")

    @staticmethod
    def _selector_exists(selector):
        exists = getattr(selector, "exists", None)
        if callable(exists):
            try:
                return bool(exists(timeout=0))
            except TypeError:
                return bool(exists())
            except Exception:
                return False
        if isinstance(exists, bool):
            return exists
        if hasattr(selector, "wait"):
            try:
                return bool(selector.wait(timeout=0))
            except Exception:
                return False
        return False

    def _has_any_element(self, selectors):
        """Return True if any selector matches immediately."""
        for by, value in selectors:
            if self._has_element(by, value):
                return True
        return False

    # ------------------------------------------------------------------
    # Click operations
    # ------------------------------------------------------------------

    def _click_coordinates(self, x, y, duration=50):
        """Click a fixed screen coordinate via gesture."""
        x = int(x)
        y = int(y)
        if not self._using_u2():
            self.driver.execute_script(
                "mobile: clickGesture",
                {"x": x, "y": y, "duration": duration},
            )
            return

        if duration <= 50:
            self.d.click(x, y)
        else:
            self.d.long_click(x, y, max(duration / 1000, 0.05))

    def _click_element_center(self, element, duration=50):
        """Click the center point of an element via gesture."""
        rect = self._element_rect(element)
        if rect["width"] > 0 and rect["height"] > 0:
            x = rect["x"] + rect["width"] // 2
            y = rect["y"] + rect["height"] // 2
            self._click_coordinates(x, y, duration=duration)
            return

        if hasattr(element, "click"):
            element.click()
            return

        raise RuntimeError("无法定位元素中心点用于点击")

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

    def ultra_fast_click(self, by, value, timeout=1.5):
        """超快速点击 - 适合抢票场景"""
        try:
            el = self._wait_for_element(by, value, timeout=timeout)
            self._click_element_center(el, duration=50)
            return True
        except TimeoutException:
            return False

    def _cached_tap(self, cache_key, by, value, timeout=0.5):
        """在 u2 模式下，首次查找元素并缓存坐标，热路径重试直接手势点击（单次 HTTP 调用）。

        冷路径（cache miss）: selector.wait + selector.info + _click_coordinates = 3 次 HTTP 调用。
        热路径（cache hit）:  _click_coordinates = 1 次 HTTP 调用，跳过所有元素查找。
        Returns True if tapped, False if element not found within timeout.
        """
        cache_getter = getattr(self, "_get_hot_coord", None)
        cached = (
            cache_getter(cache_key)
            if callable(cache_getter)
            else self._cached_hot_path_coords.get(cache_key)
        )
        if cached:
            self._click_coordinates(*cached)
            return True
        if not self._using_u2():
            return self.ultra_fast_click(by, value, timeout=timeout)
        try:
            selector = self._appium_selector_to_u2(by, value)
            if not selector.wait(timeout=timeout):
                return False
            info = selector.info
            bounds = info.get("bounds") if isinstance(info, dict) else {}
            if isinstance(bounds, dict) and "left" in bounds:
                x = (int(bounds["left"]) + int(bounds["right"])) // 2
                y = (int(bounds["top"]) + int(bounds["bottom"])) // 2
                cache_putter = getattr(self, "_put_hot_coord", None)
                if callable(cache_putter):
                    cache_putter(cache_key, (x, y))
                else:
                    self._cached_hot_path_coords[cache_key] = (x, y)
                self._click_coordinates(x, y)
                return True
            # Fallback: couldn't extract bounds — click via element center (no caching).
            el = selector.get()
            self._click_element_center(el, duration=50)
            return True
        except Exception:
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
                el = self._wait_for_element(by, value, timeout=timeout)
                rect = self._element_rect(el)
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
            self._click_coordinates(x, y, duration=30)
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
                el = self._wait_for_element(selector_by, selector_value, timeout=timeout)
                self._click_element_center(el, duration=50)
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
                self._wait_for_element(selector_by, selector_value, timeout=timeout)
                return True
            except TimeoutException:
                continue
        return False

    def _press_keycode_safe(self, keycode, context=""):
        """Press an Android keycode with error handling to avoid hard crashes."""
        try:
            if not self._using_u2():
                self.driver.press_keycode(keycode)
            else:
                u2_key = {4: "back", 66: "enter"}.get(keycode, keycode)
                self.d.press(u2_key)
            return True
        except Exception as exc:
            suffix = f"（{context}）" if context else ""
            logger.warning(f"按键事件失败{suffix}: keycode={keycode}, err={exc}")
            return False

    # ------------------------------------------------------------------
    # Element inspection
    # ------------------------------------------------------------------

    def _element_rect(self, element):
        """返回统一 rect 结构：{'x','y','width','height'}。"""
        if hasattr(element, "rect"):
            rect = element.rect
            if isinstance(rect, dict):
                return rect
            if isinstance(rect, (list, tuple)) and len(rect) == 4:
                x, y, width, height = [int(v) for v in rect]
                return {
                    "x": x,
                    "y": y,
                    "width": max(0, width),
                    "height": max(0, height),
                }

        try:
            bounds_tuple = getattr(element, "bounds", None)
            if isinstance(bounds_tuple, (list, tuple)) and len(bounds_tuple) == 4:
                left, top, right, bottom = [int(v) for v in bounds_tuple]
                return {
                    "x": left,
                    "y": top,
                    "width": max(0, right - left),
                    "height": max(0, bottom - top),
                }
        except Exception:
            pass

        bounds = element.info.get("bounds") or {}
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        right = int(bounds.get("right", left))
        bottom = int(bounds.get("bottom", top))
        return {
            "x": left,
            "y": top,
            "width": max(0, right - left),
            "height": max(0, bottom - top),
        }

    @staticmethod
    def _is_clickable(element):
        if hasattr(element, "get_attribute"):
            try:
                return str(element.get_attribute("clickable")).lower() == "true"
            except Exception:
                return False
        try:
            return bool(element.info.get("clickable", False))
        except Exception:
            return False

    @staticmethod
    def _is_checked(element):
        if hasattr(element, "get_attribute"):
            try:
                return str(element.get_attribute("checked")).lower() == "true"
            except Exception:
                return False
        try:
            return bool(element.info.get("checked", False))
        except Exception:
            return False

    def _container_find_elements(self, container, by, value):
        """容器内元素查找，兼容 Appium element 与 u2 selector。"""
        if container is self.driver:
            return self._find_all(by, value)

        if not self._using_u2():
            elements = container.find_elements(by=by, value=value)
            if isinstance(elements, (list, tuple)):
                return list(elements)
            try:
                return list(elements)
            except TypeError:
                return []

        if by == By.ID:
            container_elem = getattr(container, "elem", None)
            if container_elem is not None and hasattr(container_elem, "iter"):
                try:
                    return [node for node in container_elem.iter() if node.get("resource-id") == value]
                except Exception:
                    pass
            results = []
            for index in range(24):
                child = container.child(resourceId=value, instance=index)
                if not self._selector_exists(child):
                    break
                try:
                    info = getattr(child, "info", {}) or {}
                except Exception:
                    info = {}
                actual = info.get("resourceId")
                if actual and actual != value:
                    if index == 0:
                        return []
                    break
                results.append(child)
            return results
        if by == By.CLASS_NAME:
            container_elem = getattr(container, "elem", None)
            if container_elem is not None and hasattr(container_elem, "iter"):
                try:
                    return [node for node in container_elem.iter() if node.get("class") == value]
                except Exception:
                    pass
            results = []
            for index in range(24):
                child = container.child(className=value, instance=index)
                if not self._selector_exists(child):
                    break
                try:
                    info = getattr(child, "info", {}) or {}
                except Exception:
                    info = {}
                actual = info.get("className")
                if actual and actual != value:
                    if index == 0:
                        return []
                    break
                results.append(child)
            return results
        if by == By.XPATH and value == ".//*":
            return self._collect_descendant_texts(container, return_text=False)
        return []

    def _safe_element_text(self, container, by, value):
        """Read the first child text if present."""
        try:
            elements = self._container_find_elements(container, by, value)
        except Exception:
            return ""

        for element in elements:
            text = self._normalize_element_text(self._read_element_text(element))
            if text:
                return text
        return ""

    def _safe_element_texts(self, container, by, value):
        """Read all non-empty child texts if present."""
        try:
            elements = self._container_find_elements(container, by, value)
        except Exception:
            return []

        texts = []
        seen = set()
        for element in elements:
            text = self._normalize_element_text(self._read_element_text(element))
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    def _read_element_text(self, element):
        """Read element text across Appium and u2 element types."""
        try:
            value = getattr(element, "text", "")
            if isinstance(value, str) and value.strip():
                return value
        except Exception:
            pass

        try:
            if hasattr(element, "get_text"):
                value = element.get_text()
                if isinstance(value, str) and value.strip():
                    return value
        except Exception:
            pass

        try:
            info = getattr(element, "info", {})
            if isinstance(info, dict):
                value = info.get("text", "")
                if isinstance(value, str) and value.strip():
                    return value
        except Exception:
            pass

        try:
            attrib = getattr(element, "attrib", {})
            if attrib is not None and hasattr(attrib, "get"):
                value = attrib.get("text", "")
                if isinstance(value, str) and value.strip():
                    return value
        except Exception:
            pass

        return ""

    @staticmethod
    def _normalize_element_text(value):
        """Normalize UI text values; ignore non-string placeholders from mocked elements."""
        if isinstance(value, str):
            return value.strip()
        return ""

    def _collect_descendant_texts(self, container, return_text=True, xml_root=None):
        """Collect all visible descendant texts under a container.

        xml_root: pre-parsed ET root to avoid a redundant dump_hierarchy() call.
        """
        if not self._using_u2():
            descendants = []
            try:
                descendants = container.find_elements(By.XPATH, ".//*")
            except Exception:
                descendants = []

            if not return_text:
                return descendants

            texts = []
            seen = set()
            for element in descendants:
                try:
                    text = self._normalize_element_text(self._read_element_text(element))
                except Exception:
                    text = ""
                if not text or text in seen:
                    continue
                texts.append(text)
                seen.add(text)
            return texts

        # u2: parse hierarchy XML and keep only nodes inside container bounds.
        texts = []
        seen = set()
        nodes = []
        try:
            container_bounds = container.info.get("bounds")
            if not container_bounds:
                return [] if return_text else []
            outer = (
                int(container_bounds.get("left", 0)),
                int(container_bounds.get("top", 0)),
                int(container_bounds.get("right", 0)),
                int(container_bounds.get("bottom", 0)),
            )
            root = xml_root if xml_root is not None else ET.fromstring(self.d.dump_hierarchy())
            for node in root.iter("node"):
                parsed = self._parse_bounds(node.get("bounds", ""))
                if parsed is None or not self._bounds_inside(parsed, outer):
                    continue
                nodes.append(node)
        except Exception:
            return [] if return_text else []

        if not return_text:
            return nodes

        for node in nodes:
            text = self._normalize_element_text(node.get("text", ""))
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_bounds(bounds_text):
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_text or "")
        if not match:
            return None
        left, top, right, bottom = map(int, match.groups())
        return left, top, right, bottom

    @staticmethod
    def _bounds_inside(inner, outer):
        return (
            inner[0] >= outer[0]
            and inner[1] >= outer[1]
            and inner[2] <= outer[2]
            and inner[3] <= outer[3]
        )

    def _qualify_resource_id(self, value: str) -> str:
        """补全裸 ID 为完整 resourceId（如 'img_jia' → 'cn.damai:id/img_jia'）。"""
        if not value or ":id/" in value:
            return value
        pkg = getattr(self.config, "app_package", "cn.damai")
        return f"{pkg}:id/{value}"

    def _appium_selector_to_u2(self, by, value):
        if by == By.ID:
            return self.d(resourceId=self._qualify_resource_id(value))
        if by == By.CLASS_NAME:
            return self.d(className=value)
        if by == By.XPATH:
            return self.d.xpath(value)
        if by in self._uiautomator_by_values():
            return self._parse_uiselector(value)
        raise ValueError(f"不支持的 by 类型: {by}")

    def _parse_uiselector(self, uiselector_str):
        """将常见 UiSelector 表达式转换为 u2 selector。"""
        kwargs = {}
        for pattern, key in [
            (r'\.text\("([^"]+)"\)', "text"),
            (r'\.textContains\("([^"]+)"\)', "textContains"),
            (r'\.textMatches\("([^"]+)"\)', "textMatches"),
            (r'\.className\("([^"]+)"\)', "className"),
        ]:
            match = re.search(pattern, uiselector_str)
            if match:
                kwargs[key] = match.group(1)

        match = re.search(r"\.clickable\((true|false)\)", uiselector_str)
        if match:
            kwargs["clickable"] = match.group(1) == "true"

        # Appium UiSelector 既出现过 instance(N) 也出现过 index(N)。
        match = re.search(r"\.(instance|index)\((\d+)\)", uiselector_str)
        if match:
            kwargs["instance"] = int(match.group(2))

        if not kwargs:
            raise ValueError(f"无法解析 UiSelector: {uiselector_str!r}")
        return self.d(**kwargs)

    def _dump_hierarchy_xml(self):
        """Return a parsed ET root for the current UI hierarchy, or None on error."""
        if not self._using_u2():
            return None
        try:
            return ET.fromstring(self.d.dump_hierarchy())
        except Exception:
            return None

    def _get_current_activity(self):
        """获取当前 Activity，失败时返回空字符串。"""
        try:
            if not self._using_u2():
                return self.driver.current_activity or ""
            return (self.d.app_current() or {}).get("activity", "") or ""
        except Exception:
            return ""

    def _extract_coords_from_xml_node(self, node):
        """Extract center (x, y) from an XML node's bounds attribute."""
        bounds = self._parse_bounds(node.get("bounds", ""))
        if bounds:
            left, top, right, bottom = bounds
            return ((left + right) // 2, (top + bottom) // 2)
        return None

    @staticmethod
    def _xml_find_text_by_resource_id(xml_root, resource_id):
        """Return text of the first node matching resource_id in a pre-parsed hierarchy XML."""
        if xml_root is None:
            return ""
        for node in xml_root.iter("node"):
            if node.get("resource-id") == resource_id:
                text = (node.get("text") or "").strip()
                if text:
                    return text
        return ""
