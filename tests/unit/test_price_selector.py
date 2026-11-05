"""Unit tests for PriceSelector."""

from unittest.mock import MagicMock, patch

import pytest

from mobile.price_selector import (
    PriceCard,
    PriceSelector,
    PriceSelectorError,
    SoldOutError,
    select_price_by_index,
)


@pytest.fixture(autouse=True)
def _force_price_panel_ready():
    """Most legacy PriceSelector tests do not care about page_probe state.

    Default to ``"ready"`` so the new pre-flight check does not turn passive
    MagicMock device probes into accidental ``loading`` / ``sold_out``.
    Individual state tests patch this themselves.
    """
    with patch(
        "mobile.page_probe.detect_price_panel_state", return_value="ready"
    ) as mocked:
        yield mocked


# ---------------------------------------------------------------------------
# select_price_by_index — boundary guard (P1 #31, Step 1)
# ---------------------------------------------------------------------------


def _card(index: int, text: str = "", **kw) -> PriceCard:
    return PriceCard(index=index, price_text=text, **kw)


class TestSelectPriceByIndexFunctional:
    def test_returns_card_when_index_in_range(self):
        cards = [_card(0, "380元"), _card(1, "580元"), _card(2, "880元")]
        assert select_price_by_index(cards, 1) is cards[1]

    def test_empty_cards_raises_with_three_reasons(self):
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index([], 0)
        message = str(exc_info.value)
        assert "未发现可点击价格卡片" in message
        assert "尚未开售" in message
        assert "已售罄" in message
        assert "UI 变更" in message

    def test_negative_index_raises_with_available_listing(self):
        cards = [_card(0, "380元"), _card(1, "580元")]
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index(cards, -1)
        message = str(exc_info.value)
        assert "price_index=-1" in message
        assert "[0] 380元" in message
        assert "[1] 580元" in message
        assert "config.jsonc" in message

    def test_out_of_range_raises_with_available_listing(self):
        cards = [_card(0, "380元"), _card(1, "580元", tag="缺货")]
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index(cards, 5)
        message = str(exc_info.value)
        assert "0..1" in message
        assert "[1] 580元 [缺货]" in message

    def test_dump_writer_invoked_on_empty_cards(self, tmp_path):
        dump_path = tmp_path / "price_dump.xml"
        writer = MagicMock()
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index([], 0, dump_on_fail=dump_path, dump_writer=writer)
        writer.assert_called_once_with(dump_path)
        assert str(dump_path) in str(exc_info.value)

    def test_dump_writer_invoked_on_out_of_range(self, tmp_path):
        dump_path = tmp_path / "price_dump.xml"
        writer = MagicMock()
        cards = [_card(0, "380元")]
        with pytest.raises(PriceSelectorError):
            select_price_by_index(cards, 9, dump_on_fail=dump_path, dump_writer=writer)
        writer.assert_called_once_with(dump_path)

    def test_dump_writer_failure_is_swallowed(self, tmp_path):
        writer = MagicMock(side_effect=OSError("disk full"))
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index(
                [], 0, dump_on_fail=tmp_path / "x.xml", dump_writer=writer
            )
        # No "UI dump" suffix because save failed
        assert "UI dump 已保存" not in str(exc_info.value)

    def test_dump_path_without_writer_is_noop(self, tmp_path):
        with pytest.raises(PriceSelectorError) as exc_info:
            select_price_by_index([], 0, dump_on_fail=tmp_path / "x.xml")
        assert "UI dump 已保存" not in str(exc_info.value)


class TestPriceSelectorErrorTypes:
    def test_price_selector_error_is_runtime_error(self):
        assert issubclass(PriceSelectorError, RuntimeError)

    def test_sold_out_error_is_runtime_error(self):
        assert issubclass(SoldOutError, RuntimeError)

    def test_errors_are_distinct_types(self):
        assert PriceSelectorError is not SoldOutError


