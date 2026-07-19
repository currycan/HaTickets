"""Unit tests for mobile/prompt_runner.py."""

import contextlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from adbutils.errors import AdbError  # noqa: F401
from uiautomator2.exceptions import ConnectError  # noqa: F401

from mobile.config import Config, ConfigError
from mobile.prompt_parser import PromptIntent, parse_prompt
from mobile import prompt_runner
from mobile.prompt_runner import (
    _auto_sync_device_config,
    _bootstrap_default_config_if_missing,
    _config_path,
    _load_base_config_dict,
    _format_price_option,
    _format_summary,
    _prompt_choice,
    _prompt_yes_no,
    _reject_placeholder_serial,
    _repo_root,
    _resolve_confirmed_date,
    _resolve_confirmed_price,
    _success_detail_for_mode,
    _split_city_and_venue,
    parse_args,
)


def _make_base_config() -> Config:
    return Config(
        serial="ABC123",
        app_package="cn.damai",
        app_activity=".launcher.splash.SplashMainActivity",
        keyword="旧关键词",
        users=["张志涛"],
        city="上海",
        date="04.04",
        price="899元",
        price_index=5,
        if_commit_order=False,
        probe_only=True,
        auto_navigate=False,
        rush_mode=False,
        target_title="旧标题",
        target_venue="旧场馆",
    )


def _make_massiwei_discovery():
    return {
        "used_keyword": "马思唯 演唱会",
        "page_probe": {"state": "detail_page"},
        "search_results": [
            {
                "score": 92,
                "title": "【上海】马思唯-乐透人生 The Lottery TOUR 巡回演唱会-上海站",
                "city": "上海",
                "venue": "浦发银行东方体育中心",
                "time": "2026-04-04 19:00",
            }
        ],
    }


def _make_massiwei_summary():
    return {
        "title": "【上海】马思唯-乐透人生 The Lottery TOUR 巡回演唱会-上海站",
        "venue": "上海市 · 浦发银行东方体育中心",
        "state": "detail_page",
        "reservation_mode": False,
        "dates": ["04.04", "04.05"],
        "price_options": [
            {"index": 5, "text": "看台 899元", "tag": "可选"},
            {"index": 6, "text": "内场 1299元", "tag": "可选", "source": "ocr"},
        ],
    }


def test_build_updated_config_for_probe_mode():
    base_config = _make_base_config().to_dict()
    intent = parse_prompt("帮张志涛抢一张 4 月 6 号张杰的演唱会门票，1280元")
    discovery = {
        "used_keyword": "张杰 演唱会",
        "search_results": [
            {
                "title": "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站",
                "city": "北京",
                "venue": "国家体育场-鸟巢",
            }
        ],
        "summary": {
            "title": "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站",
            "venue": "北京市 · 国家体育场-鸟巢",
        },
    }
    selected_price = {"index": 6, "text": "1280元", "tag": "可预约"}

    updated = prompt_runner.build_updated_config(
        base_config, intent, discovery, "04.06", selected_price, "probe"
    )

    assert updated["keyword"] == "张杰 演唱会"
    assert (
        updated["target_title"] == "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"
    )
    assert updated["target_venue"] == "国家体育场-鸟巢"
    assert updated["users"] == ["张志涛"]
    assert updated["city"] == "北京"
    assert updated["date"] == "04.06"
    assert updated["price"] == "1280元"
    assert updated["price_index"] == 6
    assert updated["probe_only"] is True
    assert updated["if_commit_order"] is False
    assert updated["auto_navigate"] is True
    assert updated["rush_mode"] is True
    assert updated["fast_retry_interval_ms"] == 80
    assert updated["wait_cta_ready_timeout_ms"] == 0


def test_build_updated_config_drops_stale_target_when_summary_is_unknown():
    base_config = _make_base_config().to_dict()
    base_config.update(
        {
            "target_title": "旧演出标题",
            "target_venue": "旧场馆",
            "keyword": "旧关键词",
            "city": "上海",
            "date": "04.04",
            "price": "899元",
            "price_index": 5,
            "auto_navigate": True,
        }
    )
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    discovery = {
        "used_keyword": "马思唯 演唱会",
        "search_results": [],
        "summary": {
            "title": "未识别",
            "venue": "未识别",
        },
    }
    selected_price = {"index": 5, "text": "899元", "tag": "", "source": "ocr"}

    updated = prompt_runner.build_updated_config(
        base_config, intent, discovery, "04.04", selected_price, "apply"
    )

    assert updated["target_title"] is None
    assert updated["target_venue"] is None


# ---------------------------------------------------------------------------
# _split_city_and_venue
# ---------------------------------------------------------------------------


class TestSplitCityAndVenue:
    def test_with_dot_separator(self):
        city, venue = _split_city_and_venue("北京市·国家体育场")
        assert city == "北京"
        assert venue == "国家体育场"

    def test_without_separator(self):
        city, venue = _split_city_and_venue("国家体育场")
        assert city is None
        assert venue == "国家体育场"

    def test_empty_string(self):
        city, venue = _split_city_and_venue("")
        assert city is None
        assert venue == ""

    def test_none_input(self):
        city, venue = _split_city_and_venue(None)
        assert city is None
        assert venue == ""

    def test_nbsp_stripped(self):
        city, venue = _split_city_and_venue("上海市\u00a0·\u00a0梅赛德斯奔驰")
        assert city == "上海"
        assert "梅赛德斯" in venue


