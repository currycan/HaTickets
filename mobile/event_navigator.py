"""EventNavigator — search and navigate to the target event in the Damai app.

Uses a delegate pattern: holds a reference to DamaiBot and calls its
navigation methods. This provides a clean interface for RecoveryHelper
while the actual implementation lives here.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException

from mobile.logger import get_logger

try:
    from mobile.item_resolver import normalize_text, city_keyword
except ImportError:
    from item_resolver import normalize_text, city_keyword

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)


class EventNavigator:
    """Handles searching and navigating to the target event."""

    def __init__(self, device, config, probe: PageProbe, bot=None) -> None:
        self._d = device
        self._config = config
        self._probe = probe
        self._bot = bot  # DamaiBot reference for delegation

    def set_bot(self, bot) -> None:
        """Set the DamaiBot reference (breaks circular init dependency)."""
        self._bot = bot

    def navigate_to_target_event(self, initial_probe=None) -> bool:
        """Navigate from current page to the target event detail page."""
        if not self._config.auto_navigate:
            logger.warning("auto_navigate 未启用")
            return False

        probe = initial_probe or self._probe.probe_current_page(fast=True)
        if probe["state"] == "detail_page":
            return True

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

        candidates = []
        if bot.item_detail:
            candidates.extend([bot.item_detail.item_name, bot.item_detail.item_name_display])
        if self._config.target_title:
            candidates.append(self._config.target_title)
        if self._config.keyword:
            candidates.append(self._config.keyword)

        for candidate in candidates:
            normalized_candidate = normalize_text(candidate)
            if not normalized_candidate:
                continue
            if normalized_candidate in normalized_title or normalized_title in normalized_candidate:
                return True

        keyword_tokens = bot._keyword_tokens()
        if keyword_tokens and all(token in normalized_title for token in keyword_tokens):
            return True

        return False

    def _current_page_matches_target(self, page_probe):
        """Check if the current detail/sku page already points at the expected event."""
        bot = self._bot
        if page_probe["state"] not in {"detail_page", "sku_page"}:
            return False

        if not bot.item_detail and not self._config.target_title and not self._config.keyword:
            return True

        return bot._title_matches_target(bot._get_detail_title_text())

    def _open_search_from_homepage(self):
        """Enter the homepage search flow."""
        bot = self._bot
        search_selectors = [
            (By.ID, "cn.damai:id/pioneer_homepage_header_search_btn"),
            (By.ID, "cn.damai:id/homepage_header_search"),
            (By.ID, "cn.damai:id/homepage_header_search_layout"),
            (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("搜索")'),
        ]

        for by, value in search_selectors:
            if bot.ultra_fast_click(by, value, timeout=0.8):
                search_probe = bot.wait_for_page_state({"search_page"}, timeout=2.5, poll_interval=0.15)
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
                manual_baseline_seconds=_MANUAL_STEP_BASELINES.get("搜索页输入并提交关键词")):
            try:
                search_input = bot._wait_for_element(By.ID, "cn.damai:id/header_search_v2_input", timeout=2)
            except TimeoutException:
                logger.warning("未找到搜索输入框")
                return False

            bot._click_element_center(search_input)
            time.sleep(0.12)

            current_text = bot._read_element_text(search_input).strip()
            if current_text and current_text != self._config.keyword:
                if bot._has_element(By.ID, "cn.damai:id/header_search_v2_input_delete"):
                    bot.ultra_fast_click(By.ID, "cn.damai:id/header_search_v2_input_delete", timeout=0.4)
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
            if bot._has_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("演出")'):
                bot.smart_wait_and_click(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().text("演出")',
                    timeout=0.8,
                )
                time.sleep(0.1)
            try:
                deadline = time.time() + 3.5
                while time.time() < deadline:
                    if bot._find_all(By.ID, "cn.damai:id/ll_search_item"):
                        break
                    time.sleep(0.04)
                else:
                    raise TimeoutException("搜索结果加载超时")
            except TimeoutException:
                logger.warning("搜索结果加载超时")
                return False

        return True

    def _score_search_result(self, title_text, venue_text):
        """Score a search result against the configured target."""
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

    def _open_target_from_search_results(self, max_scrolls=2, max_results=5, return_details=False):
        """Open the best-matching event from search results and optionally return scanned summaries."""
        bot = self._bot
        seen_titles = set()
        collected = []

        with bot._timed_step(
                "搜索结果扫描并打开目标",
                manual_baseline_seconds=_MANUAL_STEP_BASELINES.get("搜索结果扫描并打开目标")):
            for scroll_index in range(max_scrolls + 1):
                result_cards = bot._find_all(By.ID, "cn.damai:id/ll_search_item")
                best_match = None
                best_score = -1

                for card in result_cards:
                    title_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_name")
                    if not title_text:
                        continue

                    venue_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_venueName")
                    city_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_city").replace("|", "").strip()
                    time_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_time")
                    score = bot._score_search_result(title_text, venue_text)

                    normalized_title = normalize_text(title_text)
                    if normalized_title and normalized_title not in seen_titles:
                        collected.append({
                            "title": title_text,
                            "venue": venue_text,
                            "city": city_text,
                            "time": time_text,
                            "score": score,
                        })
                        seen_titles.add(normalized_title)

                    if score > best_score:
                        best_score = score
                        best_match = card

                if best_match is not None and best_score >= 60:
                    bot._click_element_center(best_match)
                    detail_probe = bot.wait_for_page_state({"detail_page", "sku_page"}, timeout=5.5)
                    if detail_probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(detail_probe):
                        collected.sort(key=lambda item: item["score"], reverse=True)
                        details = {"opened": True, "search_results": collected[:max_results]}
                        return details if return_details else True

                    logger.warning("已进入详情页，但标题与目标演出不一致，返回搜索结果继续尝试")
                    if not bot._press_keycode_safe(4, context="返回搜索列表"):
                        break
                    time.sleep(0.25)
                    bot.dismiss_startup_popups()
                else:
                    logger.info(f"本屏搜索结果未找到明确匹配项，已扫描: {len(seen_titles)} 条")

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
                title_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_name")
                if not title_text:
                    continue

                normalized_title = normalize_text(title_text)
                if normalized_title in seen:
                    continue

                venue_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_venueName")
                city_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_city").replace("|", "").strip()
                time_text = bot._safe_element_text(card, By.ID, "cn.damai:id/tv_project_time")
                price_text = bot._build_compound_price_text(card)
                score = bot._score_search_result(title_text, venue_text)

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

        if page_probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(page_probe):
            return True

        if page_probe["state"] in {"detail_page", "sku_page"} and not bot._current_page_matches_target(page_probe):
            page_probe = bot._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(page_probe):
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

    def discover_target_event(self, keyword_candidates, initial_probe=None, search_scrolls=1, result_limit=5):
        """Try multiple keywords, collect candidate summaries, and open the best match."""
        bot = self._bot
        bot._last_discovery_step_timings = []
        page_probe = initial_probe or bot.probe_current_page()
        page_probe = bot._recover_to_navigation_start(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(page_probe):
            return {
                "used_keyword": self._config.keyword,
                "search_results": [],
                "page_probe": page_probe,
                "step_timings": list(bot._last_discovery_step_timings),
            }

        if page_probe["state"] in {"detail_page", "sku_page"} and not bot._current_page_matches_target(page_probe):
            page_probe = bot._exit_non_target_event_context(page_probe)

        if page_probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(page_probe):
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
        for keyword in keyword_candidates:
            normalized_keyword = normalize_text(keyword)
            if not normalized_keyword or normalized_keyword in tried:
                continue

            if tried:
                bot.dismiss_startup_popups()
                probe = bot.probe_current_page()
                if probe["state"] in {"detail_page", "sku_page"}:
                    probe = bot._exit_non_target_event_context(probe)
                    if probe["state"] in {"detail_page", "sku_page"} and bot._current_page_matches_target(probe):
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
                    logger.warning(f"重试关键词前页面状态异常: {probe['state']}，跳过该关键词")
                    continue

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
                logger.info(f"搜索到 {len(search_results)} 条候选结果，最高分 {search_results[0]['score']}")
            if open_result["opened"]:
                page_probe = bot.probe_current_page()
                return {
                    "used_keyword": keyword,
                    "search_results": search_results,
                    "page_probe": page_probe,
                    "step_timings": list(bot._last_discovery_step_timings),
                }

            tried.add(normalized_keyword)

        logger.warning("根据提示词尝试多个搜索关键词后，仍未打开目标演出")
        return None


# Module-level constant (matches damai_app.py)
_MANUAL_STEP_BASELINES = {
    "搜索页输入并提交关键词": 6.0,
    "搜索结果扫描并打开目标": 12.0,
}