class TestSelectByIndex:
    def test_clicks_correct_coordinates_via_bot(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = (100, 200)
        d = MagicMock()
        config = MagicMock()
        config.price_index = 2
        selector = PriceSelector(device=d, config=config, probe=MagicMock())
        selector.set_bot(bot)
        result = selector.select_by_index()
        assert result is True
        d.click.assert_called_once_with(100, 200)

    def test_returns_false_when_bot_returns_none(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = None
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(price_index=99), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.select_by_index() is False

    def test_returns_false_when_no_bot(self):
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(price_index=0), probe=MagicMock()
        )
        assert selector.select_by_index() is False

    def test_click_coordinates_swallows_exception(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = (10, 20)
        d = MagicMock()
        d.click.side_effect = Exception("device error")
        selector = PriceSelector(
            device=d, config=MagicMock(price_index=0), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.select_by_index() is True


class TestGetPriceCoordsByIndex:
    def test_exception_returns_none(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.side_effect = RuntimeError(
            "fail"
        )
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.get_price_coords_by_index() is None

    def test_success_returns_coords(self):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = (50, 60)
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.get_price_coords_by_index() == (50, 60)


class TestGetBuyButtonCoordsExc:
    def test_exception_returns_none(self):
        bot = MagicMock()
        bot._get_buy_button_coordinates.side_effect = RuntimeError("fail")
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.get_buy_button_coords() is None


class TestExtractPriceDigits:
    def _selector(self):
        return PriceSelector(device=MagicMock(), config=MagicMock(), probe=MagicMock())

    def test_extracts_digits_from_price_text(self):
        assert self._selector()._extract_price_digits("¥580元") == 580

    def test_returns_none_for_no_digits(self):
        assert self._selector()._extract_price_digits("免费") is None

    def test_handles_non_string(self):
        assert self._selector()._extract_price_digits(None) is None

    def test_extracts_four_digit_price(self):
        assert self._selector()._extract_price_digits("1680元") == 1680


class TestIsPriceOptionAvailable:
    def _selector(self):
        return PriceSelector(device=MagicMock(), config=MagicMock(), probe=MagicMock())

    def test_available_when_no_tag(self):
        assert self._selector()._is_price_option_available({"tag": ""}) is True

    def test_unavailable_when_sold_out(self):
        assert self._selector()._is_price_option_available({"tag": "售罄"}) is False

    def test_unavailable_when_no_ticket(self):
        assert self._selector()._is_price_option_available({"tag": "无票"}) is False

    @pytest.mark.parametrize("tag", ["暂时售罄", "该票档已售罄", "暂不可售"])
    def test_unavailable_sentence_variants(self, tag):
        assert self._selector()._is_price_option_available({"tag": tag}) is False

    def test_available_when_tag_is_none(self):
        assert self._selector()._is_price_option_available({"tag": None}) is True


class TestNormalizeOcrPriceText:
    def _selector(self):
        return PriceSelector(device=MagicMock(), config=MagicMock(), probe=MagicMock())

    def test_extracts_three_digit_price(self):
        assert self._selector()._normalize_ocr_price_text("票价580元起") == "580元"

    def test_extracts_four_digit_price(self):
        assert self._selector()._normalize_ocr_price_text("1280RMB") == "1280元"

    def test_returns_empty_for_no_digits(self):
        assert self._selector()._normalize_ocr_price_text("免费入场") == ""

    def test_handles_non_string(self):
        assert self._selector()._normalize_ocr_price_text(None) == ""

    def test_scattered_digits_three(self):
        assert self._selector()._normalize_ocr_price_text("价 3 8 0 元") == "380元"


class TestPriceTextMatchesTarget:
    def test_exact_match(self):
        config = MagicMock()
        config.price = "580元"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target("580元") is True

    def test_digit_match(self):
        config = MagicMock()
        config.price = "¥580"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target("580元") is True

    def test_no_match(self):
        config = MagicMock()
        config.price = "380元"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target("580元") is False

    @pytest.mark.parametrize("observed", ["1699元", "内场1699元"])
    def test_rejects_larger_amount_containing_target_digits(self, observed):
        config = MagicMock()
        config.price = "699元"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target(observed) is False

    def test_rejects_conflicting_labels_at_same_amount(self):
        config = MagicMock()
        config.price = "内场699元"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target("看台699元") is False

    def test_accepts_optional_observed_label_at_same_amount(self):
        config = MagicMock()
        config.price = "699元"
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        assert selector._price_text_matches_target("内场699元") is True


class TestGetBuyButtonCoords:
    def test_delegates_to_bot(self):
        bot = MagicMock()
        bot._get_buy_button_coordinates.return_value = (300, 400)
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(), probe=MagicMock()
        )
        selector.set_bot(bot)
        assert selector.get_buy_button_coords() == (300, 400)

    def test_returns_none_when_no_bot(self):
        selector = PriceSelector(
            device=MagicMock(), config=MagicMock(), probe=MagicMock()
        )
        assert selector.get_buy_button_coords() is None


class TestGetPriceCoordsFromXml:
    def _make_xml(self, container_id, num_cards):
        """Build minimal XML with clickable FrameLayout cards."""
        import xml.etree.ElementTree as ET

        root = ET.Element("hierarchy")
        container = ET.SubElement(
            root,
            "node",
            attrib={
                "resource-id": container_id,
                "class": "android.widget.LinearLayout",
            },
        )
        for i in range(num_cards):
            x1, y1, x2, y2 = 100 * i, 200, 100 * i + 80, 280
            ET.SubElement(
                container,
                "node",
                attrib={
                    "class": "android.widget.FrameLayout",
                    "clickable": "true",
                    "bounds": f"[{x1},{y1}][{x2},{y2}]",
                },
            )
        return root

    def test_finds_card_in_primary_container(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        bot._parse_bounds.return_value = (0, 200, 80, 280)
        config = MagicMock()
        config.price_index = 0
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        selector.set_bot(bot)
        xml = self._make_xml("cn.damai:id/project_detail_perform_price_flowlayout", 3)
        result = selector._get_price_coords_from_xml(xml)
        assert result == (40, 240)

    def test_falls_back_to_layout_price(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        bot._parse_bounds.return_value = (100, 200, 180, 280)
        config = MagicMock()
        config.price_index = 1
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        selector.set_bot(bot)
        xml = self._make_xml("cn.damai:id/layout_price", 5)
        result = selector._get_price_coords_from_xml(xml)
        assert result == (140, 240)

    def test_returns_none_when_index_out_of_range(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        config = MagicMock()
        config.price_index = 10
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        selector.set_bot(bot)
        xml = self._make_xml("cn.damai:id/project_detail_perform_price_flowlayout", 3)
        result = selector._get_price_coords_from_xml(xml)
        assert result is None

    def test_returns_none_when_no_xml(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        bot._dump_hierarchy_xml.return_value = None
        config = MagicMock()
        config.price_index = 0
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        selector.set_bot(bot)
        result = selector._get_price_coords_from_xml(None)
        assert result is None

    def test_retry_on_cold_path(self):
        """When xml_root is provided but index out of range, retry with fresh dump."""

        bot = MagicMock()
        bot._using_u2.return_value = True
        bot._parse_bounds.return_value = (0, 200, 80, 280)
        # First XML: only 2 cards (index 2 out of range)
        xml_small = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 2
        )
        # Fresh dump returns 5 cards
        xml_full = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 5
        )
        bot._dump_hierarchy_xml.return_value = xml_full
        config = MagicMock()
        config.price_index = 2
        selector = PriceSelector(device=MagicMock(), config=config, probe=MagicMock())
        selector.set_bot(bot)
        result = selector._get_price_option_coordinates_by_config_index(
            xml_root=xml_small
        )
        assert result is not None  # retry succeeded

    def _add_card_text(self, xml, index, text, tag=""):
        container = next(xml.iter("node"))
        card = list(container)[index]
        import xml.etree.ElementTree as ET

        ET.SubElement(card, "node", attrib={"text": text})
        if tag:
            ET.SubElement(card, "node", attrib={"text": tag})

    def test_formal_mode_verifies_price_text_from_same_xml(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        bot._parse_bounds.return_value = (100, 200, 180, 280)
        config = MagicMock()
        config.price_index = 1
        config.price = "580元"
        config.if_commit_order = True
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)
        xml = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 3
        )
        self._add_card_text(xml, 1, "¥580")

        assert selector._get_price_coords_from_xml(xml) == (140, 240)
        assert bot._verified_price_selection == {
            "coords": (140, 240),
            "price_index": 1,
            "target_price": "580元",
            "observed_price": "¥580",
        }
        bot._set_terminal_failure.assert_not_called()

    def test_formal_mode_rejects_mismatched_price_text(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        config = MagicMock()
        config.price_index = 1
        config.price = "580元"
        config.if_commit_order = True
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)
        xml = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 3
        )
        self._add_card_text(xml, 1, "880元")

        assert selector._get_price_coords_from_xml(xml) is None
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_unconfirmed"
        )

    def test_formal_mode_rejects_amount_containing_target_digits(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        config = MagicMock()
        config.price_index = 1
        config.price = "699元"
        config.if_commit_order = True
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)
        xml = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 3
        )
        self._add_card_text(xml, 1, "内场1699元")

        assert selector._get_price_coords_from_xml(xml) is None
        assert bot._verified_price_selection is None
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_unconfirmed"
        )

    def test_formal_mode_rejects_empty_price_text(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        config = MagicMock()
        config.price_index = 0
        config.price = "580元"
        config.if_commit_order = True
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)
        xml = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 1
        )

        assert selector._get_price_coords_from_xml(xml) is None
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_unconfirmed"
        )

    def test_formal_mode_rejects_unavailable_target_price(self):
        bot = MagicMock()
        bot._using_u2.return_value = True
        config = MagicMock()
        config.price_index = 0
        config.price = "580元"
        config.if_commit_order = True
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)
        xml = self._make_xml(
            "cn.damai:id/project_detail_perform_price_flowlayout", 1
        )
        self._add_card_text(xml, 0, "580元", tag="售罄")

        assert selector._get_price_coords_from_xml(xml) is None
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_unavailable"
        )