# ---------------------------------------------------------------------------
# _format_price_option
# ---------------------------------------------------------------------------


class TestFormatPriceOption:
    def test_basic_option(self):
        result = _format_price_option({"index": 0, "text": "580元", "tag": "可预约"})
        assert "[0]" in result
        assert "580元" in result
        assert "可预约" in result

    def test_no_tag(self):
        result = _format_price_option({"index": 1, "text": "380元"})
        assert "[1]" in result
        assert "[" not in result.split("]", 1)[1].strip()[:3]  # no tag bracket

    def test_ocr_source(self):
        result = _format_price_option({"index": 2, "text": "280元", "source": "ocr"})
        assert "OCR" in result

    def test_no_text_shows_placeholder(self):
        result = _format_price_option({"index": 0, "text": None})
        assert "未识别" in result

    def test_missing_text_key(self):
        result = _format_price_option({"index": 0})
        assert "未识别" in result


# ---------------------------------------------------------------------------
# _resolve_confirmed_date
# ---------------------------------------------------------------------------


class TestResolveConfirmedDate:
    def _summary(self, dates=None):
        return {"dates": dates or []}

    def test_intent_date_matches(self):
        intent = PromptIntent(raw_prompt="test", date="04.06")
        result = _resolve_confirmed_date(
            intent, self._summary(["04.06", "04.07"]), assume_yes=True
        )
        assert result == "04.06"

    def test_no_visible_dates_returns_intent_date(self):
        intent = PromptIntent(raw_prompt="test", date="04.06")
        result = _resolve_confirmed_date(intent, self._summary([]), assume_yes=True)
        assert result == "04.06"

    def test_single_date_returns_it(self):
        intent = PromptIntent(raw_prompt="test", date=None)
        result = _resolve_confirmed_date(
            intent, self._summary(["04.06"]), assume_yes=True
        )
        assert result == "04.06"

    def test_no_dates_no_intent_returns_none(self):
        intent = PromptIntent(raw_prompt="test", date=None)
        result = _resolve_confirmed_date(intent, self._summary([]), assume_yes=True)
        assert result is None

    def test_assume_yes_with_ambiguous_raises(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with pytest.raises(ValueError):
            _resolve_confirmed_date(
                intent, self._summary(["04.06", "04.07"]), assume_yes=True
            )

    def test_interactive_choice(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with patch("mobile.prompt_runner._prompt_choice", return_value="0"):
            result = _resolve_confirmed_date(
                intent, self._summary(["04.06", "04.07"]), assume_yes=False
            )
        assert result == "04.06"

    def test_interactive_cancelled(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with patch("mobile.prompt_runner._prompt_choice", return_value=None):
            result = _resolve_confirmed_date(
                intent, self._summary(["04.06", "04.07"]), assume_yes=False
            )
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_confirmed_price
# ---------------------------------------------------------------------------


class TestResolveConfirmedPrice:
    def _summary(self, options=None):
        return {"price_options": options or []}

    def test_chosen_price_returned_directly(self):
        intent = PromptIntent(raw_prompt="test")
        chosen = {"index": 1, "text": "1280元", "tag": "可预约"}
        result = _resolve_confirmed_price(
            intent, self._summary(), chosen, assume_yes=True
        )
        assert result is chosen

    def test_single_available_returned(self):
        intent = PromptIntent(raw_prompt="test")
        options = [{"index": 0, "text": "580元", "tag": "可预约"}]
        result = _resolve_confirmed_price(
            intent, self._summary(options), None, assume_yes=True
        )
        assert result["index"] == 0

    def test_no_available_returns_none(self):
        intent = PromptIntent(raw_prompt="test")
        options = [{"index": 0, "text": "580元", "tag": "售罄"}]
        result = _resolve_confirmed_price(
            intent, self._summary(options), None, assume_yes=True
        )
        assert result is None

    def test_assume_yes_multiple_available_raises(self):
        intent = PromptIntent(raw_prompt="test")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        with pytest.raises(ValueError):
            _resolve_confirmed_price(
                intent, self._summary(options), None, assume_yes=True
            )

    def test_assume_yes_price_mismatch_shows_requested_and_available_options(self):
        intent = PromptIntent(
            raw_prompt="test",
            price_hint="内场1080元",
            seat_hint="内场",
            numeric_price_hint=1080,
        )
        options = [
            {"index": 8, "text": "1280元", "tag": "", "source": "ocr"},
            {"index": 9, "text": "1680元", "tag": "", "source": "ocr"},
        ]

        with pytest.raises(ValueError) as exc_info:
            _resolve_confirmed_price(
                intent, self._summary(options), None, assume_yes=True
            )

        message = str(exc_info.value)
        assert "提示词里的目标票档是：内场1080元" in message
        assert "当前页面可选票档是：[8] 1280元 (OCR)、[9] 1680元 (OCR)" in message
        assert "请修改提示词里的票价/票档描述后重试。" in message

    def test_interactive_choice(self):
        intent = PromptIntent(raw_prompt="test")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        with patch("mobile.prompt_runner._prompt_choice", return_value="0"):
            result = _resolve_confirmed_price(
                intent, self._summary(options), None, assume_yes=False
            )
        assert result["index"] == 0

    def test_interactive_cancelled(self):
        intent = PromptIntent(raw_prompt="test")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        with patch("mobile.prompt_runner._prompt_choice", return_value=None):
            result = _resolve_confirmed_price(
                intent, self._summary(options), None, assume_yes=False
            )
        assert result is None


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_basic_prompt(self):
        args = parse_args(["帮我抢一张张杰演唱会"])
        assert args.prompt == "帮我抢一张张杰演唱会"
        assert args.mode == "summary"
        assert args.yes is False

    def test_mode_probe(self):
        args = parse_args(["张杰演唱会", "--mode", "probe"])
        assert args.mode == "probe"

    def test_mode_apply(self):
        args = parse_args(["张杰演唱会", "--mode", "apply"])
        assert args.mode == "apply"

    def test_mode_confirm_is_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["张杰演唱会", "--mode", "confirm"])

    def test_yes_flag(self):
        args = parse_args(["张杰演唱会", "-y"])
        assert args.yes is True

    def test_yes_long_flag(self):
        args = parse_args(["张杰演唱会", "--yes"])
        assert args.yes is True

    def test_config_override(self):
        args = parse_args(["张杰演唱会", "--config", "/tmp/custom.jsonc"])
        assert args.config == "/tmp/custom.jsonc"


class TestParseArgsStrict:
    """未知/缩写旗标 fail-fast（U-02：allow_abbrev=False）。"""

    def test_unknown_flag_porbe_rejected(self):
        # argparse 既有行为的回归锚，防止未来有人换成 parse_known_args
        with pytest.raises(SystemExit) as e:
            parse_args(["张杰演唱会", "--porbe"])
        assert e.value.code == 2

    def test_abbrev_mod_rejected_after_allow_abbrev_false(self):
        # 修复前 --mod 被静默缩写匹配为 --mode
        with pytest.raises(SystemExit) as e:
            parse_args(["张杰演唱会", "--mod", "summary"])
        assert e.value.code == 2

    def test_abbrev_conf_rejected(self):
        with pytest.raises(SystemExit) as e:
            parse_args(["张杰演唱会", "--conf", "x.jsonc"])
        assert e.value.code == 2

    def test_abbrev_non_rejected(self):
        with pytest.raises(SystemExit) as e:
            parse_args(["张杰演唱会", "--non"])
        assert e.value.code == 2

    def test_full_flags_unaffected_by_allow_abbrev(self):
        args = parse_args(
            [
                "张杰演唱会",
                "--mode",
                "probe",
                "--yes",
                "--config",
                "x.jsonc",
                "--non-interactive",
            ]
        )
        assert args.mode == "probe"
        assert args.yes is True
        assert args.config == "x.jsonc"
        assert args.non_interactive is True
        # 短参 -y 不受 allow_abbrev=False 影响
        assert parse_args(["张杰演唱会", "-y"]).yes is True

    def test_mode_value_typo_still_rejected(self):
        # choices 校验既有行为（值拼错），与 flag 拼错区分开
        with pytest.raises(SystemExit) as e:
            parse_args(["张杰演唱会", "--mode", "prob"])
        assert e.value.code == 2


# ---------------------------------------------------------------------------
# _format_summary
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def _base_discovery(self, **kwargs):
        d = {
            "used_keyword": "张杰 演唱会",
            "search_results": [],
            "summary": {
                "title": "张杰演唱会",
                "venue": "北京市·国家体育场",
                "state": "detail_page",
                "reservation_mode": False,
                "dates": ["04.06"],
                "price_options": [{"index": 0, "text": "580元", "tag": "可预约"}],
            },
        }
        d["summary"].update(kwargs)
        return d

    def test_basic_output_contains_keyword(self):
        intent = parse_prompt("帮张志涛抢张杰演唱会")
        discovery = self._base_discovery()
        result = _format_summary(intent, discovery, None)
        assert "张杰 演唱会" in result
        assert "观演人: 张志涛" in result
        assert "规范提示词:" in result
        assert "请先在大麦 App 中确认你已添加并保存以下观演人：张志涛" in result

    def test_no_price_options(self):
        intent = parse_prompt("帮我抢张杰演唱会")
        discovery = self._base_discovery(price_options=[])
        result = _format_summary(intent, discovery, None)
        assert "未识别" in result
        assert "提示词中未识别到观演人姓名" in result

    def test_chosen_price_shown(self):
        intent = parse_prompt("帮张志涛抢张杰演唱会")
        discovery = self._base_discovery()
        chosen = {"index": 0, "text": "580元", "tag": "可预约"}
        result = _format_summary(intent, discovery, chosen)
        assert "580元" in result

    def test_no_chosen_price_message(self):
        intent = parse_prompt("帮张志涛抢张杰演唱会")
        discovery = self._base_discovery()
        result = _format_summary(intent, discovery, None)
        assert "未能自动确定" in result

    def test_search_results_shown(self):
        intent = parse_prompt("帮张志涛抢张杰演唱会")
        discovery = self._base_discovery()
        discovery["search_results"] = [
            {
                "score": 95,
                "title": "张杰演唱会北京站",
                "city": "北京",
                "venue": "鸟巢",
                "time": "04.06",
            },
        ]
        result = _format_summary(intent, discovery, None)
        assert "张杰演唱会北京站" in result

    def test_step_timings_shown_with_manual_baseline(self):
        intent = parse_prompt("帮张志涛抢张杰演唱会")
        discovery = self._base_discovery()
        discovery["step_timings"] = [
            {
                "step": "搜索页输入并提交关键词",
                "seconds": 1.23,
                "manual_baseline_seconds": 6.0,
                "faster_than_manual": True,
            },
            {
                "step": "搜索结果扫描并打开目标",
                "seconds": 12.50,
                "manual_baseline_seconds": 12.0,
                "faster_than_manual": False,
            },
        ]
        result = _format_summary(intent, discovery, None)
        assert "步骤耗时:" in result
        assert "快于手动" in result
        assert "慢于手动" in result


class TestSuccessDetailForMode:
    def test_summary_message_no_longer_mentions_confirm(self):
        message = _success_detail_for_mode("summary")
        assert "apply" in message
        assert "只读 probe" in message
        assert "confirm" not in message

    def test_apply_message_no_longer_mentions_confirm(self):
        message = _success_detail_for_mode("apply", "mobile/config.jsonc")
        assert "prompt 的 probe 模式" in message
        assert "confirm" not in message


# ---------------------------------------------------------------------------
# _repo_root / _config_path
# ---------------------------------------------------------------------------


def test_repo_root_is_dir():
    root = _repo_root()
    assert root.is_dir()
    assert (root / "mobile").is_dir()


def test_config_path_returns_jsonc(monkeypatch):
    monkeypatch.delenv("HATICKETS_CONFIG_PATH", raising=False)
    path = _config_path()
    assert path.name == "config.jsonc"


def test_list_connected_device_ids_uses_utf8_wrapper():
    """issue #50 回归锁：adb devices 经 run_captured 显式 UTF-8 解码。"""
    completed = subprocess.CompletedProcess(
        args=["adb"],
        returncode=0,
        stdout="List of devices attached\nc6c4eb67\tdevice\n",
        stderr="",
    )
    with patch(
        "mobile.proc_utils.subprocess.run", return_value=completed
    ) as mock_run:
        device_ids = prompt_runner._list_connected_device_ids()

    assert device_ids == ["c6c4eb67"]
    kwargs = mock_run.call_args.kwargs
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["check"] is True


class TestAutoSyncDeviceConfig:
    def test_auto_sync_uses_single_connected_device(self):
        base_config = {"serial": "emulator-5554"}
        with patch(
            "mobile.prompt_runner._list_connected_device_ids", return_value=["c6c4eb67"]
        ):
            updated, message = _auto_sync_device_config(base_config, "apply")

        assert updated["serial"] == "c6c4eb67"
        assert "写配置时会一并保存" in message

    def test_auto_sync_summary_only_updates_runtime_message(self):
        base_config = {"serial": "emulator-5554"}
        with patch(
            "mobile.prompt_runner._list_connected_device_ids", return_value=["c6c4eb67"]
        ):
            updated, message = _auto_sync_device_config(base_config, "summary")

        assert updated["serial"] == "c6c4eb67"
        assert "summary 模式仅本次运行使用" in message

    def test_auto_sync_falls_back_to_udid(self):
        base_config = {"udid": "emulator-5554"}
        with patch(
            "mobile.prompt_runner._list_connected_device_ids",
            return_value=["emulator-5554"],
        ):
            updated, message = _auto_sync_device_config(base_config, "apply")

        assert updated["serial"] == "emulator-5554"
        assert message is not None

    def test_auto_sync_skips_when_multiple_devices_are_ambiguous(self):
        base_config = {"serial": "emulator-5554"}
        with patch(
            "mobile.prompt_runner._list_connected_device_ids",
            return_value=["abc", "def"],
        ):
            updated, message = _auto_sync_device_config(base_config, "apply")

        assert updated == base_config
        assert message is None


# ---------------------------------------------------------------------------
# _prompt_yes_no / _prompt_choice
# ---------------------------------------------------------------------------


class TestPromptYesNo:
    def test_y_returns_true(self):
        with patch("builtins.input", return_value="y"):
            assert _prompt_yes_no("确认?") is True

    def test_yes_returns_true(self):
        with patch("builtins.input", return_value="yes"):
            assert _prompt_yes_no("确认?") is True

    def test_empty_returns_false(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_yes_no("确认?") is False

    def test_n_returns_false(self):
        with patch("builtins.input", return_value="n"):
            assert _prompt_yes_no("确认?") is False


class TestPromptChoice:
    def test_valid_choice_returned(self):
        with patch("builtins.input", return_value="1"):
            result = _prompt_choice("选择:", ["[0] 选项A", "[1] 选项B"])
        assert result == "1"

    def test_empty_input_returns_none(self):
        with patch("builtins.input", return_value=""):
            result = _prompt_choice("选择:", ["[0] 选项A"])
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_confirmed_price — invalid index raises ValueError
# ---------------------------------------------------------------------------


def test_resolve_confirmed_price_invalid_index_raises():
    intent = PromptIntent(raw_prompt="test")
    options = [
        {"index": 5, "text": "380元", "tag": "可预约"},
        {"index": 7, "text": "580元", "tag": "可预约"},
    ]
    summary = {"price_options": options}
    # User inputs index 99 which doesn't match any option["index"]
    with patch("mobile.prompt_runner._prompt_choice", return_value="99"):
        with pytest.raises(ValueError, match="无效的 price_index"):
            _resolve_confirmed_price(intent, summary, None, assume_yes=False)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _make_discovery(title="张杰演唱会", venue="北京市·国家体育场"):
    return {
        "used_keyword": "张杰 演唱会",
        "search_results": [],
        "summary": {
            "title": title,
            "venue": venue,
            "state": "detail_page",
            "reservation_mode": False,
            "dates": ["04.06"],
            "price_options": [{"index": 0, "text": "580元", "tag": "可预约"}],
        },
        "page_probe": {"state": "detail_page"},
    }


class TestMain:
    def _patch_main(
        self,
        *,
        discovery=None,
        chosen_price=None,
        mode="summary",
        prompt="帮张志涛抢4月6日张杰演唱会 580元",
        yes=False,
        extra_patches=None,
    ):
        """Helper to set up common mocks for main() tests."""
        if discovery is None:
            discovery = _make_discovery()
        patches = {
            "mobile.prompt_runner._config_path": Mock(
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc")
            ),
            "mobile.prompt_runner.load_config_dict": Mock(return_value={}),
            "mobile.prompt_runner.Config.load_config": Mock(),
            "mobile.prompt_runner.DamaiBot": Mock(),
            "mobile.prompt_runner.save_config_dict": Mock(),
        }
        if extra_patches:
            patches.update(extra_patches)
        return patches, discovery

    def test_summary_mode_returns_0(self):
        from mobile.prompt_runner import main

        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot.probe_current_page.return_value = {"state": "detail_page"}
        discovery = _make_discovery()
        mock_bot.discover_target_event.return_value = discovery
        mock_bot.inspect_current_target_event.return_value = discovery["summary"]

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch("mobile.prompt_runner.Config.load_config") as mock_cfg, \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot):
            mock_cfg.return_value = Mock(
                to_dict=lambda: {
                    "serial": "ABC",
                    "app_package": "cn.damai",
                    "app_activity": ".SplashMainActivity",
                    "keyword": "张杰 演唱会",
                    "users": ["张志涛"],
                    "city": "北京",
                    "date": "04.06",
                    "price": "580元",
                    "price_index": 0,
                    "if_commit_order": False,
                    "probe_only": True,
                    "auto_navigate": True,
                },
                city="北京",
                date="04.06",
                price="580元",
                price_index=0,
            )
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])
        assert result == 0

    def test_discovery_failure_returns_1_with_friendly_error(self):
        from mobile.prompt_runner import main

        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot.probe_current_page.return_value = {"state": "detail_page"}
        mock_bot.discover_target_event.return_value = None  # discovery failed

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch("mobile.prompt_runner.Config.load_config") as mock_cfg, \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner.logger") as mock_logger:
            mock_cfg.return_value = Mock(
                to_dict=lambda: {
                    "serial": "ABC",
                    "app_package": "cn.damai",
                    "app_activity": ".SplashMainActivity",
                    "keyword": "张杰 演唱会",
                    "users": ["张志涛"],
                    "city": "北京",
                    "date": "04.06",
                    "price": "580元",
                    "price_index": 0,
                    "if_commit_order": False,
                    "probe_only": True,
                    "auto_navigate": True,
                },
                city="北京",
                date="04.06",
                price="580元",
                price_index=0,
            )
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "未能根据提示词打开目标演出" in mock_logger.error.call_args[0][0]

    def test_discovery_failure_prints_candidates(self):
        # issue #51+#50：discover 失败且 bot 暴露候选列表时，错误文案列出
        # top-5 候选并引导用户补全标题；exit code 仍为 1，绝不自动点击候选
        from mobile.prompt_runner import main

        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot.probe_current_page.return_value = {"state": "search_page"}
        mock_bot.discover_target_event.return_value = None
        mock_bot._last_failed_candidates = [
            {
                "title": "凤凰传奇「吉祥如意」2026巡演·广州站",
                "city": "广州",
                "venue": "广州体育馆",
                "time": "2026.07.18",
                "score": 64,
                "used_keyword": "凤凰传奇 演唱会",
            },
            {
                "title": "五月天2026巡回演唱会广州站",
                "city": "广州",
                "venue": "宝能观致文化中心",
                "time": "2026.08.01",
                "score": 40,
                "used_keyword": "凤凰传奇",
            },
        ]

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch("mobile.prompt_runner.Config.load_config") as mock_cfg, \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner.logger") as mock_logger:
            mock_cfg.return_value = Mock(
                to_dict=lambda: {
                    "serial": "ABC",
                    "app_package": "cn.damai",
                    "app_activity": ".SplashMainActivity",
                    "keyword": "凤凰传奇 演唱会",
                    "users": ["张志涛"],
                    "city": "广州",
                    "date": "07.18",
                    "price": "580元",
                    "price_index": 0,
                    "if_commit_order": False,
                    "probe_only": True,
                    "auto_navigate": True,
                },
                city="广州",
                date="07.18",
                price="580元",
                price_index=0,
            )
            result = main(
                ["帮张志涛抢广州的凤凰传奇演唱会门票", "--mode", "summary"]
            )

        assert result == 1
        mock_logger.error.assert_called_once()
        message = mock_logger.error.call_args[0][0]
        assert "未能自动确认目标演出" in message
        assert "凤凰传奇「吉祥如意」2026巡演·广州站" in message
        assert "五月天2026巡回演唱会广州站" in message
        assert "把完整演出标题原文写进提示词" in message

    def test_discovery_failure_with_non_list_candidates_keeps_old_message(self):
        # 防御回归：_last_failed_candidates 为 MagicMock（非 list，旧版 bot /
        # 全 Mock 场景）时不崩溃，输出原有错误文案
        from mobile.prompt_runner import _build_discovery_failure_message

        intent = Mock(candidate_keywords=["张杰 演唱会", "张杰"])
        bot = Mock()  # _last_failed_candidates 自动成为 Mock（非 list）
        assert (
            _build_discovery_failure_message(bot, intent)
            == "未能根据提示词打开目标演出"
        )

        # list 里混入非 dict 项同样兜底回原文案
        bot_bad_items = Mock()
        bot_bad_items._last_failed_candidates = ["不是字典"]
        assert (
            _build_discovery_failure_message(bot_bad_items, intent)
            == "未能根据提示词打开目标演出"
        )

    def _make_full_mock_bot(self, discovery=None):
        """Helper: build a fully-mocked bot for non-summary mode tests."""
        if discovery is None:
            discovery = _make_discovery()
        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot.probe_current_page.return_value = {"state": "detail_page"}
        mock_bot.discover_target_event.return_value = discovery
        mock_bot.inspect_current_target_event.return_value = discovery["summary"]
        mock_bot.run_with_retry.return_value = True
        return mock_bot

    def _base_config_mock(self):
        return Mock(
            to_dict=lambda: {
                "server_url": "http://127.0.0.1:4723",
                "device_name": "Android",
                "udid": "ABC",
                "platform_version": "16",
                "app_package": "cn.damai",
                "app_activity": ".SplashMainActivity",
                "keyword": "张杰 演唱会",
                "target_title": "张杰演唱会",
                "target_venue": "国家体育场",
                "users": ["张志涛"],
                "city": "北京",
                "date": "04.06",
                "price": "580元",
                "price_index": 0,
                "if_commit_order": False,
                "probe_only": True,
                "auto_navigate": True,
            },
            keyword="张杰 演唱会",
            target_title="张杰演唱会",
            target_venue="国家体育场",
            city="北京",
            date="04.06",
            price="580元",
            price_index=0,
        )

    # U-05「先构造后写盘」要求 build_updated_config 的返回值能真实构造 Config，
    # 不能再用 {} 占位（Config(**{}) 会 TypeError 被兜底捕获返回 1）
    _FULL_UPDATED_CONFIG = {
        "serial": "ABC",
        "keyword": "张杰 演唱会",
        "target_title": "张杰演唱会",
        "target_venue": "国家体育场",
        "users": ["张志涛"],
        "city": "北京",
        "date": "04.06",
        "price": "580元",
        "price_index": 0,
        "if_commit_order": False,
        "probe_only": True,
        "auto_navigate": True,
        "rush_mode": True,
        "fast_retry_interval_ms": 80,
    }

    def test_apply_mode_saves_config_no_execute(self):
        """apply mode: saves config and returns 0 (execute=False)."""
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner.update_config_values") as mock_save, \
            patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
            patch(
                "mobile.prompt_runner._resolve_confirmed_price",
                return_value={"index": 0, "text": "580元", "tag": "可预约"},
            ), \
            patch(
                "mobile.prompt_runner.build_updated_config",
                return_value=dict(self._FULL_UPDATED_CONFIG),
            ), \
            patch("mobile.prompt_runner._prompt_yes_no", return_value=True):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 0
        mock_save.assert_called_once()

    def test_probe_mode_is_observation_only_and_does_not_write(self):
        """Prompt probe uses the existing target without navigation or writes."""
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        page_probe = {
            "state": "detail_page",
            "observed_title": "张杰演唱会",
            "purchase_button": True,
            "price_container": True,
        }
        mock_bot.probe_current_page.return_value = page_probe

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch(
                "mobile.prompt_runner.DamaiBot",
                return_value=mock_bot,
            ) as mock_bot_cls, \
            patch("mobile.prompt_runner.update_config_values") as mock_save:
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "probe", "-y"])

        assert result == 0
        mock_save.assert_not_called()
        mock_bot.dismiss_startup_popups.assert_not_called()
        mock_bot.discover_target_event.assert_not_called()
        mock_bot.inspect_current_target_event.assert_not_called()
        mock_bot.run_with_retry.assert_called_once_with(
            max_retries=1,
            initial_page_probe=page_probe,
        )
        effective_config = mock_bot_cls.call_args.kwargs["config"]
        assert effective_config.probe_only is True
        assert effective_config.if_commit_order is False
        assert effective_config.auto_navigate is False

    def test_probe_mode_off_target_fails_without_any_navigation(self):
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        mock_bot.probe_current_page.return_value = {
            "state": "homepage",
            "observed_title": "",
        }

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner.update_config_values") as mock_save:
            result = main(
                [
                    "帮张志涛抢4月6日张杰演唱会 580元",
                    "--mode",
                    "probe",
                    "-y",
                ]
            )

        assert result == 1
        mock_save.assert_not_called()
        mock_bot.dismiss_startup_popups.assert_not_called()
        mock_bot.discover_target_event.assert_not_called()
        mock_bot.inspect_current_target_event.assert_not_called()
        mock_bot.run_with_retry.assert_not_called()

    def test_no_date_cancels_flow(self):
        """If date cannot be confirmed, returns 1."""
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner._resolve_confirmed_date", return_value=None):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 1

    def test_no_price_cancels_flow(self):
        """If price cannot be confirmed, returns 1."""
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
            patch("mobile.prompt_runner._resolve_confirmed_price", return_value=None):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 1

    def test_user_declines_write_returns_1(self):
        """If user says no to overwrite prompt, returns 1."""
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
            patch(
                "mobile.prompt_runner._resolve_confirmed_price",
                return_value={"index": 0, "text": "580元", "tag": "可预约"},
            ), \
            patch("mobile.prompt_runner.build_updated_config", return_value={}), \
            patch("mobile.prompt_runner._prompt_yes_no", return_value=False):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply"])
        assert result == 1

    def test_apply_mode_requires_attendee_name(self):
        from mobile.prompt_runner import main

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch(
                "mobile.prompt_runner.load_config_dict",
                return_value={"users": ["张三", "李四"]},
            ), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
            patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮我抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "提示词有问题，已停止执行" in mock_logger.error.call_args[0][0]
        assert "缺少关键信息：观演人姓名" in mock_logger.error.call_args[0][0]
        assert (
            "./mobile/scripts/run_from_prompt.sh --mode apply --yes"
            in mock_logger.error.call_args[0][0]
        )
        assert (
            "帮<观演人姓名列表>抢，4 月 6 号，张杰的演唱会门票"
            in mock_logger.error.call_args[0][0]
        )
        assert (
            "如果你就是给当前配置里的 2 位观演人买票"
            in mock_logger.error.call_args[0][0]
        )

    def test_summary_mode_requires_attendee_name(self):
        from mobile.prompt_runner import main

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch(
                "mobile.prompt_runner.load_config_dict",
                return_value={"users": ["张三", "李四"]},
            ), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
            patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮我抢4月6日张杰演唱会 580元", "--mode", "summary", "-y"])

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "不会继续搜索、连接设备或写配置" in mock_logger.error.call_args[0][0]
        assert (
            "./mobile/scripts/run_from_prompt.sh --mode summary --yes"
            in mock_logger.error.call_args[0][0]
        )
        assert (
            "帮<观演人姓名列表>抢，4 月 6 号，张杰的演唱会门票"
            in mock_logger.error.call_args[0][0]
        )

    def test_summary_mode_requires_search_keyword(self):
        from mobile.prompt_runner import main

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch(
                "mobile.prompt_runner.load_config_dict",
                return_value={"users": ["张三", "李四"]},
            ), \
            patch(
                "mobile.prompt_runner.parse_prompt",
                side_effect=ValueError("无法从提示词中提取搜索关键词"),
            ), \
            patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["12313", "--mode", "summary", "-y"])

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "缺少关键信息：演出名称或搜索关键词" in mock_logger.error.call_args[0][0]
        assert "帮<观演人姓名列表>抢，<日期>" in mock_logger.error.call_args[0][0]
        assert (
            "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元"
            in mock_logger.error.call_args[0][0]
        )

    def test_summary_mode_stops_when_attendee_count_mismatches_quantity(self):
        from mobile.prompt_runner import main

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch(
                "mobile.prompt_runner.load_config_dict",
                return_value={"users": ["张三", "李四"]},
            ), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
            patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(
                [
                    "帮张文和张志涛抢一张 4月6日张杰演唱会 580元",
                    "--mode",
                    "summary",
                    "-y",
                ]
            )

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "观演人数量与购票张数不一致" in mock_logger.error.call_args[0][0]
        assert (
            "请直接复制下面任意一条正确命令重新执行"
            in mock_logger.error.call_args[0][0]
        )
        assert "给这 2 位观演人都买票" in mock_logger.error.call_args[0][0]
        assert (
            "./mobile/scripts/run_from_prompt.sh --mode summary --yes"
            in mock_logger.error.call_args[0][0]
        )

    def test_summary_mode_accepts_multiple_attendees_without_explicit_quantity(self):
        from mobile.prompt_runner import main

        mock_bot = self._make_full_mock_bot()
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
            patch("builtins.print") as mock_print:
            result = main(
                [
                    "帮张文、张志涛抢，6月6号，陈慧娴的演唱会门票，上海站，内场，票价 1380 元",
                    "--mode",
                    "summary",
                    "-y",
                ]
            )

        assert result == 0
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args)
            for call in mock_print.call_args_list
        )
        assert "规范提示词:" in printed
        assert (
            "帮张文、张志涛抢，6 月 6 号，陈慧娴的演唱会门票，内场，票价 1380 元"
            in printed
        )

    def test_summary_mode_logs_driver_start_failure_cleanly(self):
        from mobile.prompt_runner import main

        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch(
                "mobile.prompt_runner.DamaiBot",
                side_effect=ConnectError(
                    "Device emulator-5554 was not in the list of connected devices"
                ),
            ), \
            patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(
                ["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary", "-y"]
            )

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "启动 u2 会话失败" in mock_logger.error.call_args[0][0]

    def test_bot_driver_quit_called_in_finally(self):
        """driver.quit() is called in finally block."""
        from mobile.prompt_runner import main

        mock_driver = Mock()
        mock_bot = self._make_full_mock_bot()
        mock_bot.driver = mock_driver
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])
        mock_driver.quit.assert_called_once()
        assert result == 0

    def test_bot_driver_quit_exception_swallowed(self):
        """Exception in driver.quit() in finally block is silently ignored."""
        from mobile.prompt_runner import main

        mock_driver = Mock()
        mock_driver.quit.side_effect = Exception("quit failed")
        mock_bot = self._make_full_mock_bot()
        mock_bot.driver = mock_driver
        with \
            patch(
                "mobile.prompt_runner._config_path",
                return_value=Mock(__str__=lambda s: "/mock/config.jsonc"),
            ), \
            patch("mobile.prompt_runner.load_config_dict", return_value={}), \
            patch(
                "mobile.prompt_runner.Config.load_config",
                return_value=self._base_config_mock(),
            ), \
            patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])
        assert result == 0  # no exception propagated

    def test_config_path_defaults_to_shared_config(self, tmp_path, monkeypatch):
        """_config_path() defaults to config.jsonc even when config.local.jsonc exists."""
        from mobile.prompt_runner import _config_path

        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        (mobile_dir / "config.local.jsonc").write_text("{}")
        (mobile_dir / "config.jsonc").write_text("{}")
        with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
            path = _config_path()
        assert path.name == "config.jsonc"

    def test_config_path_uses_env_override_when_set(self, tmp_path, monkeypatch):
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        local_cfg = mobile_dir / "config.local.jsonc"
        local_cfg.write_text("{}")
        monkeypatch.setenv("HATICKETS_CONFIG_PATH", str(local_cfg))

        path = _config_path()

        assert path == local_cfg

    def test_config_path_uses_explicit_argument(self):
        path = _config_path("/tmp/custom.jsonc")
        assert path == Path("/tmp/custom.jsonc")


