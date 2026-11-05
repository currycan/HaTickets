"""EventNavigator — search and navigate to the target event in the Damai app.

Uses a delegate pattern: holds a reference to DamaiBot and calls its
navigation methods. This provides a clean interface for RecoveryHelper
while the actual implementation lives here.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from selenium.webdriver.common.by import By

from mobile.ui_primitives import ANDROID_UIAUTOMATOR
from selenium.common.exceptions import TimeoutException

from mobile.logger import get_logger, log_event

try:
    from mobile.item_resolver import (
        city_keyword,
        find_conflicting_city,
        normalize_text,
        title_similarity,
    )
except ImportError:
    from item_resolver import (  # type: ignore[no-redef]
        city_keyword,
        find_conflicting_city,
        normalize_text,
        title_similarity,
    )

try:
    from mobile.date_utils import normalize_date
except ImportError:
    from date_utils import normalize_date  # type: ignore[no-redef]

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)
_monotonic = time.monotonic


# issue #51+#50 搜索/标题匹配阈值（离线校准，调参原则：负向信号——城市/年份
# 冲突——永远优先于放松匹配）：
# - _TITLE_FUZZY_THRESHOLD：标题模糊回退的相似度下限（词序颠倒 0.870 /
#   官方词序 0.823 / 省略号截断 0.854 需通过；无关演出 <=0.31 需拒绝）
# - _CLICK_SCORE_THRESHOLD：搜索卡点击分数阈值（原硬编码 60 提取为常量）
# - _SIMILARITY_BONUS_MIN：打分相似度加分的下限（#50 巡演写法 0.482 需加分，
#   无关演出 0.314 不加分）
_TITLE_FUZZY_THRESHOLD = 0.75
_CLICK_SCORE_THRESHOLD = 60
_SIMILARITY_BONUS_MIN = 0.45


# Re-export the page-recovery helpers so callers and tests can keep importing
# from ``mobile.event_navigator``.  Implementations live in
# :mod:`mobile.page_helpers` to keep this module under the 800-line ceiling.
from mobile.page_helpers import (  # noqa: E402, F401
    HomeNotReadyError,
    SearchAmbiguousError,
    SearchEmptyError,
    _build_parent_map,
    _find_clickable_ancestor,
    _parse_bounds_center,
    select_search_result,
    wait_for_home_ready,
)


# ---------------------------------------------------------------------------
# Multi-session selection (P1 #25, Step 2)
# ---------------------------------------------------------------------------


class SessionNotFoundError(RuntimeError):
    """Raised when :func:`select_session` cannot identify a unique target."""


_SESSION_PANEL_RESOURCE_ID = "cn.damai:id/sku_panel_dates"
_SESSION_DATE_RESOURCE_ID = "cn.damai:id/tv_date"


def _enumerate_sessions_from_xml(xml_str: str) -> List[Dict[str, Any]]:
    """Parse a u2 hierarchy dump and return one descriptor per session card."""
    if not xml_str:
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    panel = None
    for node in root.iter("node"):
        if node.get("resource-id") == _SESSION_PANEL_RESOURCE_ID:
            panel = node
            break
    if panel is None:
        return []

    parent_map = _build_parent_map(root)
    cards: List[Dict[str, Any]] = []
    for date_node in panel.iter("node"):
        if date_node.get("resource-id") != _SESSION_DATE_RESOURCE_ID:
            continue
        clickable = _find_clickable_ancestor(parent_map, date_node)
        if clickable is None:
            clickable = date_node
        texts = [
            (desc.get("text") or "").strip()
            for desc in clickable.iter("node")
            if (desc.get("text") or "").strip()
        ]
        cards.append(
            {
                "index": len(cards),
                "date": (date_node.get("text") or "").strip(),
                "text": " ".join(texts),
                "bounds": clickable.get("bounds") or date_node.get("bounds"),
            }
        )
    return cards


def _date_equals(card_date: str, target_date: str) -> bool:
    if not card_date or not target_date:
        return False
    norm_card = normalize_date(card_date) or card_date.strip()
    norm_target = normalize_date(target_date) or target_date.strip()
    return norm_card == norm_target


def _click_session_card(driver: Any, card: Dict[str, Any]) -> None:
    coords = _parse_bounds_center(card.get("bounds"))
    if coords is None:
        raise SessionNotFoundError(f"无法解析场次卡片 bounds={card.get('bounds')!r}")
    try:
        driver.click(*coords)
    except Exception as exc:  # noqa: BLE001 — surface as SessionNotFoundError
        raise SessionNotFoundError(f"点击场次卡片失败: {exc}") from exc


def select_session(
    driver: Any,
    *,
    date: Optional[str] = None,
    city: Optional[str] = None,
    fallback_index: Optional[int] = None,
    strict: bool = False,
) -> int:
    """Pick a session on the multi-session SKU panel.

    Priority order:

    1. ``date`` + ``city`` exact match (must be unique).
    2. ``date`` alone if it identifies a unique session.
    3. ``fallback_index`` (zero-based) if provided.

    Args:
        driver: A uiautomator2 device (anything with ``dump_hierarchy()``
            and ``click(x, y)``).
        date: Target date in ``MM.DD`` format (output of
            :func:`mobile.date_utils.normalize_date`).
        city: City keyword expected to appear in the card text.
        fallback_index: Zero-based card index used when neither ``date``
            nor ``city`` resolves a unique candidate.
        strict: Require the configured date/city criteria to identify exactly
            one card.  When true, no date-only or index fallback is allowed
            after a date+city mismatch.

    Returns:
        The zero-based index of the selected session.

    Raises:
        SessionNotFoundError: When no unique candidate can be picked.
    """

    def _emit_err(scene: str, type_name: str = "SessionNotFoundError", **extra):
        log_event(
            logger,
            "error",
            level=logging.ERROR,
            type=type_name,
            scene=scene,
            date=date,
            city=city,
            **extra,
        )

    try:
        xml_str = driver.dump_hierarchy()
    except Exception as exc:  # noqa: BLE001
        _emit_err("select_session.dump_hierarchy", type_name=type(exc).__name__)
        raise SessionNotFoundError(f"dump_hierarchy 失败: {exc}") from exc

    cards = _enumerate_sessions_from_xml(xml_str)
    if not cards:
        _emit_err("select_session.no_cards")
        raise SessionNotFoundError(
            "未发现可选场次（sku_panel_dates 内未找到 tv_date 节点）"
        )

    chosen: Optional[Dict[str, Any]] = None

    if strict:
        if date and city:
            matches = [
                c
                for c in cards
                if _date_equals(c["date"], date) and city in c["text"]
            ]
            criteria = f"date={date} + city={city}"
        elif date:
            matches = [c for c in cards if _date_equals(c["date"], date)]
            criteria = f"date={date}"
        elif city:
            matches = [c for c in cards if city in c["text"]]
            criteria = f"city={city}"
        else:
            _emit_err("select_session.strict_no_criteria", total=len(cards))
            raise SessionNotFoundError("正式模式未配置 date/city，无法确认目标场次")

        if len(matches) != 1:
            _emit_err(
                "select_session.strict_not_unique",
                matches=len(matches),
                criteria=criteria,
            )
            raise SessionNotFoundError(
                f"{criteria} 命中 {len(matches)} 条场次，正式模式无法唯一确认"
            )
        chosen = matches[0]
        log_event(
            logger,
            "session_selected",
            match="strict",
            date=date,
            city=city,
            index=chosen["index"],
            total=len(cards),
        )

    if chosen is None and date and city:
        matches = [
            c for c in cards if _date_equals(c["date"], date) and city in c["text"]
        ]
        if len(matches) == 1:
            chosen = matches[0]
            log_event(
                logger,
                "session_selected",
                match="date+city",
                date=date,
                city=city,
                index=chosen["index"],
                total=len(cards),
            )
        elif len(matches) > 1:
            _emit_err("select_session.ambiguous_date_city", matches=len(matches))
            raise SessionNotFoundError(
                f"date={date} + city={city} 命中 {len(matches)} 条场次，无法唯一确定"
            )

    if chosen is None and date:
        matches = [c for c in cards if _date_equals(c["date"], date)]
        if len(matches) == 1:
            chosen = matches[0]
            log_event(
                logger,
                "session_selected",
                match="date",
                date=date,
                city=None,
                index=chosen["index"],
                total=len(cards),
            )
        elif len(matches) > 1:
            _emit_err("select_session.ambiguous_date_only", matches=len(matches))
            raise SessionNotFoundError(
                f"date={date} 命中 {len(matches)} 条场次，需配合 city 才能确定"
            )

    if chosen is None and fallback_index is not None:
        if 0 <= fallback_index < len(cards):
            chosen = cards[fallback_index]
            log_event(
                logger,
                "session_selected",
                level=logging.WARNING,
                match="fallback_index",
                index=fallback_index,
                total=len(cards),
            )
        else:
            _emit_err(
                "select_session.fallback_out_of_range",
                fallback_index=fallback_index,
                total=len(cards),
            )
            raise SessionNotFoundError(
                f"fallback_index={fallback_index} 越界（共 {len(cards)} 条场次）"
            )

    if chosen is None:
        _emit_err("select_session.no_criteria", total=len(cards))
        raise SessionNotFoundError(
            f"未提供 date/city/fallback_index，无法选择场次（共 {len(cards)} 条候选）"
        )

    _click_session_card(driver, chosen)
    return chosen["index"]


class EventNavigator:
    """Handles searching and navigating to the target event."""

    def __init__(self, device, config, probe: PageProbe, bot=None) -> None:
        self._d = device
        self._config = config
        self._probe = probe
        self._bot = bot  # DamaiBot reference for delegation
        # discover_target_event 最终失败时的候选缓存（issue #51+#50：
        # prompt_runner 通过 DamaiBot._last_failed_candidates 只读 property
        # 读取，用于失败文案中列出 top-5 候选引导用户补全标题）
        self._last_failed_candidates: list = []
        # Formal submission may only consume identity observed on the current
        # detail/SKU/session page.  Search-card and item-resolver candidates are
        # intentionally excluded from this proof.
        self._last_formal_event_identity: Optional[Dict[str, str]] = None

    def set_bot(self, bot) -> None:
        """Set the DamaiBot reference (breaks circular init dependency)."""
        self._bot = bot

    def navigate_to_target_event(self, initial_probe=None) -> bool:
        """Navigate from current page to the target event detail page."""
        if getattr(self._config, "probe_only", False) is True:
            logger.warning(
                "probe_only 为只读模式，未执行自动搜索或页面导航"
            )
            return False
        if not self._config.auto_navigate:
            logger.warning("auto_navigate 未启用")
            return False

        probe = initial_probe or self._probe.probe_current_page(fast=True)
        if probe["state"] == "detail_page":
            if self._bot is None:
                return True
            if self._bot._current_page_matches_target(probe):
                return True
            logger.warning("当前详情页不是目标演出，重新执行目标导航")

        # Delegate to DamaiBot's real navigation
        if self._bot is not None:
            try:
                return self._bot._navigate_to_target_impl(initial_probe=probe)
            except Exception as exc:
                logger.warning(f"导航失败: {exc}")
                return False

        logger.warning("EventNavigator: no bot reference for navigation")
        return False

    # ------------------------------------------------------------------
    # Migrated method bodies from DamaiBot
    # ------------------------------------------------------------------

    def _keyword_tokens(self):
        """Split the configured keyword into reusable fuzzy-match tokens."""
        keyword = self._config.keyword or ""
        tokens = []
        for raw in re.split(r"[\s,，、|/]+", keyword):
            token = normalize_text(raw)
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
        return tokens

    def _title_matches_target(self, title_text):
        """Check whether a page or search result title matches the configured target."""
        bot = self._bot
        normalized_title = normalize_text(title_text)
        if not normalized_title:
            return False

        # 城市冲突 veto（issue #50 反向风险收紧）：目标城市明确、标题却写着
        # 另一个已知城市时直接拒绝——置于所有通过路径之前，防止错城市场次
        # 被全 token 命中放行。isinstance 守卫兼容 MagicMock config。
        target_city = (
            self._config.city if isinstance(self._config.city, str) else None
        )
        if target_city:
            conflict_city = find_conflicting_city(normalized_title, target_city)
            if conflict_city:
                log_event(
                    logger,
                    "title_city_conflict",
                    level=logging.WARNING,
                    title=title_text,
                    conflict_city=conflict_city,
                    target_city=target_city,
                )
                return False

        candidates = []
        if bot.item_detail:
            candidates.extend(
                [bot.item_detail.item_name, bot.item_detail.item_name_display]
            )
        if self._config.target_title:
            candidates.append(self._config.target_title)
        if self._config.keyword:
            candidates.append(self._config.keyword)

        for candidate in candidates:
            normalized_candidate = normalize_text(candidate)
            if not normalized_candidate:
                continue
            if (
                normalized_candidate in normalized_title
                or normalized_title in normalized_candidate
            ):
                return True

        keyword_tokens = bot._keyword_tokens()
        if keyword_tokens and all(
            token in normalized_title for token in keyword_tokens
        ):
            return True

        # 模糊回退（issue #51）：词序颠倒/短标题前缀/UI 省略号截断等文案变体
        # 在精确路径全部失败后按相似度兜底。
        best_similarity = 0.0
        best_candidate = None
        for candidate in candidates:
            similarity = title_similarity(candidate, title_text)
            if similarity > best_similarity:
                best_similarity = similarity
                best_candidate = candidate
        if best_similarity >= _TITLE_FUZZY_THRESHOLD:
            log_event(
                logger,
                "title_fuzzy_match",
                title=title_text,
                candidate=best_candidate,
                similarity=round(best_similarity, 3),
            )
            return True

        return False

    def _formal_title_matches_target(self, title_text):
        """Require normalized exact title identity for authorised submission."""
        normalized_title = normalize_text(title_text)
        configured_title = getattr(self._config, "target_title", None)
        normalized_target = (
            normalize_text(configured_title)
            if isinstance(configured_title, str)
            else ""
        )
        if not normalized_target:
            log_event(
                logger,
                "formal_target_title_missing",
                level=logging.WARNING,
            )
            return False
        return bool(normalized_title and normalized_title == normalized_target)

    def _record_formal_event_identity(
        self, *, observed_title: str, observed_venue: str = ""
    ) -> None:
        """Keep the exact page observation for the orchestrator proof chain."""
        configured_title = getattr(self._config, "target_title", None)
        configured_venue = getattr(self._config, "target_venue", None)
        self._last_formal_event_identity = {
            "target_title": normalize_text(
                configured_title if isinstance(configured_title, str) else ""
            ),
            "observed_title": observed_title.strip(),
            "observed_title_normalized": normalize_text(observed_title),
            "target_venue": normalize_text(
                configured_venue if isinstance(configured_venue, str) else ""
            ),
            "observed_venue": observed_venue.strip(),
            "observed_venue_normalized": normalize_text(observed_venue),
        }

    def _current_page_matches_target(self, page_probe, clicked_title=None):
        """Check if the current detail/SKU/session page points at the target event.

        Args:
            page_probe: 页面状态探测结果。
            clicked_title: 刚点击的搜索卡片标题原文（issue #51：详情页标题与
                搜索卡片是两套文案，用「刚点击的卡片」锚定校验可容忍词序/
                短标题/截断差异）。``None`` 时保持旧行为。
        """
        bot = self._bot
        formal_commit = (
            getattr(self._config, "if_commit_order", False) is True
        )
        if formal_commit:
            # Never let a prior successful observation survive a later failed
            # state, unreadable page, or identity check.
            self._last_formal_event_identity = None
        if page_probe["state"] not in {
            "detail_page",
            "sku_page",
            "session_picker",
        }:
            return False

        if formal_commit and not normalize_text(
            self._config.target_title
            if isinstance(
                getattr(self._config, "target_title", None), str
            )
            else ""
        ):
            log_event(
                logger,
                "formal_target_title_missing",
                level=logging.WARNING,
            )
            return False

        if (
            not bot.item_detail
            and not self._config.target_title
            and not self._config.keyword
        ):
            return True

        observed_title = page_probe.get("observed_title")
        detail_title = (
            observed_title.strip()
            if isinstance(observed_title, str) and observed_title.strip()
            else bot._get_detail_title_text()
        )
        target_venue = (
            normalize_text(self._config.target_venue)
            if isinstance(
                getattr(self._config, "target_venue", None), str
            )
            else ""
        )
        detail_venue_loaded = False
        detail_venue = ""
        detail_venue_text = ""

        def venue_allows_title_match():
            """Apply configured venue evidence without another hot-path scan."""
            nonlocal detail_venue_loaded, detail_venue, detail_venue_text
            if not target_venue:
                return True
            if not detail_venue_loaded:
                detail_venue_loaded = True
                observed_venue = page_probe.get("observed_venue")
                detail_venue_text = (
                    observed_venue
                    if isinstance(observed_venue, str)
                    and observed_venue.strip()
                    else bot._get_detail_venue_text() or ""
                )
                detail_venue = normalize_text(detail_venue_text)
            if detail_venue and (
                (formal_commit and target_venue == detail_venue)
                or (
                    not formal_commit
                    and (
                        target_venue in detail_venue
                        or detail_venue in target_venue
                    )
                )
            ):
                return True
            if detail_venue:
                log_event(
                    logger,
                    "target_venue_mismatch",
                    level=logging.WARNING,
                    expected_venue=target_venue,
                    observed_venue=detail_venue,
                )
                return False
            if formal_commit:
                log_event(
                    logger,
                    "target_venue_unconfirmed",
                    level=logging.WARNING,
                    expected_venue=target_venue,
                )
                return False
            return True

        if clicked_title:
            if not detail_title:
                # 详情页标题异步渲染读空：延长轮询（真机实测单次 0.3s 重试不够，
                # 慢网络下标题可持续读空数秒，误判 mismatch 会把正确条目拉黑）
                for _ in range(6):
                    time.sleep(0.35)
                    detail_title = bot._get_detail_title_text()
                    if detail_title:
                        break
            if not detail_title:
                # clicked_title 高分但详情标题为空：显式记录，不静默 False
                log_event(
                    logger,
                    "detail_title_empty",
                    level=logging.WARNING,
                    clicked_title=clicked_title,
                )
                if formal_commit:
                    # A matching venue is not unique evidence: one venue can
                    # host multiple projects on the same day.  Formal mode
                    # therefore requires readable title evidence as well.
                    log_event(
                        logger,
                        "target_identity_unconfirmed",
                        level=logging.WARNING,
                        clicked_title=clicked_title,
                        reason="detail_title_empty",
                    )
                    return False
                # 兜底 1：场馆文案可读且与目标场馆一致 → 视为已验证。
                target_venue = normalize_text(self._config.target_venue or "")
                if target_venue:
                    if not detail_venue_loaded:
                        detail_venue_loaded = True
                        observed_venue = page_probe.get("observed_venue")
                        detail_venue_text = (
                            observed_venue
                            if isinstance(observed_venue, str)
                            and observed_venue.strip()
                            else bot._get_detail_venue_text() or ""
                        )
                        detail_venue = normalize_text(detail_venue_text)
                    if detail_venue and (
                        target_venue in detail_venue or detail_venue in target_venue
                    ):
                        log_event(
                            logger,
                            "detail_title_verified",
                            match="venue_fallback",
                            venue=detail_venue,
                        )
                        return True
                # 兜底 2：标题「读不到」≠「不一致」。clicked_title 已经过搜索卡
                # 评分 + 城市冲突否决筛选，此处 reject 会将正确条目加入黑名单
                # 造成死循环（2026-07-11 真机实测）。仅非正式模式带 WARNING
                # 放行；正式模式在上面 fail closed，不能把搜索卡片当成最终证据。
                log_event(
                    logger,
                    "detail_title_unverified_accept",
                    level=logging.WARNING,
                    clicked_title=clicked_title,
                )
                return True
            if formal_commit:
                if not self._formal_title_matches_target(detail_title):
                    log_event(
                        logger,
                        "formal_target_title_mismatch",
                        level=logging.WARNING,
                        detail_title=detail_title,
                        clicked_title=clicked_title,
                    )
                    return False
                if not venue_allows_title_match():
                    return False
                observed_venue = detail_venue_text
                if not observed_venue:
                    snapshot_venue = page_probe.get("observed_venue")
                    if isinstance(snapshot_venue, str):
                        observed_venue = snapshot_venue
                self._record_formal_event_identity(
                    observed_title=detail_title,
                    observed_venue=observed_venue,
                )
                return True
            else:
                normalized_clicked = normalize_text(clicked_title)
                normalized_detail = normalize_text(detail_title)
                similarity = title_similarity(clicked_title, detail_title)
                if (
                    normalized_clicked
                    and normalized_detail
                    and (
                        normalized_clicked in normalized_detail
                        or normalized_detail in normalized_clicked
                    )
                ) or similarity >= _TITLE_FUZZY_THRESHOLD:
                    log_event(
                        logger,
                        "detail_title_verified",
                        match="clicked_card",
                        similarity=round(similarity, 3),
                    )
                    return venue_allows_title_match()

        if formal_commit:
            if not self._formal_title_matches_target(detail_title):
                return False
            if not venue_allows_title_match():
                return False
            observed_venue = detail_venue_text
            if not observed_venue:
                snapshot_venue = page_probe.get("observed_venue")
                if isinstance(snapshot_venue, str):
                    observed_venue = snapshot_venue
            self._record_formal_event_identity(
                observed_title=detail_title,
                observed_venue=observed_venue,
            )
            return True

        if bot._title_matches_target(detail_title):
            return venue_allows_title_match()

        if clicked_title:
            log_event(
                logger,
                "detail_title_mismatch",
                level=logging.WARNING,
                detail_title=detail_title,
                clicked_title=clicked_title,
                keyword=self._config.keyword,
                similarity=round(title_similarity(clicked_title, detail_title), 3),
            )
        return False

    def _open_search_from_homepage(self):
        """Enter the homepage search flow."""
        bot = self._bot
        search_selectors = [
            (By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"),
            (By.ID, "cn.damai:id/homepage_header_search"),
            (By.ID, "cn.damai:id/homepage_header_search_layout"),
            (ANDROID_UIAUTOMATOR, 'new UiSelector().text("搜索")'),
        ]

        for by, value in search_selectors:
            if bot.ultra_fast_click(by, value, timeout=0.8):
                search_probe = bot.wait_for_page_state(
                    {"search_page"}, timeout=2.5, poll_interval=0.15
                )
                if search_probe["state"] == "search_page":
                    return True

        search_probe = bot.probe_current_page()
        if search_probe["state"] == "search_page":
            return True

        logger.warning("未能从首页打开搜索页")
        return False

    def _submit_search_keyword(self):
        """Fill the configured keyword into the Damai search box and submit."""
        bot = self._bot
        if not self._config.keyword:
            logger.warning("缺少 keyword，无法执行自动搜索")
            return False

        with bot._timed_step(
            "搜索页输入并提交关键词",
            manual_baseline_seconds=_MANUAL_STEP_BASELINES.get(
                "搜索页输入并提交关键词"
            ),
        ):
            try:
                search_input = bot._wait_for_element(
                    By.ID, "cn.damai:id/header_search_v2_input", timeout=2
                )
            except TimeoutException:
                logger.warning("未找到搜索输入框")
                return False

            bot._click_element_center(search_input)
            time.sleep(0.12)

            current_text = bot._read_element_text(search_input).strip()
            if current_text and current_text != self._config.keyword:
                if bot._has_element(By.ID, "cn.damai:id/header_search_v2_input_delete"):
                    bot.ultra_fast_click(
                        By.ID, "cn.damai:id/header_search_v2_input_delete", timeout=0.4
                    )
                    time.sleep(0.05)
                else:
                    try:
                        if hasattr(search_input, "clear"):
                            search_input.clear()
                        elif hasattr(search_input, "set_text"):
                            search_input.set_text("")
                    except Exception:
                        pass

            if bot._read_element_text(search_input).strip() != self._config.keyword:
                try:
                    if bot._using_u2() and hasattr(search_input, "set_text"):
                        search_input.set_text(self._config.keyword)
                    elif hasattr(search_input, "send_keys"):
                        search_input.send_keys(self._config.keyword)
                    elif hasattr(search_input, "set_text"):
                        search_input.set_text(self._config.keyword)
                except Exception:
                    return False

            if not bot._press_keycode_safe(66, context="提交搜索关键词"):
                return False
            if bot._has_element(ANDROID_UIAUTOMATOR, 'new UiSelector().text("演出")'):
                bot.smart_wait_and_click(
                    ANDROID_UIAUTOMATOR,
                    'new UiSelector().text("演出")',
                    timeout=0.8,
                )
                time.sleep(0.1)
            try:
                deadline = _monotonic() + 3.5
                while _monotonic() < deadline:
                    if bot._find_all(By.ID, "cn.damai:id/ll_search_item"):
                        break
                    time.sleep(0.04)
                else:
                    raise TimeoutException("搜索结果加载超时")
            except TimeoutException:
                logger.warning("搜索结果加载超时")
                return False

        return True

    def _score_search_result(self, title_text, venue_text, city_text=None):
        """Score a search result against the configured target.

        Args:
            title_text: 搜索卡片标题（tv_project_name）。
            venue_text: 搜索卡片场馆（tv_project_venueName）。
            city_text: 搜索卡片城市字段（tv_project_city，issue #50：此前被
                采集却不参与打分）。``None`` 时跳过城市字段加/罚分。
        """
        bot = self._bot
        normalized_title = normalize_text(title_text)
        normalized_venue = normalize_text(venue_text)
        if not normalized_title:
            return -1

        score = 0
        if bot._title_matches_target(title_text):
            score += 100

        normalized_keyword = normalize_text(self._config.keyword)
        if normalized_keyword:
            if normalized_keyword == normalized_title:
                score += 80
            elif normalized_keyword in normalized_title:
                score += 50

        keyword_tokens = bot._keyword_tokens()
        if keyword_tokens:
            token_hits = sum(1 for token in keyword_tokens if token in normalized_title)
            score += token_hits * 20
            if token_hits == len(keyword_tokens) and len(keyword_tokens) >= 2:
                score += 30

        normalized_city = normalize_text(city_keyword(self._config.city))
        if normalized_city and normalized_city in normalized_title:
            score += 20

        if bot.item_detail:
            expected_venue = normalize_text(bot.item_detail.venue_name)
            if expected_venue and expected_venue in normalized_venue:
                score += 20

            expected_city = normalize_text(bot.item_detail.city_keyword)
            if expected_city and expected_city in normalized_title:
                score += 10

        if self._config.target_venue:
            expected_venue = normalize_text(self._config.target_venue)
            if expected_venue and expected_venue in normalized_venue:
                score += 30

        # 相似度分（issue #50：「巡演」等写法不含连写「演唱会」时，token 分
        # 不够过点击阈值；按 keyword 相似度补分，无关演出 <0.45 不加分）
        keyword_for_similarity = (
            self._config.keyword if isinstance(self._config.keyword, str) else None
        )
        if keyword_for_similarity:
            similarity = title_similarity(keyword_for_similarity, title_text)
            if similarity >= _SIMILARITY_BONUS_MIN:
                score += int(50 * similarity)

        # 城市字段分（issue #50：tv_project_city 参与打分）。加分为 +10 而非
        # +20——对抗审查修正 2：+20 会把「同城无关演出」精确推到点击阈值 60。
        target_city = (
            self._config.city if isinstance(self._config.city, str) else None
        )
        if target_city and isinstance(city_text, str) and city_text:
            normalized_city_text = normalize_text(city_text)
            normalized_target_city = normalize_text(city_keyword(target_city))
            if normalized_target_city and normalized_target_city in normalized_city_text:
                score += 10
            elif find_conflicting_city(city_text, target_city):
                score -= 80
                log_event(
                    logger,
                    "search_result_city_conflict",
                    title=title_text,
                    city_text=city_text,
                    target_city=target_city,
                )

        # 年份冲突罚分：keyword 与标题都写明 4 位年份且无交集时罚 60 分，
        # 防同名跨年巡演误配（限定 19xx/20xx，避免误伤票价类 4 位数字）
        if keyword_for_similarity and isinstance(title_text, str):
            year_pattern = r"(?<!\d)((?:19|20)\d{2})(?!\d)"
            keyword_years = set(re.findall(year_pattern, keyword_for_similarity))
            title_years = set(re.findall(year_pattern, title_text))
            if keyword_years and title_years and not (keyword_years & title_years):
                score -= 60

        return score

    def _scroll_search_results(self):
        """Scroll the search result list upward."""
        bot = self._bot
        if not bot._using_u2():
            bot.driver.execute_script(
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
            return
        bot.d.swipe(540, 1770, 540, 520, duration=0.3)

    def _open_target_from_search_results(
        self, max_scrolls=2, max_results=5, return_details=False
    ):
        """Open the best-matching event from search results and optionally return scanned summaries."""
        bot = self._bot
        seen_titles = set()
        collected = []
        # 详情页校验失败的卡片黑名单：后续扫描仍计入 collected，但不再参与
        # best_match 竞选，避免反复点击同一张失败卡（issue #51 死循环根因）
        rejected_titles = set()

        with bot._timed_step(
            "搜索结果扫描并打开目标",
            manual_baseline_seconds=_MANUAL_STEP_BASELINES.get(
                "搜索结果扫描并打开目标"
            ),
        ):
            for scroll_index in range(max_scrolls + 1):
                result_cards = bot._find_all(By.ID, "cn.damai:id/ll_search_item")
                best_match = None
                best_score = -1
                best_title = None

                for card in result_cards:
                    title_text = bot._safe_element_text(
                        card, By.ID, "cn.damai:id/tv_project_name"
                    )
                    if not title_text:
                        continue

                    venue_text = bot._safe_element_text(
                        card, By.ID, "cn.damai:id/tv_project_venueName"
                    )
                    city_text = (
                        bot._safe_element_text(
                            card, By.ID, "cn.damai:id/tv_project_city"
                        )
                        .replace("|", "")
                        .strip()
                    )
                    time_text = bot._safe_element_text(
                        card, By.ID, "cn.damai:id/tv_project_time"
                    )
                    score = bot._score_search_result(title_text, venue_text, city_text)

                    normalized_title = normalize_text(title_text)
                    if normalized_title and normalized_title not in seen_titles:
                        collected.append(
                            {
                                "title": title_text,
                                "venue": venue_text,
                                "city": city_text,
                                "time": time_text,
                                "score": score,
                            }
                        )
                        seen_titles.add(normalized_title)

                    if normalized_title in rejected_titles:
                        log_event(logger, "search_result_rejected", title=title_text)
                        continue

                    if score > best_score:
                        best_score = score
                        best_match = card
                        best_title = title_text

                if best_match is not None and best_score >= _CLICK_SCORE_THRESHOLD:
                    # A result-card tap may open a different project, even when
                    # the visible title looked similar.  Purge every page scope
                    # before crossing that boundary so no coordinate or
                    # same-snapshot price proof can leak from the prior event.
                    bot._invalidate_all_hot_cache(
                        reason="search_result_opened",
                    )
                    bot._verified_price_selection = None
                    bot._click_element_center(best_match)
                    detail_probe = bot.wait_for_page_state(
                        {"detail_page", "sku_page"}, timeout=5.5
                    )
                    if detail_probe["state"] in {
                        "detail_page",
                        "sku_page",
                    } and bot._current_page_matches_target(
                        detail_probe, clicked_title=best_title
                    ):
                        collected.sort(key=lambda item: item["score"], reverse=True)
                        details = {
                            "opened": True,
                            "search_results": collected[:max_results],
                        }
                        return details if return_details else True

                    logger.warning(
                        "已进入详情页，但标题与目标演出不一致，返回搜索结果继续尝试"
                    )
                    rejected_titles.add(normalize_text(best_title))
                    if not bot._press_keycode_safe(4, context="返回搜索列表"):
                        break
                    time.sleep(0.25)
                    bot.dismiss_startup_popups()
                else:
                    logger.info(
                        f"本屏搜索结果未找到明确匹配项，已扫描: {len(seen_titles)} 条"
                    )

                if scroll_index < max_scrolls:
                    bot._scroll_search_results()
                    time.sleep(0.2)

        logger.warning("自动搜索后未找到目标演出")
        collected.sort(key=lambda item: item["score"], reverse=True)
        details = {"opened": False, "search_results": collected[:max_results]}
        return details if return_details else False

    def collect_search_results(self, max_scrolls=0, max_results=5):
        """Collect search result summaries without opening them."""
        bot = self._bot
        seen = set()
        collected = []

        for scroll_index in range(max_scrolls + 1):
            result_cards = bot._find_all(By.ID, "cn.damai:id/ll_search_item")
            for card in result_cards:
                title_text = bot._safe_element_text(
                    card, By.ID, "cn.damai:id/tv_project_name"
                )
                if not title_text:
                    continue

                normalized_title = normalize_text(title_text)
                if normalized_title in seen:
                    continue

                venue_text = bot._safe_element_text(
                    card, By.ID, "cn.damai:id/tv_project_venueName"
                )
                city_text = (
                    bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_city")
                    .replace("|", "")
                    .strip()
                )
                time_text = bot._safe_element_text(
                    card, By.ID, "cn.damai:id/tv_project_time"
                )
                price_text = bot._build_compound_price_text(card)
                score = bot._score_search_result(title_text, venue_text)

                collected.append(
                    {
                        "title": title_text,
                        "venue": venue_text,
                        "city": city_text,
                        "time": time_text,
                        "price": price_text,
                        "score": score,
                    }
                )
                seen.add(normalized_title)

            if len(collected) >= max_results:
                break

            if scroll_index < max_scrolls:
                bot._scroll_search_results()
                time.sleep(0.4)

        collected.sort(key=lambda item: item["score"], reverse=True)
        return collected[:max_results]

    def _navigate_to_target_impl(self, initial_probe=None):
        """Auto-navigate from homepage/search to the target event detail page."""
        bot = self._bot
        if not self._config.auto_navigate:
            return False

        page_probe = initial_probe or bot.probe_current_page()
        page_probe = bot._recover_to_navigation_start(page_probe)

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and bot._current_page_matches_target(page_probe):
            return True

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and not bot._current_page_matches_target(page_probe):
            page_probe = bot._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and bot._current_page_matches_target(page_probe):
            return True

        if page_probe["state"] == "homepage":
            logger.info("当前位于首页，开始自动搜索目标演出")
            if not bot._open_search_from_homepage():
                return False
            page_probe = bot.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning(f"当前页面不适合自动搜索: {page_probe['state']}")
            return False

        if not bot._submit_search_keyword():
            return False

        return bot._open_target_from_search_results()

    def discover_target_event(
        self, keyword_candidates, initial_probe=None, search_scrolls=1, result_limit=5
    ):
        """Try multiple keywords, collect candidate summaries, and open the best match."""
        if getattr(self._config, "probe_only", False) is True:
            logger.warning(
                "probe_only 为只读观察模式，discover_target_event 未执行返回、"
                "搜索、输入、滚动或结果点击"
            )
            return None
        bot = self._bot
        bot._last_discovery_step_timings = []
        self._last_failed_candidates = []
        page_probe = initial_probe or bot.probe_current_page()
        page_probe = bot._recover_to_navigation_start(page_probe)

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and bot._current_page_matches_target(page_probe):
            return {
                "used_keyword": self._config.keyword,
                "search_results": [],
                "page_probe": page_probe,
                "step_timings": list(bot._last_discovery_step_timings),
            }

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and not bot._current_page_matches_target(page_probe):
            page_probe = bot._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {
            "detail_page",
            "sku_page",
        } and bot._current_page_matches_target(page_probe):
            return {
                "used_keyword": self._config.keyword,
                "search_results": [],
                "page_probe": page_probe,
                "step_timings": list(bot._last_discovery_step_timings),
            }

        if page_probe["state"] == "homepage":
            if not bot._open_search_from_homepage():
                return None
            page_probe = bot.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning(f"当前页面不适合执行提示词检索: {page_probe['state']}")
            return None

        tried = set()
        failed_candidates: List[Dict[str, Any]] = []
        for keyword in keyword_candidates:
            normalized_keyword = normalize_text(keyword)
            if not normalized_keyword or normalized_keyword in tried:
                continue

            if tried:
                bot.dismiss_startup_popups()
                probe = bot.probe_current_page()
                if probe["state"] in {"detail_page", "sku_page"}:
                    probe = bot._exit_non_target_event_context(probe)
                    if probe["state"] in {
                        "detail_page",
                        "sku_page",
                    } and bot._current_page_matches_target(probe):
                        return {
                            "used_keyword": keyword,
                            "search_results": [],
                            "page_probe": probe,
                            "step_timings": list(bot._last_discovery_step_timings),
                        }
                if probe["state"] == "homepage":
                    if not bot._open_search_from_homepage():
                        logger.warning("重试关键词前无法从首页进入搜索页，跳过该关键词")
                        continue
                    probe = bot.probe_current_page()
                if probe["state"] != "search_page":
                    logger.warning(
                        f"重试关键词前页面状态异常: {probe['state']}，跳过该关键词"
                    )
                    continue

            if normalize_text(self._config.keyword) != normalized_keyword:
                bot._invalidate_all_hot_cache(
                    reason="target_keyword_changed",
                )
                bot._verified_price_selection = None
            self._config.keyword = keyword
            logger.info(f"尝试搜索关键词: {keyword}")
            if not bot._submit_search_keyword():
                tried.add(normalized_keyword)
                continue

            open_result = bot._open_target_from_search_results(
                max_scrolls=search_scrolls,
                max_results=result_limit,
                return_details=True,
            )
            search_results = open_result["search_results"]
            if search_results:
                logger.info(
                    f"搜索到 {len(search_results)} 条候选结果，最高分 {search_results[0]['score']}"
                )
            if open_result["opened"]:
                page_probe = bot.probe_current_page()
                return {
                    "used_keyword": keyword,
                    "search_results": search_results,
                    "page_probe": page_probe,
                    "step_timings": list(bot._last_discovery_step_timings),
                }

            for item in search_results:
                entry = dict(item)
                entry["used_keyword"] = keyword
                failed_candidates.append(entry)

            tried.add(normalized_keyword)

        # 最终失败：合并各关键词轮次候选（按标题归一化去重，重复取高分），
        # 按 score 降序缓存 top-5，供 prompt_runner 失败文案引导用户补全标题。
        # 契约保持不变：失败仍 return None（调用方依赖 falsy 判定）。
        merged: Dict[str, Dict[str, Any]] = {}
        for entry in failed_candidates:
            key = normalize_text(str(entry.get("title") or ""))
            if not key:
                continue
            existing = merged.get(key)
            if existing is None or entry.get("score", 0) > existing.get("score", 0):
                merged[key] = entry
        top_candidates = sorted(
            merged.values(), key=lambda item: item.get("score", 0), reverse=True
        )[:5]
        self._last_failed_candidates = top_candidates
        log_event(
            logger,
            "discovery_failed",
            level=logging.WARNING,
            keywords_tried=len(tried),
            candidates=len(top_candidates),
            top_score=top_candidates[0].get("score") if top_candidates else None,
        )
        logger.warning("根据提示词尝试多个搜索关键词后，仍未打开目标演出")
        return None


# Module-level constant (matches damai_app.py)
_MANUAL_STEP_BASELINES = {
    "搜索页输入并提交关键词": 6.0,
    "搜索结果扫描并打开目标": 12.0,
}
