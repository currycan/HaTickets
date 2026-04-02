"""PriceSelector — ticket price and SKU selection on the Damai app.

Uses delegate pattern: delegates complex operations to DamaiBot while
providing a clean interface for FastPipeline and other consumers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from mobile.logger import get_logger

if TYPE_CHECKING:
    from mobile.page_probe import PageProbe

logger = get_logger(__name__)


class PriceSelector:
    """Handles price/SKU selection on SKU and detail pages."""

    def __init__(self, device, config, probe: PageProbe, bot=None) -> None:
        self._d = device
        self._config = config
        self._probe = probe
        self._bot = bot

    def set_bot(self, bot) -> None:
        """Set DamaiBot reference for delegation."""
        self._bot = bot

    def select_by_index(self, xml_root=None) -> bool:
        """Select price option by config.price_index. Returns True on success."""
        coords = self.get_price_coords_by_index(xml_root=xml_root)
        if coords is None:
            logger.warning(f"无法定位 price_index={self._config.price_index} 的坐标")
            return False
        self._click_coordinates(*coords)
        logger.info(f"通过配置索引选择票价: price_index={self._config.price_index}")
        return True

    def get_price_coords_by_index(self, xml_root=None) -> Optional[Tuple[int, int]]:
        """Get coordinates for price option at config.price_index."""
        if self._bot is not None:
            try:
                return self._bot._get_price_option_coordinates_by_config_index(xml_root=xml_root)
            except Exception as exc:
                logger.warning(f"获取票价坐标失败: {exc}")
                return None
        return None

    def get_buy_button_coords(self, xml_root=None) -> Optional[Tuple[int, int]]:
        """Get coordinates for the buy/confirm button."""
        if self._bot is not None:
            try:
                return self._bot._get_buy_button_coordinates(xml_root=xml_root)
            except Exception as exc:
                logger.warning(f"获取购买按钮坐标失败: {exc}")
                return None
        return None

    def _click_coordinates(self, x, y) -> None:
        try:
            self._d.click(int(x), int(y))
        except Exception:
            pass
