# -*- coding: UTF-8 -*-
"""
FastPipeline — Global-deadline ticket purchase pipeline.

Replaces the cascading-timeout approach (SKU 6s + confirm 8s = 14s dead time)
with a single 5s global deadline shared across all phases.
"""

import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, List, Optional, Tuple

from selenium.webdriver.common.by import By

try:
    from mobile.logger import get_logger
except ImportError:
    from logger import get_logger

logger = get_logger(__name__)

_PIPELINE_DEADLINE_S = 5.0

# Keys that must all exist in _cached_coords for a warm run.
_WARM_REQUIRED_KEYS = frozenset({
    "detail_buy",
    "price",
    "sku_buy",
    "attendee_checkboxes",
})


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def poll_until(condition_fn: Callable[[], bool], deadline: float,
               interval_s: float = 0.05) -> bool:
    """Poll *condition_fn* until it returns True or *deadline* is exceeded.

    Returns True if the condition was met before the deadline, False otherwise.
    """
    while time.time() < deadline:
        if condition_fn():
            return True
        time.sleep(interval_s)
    return False


def batch_shell_taps(device, coordinates: List[Tuple[int, int]]) -> None:
    """Send multiple ``input tap x y`` commands in a single shell call."""
    if not coordinates:
        return
    cmd = "; ".join(f"input tap {x} {y}" for x, y in coordinates)
    device.shell(cmd)


# ---------------------------------------------------------------------------
# FastPipeline
# ---------------------------------------------------------------------------

