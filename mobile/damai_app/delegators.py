# -*- coding: UTF-8 -*-
"""Thin sub-module delegator helpers for DamaiBot.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  These are 1-liner wrappers that forward to the appropriate
sub-module attached on the bot (``self._attendee_sel``, ``self._price_sel``,
``self._navigator``, ``self._pipeline``).  Kept on the bot class so external
callers and tests that exercise the whole bot interface keep working.
"""

from __future__ import annotations

from . import logger


class DelegatorsMixin:
    """Mixin contributing thin delegator methods to ``DamaiBot``.

    The implementations forward to ``AttendeeSelector`` / ``PriceSelector`` /
    ``EventNavigator`` / ``FastPipeline`` instances configured during
    ``DamaiBot.__init__``.
    """

    # ------------------------------------------------------------------ #
    # AttendeeSelector delegators
    # ------------------------------------------------------------------ #

    def _attendee_required_count_on_confirm_page(self):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_required_count_on_confirm_page()
        logger.warning("AttendeeSelector 未初始化")
        return max(1, len(self.config.users or []))

    def _attendee_checkbox_elements(self):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_checkbox_elements()
        return []

    @staticmethod
    def _is_checkbox_selected(checkbox):
        # Static helper — defers to the shared ``_is_checked`` static method
        # inherited from UIPrimitives.  Imports lazily to avoid loading the
        # orchestrator at module-import time.
        try:
            from mobile.ui_primitives import UIPrimitives
        except ImportError:  # pragma: no cover
            from ui_primitives import UIPrimitives  # type: ignore[no-redef]
        return UIPrimitives._is_checked(checkbox)

    def _attendee_selected_count(
        self, checkbox_elements=None, use_source_fallback=True
    ):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._attendee_selected_count(
                checkbox_elements, use_source_fallback
            )
        return 0

    def _click_attendee_checkbox(self, checkbox):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._click_attendee_checkbox(checkbox)
        return False

    def _click_attendee_checkbox_fast(self, checkbox):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._click_attendee_checkbox_fast(checkbox)
        return False

    def _select_attendee_checkbox_by_name(self, user_name):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._select_attendee_checkbox_by_name(user_name)
        return False

    def _ensure_attendees_selected_on_confirm_page(
        self, require_attendee_section=False
    ):
        if hasattr(self, "_attendee_sel"):
            return self._attendee_sel._ensure_attendees_selected_on_confirm_page(
                require_attendee_section
            )
        logger.warning("AttendeeSelector 未初始化")
        return False

    # ------------------------------------------------------------------ #
    # PriceSelector delegators
    # ------------------------------------------------------------------ #

    def _build_compound_price_text(self, container):
        if hasattr(self, "_price_sel"):
            return self._price_sel._build_compound_price_text(container)
        return ""

    def _price_option_text_from_descendants(self, texts):
        if hasattr(self, "_price_sel"):
            return self._price_sel._price_option_text_from_descendants(texts)
        return ""

    def _normalize_ocr_price_text(self, ocr_output):
        if hasattr(self, "_price_sel"):
            return self._price_sel._normalize_ocr_price_text(ocr_output)
        return ""

    def _ocr_price_text_from_card(self, screenshot_path, rect):
        if hasattr(self, "_price_sel"):
            return self._price_sel._ocr_price_text_from_card(screenshot_path, rect)
        return ""

    def _extract_price_digits(self, text):
        if hasattr(self, "_price_sel"):
            return self._price_sel._extract_price_digits(text)
        return None

    def _price_text_matches_target(self, text):
        if hasattr(self, "_price_sel"):
            return self._price_sel._price_text_matches_target(text)
        return False

    def _is_price_option_available(self, option):
        if hasattr(self, "_price_sel"):
            return self._price_sel._is_price_option_available(option)
        return True

    def _click_visible_price_option(self, card_index):
        if hasattr(self, "_price_sel"):
            return self._price_sel._click_visible_price_option(card_index)
        return False

    def _click_price_option_by_config_index(self, burst=False, coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._click_price_option_by_config_index(burst, coords)
        return False

    def _select_price_option_fast(self, cached_coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._select_price_option_fast(cached_coords)
        return None

    def _select_price_option(self, cached_coords=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._select_price_option(cached_coords)
        return False

    def get_visible_price_options(self, allow_ocr=True, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel.get_visible_price_options(
                allow_ocr=allow_ocr, xml_root=xml_root
            )
        return []

    def _get_visible_price_options_from_xml(self, xml_root, allow_ocr=True):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_visible_price_options_from_xml(
                xml_root, allow_ocr=allow_ocr
            )
        return []

    # ------------------------------------------------------------------ #
    # EventNavigator delegators
    # ------------------------------------------------------------------ #

    def _keyword_tokens(self):
        if hasattr(self, "_navigator"):
            return self._navigator._keyword_tokens()
        return []

    def _title_matches_target(self, title_text):
        if hasattr(self, "_navigator"):
            return self._navigator._title_matches_target(title_text)
        return False

    def _current_page_matches_target(self, page_probe):
        if hasattr(self, "_navigator"):
            return self._navigator._current_page_matches_target(page_probe)
        return False

    def _open_search_from_homepage(self):
        if hasattr(self, "_navigator"):
            return self._navigator._open_search_from_homepage()
        return False

    def _submit_search_keyword(self):
        if hasattr(self, "_navigator"):
            return self._navigator._submit_search_keyword()
        return False

    def _score_search_result(self, title_text, venue_text):
        if hasattr(self, "_navigator"):
            return self._navigator._score_search_result(title_text, venue_text)
        return -1

    def _scroll_search_results(self):
        if hasattr(self, "_navigator"):
            return self._navigator._scroll_search_results()

    def _open_target_from_search_results(
        self, max_scrolls=2, max_results=5, return_details=False
    ):
        if hasattr(self, "_navigator"):
            return self._navigator._open_target_from_search_results(
                max_scrolls, max_results, return_details
            )
        return {"opened": False, "search_results": []} if return_details else False

    def collect_search_results(self, max_scrolls=0, max_results=5):
        if hasattr(self, "_navigator"):
            return self._navigator.collect_search_results(max_scrolls, max_results)
        return []

    def navigate_to_target_event(self, initial_probe=None):
        """Navigate to the target event. Delegates to EventNavigator."""
        if hasattr(self, "_navigator") and self._navigator is not None:
            return self._navigator.navigate_to_target_event(initial_probe=initial_probe)
        return self._navigate_to_target_impl(initial_probe=initial_probe)

    def _navigate_to_target_impl(self, initial_probe=None):
        if hasattr(self, "_navigator"):
            return self._navigator._navigate_to_target_impl(initial_probe)
        return False

    def discover_target_event(
        self, keyword_candidates, initial_probe=None, search_scrolls=1, result_limit=5
    ):
        if hasattr(self, "_navigator"):
            return self._navigator.discover_target_event(
                keyword_candidates, initial_probe, search_scrolls, result_limit
            )
        return None

    # ------------------------------------------------------------------ #
    # FastPipeline delegators
    # ------------------------------------------------------------------ #

    def _run_cold_validation_pipeline(self, start_time):
        self._ensure_pipeline()
        return self._pipeline.run_cold_validation(start_time)

    def _cold_pipeline_finish_confirm(self, start_time):
        self._ensure_pipeline()
        return self._pipeline._finish_confirm(start_time)

    def _run_warm_validation_pipeline(self, start_time):
        self._ensure_pipeline()
        return self._pipeline.run_warm_validation(start_time)

    def _rush_preselect_and_buy_via_xml(self):
        self._ensure_pipeline()
        return self._pipeline.rush_preselect_and_buy_via_xml()
