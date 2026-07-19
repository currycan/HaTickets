# -*- coding: UTF-8 -*-
"""Unit tests for mobile/recovery.py — RecoveryHelper class."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mobile.page_probe import (
    MANUAL_VERIFICATION_MARKERS,
    PENDING_ORDER_MARKERS,
)
from mobile.recovery import (
    RecoveryHelper,
    _MAX_BACK_STEPS,
    _TRANSIENT_ACTION_BUDGET,
    _TRANSIENT_ACTION_COOLDOWN_S,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(page: str) -> dict:
    """Build a minimal probe result dict."""
    return {"state": page}


def _build_helper(probe_states):
    """Create a RecoveryHelper with mocked dependencies.

    Args:
        probe_states: A list of dicts that ``probe.probe_current_page`` will
            return on successive calls.

    Returns:
        Tuple of (helper, device_mock, probe_mock, navigator_mock).
    """
    device = MagicMock()
    device.shell.return_value = SimpleNamespace(exit_code=0)
    probe = MagicMock()
    navigator = MagicMock()

    probe.probe_current_page.side_effect = list(probe_states)

    return RecoveryHelper(device, probe, navigator), device, probe, navigator


_LOCAL_TRANSIENT_RETRY_XML = """
<hierarchy>
  <node resource-id="cn.damai:id/app_root">
    <node resource-id="cn.damai:id/network_error_panel">
      <node text="网络开小差，请稍后重试" bounds="[0,0][100,100]" />
      <node text="重新加载" bounds="[100,200][300,300]" />
    </node>
  </node>
</hierarchy>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecoveryHelper:
    """Tests for RecoveryHelper.recover_to_detail_page."""

    def test_already_on_detail_page(self):
        """If already on detail_page, return immediately — no back pressed."""
        helper, device, probe, navigator = _build_helper([
            _make_state("detail_page"),
        ])

        result = helper.recover_to_detail_page()

        assert result["state"] == "detail_page"
        device.press.assert_not_called()
        navigator.navigate_to_target_event.assert_not_called()
        # Only one probe call (the initial check).
        assert probe.probe_current_page.call_count == 1

    def test_already_on_sku_page(self):
        """sku_page is also a valid target — return immediately."""
        helper, device, probe, navigator = _build_helper([
            _make_state("sku_page"),
        ])

        result = helper.recover_to_detail_page()

        assert result["state"] == "sku_page"
        device.press.assert_not_called()

    def test_one_back_reaches_detail(self):
        """One back press reaches detail_page."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),   # initial check
            _make_state("detail_page"),  # after 1st back
        ])

        result = helper.recover_to_detail_page()

        assert result["state"] == "detail_page"
        device.press.assert_called_once_with("back")
        probe.invalidate_cache.assert_called_once()
        navigator.navigate_to_target_event.assert_not_called()

    def test_deep_back_five_steps(self):
        """Five back presses before reaching detail_page."""
        helper, device, probe, navigator = _build_helper([
            _make_state("some_page_0"),  # initial
            _make_state("some_page_1"),  # back 1
            _make_state("some_page_2"),  # back 2
            _make_state("some_page_3"),  # back 3
            _make_state("some_page_4"),  # back 4
            _make_state("detail_page"),  # back 5
        ])

        result = helper.recover_to_detail_page()

        assert result["state"] == "detail_page"
        assert device.press.call_count == 5
        assert probe.invalidate_cache.call_count == 5
        navigator.navigate_to_target_event.assert_not_called()

    def test_homepage_triggers_forward_navigation(self):
        """Hitting homepage during back loop triggers forward navigation."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),   # initial
            _make_state("some_page"),    # back 1
            _make_state("homepage"),     # back 2 → break to forward nav
            _make_state("detail_page"),  # after forward navigation probe
        ])

        result = helper.recover_to_detail_page()

        assert result["state"] == "detail_page"
        assert device.press.call_count == 2
        navigator.navigate_to_target_event.assert_called_once_with(
            initial_probe=_make_state("homepage")
        )

    @patch("mobile.recovery.time.sleep")
    def test_all_failed_returns_last_state(self, mock_sleep):
        """When all strategies fail, return last known state without crashing."""
        # Initial check + 8 backs (all unknown) + forward nav probe (still unknown)
        states = (
            [_make_state("unknown")]      # initial
            + [_make_state("unknown")] * _MAX_BACK_STEPS  # 8 backs
            + [_make_state("unknown")]    # after forward navigation
        )
        helper, device, probe, navigator = _build_helper(states)

        monotonic_values = iter(
            value
            for step in range(_MAX_BACK_STEPS)
            for value in (float(step), float(step) + 1.0)
        )
        with patch(
            "mobile.recovery.time.monotonic",
            side_effect=lambda: next(monotonic_values),
        ):
            result = helper.recover_to_detail_page()

        assert result["state"] == "unknown"
        assert device.press.call_count == _MAX_BACK_STEPS
        navigator.navigate_to_target_event.assert_called_once_with(
            initial_probe=_make_state("unknown")
        )


