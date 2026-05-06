# -*- coding: UTF-8 -*-
"""Page-level recovery helpers for the Damai mobile flow.

Houses thin recovery functions that surround the main navigator:

- :func:`wait_for_home_ready` — polls the homepage state with a timeout,
  dumping the UI hierarchy on failure (P2 #28).

Kept in a separate module so :mod:`mobile.event_navigator` stays under the
800-line ceiling.  :mod:`mobile.event_navigator` re-exports the public names
so callers/tests can import either path.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, Dict, List, Optional

try:
    from mobile.logger import get_logger
except ImportError:  # pragma: no cover - test-only fallback
    from logger import get_logger  # type: ignore[no-redef]

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HomeNotReadyError(RuntimeError):
    """Raised when :func:`wait_for_home_ready` cannot see the Damai homepage."""


# ---------------------------------------------------------------------------
# Shared XML helpers (used by select_session in event_navigator and by the
# new recovery helpers below).
# ---------------------------------------------------------------------------


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds_center(bounds_str: Optional[str]) -> Optional[tuple]:
    if not bounds_str:
        return None
    match = _BOUNDS_RE.match(bounds_str)
    if not match:
        return None
    x1, y1, x2, y2 = (int(v) for v in match.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _build_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _find_clickable_ancestor(
    parent_map: Dict[ET.Element, ET.Element], node: ET.Element
) -> Optional[ET.Element]:
    cur = parent_map.get(node)
    while cur is not None:
        if cur.get("clickable") == "true":
            return cur
        cur = parent_map.get(cur)
    return None


# ---------------------------------------------------------------------------
# UI dump helpers (XML-only — no screenshots, per P2 brief)
# ---------------------------------------------------------------------------


def _safe_dump_hierarchy(driver: Any) -> str:
    try:
        return driver.dump_hierarchy() or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("dump_hierarchy 失败: %s", exc)
        return ""


def _dump_ui(driver: Any, dump_dir: str, scene: str) -> Optional[str]:
    """Persist the current UI hierarchy to ``<dump_dir>/<scene>_<ts>.xml``.

    Returns the file path on success, or ``None`` if dumping failed.  Failures
    never propagate — the caller still raises whatever error triggered the dump.
    """
    try:
        os.makedirs(dump_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(dump_dir, f"{scene}_{ts}.xml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_safe_dump_hierarchy(driver))
        return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("UI dump 写入失败 (%s): %s", scene, exc)
        return None


def _summarise_ui(xml_str: str, max_items: int = 10) -> Dict[str, List[str]]:
    """Extract visible texts and resource_ids from a hierarchy dump for logging."""
    texts: List[str] = []
    ids: List[str] = []
    if not xml_str:
        return {"texts": texts, "resource_ids": ids}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return {"texts": texts, "resource_ids": ids}
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        if text:
            texts.append(text)
        rid = (node.get("resource-id") or "").strip()
        if rid:
            ids.append(rid)
    return {"texts": texts[:max_items], "resource_ids": ids[:max_items]}


# ---------------------------------------------------------------------------
# Issue #28 — wait_for_home_ready
# ---------------------------------------------------------------------------


def wait_for_home_ready(
    driver: Any,
    probe: "PageProbe",
    *,
    timeout: float = 8.0,
    poll_interval: float = 0.2,
    dump_dir: str = "tmp",
) -> Dict[str, Any]:
    """Wait for the Damai homepage state to be classified.

    Polls ``probe.classify()`` until ``state == "homepage"`` or until ``timeout``
    seconds have elapsed.  On timeout, dumps the UI hierarchy to
    ``<dump_dir>/home_probe_<ts>.xml``, logs visible key texts + resource_ids,
    and raises :class:`HomeNotReadyError`.

    Args:
        driver: A uiautomator2 device exposing ``dump_hierarchy()``.
        probe: A :class:`mobile.page_probe.PageProbe` used to classify state.
        timeout: Maximum seconds to wait for the homepage. Defaults to 8s.
        poll_interval: Seconds between probe attempts.
        dump_dir: Directory to write hierarchy dumps.

    Returns:
        The probe classification dict whose ``state`` is ``"homepage"``.

    Raises:
        HomeNotReadyError: When the homepage state is not detected before
            ``timeout`` elapses.
    """
    deadline = time.time() + max(0.0, float(timeout))
    last_result: Optional[Dict[str, Any]] = None
    while True:
        try:
            probe.invalidate_cache()
        except AttributeError:
            pass
        last_result = probe.classify()
        state = (last_result or {}).get("state")
        if state == "homepage":
            return last_result
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    dump_path = _dump_ui(driver, dump_dir, "home_probe")
    snapshot = _summarise_ui(_safe_dump_hierarchy(driver))
    logger.warning(
        "wait_for_home_ready 超时: timeout=%.1fs state=%s dump=%s texts=%s ids=%s",
        timeout,
        (last_result or {}).get("state"),
        dump_path,
        snapshot["texts"],
        snapshot["resource_ids"],
    )
    raise HomeNotReadyError(
        f"首页未就绪 (timeout={timeout:.1f}s, "
        f"state={(last_result or {}).get('state')}, dump={dump_path})"
    )