class TestFormalPriceSelectionProof:
    def test_rush_mode_requires_same_snapshot_price_proof(self):
        bot = MagicMock()
        config = MagicMock()
        config.rush_mode = True
        config.if_commit_order = True
        config.price_index = 2
        config.price = "880元"
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        assert selector._select_price_option_fast(
            cached_coords=(300, 1200)
        ) is False
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_unconfirmed"
        )
        bot._click_price_option_by_config_index.assert_not_called()

    def test_rush_mode_uses_verified_same_snapshot_coords(self):
        bot = MagicMock()
        bot._click_price_option_by_config_index.return_value = True
        stamp = object()
        bot._run_generation = 7
        bot._cached_hot_path_coord_meta = {"price": stamp}
        bot._get_hot_coord.return_value = (300, 1200)
        bot._verified_price_selection = {
            "coords": (300, 1200),
            "price_index": 2,
            "target_price": "880元",
            "observed_price": "880元",
            "run_generation": 7,
            "stamp": stamp,
        }
        config = MagicMock()
        config.rush_mode = True
        config.if_commit_order = True
        config.price_index = 2
        config.price = "880元"
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        with patch.object(
            selector,
            "_verify_formal_price_selection_after_click",
            return_value=True,
        ) as verify_selected:
            assert selector._select_price_option_fast(
                cached_coords=(300, 1200)
            ) is True
        bot._click_price_option_by_config_index.assert_called_once_with(
            burst=False, coords=(300, 1200)
        )
        verify_selected.assert_called_once_with((300, 1200))

    def test_formal_mode_never_uses_global_price_text_fallback(self):
        bot = MagicMock()
        config = MagicMock()
        config.rush_mode = False
        config.if_commit_order = True
        config.price_index = 0
        config.price = "880元"
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        assert selector._select_price_option_fast() is False
        bot.ultra_fast_click.assert_not_called()
        bot._click_visible_price_option.assert_not_called()

    def test_formal_coordinate_click_does_not_requery_live_index(self):
        bot = MagicMock()
        config = MagicMock()
        config.rush_mode = True
        config.if_commit_order = True
        config.price_index = 1
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        assert selector._click_price_option_by_config_index(
            burst=True,
            coords=(320, 1400),
        ) is True
        bot._click_coordinates.assert_called_once_with(320, 1400, duration=30)
        bot._burst_click_coordinates.assert_not_called()
        bot._click_price_card_element.assert_not_called()

    def test_formal_mode_stops_when_post_click_selection_is_not_observed(self):
        bot = MagicMock()
        bot._click_price_option_by_config_index.return_value = True
        stamp = object()
        bot._run_generation = 7
        bot._cached_hot_path_coord_meta = {"price": stamp}
        bot._get_hot_coord.return_value = (300, 1200)
        bot._verified_price_selection = {
            "coords": (300, 1200),
            "price_index": 2,
            "target_price": "880元",
            "observed_price": "880元",
            "run_generation": 7,
            "stamp": stamp,
        }
        config = MagicMock(
            rush_mode=True,
            if_commit_order=True,
            price_index=2,
            price="880元",
        )
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        with patch.object(
            selector,
            "_verify_formal_price_selection_after_click",
            return_value=False,
        ):
            assert selector._select_price_option_fast(
                cached_coords=(300, 1200)
            ) is False

        bot._click_price_option_by_config_index.assert_called_once_with(
            burst=False, coords=(300, 1200)
        )

    @pytest.mark.parametrize("selected_attr", ("selected", "checked"))
    def test_post_click_hierarchy_proves_exact_target_card_selected(
        self, selected_attr
    ):
        import xml.etree.ElementTree as ET

        root = ET.Element("hierarchy")
        container = ET.SubElement(
            root,
            "node",
            attrib={
                "resource-id": (
                    "cn.damai:id/project_detail_perform_price_flowlayout"
                )
            },
        )
        card = ET.SubElement(
            container,
            "node",
            attrib={
                "class": "android.widget.FrameLayout",
                "clickable": "true",
                "bounds": "[280,1160][320,1240]",
                selected_attr: "true",
            },
        )
        ET.SubElement(card, "node", attrib={"text": "880元"})

        bot = MagicMock()
        bot._parse_bounds.return_value = (280, 1160, 320, 1240)
        bot._dump_hierarchy_xml.return_value = root
        bot._verified_price_selection = {
            "coords": (300, 1200),
            "price_index": 0,
            "target_price": "880元",
            "observed_price": "880元",
        }
        config = MagicMock(
            if_commit_order=True,
            price_index=0,
            price="880元",
        )
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        assert selector._verify_formal_price_selection_after_click(
            (300, 1200)
        ) is True
        assert bot._verified_price_selection["selection_verified"] is True
        assert (
            bot._verified_price_selection["selection_source"]
            == "post_click_hierarchy"
        )
        bot._set_terminal_failure.assert_not_called()

    def test_post_click_hierarchy_rejects_target_card_without_selected_state(self):
        import xml.etree.ElementTree as ET

        root = ET.Element("hierarchy")
        container = ET.SubElement(
            root,
            "node",
            attrib={
                "resource-id": (
                    "cn.damai:id/project_detail_perform_price_flowlayout"
                )
            },
        )
        card = ET.SubElement(
            container,
            "node",
            attrib={
                "class": "android.widget.FrameLayout",
                "clickable": "true",
                "bounds": "[280,1160][320,1240]",
            },
        )
        ET.SubElement(card, "node", attrib={"text": "880元"})

        bot = MagicMock()
        bot._parse_bounds.return_value = (280, 1160, 320, 1240)
        bot._dump_hierarchy_xml.return_value = root
        bot._verified_price_selection = {
            "coords": (300, 1200),
            "price_index": 0,
            "target_price": "880元",
            "observed_price": "880元",
        }
        config = MagicMock(
            if_commit_order=True,
            price_index=0,
            price="880元",
        )
        selector = PriceSelector(
            device=MagicMock(), config=config, probe=MagicMock()
        )
        selector.set_bot(bot)

        assert selector._verify_formal_price_selection_after_click(
            (300, 1200)
        ) is False
        assert bot._verified_price_selection is None
        bot._set_terminal_failure.assert_called_once_with(
            "target_price_selection_unconfirmed"
        )