class TestTransientRecovery:
    def test_persistent_transient_fingerprint_is_dispatched_only_once(self):
        helper, device, probe, _ = _build_helper(
            [_make_state("transient_error")] * 100
        )
        device.dump_hierarchy.return_value = _LOCAL_TRANSIENT_RETRY_XML
        clock = [0.0]

        def monotonic():
            return clock[0]

        def sleep(seconds):
            clock[0] += seconds

        with patch(
            "mobile.recovery.time.monotonic", side_effect=monotonic
        ), patch("mobile.recovery.time.sleep", side_effect=sleep):
            first = helper.recover_transient_failure(
                expected_states={"detail_page"},
                max_attempts=_TRANSIENT_ACTION_BUDGET,
                settle_timeout_s=_TRANSIENT_ACTION_COOLDOWN_S,
                initial_probe=_make_state("transient_error"),
            )
            repeated = helper.recover_transient_failure(
                expected_states={"detail_page"},
                max_attempts=1,
                initial_probe=_make_state("transient_error"),
            )

        assert helper._transient_action_budget == 3
        assert first["recovery_actions_used"] == 1
        assert first["recovery_duplicate_fingerprint"] is True
        assert repeated["recovery_duplicate_fingerprint"] is True
        assert repeated["recovery_actions_used"] == 1
        assert device.shell.call_count == 1
        device.click.assert_not_called()

    def test_same_fingerprint_never_rearms_after_cooldown(self):
        helper, _, _, _ = _build_helper([])
        snapshot = {
            "transient": True,
            "blank": False,
            "retry_coords": (200, 250),
        }

        with patch(
            "mobile.recovery.time.monotonic",
            side_effect=[10.0, 10.799, 10.8],
        ):
            assert helper._reserve_transient_action(snapshot) == "allowed"
            assert helper._reserve_transient_action(snapshot) == "cooldown"
            assert helper._reserve_transient_action(snapshot) == "duplicate"

        assert _TRANSIENT_ACTION_COOLDOWN_S == 0.8
        assert helper._transient_actions_used == 1

    def test_changed_layout_fingerprint_can_use_next_bounded_action(self):
        helper, _, _, _ = _build_helper([])
        first = {
            "transient": True,
            "blank": False,
            "activity": "ProjectDetailActivity",
            "layout_fingerprint": "layout-a",
            "error_markers": ("网络异常",),
            "retry_coords": (200, 250),
        }
        changed = {**first, "layout_fingerprint": "layout-b"}

        with patch(
            "mobile.recovery.time.monotonic",
            side_effect=[10.0, 10.1],
        ):
            assert helper._reserve_transient_action(first) == "allowed"
            assert helper._reserve_transient_action(changed) == "allowed"

        assert helper._transient_actions_used == 2

    def test_reset_restores_budget_and_clears_cooldown_fingerprint(self):
        helper, _, _, _ = _build_helper([])
        helper._transient_actions_used = _TRANSIENT_ACTION_BUDGET
        helper._transient_last_action_at = 123.0
        helper._transient_last_fingerprint = (True, False, (200, 250))
        helper._transient_dispatched_fingerprints.add(
            helper._transient_last_fingerprint
        )

        helper.reset_transient_action_budget()

        assert helper._transient_actions_used == 0
        assert helper._transient_last_action_at is None
        assert helper._transient_last_fingerprint is None
        assert helper._transient_dispatched_fingerprints == set()

    def test_reuses_fresh_initial_probe_before_first_retry(self):
        helper, device, probe, _ = _build_helper(
            [
                _make_state("detail_page"),
            ]
        )
        device.dump_hierarchy.return_value = _LOCAL_TRANSIENT_RETRY_XML

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            settle_timeout_s=0.1,
            initial_probe=_make_state("transient_error"),
        )

        assert result["state"] == "detail_page"
        assert result["recovered"] is True
        assert probe.probe_current_page.call_count == 1
        device.shell.assert_called_once_with("input tap 200 250")

    def test_clean_snapshot_after_seeded_error_reprobes_without_clicking(self):
        helper, device, probe, _ = _build_helper(
            [_make_state("detail_page")]
        )
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/title_tv"
                text="演出详情" visible-to-user="true" />
        </hierarchy>
        """

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            initial_probe=_make_state("transient_error"),
        )

        assert result["state"] == "detail_page"
        assert result["recovered"] is True
        assert result["action_dispatched"] is False
        assert result["recovery_actions_used"] == 0
        probe.probe_current_page.assert_called_once_with(fast=False)
        device.shell.assert_not_called()
        device.click.assert_not_called()

    def test_initial_expected_state_returns_without_device_probe(self):
        helper, device, probe, _ = _build_helper([])

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            initial_probe=_make_state("detail_page"),
        )

        assert result["state"] == "detail_page"
        probe.probe_current_page.assert_not_called()
        device.dump_hierarchy.assert_not_called()

    def test_clicks_retry_only_when_error_marker_is_present(self):
        helper, device, probe, _ = _build_helper(
            [
                _make_state("transient_error"),
                _make_state("detail_page"),
            ]
        )
        device.dump_hierarchy.return_value = _LOCAL_TRANSIENT_RETRY_XML

        result = helper.recover_transient_failure(
            expected_states={"detail_page"}, settle_timeout_s=0.1
        )

        assert result["state"] == "detail_page"
        assert result["recovered"] is True
        device.shell.assert_called_once_with("input tap 200 250")

    def test_unrelated_refresh_control_is_not_clicked(self):
        helper, device, probe, _ = _build_helper([])
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/app_root">
            <node resource-id="cn.damai:id/page_content">
              <node resource-id="cn.damai:id/network_error_panel">
                <node text="网络异常" bounds="[0,0][300,100]" />
              </node>
              <node resource-id="cn.damai:id/article_toolbar">
                <node text="刷新" clickable="true"
                      bounds="[800,0][1000,100]" />
              </node>
            </node>
          </node>
        </hierarchy>
        """

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            initial_probe=_make_state("transient_error"),
        )

        assert result["state"] == "transient_error"
        assert result["recovered"] is False
        assert result["retry_action_unconfirmed"] is True
        assert result["action_dispatched"] is False
        assert helper._transient_actions_used == 0
        device.shell.assert_not_called()
        device.click.assert_not_called()
        probe.probe_current_page.assert_not_called()

    def test_observe_only_reports_retry_plan_without_click_or_budget_use(self):
        helper, device, probe, _ = _build_helper([])
        device.dump_hierarchy.return_value = _LOCAL_TRANSIENT_RETRY_XML

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            initial_probe=_make_state("transient_error"),
            observe_only=True,
        )

        assert result["observe_only"] is True
        assert result["planned_action"] == "tap_retry"
        assert result["planned_action_coords"] == (200, 250)
        assert result["action_dispatched"] is False
        assert result["recovery_actions_used"] == 0
        assert helper._transient_actions_used == 0
        probe.probe_current_page.assert_not_called()
        device.shell.assert_not_called()
        device.app_start.assert_not_called()
        device.click.assert_not_called()

    def test_reuses_recent_page_probe_snapshot_without_second_hierarchy_dump(self):
        helper, device, probe, _ = _build_helper([])
        with patch("mobile.recovery.time.monotonic", return_value=10.1):
            result = helper.recover_transient_failure(
                expected_states={"detail_page"},
                initial_probe={
                    "state": "transient_error",
                    "snapshot_observed_at": 10.0,
                    "transient_retry_coords": (200, 250),
                },
                observe_only=True,
            )

        assert result["planned_action"] == "tap_retry"
        assert result["planned_action_coords"] == (200, 250)
        assert result["reused_snapshot"] is True
        device.dump_hierarchy.assert_not_called()
        device.shell.assert_not_called()
        probe.probe_current_page.assert_not_called()

    def test_action_mode_revalidates_recent_retry_coordinates_before_tap(self):
        helper, device, probe, _ = _build_helper(
            [_make_state("detail_page")]
        )
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="演出详情" bounds="[0,0][1080,2400]" />
        </hierarchy>
        """

        with patch("mobile.recovery.time.monotonic", return_value=10.1):
            result = helper.recover_transient_failure(
                expected_states={"detail_page"},
                initial_probe={
                    "state": "transient_error",
                    "snapshot_observed_at": 10.0,
                    "transient_retry_coords": (200, 250),
                },
                observe_only=False,
            )

        assert result["state"] == "detail_page"
        assert result["recovered"] is True
        device.dump_hierarchy.assert_called_once_with()
        device.shell.assert_not_called()

    def test_repeated_error_page_is_bounded_across_calls(self):
        helper, device, probe, _ = _build_helper(
            [_make_state("transient_error")] * 10
        )
        helper._transient_action_budget = 1
        device.dump_hierarchy.return_value = _LOCAL_TRANSIENT_RETRY_XML

        first = helper.recover_transient_failure(
            expected_states={"detail_page"},
            max_attempts=1,
            settle_timeout_s=0.05,
            initial_probe=_make_state("transient_error"),
        )
        helper._transient_last_action_at -= _TRANSIENT_ACTION_COOLDOWN_S + 0.1
        second = helper.recover_transient_failure(
            expected_states={"detail_page"},
            max_attempts=1,
            settle_timeout_s=0.05,
            initial_probe=_make_state("transient_error"),
        )

        assert first["action_dispatched"] is True
        assert second["recovery_duplicate_fingerprint"] is True
        assert second["recovery_actions_used"] == 1
        assert device.shell.call_count == 1

    def test_uncertain_shell_result_never_falls_back_to_second_click(self):
        helper, device, _, _ = _build_helper([])
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/app_root">
            <node resource-id="cn.damai:id/network_error_panel">
              <node text="页面加载失败" bounds="[0,0][100,100]" />
              <node text="重试" bounds="[100,200][300,300]" />
            </node>
          </node>
        </hierarchy>
        """
        device.shell.side_effect = RuntimeError("transport lost")

        result = helper.recover_transient_failure(
            expected_states={"detail_page"},
            initial_probe=_make_state("transient_error"),
        )

        assert result["device_error"] is True
        assert result["action_dispatch_uncertain"] is True
        assert result["action_dispatched"] is True
        device.click.assert_not_called()

    def test_nonzero_or_unknown_shell_result_is_uncertain_and_never_reclicked(self):
        for shell_response in (
            SimpleNamespace(exit_code=1),
            object(),
        ):
            helper, device, probe, _ = _build_helper([])
            device.dump_hierarchy.return_value = """
            <hierarchy>
              <node resource-id="cn.damai:id/app_root">
                <node resource-id="cn.damai:id/network_error_panel">
                  <node text="页面加载失败" bounds="[0,0][100,100]" />
                  <node text="重试" bounds="[100,200][300,300]" />
                </node>
              </node>
            </hierarchy>
            """
            device.shell.return_value = shell_response

            result = helper.recover_transient_failure(
                expected_states={"detail_page"},
                initial_probe=_make_state("transient_error"),
            )

            assert result["device_error"] is True
            assert result["action_dispatch_uncertain"] is True
            assert result["action_dispatched"] is True
            assert result["recovery_actions_used"] == 1
            device.shell.assert_called_once_with("input tap 200 250")
            device.click.assert_not_called()
            probe.probe_current_page.assert_not_called()

    def test_never_operates_manual_verification(self):
        for marker in MANUAL_VERIFICATION_MARKERS:
            helper, device, _, _ = _build_helper(
                [_make_state("transient_error")]
            )
            device.dump_hierarchy.return_value = f"""
            <hierarchy>
              <node text="{marker}" bounds="[0,0][100,100]" />
              <node text="重试" bounds="[100,200][300,300]" />
            </hierarchy>
            """

            result = helper.recover_transient_failure(
                expected_states={"detail_page"}
            )

            assert result["manual_verification"] is True, marker
            assert result["recovered"] is False, marker
            device.shell.assert_not_called()
            device.click.assert_not_called()

    def test_pending_order_markers_never_trigger_refresh(self):
        for marker in PENDING_ORDER_MARKERS:
            helper, device, _, _ = _build_helper(
                [_make_state("transient_error")]
            )
            device.dump_hierarchy.return_value = f"""
            <hierarchy>
              <node text="{marker}" bounds="[0,0][100,100]" />
              <node text="重试" bounds="[100,200][300,300]" />
            </hierarchy>
            """

            result = helper.recover_transient_failure(
                expected_states={"detail_page"}
            )

            assert result["state"] == "pending_order_dialog", marker
            assert result["pending_order_dialog"] is True, marker
            assert result["recovered"] is False, marker
            device.shell.assert_not_called()
            device.click.assert_not_called()

    def test_retry_phrase_uses_clickable_parent_bounds(self):
        helper, device, _, _ = _build_helper(
            [
                _make_state("transient_error"),
                _make_state("detail_page"),
            ]
        )
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node resource-id="cn.damai:id/app_root">
            <node resource-id="cn.damai:id/network_error_panel">
              <node text="页面加载失败" bounds="[0,0][100,100]" />
              <node clickable="true" bounds="[100,200][500,400]">
                <node text="重新加载页面" bounds="[150,230][450,330]" />
              </node>
            </node>
          </node>
        </hierarchy>
        """

        result = helper.recover_transient_failure(expected_states={"detail_page"})

        assert result["state"] == "detail_page"
        device.shell.assert_called_once_with("input tap 300 300")

    def test_visible_combined_click_retry_copy_uses_its_own_bounds(self):
        helper, device, _, _ = _build_helper(
            [
                _make_state("transient_error"),
                _make_state("detail_page"),
            ]
        )
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="网络异常，请点击重试"
                visible-to-user="true"
                bounds="[100,200][500,400]" />
        </hierarchy>
        """

        result = helper.recover_transient_failure(
            expected_states={"detail_page"}
        )

        assert result["state"] == "detail_page"
        device.shell.assert_called_once_with("input tap 300 300")

    def test_hierarchy_transport_error_never_triggers_blank_foreground_restore(self):
        helper, device, _, _ = _build_helper([_make_state("unknown")])
        device.dump_hierarchy.side_effect = RuntimeError("device offline")

        result = helper.recover_transient_failure(expected_states={"detail_page"})

        assert result["device_error"] is True
        assert result["recovered"] is False
        device.app_start.assert_not_called()

    def test_blank_unknown_page_brings_existing_app_to_foreground(self):
        helper, device, probe, navigator = _build_helper(
            [
                _make_state("unknown"),
                _make_state("detail_page"),
            ]
        )
        navigator._config.app_package = "cn.damai"
        device.dump_hierarchy.return_value = "<hierarchy/>"

        result = helper.recover_transient_failure(
            expected_states={"detail_page"}, settle_timeout_s=0.1
        )

        assert result["state"] == "detail_page"
        device.app_start.assert_called_once_with("cn.damai", stop=False)

    def test_layout_only_blank_page_is_treated_as_blank(self):
        helper, device, _, navigator = _build_helper(
            [_make_state("unknown"), _make_state("detail_page")]
        )
        navigator._config.app_package = "cn.damai"
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node class="android.widget.FrameLayout" package="cn.damai"
                bounds="[0,0][1080,2400]" />
        </hierarchy>
        """

        result = helper.recover_transient_failure(expected_states={"detail_page"})

        assert result["state"] == "detail_page"
        device.app_start.assert_called_once_with("cn.damai", stop=False)

    def test_canvas_resource_page_is_not_misclassified_as_blank(self):
        helper, device, _, _ = _build_helper([_make_state("unknown")])
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node class="android.view.View" package="cn.damai"
                resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
                bounds="[0,1800][1080,2400]" />
        </hierarchy>
        """

        result = helper.recover_transient_failure(expected_states={"detail_page"})

        assert result["state"] == "unknown"
        device.app_start.assert_not_called()

    def test_hidden_error_and_retry_nodes_are_never_operated(self):
        helper, device, _, _ = _build_helper(
            [
                _make_state("transient_error"),
                _make_state("detail_page"),
            ]
        )
        device.dump_hierarchy.return_value = """
        <hierarchy>
          <node text="网络异常" visible-to-user="false"
                bounds="[0,0][100,100]" />
          <node text="重试" visible-to-user="false"
                clickable="true" bounds="[100,200][300,300]" />
          <node resource-id="cn.damai:id/title_tv"
                visible-to-user="true" />
        </hierarchy>
        """

        result = helper.recover_transient_failure(
            expected_states={"detail_page"}
        )

        assert result["state"] == "detail_page"
        assert result["recovered"] is True
        device.shell.assert_not_called()
        device.click.assert_not_called()

    @patch("mobile.recovery.time.sleep")
    def test_back_waits_for_async_state_change(self, mock_sleep):
        """A back press is polled before another back can be sent."""
        helper, device, probe, navigator = _build_helper([
            _make_state("order_page"),
            _make_state("order_page"),
            _make_state("some_page"),
            _make_state("detail_page"),
        ])

        helper.recover_to_detail_page()

        mock_sleep.assert_called_once_with(0.03)