class TestConfigBootstrap:
    def test_load_base_config_dict_bootstraps_default_config_from_template(
        self, tmp_path
    ):
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        config_path = mobile_dir / "config.jsonc"
        template_path = mobile_dir / "config.example.jsonc"
        template_path.write_text(
            '{"keyword":"张杰","users":["张三"],"city":"北京","date":"04.06","price":"580元","price_index":0,"if_commit_order":false}',
            encoding="utf-8",
        )

        with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
            config_dict = _load_base_config_dict(config_path)

        assert config_path.exists()
        assert config_dict["keyword"] == "张杰"
        assert config_dict["users"] == ["张三"]

    def test_bootstrap_only_for_default_config_filename(self, tmp_path):
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        custom_path = mobile_dir / "config.local.jsonc"
        (mobile_dir / "config.example.jsonc").write_text("{}", encoding="utf-8")

        with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                _load_base_config_dict(custom_path)

    def test_bootstrap_requires_template(self, tmp_path):
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        config_path = mobile_dir / "config.jsonc"

        with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="配置模板未找到"):
                _bootstrap_default_config_if_missing(config_path)


def test_main_summary_mode_output_format(tmp_path, capsys):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    discovery = _make_massiwei_discovery()
    summary = _make_massiwei_summary()
    discovery["summary"] = summary
    bot.discover_target_event.return_value = discovery
    bot.inspect_current_target_event.return_value = summary

    with \
        patch("mobile.prompt_runner._config_path", return_value=config_path), \
        patch(
            "mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()
        ), \
        patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
        patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
        patch(
            "mobile.prompt_runner.choose_price_option",
            return_value=summary["price_options"][0],
        ):
        assert (
            prompt_runner.main(["帮张志涛买一张马思唯的上海 4 月 4 日的看台票 899"])
            == 0
        )

    output = capsys.readouterr().out
    assert "匹配结果:" in output
    assert "推荐票档: [5] 看台 899元 [可选]" in output
    assert "执行结果：成功" in output
    bot.dismiss_startup_popups.assert_called_once()
    bot.driver.quit.assert_called_once()