# ---------------------------------------------------------------------------
# Page-probe integration (P1 #31, Step 3)
# ---------------------------------------------------------------------------


class TestSelectByIndexPanelStateIntegration:
    def _make_selector(self, *, coords=(10, 20)):
        bot = MagicMock()
        bot._get_price_option_coordinates_by_config_index.return_value = coords
        selector = PriceSelector(
            device=MagicMock(),
            config=MagicMock(price_index=0),
            probe=MagicMock(),
        )
        selector.set_bot(bot)
        return selector

    def test_sold_out_raises_sold_out_error(self, _force_price_panel_ready):
        _force_price_panel_ready.return_value = "sold_out"
        selector = self._make_selector()
        with pytest.raises(SoldOutError):
            selector.select_by_index()

    def test_loading_then_ready_succeeds(self, _force_price_panel_ready):
        _force_price_panel_ready.side_effect = ["loading", "ready"]
        selector = self._make_selector()
        with patch("time.sleep") as mock_sleep:
            with patch(
                "time.monotonic",
                side_effect=[0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3],
            ):
                assert selector.select_by_index() is True
        assert mock_sleep.called

    def test_loading_timeout_raises_price_selector_error(
        self, _force_price_panel_ready
    ):
        _force_price_panel_ready.return_value = "loading"
        selector = self._make_selector()
        # monotonic: first start = 0.0, deadline = 2.0; subsequent values jump past
        with patch("time.sleep"):
            with patch("time.monotonic", side_effect=[0.0, 0.5, 1.0, 1.5, 2.5]):
                with pytest.raises(PriceSelectorError, match="加载超时"):
                    selector.select_by_index()

    def test_unknown_logs_warning_and_continues(self, _force_price_panel_ready):
        _force_price_panel_ready.return_value = "unknown"
        selector = self._make_selector()
        with patch("mobile.price_selector.logger") as mock_logger:
            assert selector.select_by_index() is True
        warning_calls = [c for c in mock_logger.warning.call_args_list]
        assert any("state=unknown" in str(c) for c in warning_calls)

    def test_ready_proceeds_without_warning(self, _force_price_panel_ready):
        _force_price_panel_ready.return_value = "ready"
        selector = self._make_selector()
        with patch("mobile.price_selector.logger") as mock_logger:
            assert selector.select_by_index() is True
        warning_calls = [c for c in mock_logger.warning.call_args_list]
        assert not any("state=unknown" in str(c) for c in warning_calls)
