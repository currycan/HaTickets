# -*- coding: UTF-8 -*-
"""Page-level recovery helpers for the Damai mobile flow.

Houses two thin recovery functions that surround the main navigator:

- :func:`wait_for_home_ready` — polls the homepage state with a timeout,
  dumping the UI hierarchy on failure (P2 #28).
- :func:`select_search_result` — waits for the search results list and
  picks 0/1/N results with deterministic fallback rules (P2 #23).

Kept in a separate module so :mod:`mobile.event_navigator` stays under the
800-line ceiling.  :mod:`mobile.event_navigator` re-exports the public
names so callers/tests can import either path.
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


class SearchEmptyError(RuntimeError):
    """Raised when :func:`select_search_result` finds zero result cards."""


class SearchAmbiguousError(RuntimeError):
    """Raised when :func:`select_search_result` cannot disambiguate >1 results
    in strict mode (no fallback to the first result is allowed).
    """


# ---------------------------------------------------------------------------
# UI dump helpers (XML-only — no screenshots, per P2 brief)
# ---------------------------------------------------------------------------


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_HOME_READY_RESOURCE_IDS = (
    "cn.damai:id/pioneer_homepage_header_search_btn",
    "cn.damai:id/homepage_header_search",
    "cn.damai:id/homepage_header_search_layout",
)
_SEARCH_RESULT_RESOURCE_ID = "cn.damai:id/ll_search_item"
_SEARCH_RESULT_TITLE_RESOURCE_ID = "cn.damai:id/tv_project_name"


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


# ---------------------------------------------------------------------------
# Issue #23 — select_search_result
# ---------------------------------------------------------------------------


def _enumerate_search_results_from_xml(xml_str: str) -> List[Dict[str, Any]]:
    """Parse a u2 hierarchy dump and return one descriptor per search result card."""
    if not xml_str:
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    results: List[Dict[str, Any]] = []
    for node in root.iter("node"):
        if node.get("resource-id") != _SEARCH_RESULT_RESOURCE_ID:
            continue
        title = ""
        for desc in node.iter("node"):
            if desc.get("resource-id") == _SEARCH_RESULT_TITLE_RESOURCE_ID:
                title = (desc.get("text") or "").strip()
                break
        results.append(
            {
                "index": len(results),
                "title": title,
                "bounds": node.get("bounds"),
            }
        )
    return results


def _click_search_result(driver: Any, item: Dict[str, Any]) -> None:
    coords = _parse_bounds_center(item.get("bounds"))
    if coords is None:
        raise RuntimeError(f"无法解析搜索结果 bounds={item.get('bounds')!r}")
    driver.click(*coords)


def _fuzzy_match(target: str, title: str) -> bool:
    if not target or not title:
        return False
    t = target.strip().lower()
    h = title.strip().lower()
    if not t or not h:
        return False
    return t in h or h in t


def select_search_result(
    driver: Any,
    *,
    keyword: str = "",
    target_title: Optional[str] = None,
    timeout: float = 5.0,
    poll_interval: float = 0.2,
    strict: bool = False,
    dump_dir: str = "tmp",
) -> Dict[str, Any]:
    """Wait for the search result list, then pick a result with 0/1/N rules.

    Polls ``driver.dump_hierarchy()`` every ``poll_interval`` seconds for up to
    ``timeout`` seconds, then applies these branches:

    - **0 results** → dump UI, log ``keyword``, raise :class:`SearchEmptyError`.
    - **1 result** → click it and return its descriptor.
    - **N results** → fuzzy-match against ``target_title``.  On match, click it.
      Otherwise, click the first card (when ``strict=False``) or raise
      :class:`SearchAmbiguousError` (when ``strict=True``).

    Args:
        driver: u2 device exposing ``dump_hierarchy()`` and ``click(x, y)``.
        keyword: Original search keyword — logged on empty results to aid
            debugging of split/normalisation issues.
        target_title: Optional title fragment for fuzzy matching when N>1.
        timeout: Maximum seconds to wait for at least one result card.
        poll_interval: Seconds between hierarchy polls (default 0.2 → 5s/25 polls).
        strict: When True and no card matches ``target_title``, raise
            :class:`SearchAmbiguousError` instead of falling back to the first.
        dump_dir: Directory for failure UI dumps (created if missing).

    Returns:
        The chosen result descriptor (``{index, title, bounds}``).

    Raises:
        SearchEmptyError: No result cards visible after ``timeout`` seconds.
        SearchAmbiguousError: ``strict=True`` and N>1 with no fuzzy match.
    """
    deadline = time.time() + max(0.0, float(timeout))
    results: List[Dict[str, Any]] = []
    while True:
        results = _enumerate_search_results_from_xml(_safe_dump_hierarchy(driver))
        if results:
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    if not results:
        dump_path = _dump_ui(driver, dump_dir, "search_probe")
        logger.warning(
            "search_result 0 条 keyword=%r timeout=%.1fs dump=%s",
            keyword,
            timeout,
            dump_path,
        )
        raise SearchEmptyError(
            f"搜索结果为空 (keyword={keyword!r}, timeout={timeout:.1f}s, "
            f"dump={dump_path})"
        )

    if len(results) == 1:
        chosen = results[0]
        logger.info(
            "select_search_result: 唯一结果 idx=0 title=%r",
            chosen.get("title"),
        )
        _click_search_result(driver, chosen)
        return chosen

    if target_title:
        for item in results:
            if _fuzzy_match(target_title, item.get("title") or ""):
                logger.info(
                    "select_search_result: 模糊匹配 idx=%d title=%r target=%r",
                    item["index"],
                    item.get("title"),
                    target_title,
                )
                _click_search_result(driver, item)
                return item

    if strict:
        dump_path = _dump_ui(driver, dump_dir, "search_probe")
        titles = [r.get("title") for r in results[:5]]
        logger.warning(
            "search_result 歧义 keyword=%r target_title=%r titles=%s dump=%s",
            keyword,
            target_title,
            titles,
            dump_path,
        )
        raise SearchAmbiguousError(
            f"搜索结果歧义 (共 {len(results)} 条, target_title={target_title!r}, "
            f"dump={dump_path})"
        )

    chosen = results[0]
    logger.warning(
        "select_search_result: %d 条结果未匹配 target_title=%r，回退到 idx=0 title=%r",
        len(results),
        target_title,
        chosen.get("title"),
    )
    _click_search_result(driver, chosen)
    return chosen
