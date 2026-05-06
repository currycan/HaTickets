# -*- coding: UTF-8 -*-
"""Coordinate-cache helpers for DamaiBot's hot-path mixins.

Methods relocated from ``mobile/damai_app.py`` (W4-01 split, zero behavior
change).  Each helper either delegates to ``self._price_sel`` /
``self._pipeline`` or reads the bot-level coordinate cache directly.
"""

from __future__ import annotations


class CoordsCacheMixin:
    """Mixin contributing hot-path coordinate cache helpers to ``DamaiBot``."""

    def _has_warm_pipeline_coords(self):
        """Check if all coordinates required for the blind pipeline are cached."""
        if hasattr(self, "_pipeline"):
            return self._pipeline.has_warm_coords()
        c = self._cached_hot_path_coords
        return all(
            [
                c.get("detail_buy"),
                c.get("price"),
                c.get("sku_buy"),
                c.get("attendee_checkboxes"),
            ]
        )

    def _get_buy_button_coordinates(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_buy_button_coordinates(xml_root)
        return None

    def _get_price_option_coordinates_by_config_index(self, xml_root=None):
        if hasattr(self, "_price_sel"):
            return self._price_sel._get_price_option_coordinates_by_config_index(
                xml_root
            )
        return None