class FastPipeline:
    """Coordinate-driven pipeline with a single global deadline."""

    def __init__(self, device, config, probe: bool, guard):
        self._device = device
        self._config = config
        self._probe = probe
        self._guard = guard
        self._bot = None

        self._cached_coords: dict = {}
        self._cached_no_match: set = set()

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference for delegation."""
        self._bot = bot

    # -- Public helpers -----------------------------------------------------

    def has_warm_coords(self) -> bool:
        """True when all keys required for a warm run are cached."""
        return _WARM_REQUIRED_KEYS.issubset(self._cached_coords.keys())

    # -- Warm path ----------------------------------------------------------

    def run_warm(self, start_time: float) -> Optional[bool]:
        """Execute the warm (cached-coordinate) pipeline.

        Returns True on success, None on timeout / failure.
        """
        deadline = start_time + _PIPELINE_DEADLINE_S

        # Step 1: batch city + detail_buy taps
        taps: List[Tuple[int, int]] = []
        city_coord = self._cached_coords.get("city")
        if city_coord is not None:
            taps.append(city_coord)
        detail_buy_coord = self._cached_coords.get("detail_buy")
        if detail_buy_coord is not None:
            taps.append(detail_buy_coord)
        if taps:
            batch_shell_taps(self._device, taps)

        if time.time() >= deadline:
            return None

        # Step 2: background blind clicker (price + sku_buy every 20ms)
        stop_event = threading.Event()
        price_coord = self._cached_coords.get("price")
        sku_buy_coord = self._cached_coords.get("sku_buy")

        def _blind_clicker():
            blind_taps: List[Tuple[int, int]] = []
            if price_coord is not None:
                blind_taps.append(price_coord)
            if sku_buy_coord is not None:
                blind_taps.append(sku_buy_coord)
            if not blind_taps:
                return
            while not stop_event.is_set():
                batch_shell_taps(self._device, blind_taps)
                stop_event.wait(0.02)

        clicker_thread = threading.Thread(target=_blind_clicker, daemon=True)
        clicker_thread.start()

        try:
            # Step 3: poll for attendee checkbox
            checkbox_found = poll_until(
                lambda: self._has_checkbox(),
                deadline=deadline,
            )
            if not checkbox_found:
                return None

            # Step 4: click attendee coordinates
            attendee_coord = self._cached_coords.get("attendee_checkboxes")
            if attendee_coord is not None:
                batch_shell_taps(self._device, [attendee_coord])
            return True
        finally:
            stop_event.set()
            clicker_thread.join(timeout=1.0)

    # -- Cold path ----------------------------------------------------------

    def run_cold(self, start_time: float) -> Optional[bool]:
        """Execute the cold (XML-dump) pipeline.

        Returns True on success, None on timeout / failure.
        """
        deadline = start_time + _PIPELINE_DEADLINE_S

        # Phase 1: initial XML dump
        if time.time() >= deadline:
            return None
        try:
            xml_src = self._device.dump_hierarchy()
            if xml_src:
                ET.fromstring(xml_src)
        except Exception:
            logger.debug("cold pipeline: initial XML dump failed")

        # Phase 2: poll for SKU page
        if time.time() >= deadline:
            return None
        sku_found = poll_until(
            lambda: self._has_sku_layout(),
            deadline=deadline,
        )
        if not sku_found:
            return None

        # Phase 3: SKU XML dump
        if time.time() >= deadline:
            return None
        try:
            xml_src = self._device.dump_hierarchy()
            if xml_src:
                ET.fromstring(xml_src)
        except Exception:
            logger.debug("cold pipeline: SKU XML dump failed")

        # Phase 4: poll for confirm checkbox
        if time.time() >= deadline:
            return None
        checkbox_found = poll_until(
            lambda: self._has_checkbox(),
            deadline=deadline,
        )
        if not checkbox_found:
            return None

        return True

    # -- Private helpers ----------------------------------------------------

    def _has_checkbox(self) -> bool:
        """Check for attendee checkbox via u2 element lookup."""
        try:
            el = self._device(resourceId="cn.damai:id/checkbox")
            return el.exists
        except Exception:
            return False

    def _has_sku_layout(self) -> bool:
        """Check for SKU layout via u2 element lookup."""
        try:
            el = self._device(resourceId="cn.damai:id/layout_sku")
            return el.exists
        except Exception:
            return False

    # -- Migrated pipeline methods (from DamaiBot) --------------------------

    def rush_preselect_and_buy_via_xml(self):
        """Cold rush path: single XML dump to find city/date/buy button and cache coords.

        Replaces 3-6 sequential _cached_tap HTTP calls (~3-4s cold) with 1 dump_hierarchy
        (~0.3s) + local XML parsing + 2-3 cached clicks (~0.2-0.3s).
        Returns (buy_clicked: bool).
        """
        bot = self._bot
        xml_root = bot._dump_hierarchy_xml()
        if xml_root is None:
            return False

        # --- Extract coords from single XML dump ---
        buy_coords = None
        city_coords = None
        date_coords = None

        for node in xml_root.iter("node"):
            rid = node.get("resource-id", "")
            text = node.get("text", "")

            # Purchase button (detail page)
            if not buy_coords and rid in (
                "trade_project_detail_purchase_status_bar_container_fl",
                "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
            ):
                buy_coords = bot._extract_coords_from_xml_node(node)

            # City match
            if not city_coords and self._config.city and text and self._config.city in text:
                city_coords = bot._extract_coords_from_xml_node(node)

            # Date match
            if not date_coords and self._config.date and text and self._config.date in text:
                date_coords = bot._extract_coords_from_xml_node(node)

        # --- Cache coords and batch-click via shell ---
        tap_cmds = []
        if date_coords:
            self._cached_coords["date"] = date_coords
            tap_cmds.append(f"input tap {int(date_coords[0])} {int(date_coords[1])}")
            logger.info(f"极速模式预选日期: {self._config.date}")
        elif self._config.date:
            self._cached_no_match.add("date")

        if city_coords:
            self._cached_coords["city"] = city_coords
            tap_cmds.append(f"input tap {int(city_coords[0])} {int(city_coords[1])}")
            logger.info(f"极速模式预选城市: {self._config.city}")
        elif self._config.city:
            self._cached_no_match.add("city")

        if buy_coords:
            self._cached_coords["detail_buy"] = buy_coords
            logger.info("点击购票按钮...")
            tap_cmds.append(f"input tap {int(buy_coords[0])} {int(buy_coords[1])}")
            self._device.shell("; ".join(tap_cmds))
            return True

        if tap_cmds:
            self._device.shell("; ".join(tap_cmds))
        return False

    def run_cold_validation(self, start_time):
        """Fast cold validation: XML dump -> shell batch -> concurrent polling.

        Returns True on success, None to fall back to the normal flow.
        """
        bot = self._bot

        # --- Phase 1: detail page --- XML dump for city/buy coords, shell batch click ---
        if not self.rush_preselect_and_buy_via_xml():
            return None

        # --- Phase 2: poll for SKU page ---
        logger.info("选择票价...")
        global_deadline = start_time + _PIPELINE_DEADLINE_S
        sku_detected = False
        while time.time() < global_deadline:
            if bot._has_element(By.ID, "cn.damai:id/layout_sku"):
                sku_detected = True
                break
        if not sku_detected:
            # May have jumped straight to confirm page (e.g. single-price event).
            if bot._has_element(By.ID, "cn.damai:id/checkbox"):
                return self._finish_confirm(start_time)
            return None

        # --- Phase 3: SKU page --- single XML dump for price + sku_buy coords ---
        sku_xml = bot._dump_hierarchy_xml()
        if sku_xml is None:
            return None
        price_coords = bot._get_price_option_coordinates_by_config_index(xml_root=sku_xml)
        sku_buy_coords = bot._get_buy_button_coordinates(xml_root=sku_xml)
        if not price_coords or not sku_buy_coords:
            return None

        # Cache for future warm-pipeline runs.
        self._cached_coords["price"] = price_coords
        self._cached_coords["sku_buy"] = sku_buy_coords

        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        logger.info("选择数量...")
        logger.info("确定购买...")

        # --- Phase 4: shell batch price + buy, concurrent poll for confirm ---
        px, py = int(price_coords[0]), int(price_coords[1])
        bx, by = int(sku_buy_coords[0]), int(sku_buy_coords[1])
        # Fire the initial price + buy clicks via shell.
        self._device.shell(f"input tap {px} {py}; input tap {bx} {by}")

        # Background blind clicker keeps retrying in case the first tap was too early.
        stop_event = threading.Event()
        tap_cmd = f"input tap {px} {py}; input tap {bx} {by}"

        def _blind_click_loop():
            while not stop_event.is_set():
                try:
                    self._device.shell(tap_cmd)
                except Exception:
                    pass
                if stop_event.wait(timeout=0.02):
                    break

        clicker = threading.Thread(target=_blind_click_loop, daemon=True)
        clicker.start()

        confirmed = False
        while time.time() < global_deadline:
            if bot._has_element(By.ID, "cn.damai:id/checkbox"):
                confirmed = True
                break

        stop_event.set()
        clicker.join(timeout=0.3)

        if not confirmed:
            return None

        return self._finish_confirm(start_time)

    def _finish_confirm(self, start_time):
        """Shared tail for the cold pipeline: select attendees on confirm page."""
        bot = self._bot
        required_count = max(1, len(self._config.users or []))
        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）")
        logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")

        # Find and click attendee checkboxes (also caches coords for warm pipeline).
        checkbox_elements = bot._attendee_checkbox_elements()
        if checkbox_elements:
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
                self._cached_coords["attendee_checkboxes"] = _coords
            for checkbox in checkbox_elements[:required_count]:
                bot._click_attendee_checkbox_fast(checkbox)

        bot._set_run_outcome("validation_ready")
        logger.info("if_commit_order=False，已完成观演人勾选，停止在\"立即提交\"前")
        logger.info(f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {time.time() - start_time:.2f}秒")
        return True

    def run_warm_validation(self, start_time):
        """Ultra-fast warm validation: blind shell clicks + concurrent polling.

        Returns True on success, None to fall back to the normal flow.
        """
        bot = self._bot
        coords = self._cached_coords
        no_match = self._cached_no_match

        detail_buy = coords["detail_buy"]
        price = coords["price"]
        sku_buy = coords["sku_buy"]
        attendees = coords["attendee_checkboxes"]
        city = coords.get("city")
        required_count = max(1, len(self._config.users or []))

        # --- Step 1: city preselect + detail_buy via batched shell --------
        tap_cmds = []
        if city and "city" not in no_match:
            tap_cmds.append(f"input tap {int(city[0])} {int(city[1])}")
            logger.info(f"极速模式预选城市: {self._config.city}")
        logger.info("点击购票按钮...")
        tap_cmds.append(f"input tap {int(detail_buy[0])} {int(detail_buy[1])}")
        self._device.shell("; ".join(tap_cmds))

        # --- Step 2: background blind clicker ----------------------------
        stop_event = threading.Event()
        px, py = int(price[0]), int(price[1])
        bx, by = int(sku_buy[0]), int(sku_buy[1])
        tap_cmd = f"input tap {px} {py}; input tap {bx} {by}"

        def _blind_click_loop():
            while not stop_event.is_set():
                try:
                    self._device.shell(tap_cmd)
                except Exception:
                    pass
                if stop_event.wait(timeout=0.02):
                    break

        clicker = threading.Thread(target=_blind_click_loop, daemon=True)
        clicker.start()

        # --- Step 3: main thread polls for confirm page ------------------
        logger.info("选择票价...")
        logger.info(f"通过配置索引直接选择票价: price_index={self._config.price_index}")
        logger.info("选择数量...")
        logger.info("确定购买...")

        confirmed = False
        deadline = start_time + _PIPELINE_DEADLINE_S
        while time.time() < deadline:
            if bot._has_element(By.ID, "cn.damai:id/checkbox"):
                confirmed = True
                break

        stop_event.set()
        clicker.join(timeout=0.3)

        if not confirmed:
            return None

        # --- Step 4: click attendees -------------------------------------
        logger.info(f"检测到观演人未选择完成，尝试自动补选（已选 0/{required_count}）")
        logger.info("开发验证极速路径：按勾选框顺序快速补选观演人")
        for c in attendees[:required_count]:
            bot._click_coordinates(*c)

        bot._set_run_outcome("validation_ready")
        logger.info("if_commit_order=False，已完成观演人勾选，停止在\"立即提交\"前")
        logger.info(f"已到订单确认页且观演人已勾选，未提交订单（开发验证），耗时: {time.time() - start_time:.2f}秒")
        return True
