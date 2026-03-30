# -*- coding: UTF-8 -*-
"""Unit tests for web/order_submitter.py"""

from unittest.mock import Mock, MagicMock, patch

import pytest
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException


def _make_submitter(driver=None):
    from order_submitter import OrderSubmitter
    if driver is None:
        driver = Mock()
    config = Mock()
    config.fast_mode = True
    return OrderSubmitter(driver, config)


def _mock_element(text="立即提交"):
    el = Mock()
    el.text = text
    el.get_attribute = Mock(return_value="")
    return el


# ---------------------------------------------------------------------------
# try_submit_by_text
# ---------------------------------------------------------------------------

class TestTrySubmitByText:
    def test_finds_button_element_by_text(self):
        driver = Mock()
        el = _mock_element("立即提交")
        driver.find_element = Mock(return_value=el)

        sm = _make_submitter(driver)
        result = sm.try_submit_by_text(["立即提交"])

        assert result is True
        el.click.assert_called_once()

    def test_tries_multiple_text_variants(self):
        driver = Mock()
        el = _mock_element("提交订单")
        # first text fails, second succeeds
        driver.find_element = Mock(side_effect=[
            NoSuchElementException(), NoSuchElementException(), NoSuchElementException(),
            NoSuchElementException(), el,
        ])

        sm = _make_submitter(driver)
        result = sm.try_submit_by_text(["立即提交", "提交订单"])

        assert result is True

    def test_returns_false_when_all_texts_fail(self):
        driver = Mock()
        driver.find_element = Mock(side_effect=NoSuchElementException())

        sm = _make_submitter(driver)
        result = sm.try_submit_by_text(["立即提交", "提交订单"])

        assert result is False

    def test_intercepted_click_continues(self):
        driver = Mock()
        el = _mock_element("提交")
        el.click = Mock(side_effect=ElementClickInterceptedException())
        driver.find_element = Mock(return_value=el)

        sm = _make_submitter(driver)
        # ElementClickInterceptedException is caught, try_submit_by_text continues
        # since the first find_element succeeds but click is intercepted, it goes to next text
        result = sm.try_submit_by_text(["提交"])
        # Whether True or False, should not raise
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# try_submit_by_view_name
# ---------------------------------------------------------------------------

class TestTrySubmitByViewName:
    def test_finds_by_view_name(self):
        driver = Mock()
        el = _mock_element("提交")
        parent = Mock()
        el.find_element = Mock(return_value=parent)
        driver.find_element = Mock(return_value=el)

        sm = _make_submitter(driver)
        result = sm.try_submit_by_view_name()

        assert result is True
        parent.click.assert_called_once()

    def test_returns_false_when_not_found(self):
        driver = Mock()
        driver.find_element = Mock(side_effect=NoSuchElementException())

        sm = _make_submitter(driver)
        result = sm.try_submit_by_view_name()

        assert result is False


# ---------------------------------------------------------------------------
# try_submit_by_class
# ---------------------------------------------------------------------------

class TestTrySubmitByClass:
    def test_finds_by_class_name(self):
        driver = Mock()
        el = _mock_element()
        driver.find_element = Mock(return_value=el)

        sm = _make_submitter(driver)
        result = sm.try_submit_by_class()

        assert result is True
        el.click.assert_called_once()

    def test_tries_multiple_classes(self):
        driver = Mock()
        el = _mock_element()
        # first 4 classes fail, 5th succeeds
        driver.find_element = Mock(side_effect=[
            NoSuchElementException(), NoSuchElementException(),
            NoSuchElementException(), NoSuchElementException(), el,
        ])

        sm = _make_submitter(driver)
        result = sm.try_submit_by_class()

        assert result is True

    def test_returns_false_when_no_class_found(self):
        driver = Mock()
        driver.find_element = Mock(side_effect=NoSuchElementException())

        sm = _make_submitter(driver)
        result = sm.try_submit_by_class()

        assert result is False


# ---------------------------------------------------------------------------
# try_submit_by_original_xpath
# ---------------------------------------------------------------------------

class TestTrySubmitByOriginalXpath:
    def test_finds_by_original_xpath(self):
        driver = Mock()
        el = _mock_element()
        driver.find_element = Mock(return_value=el)

        sm = _make_submitter(driver)
        result = sm.try_submit_by_original_xpath()

        assert result is True
        el.click.assert_called_once()

    def test_returns_false_when_xpath_not_found(self):
        driver = Mock()
        driver.find_element = Mock(side_effect=NoSuchElementException())

        sm = _make_submitter(driver)
        result = sm.try_submit_by_original_xpath()

        assert result is False


# ---------------------------------------------------------------------------
# submit_order (all strategies fail)
# ---------------------------------------------------------------------------

class TestSubmitOrderAllFail:
    def test_no_exception_when_all_methods_fail(self):
        driver = Mock()
        driver.find_element = Mock(side_effect=NoSuchElementException())
        driver.find_elements = Mock(return_value=[])

        sm = _make_submitter(driver)
        sm.submit_order()  # should not raise


# ---------------------------------------------------------------------------
# submit_order (first strategy succeeds)
# ---------------------------------------------------------------------------

class TestSubmitOrderSuccess:
    def test_stops_after_first_success(self):
        driver = Mock()
        el = _mock_element("立即提交")
        driver.find_element = Mock(return_value=el)
        driver.find_elements = Mock(return_value=[])

        sm = _make_submitter(driver)
        sm.submit_order()

        el.click.assert_called()
