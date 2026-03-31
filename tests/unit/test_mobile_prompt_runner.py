"""Unit tests for mobile/prompt_runner.py."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from selenium.common.exceptions import WebDriverException

from mobile.config import Config
from mobile.prompt_parser import PromptIntent, parse_prompt
from mobile import prompt_runner
from mobile.prompt_runner import (
    _auto_sync_device_config,
    _config_path,
    _format_price_option,
    _format_summary,
    _prompt_choice,
    _prompt_yes_no,
    _repo_root,
    _resolve_confirmed_date,
    _resolve_confirmed_price,
    _success_detail_for_mode,
    _split_city_and_venue,
    build_updated_config,
    parse_args,
)


def _make_base_config() -> Config:
    return Config(
        server_url="http://127.0.0.1:4723",
        device_name="Android",
        udid="ABC123",
        platform_version="16",
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

    updated = prompt_runner.build_updated_config(base_config, intent, discovery, "04.06", selected_price, "probe")

    assert updated["item_url"] is None
    assert updated["item_id"] is None
    assert updated["keyword"] == "张杰 演唱会"
    assert updated["target_title"] == "【北京】2026张杰未·LIVE—「开往1982」演唱会-北京站"
    assert updated["target_venue"] == "国家体育场-鸟巢"
    assert updated["users"] == ["张志涛"]
    assert updated["city"] == "北京"
    assert updated["date"] == "04.06"
    assert updated["price"] == "1280元"
    assert updated["price_index"] == 6
    assert updated["probe_only"] is True
    assert updated["if_commit_order"] is False
    assert updated["auto_navigate"] is True


def test_build_updated_config_drops_stale_target_when_summary_is_unknown():
    base_config = _make_base_config().to_dict()
    base_config.update({
        "target_title": "旧演出标题",
        "target_venue": "旧场馆",
        "keyword": "旧关键词",
        "city": "上海",
        "date": "04.04",
        "price": "899元",
        "price_index": 5,
        "auto_navigate": True,
    })
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

    updated = prompt_runner.build_updated_config(base_config, intent, discovery, "04.04", selected_price, "apply")

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
        result = _resolve_confirmed_date(intent, self._summary(["04.06", "04.07"]), assume_yes=True)
        assert result == "04.06"

    def test_no_visible_dates_returns_intent_date(self):
        intent = PromptIntent(raw_prompt="test", date="04.06")
        result = _resolve_confirmed_date(intent, self._summary([]), assume_yes=True)
        assert result == "04.06"

    def test_single_date_returns_it(self):
        intent = PromptIntent(raw_prompt="test", date=None)
        result = _resolve_confirmed_date(intent, self._summary(["04.06"]), assume_yes=True)
        assert result == "04.06"

    def test_no_dates_no_intent_returns_none(self):
        intent = PromptIntent(raw_prompt="test", date=None)
        result = _resolve_confirmed_date(intent, self._summary([]), assume_yes=True)
        assert result is None

    def test_assume_yes_with_ambiguous_raises(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with pytest.raises(ValueError):
            _resolve_confirmed_date(intent, self._summary(["04.06", "04.07"]), assume_yes=True)

    def test_interactive_choice(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with patch("mobile.prompt_runner._prompt_choice", return_value="0"):
            result = _resolve_confirmed_date(intent, self._summary(["04.06", "04.07"]), assume_yes=False)
        assert result == "04.06"

    def test_interactive_cancelled(self):
        intent = PromptIntent(raw_prompt="test", date="04.08")
        with patch("mobile.prompt_runner._prompt_choice", return_value=None):
            result = _resolve_confirmed_date(intent, self._summary(["04.06", "04.07"]), assume_yes=False)
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
        result = _resolve_confirmed_price(intent, self._summary(), chosen, assume_yes=True)
        assert result is chosen

    def test_single_available_returned(self):
        intent = PromptIntent(raw_prompt="test")
        options = [{"index": 0, "text": "580元", "tag": "可预约"}]
        result = _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=True)
        assert result["index"] == 0

    def test_no_available_returns_none(self):
        intent = PromptIntent(raw_prompt="test")
        options = [{"index": 0, "text": "580元", "tag": "售罄"}]
        result = _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=True)
        assert result is None

    def test_assume_yes_multiple_available_raises(self):
        intent = PromptIntent(raw_prompt="test")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        with pytest.raises(ValueError):
            _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=True)

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
            _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=True)

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
            result = _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=False)
        assert result["index"] == 0

    def test_interactive_cancelled(self):
        intent = PromptIntent(raw_prompt="test")
        options = [
            {"index": 0, "text": "380元", "tag": "可预约"},
            {"index": 1, "text": "580元", "tag": "可预约"},
        ]
        with patch("mobile.prompt_runner._prompt_choice", return_value=None):
            result = _resolve_confirmed_price(intent, self._summary(options), None, assume_yes=False)
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
            {"score": 95, "title": "张杰演唱会北京站", "city": "北京", "venue": "鸟巢", "time": "04.06"},
        ]
        result = _format_summary(intent, discovery, None)
        assert "张杰演唱会北京站" in result


class TestSuccessDetailForMode:
    def test_summary_message_no_longer_mentions_confirm(self):
        message = _success_detail_for_mode("summary")
        assert "apply / probe" in message
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


class TestAutoSyncDeviceConfig:
    def test_auto_sync_uses_single_connected_device(self):
        base_config = {"udid": "emulator-5554", "platform_version": "15"}
        with patch("mobile.prompt_runner._list_connected_device_ids", return_value=["c6c4eb67"]), \
             patch("mobile.prompt_runner._read_device_android_version", return_value="16"):
            updated, message = _auto_sync_device_config(base_config, "apply")

        assert updated["udid"] == "c6c4eb67"
        assert updated["platform_version"] == "16"
        assert "写配置时会一并保存" in message

    def test_auto_sync_summary_only_updates_runtime_message(self):
        base_config = {"udid": "emulator-5554", "platform_version": "15"}
        with patch("mobile.prompt_runner._list_connected_device_ids", return_value=["c6c4eb67"]), \
             patch("mobile.prompt_runner._read_device_android_version", return_value="16"):
            updated, message = _auto_sync_device_config(base_config, "summary")

        assert updated["udid"] == "c6c4eb67"
        assert "summary 模式仅本次运行使用" in message

    def test_auto_sync_skips_when_multiple_devices_are_ambiguous(self):
        base_config = {"udid": "emulator-5554", "platform_version": "15"}
        with patch("mobile.prompt_runner._list_connected_device_ids", return_value=["abc", "def"]):
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
    def _patch_main(self, *, discovery=None, chosen_price=None, mode="summary",
                    prompt="帮张志涛抢4月6日张杰演唱会 580元", yes=False, extra_patches=None):
        """Helper to set up common mocks for main() tests."""
        if discovery is None:
            discovery = _make_discovery()
        patches = {
            "mobile.prompt_runner._config_path": Mock(return_value=Mock(__str__=lambda s: "/mock/config.jsonc")),
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

        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config") as mock_cfg, \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot):
            mock_cfg.return_value = Mock(
                to_dict=lambda: {
                    "server_url": "http://127.0.0.1:4723", "device_name": "Android",
                    "udid": "ABC", "platform_version": "16", "app_package": "cn.damai",
                    "app_activity": ".SplashMainActivity", "keyword": "张杰 演唱会",
                    "users": ["张志涛"], "city": "北京", "date": "04.06",
                    "price": "580元", "price_index": 0, "if_commit_order": False,
                    "probe_only": True, "auto_navigate": True,
                },
                city="北京", date="04.06", price="580元", price_index=0,
            )
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])
        assert result == 0

    def test_discovery_failure_returns_1_with_friendly_error(self):
        from mobile.prompt_runner import main
        mock_bot = Mock()
        mock_bot.driver = None
        mock_bot.probe_current_page.return_value = {"state": "detail_page"}
        mock_bot.discover_target_event.return_value = None  # discovery failed

        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config") as mock_cfg, \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner.logger") as mock_logger:
            mock_cfg.return_value = Mock(
                to_dict=lambda: {
                    "server_url": "http://127.0.0.1:4723", "device_name": "Android",
                    "udid": "ABC", "platform_version": "16", "app_package": "cn.damai",
                    "app_activity": ".SplashMainActivity", "keyword": "张杰 演唱会",
                    "users": ["张志涛"], "city": "北京", "date": "04.06",
                    "price": "580元", "price_index": 0, "if_commit_order": False,
                    "probe_only": True, "auto_navigate": True,
                },
                city="北京", date="04.06", price="580元", price_index=0,
            )
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary"])

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "未能根据提示词打开目标演出" in mock_logger.error.call_args[0][0]

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
                "server_url": "http://127.0.0.1:4723", "device_name": "Android",
                "udid": "ABC", "platform_version": "16", "app_package": "cn.damai",
                "app_activity": ".SplashMainActivity", "keyword": "张杰 演唱会",
                "users": ["张志涛"], "city": "北京", "date": "04.06",
                "price": "580元", "price_index": 0, "if_commit_order": False,
                "probe_only": True, "auto_navigate": True,
            },
            city="北京", date="04.06", price="580元", price_index=0,
        )

    def test_apply_mode_saves_config_no_execute(self):
        """apply mode: saves config and returns 0 (execute=False)."""
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner.save_config_dict") as mock_save, \
             patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
             patch("mobile.prompt_runner._resolve_confirmed_price",
                   return_value={"index": 0, "text": "580元", "tag": "可预约"}), \
             patch("mobile.prompt_runner.build_updated_config", return_value={}), \
             patch("mobile.prompt_runner._prompt_yes_no", return_value=True):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 0
        mock_save.assert_called_once()

    def test_probe_mode_runs_bot_after_save(self):
        """probe mode: saves config and then runs bot.run_with_retry."""
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        def _config_factory(**kwargs):
            return Mock(
                to_dict=lambda: kwargs,
                city=kwargs.get("city"),
                date=kwargs.get("date"),
                price=kwargs.get("price"),
                price_index=kwargs.get("price_index"),
            )

        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner.save_config_dict"), \
             patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
             patch("mobile.prompt_runner._resolve_confirmed_price",
                   return_value={"index": 0, "text": "580元", "tag": "可预约"}), \
             patch("mobile.prompt_runner.build_updated_config", return_value={}), \
             patch("mobile.prompt_runner.Config") as mock_config_cls, \
             patch("mobile.prompt_runner._prompt_yes_no", return_value=True):
            mock_config_cls.side_effect = _config_factory
            mock_config_cls.load_config.return_value = self._base_config_mock()
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "probe", "-y"])
        mock_bot.run_with_retry.assert_called_once()
        assert result in (0, 1)

    def test_no_date_cancels_flow(self):
        """If date cannot be confirmed, returns 1."""
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner._resolve_confirmed_date", return_value=None):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 1

    def test_no_price_cancels_flow(self):
        """If price cannot be confirmed, returns 1."""
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
             patch("mobile.prompt_runner._resolve_confirmed_price", return_value=None):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])
        assert result == 1

    def test_user_declines_write_returns_1(self):
        """If user says no to overwrite prompt, returns 1."""
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("mobile.prompt_runner._resolve_confirmed_date", return_value="04.06"), \
             patch("mobile.prompt_runner._resolve_confirmed_price",
                   return_value={"index": 0, "text": "580元", "tag": "可预约"}), \
             patch("mobile.prompt_runner.build_updated_config", return_value={}), \
             patch("mobile.prompt_runner._prompt_yes_no", return_value=False):
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "apply"])
        assert result == 1

    def test_apply_mode_requires_attendee_name(self):
        from mobile.prompt_runner import main
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={"users": ["张三", "李四"]}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
             patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮我抢4月6日张杰演唱会 580元", "--mode", "apply", "-y"])

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "提示词有问题，已停止执行" in mock_logger.error.call_args[0][0]
        assert "缺少关键信息：观演人姓名" in mock_logger.error.call_args[0][0]
        assert "./mobile/scripts/run_from_prompt.sh --mode apply --yes" in mock_logger.error.call_args[0][0]
        assert "帮<观演人姓名列表>抢，4 月 6 号，张杰的演唱会门票" in mock_logger.error.call_args[0][0]
        assert "如果你就是给当前配置里的 2 位观演人买票" in mock_logger.error.call_args[0][0]

    def test_summary_mode_requires_attendee_name(self):
        from mobile.prompt_runner import main
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={"users": ["张三", "李四"]}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
             patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮我抢4月6日张杰演唱会 580元", "--mode", "summary", "-y"])

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "不会继续搜索、连接 Appium 或写配置" in mock_logger.error.call_args[0][0]
        assert "./mobile/scripts/run_from_prompt.sh --mode summary --yes" in mock_logger.error.call_args[0][0]
        assert "帮<观演人姓名列表>抢，4 月 6 号，张杰的演唱会门票" in mock_logger.error.call_args[0][0]

    def test_summary_mode_requires_search_keyword(self):
        from mobile.prompt_runner import main
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={"users": ["张三", "李四"]}), \
             patch("mobile.prompt_runner.parse_prompt", side_effect=ValueError("无法从提示词中提取搜索关键词")), \
             patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["12313", "--mode", "summary", "-y"])

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "缺少关键信息：演出名称或搜索关键词" in mock_logger.error.call_args[0][0]
        assert "帮<观演人姓名列表>抢，<日期>" in mock_logger.error.call_args[0][0]
        assert "给张三和李四抢4 月 6 号张杰的北京站演唱会内场门票，票价 1680 元" in mock_logger.error.call_args[0][0]

    def test_summary_mode_stops_when_attendee_count_mismatches_quantity(self):
        from mobile.prompt_runner import main
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={"users": ["张三", "李四"]}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot") as mock_bot_cls, \
             patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮张文和张志涛抢一张 4月6日张杰演唱会 580元", "--mode", "summary", "-y"])

        assert result == 1
        mock_bot_cls.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "观演人数量与购票张数不一致" in mock_logger.error.call_args[0][0]
        assert "请直接复制下面任意一条正确命令重新执行" in mock_logger.error.call_args[0][0]
        assert "给这 2 位观演人都买票" in mock_logger.error.call_args[0][0]
        assert "./mobile/scripts/run_from_prompt.sh --mode summary --yes" in mock_logger.error.call_args[0][0]

    def test_summary_mode_accepts_multiple_attendees_without_explicit_quantity(self):
        from mobile.prompt_runner import main
        mock_bot = self._make_full_mock_bot()
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", return_value=mock_bot), \
             patch("builtins.print") as mock_print:
            result = main(["帮张文、张志涛抢，6月6号，陈慧娴的演唱会门票，上海站，内场，票价 1380 元", "--mode", "summary", "-y"])

        assert result == 0
        printed = "\n".join(" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list)
        assert "规范提示词:" in printed
        assert "帮张文、张志涛抢，6 月 6 号，陈慧娴的演唱会门票，内场，票价 1380 元" in printed

    def test_summary_mode_logs_driver_start_failure_cleanly(self):
        from mobile.prompt_runner import main
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
             patch("mobile.prompt_runner.DamaiBot", side_effect=WebDriverException("Device emulator-5554 was not in the list of connected devices")), \
             patch("mobile.prompt_runner.logger") as mock_logger:
            result = main(["帮张志涛抢4月6日张杰演唱会 580元", "--mode", "summary", "-y"])

        assert result == 1
        mock_logger.error.assert_called_once()
        assert "启动 Appium 会话失败" in mock_logger.error.call_args[0][0]

    def test_bot_driver_quit_called_in_finally(self):
        """driver.quit() is called in finally block."""
        from mobile.prompt_runner import main
        mock_driver = Mock()
        mock_bot = self._make_full_mock_bot()
        mock_bot.driver = mock_driver
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
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
        with patch("mobile.prompt_runner._config_path", return_value=Mock(__str__=lambda s: "/mock/config.jsonc")), \
             patch("mobile.prompt_runner.load_config_dict", return_value={}), \
             patch("mobile.prompt_runner.Config.load_config", return_value=self._base_config_mock()), \
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

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=summary["price_options"][0]):
        assert prompt_runner.main(["帮张志涛买一张马思唯的上海 4 月 4 日的看台票 899"]) == 0

    output = capsys.readouterr().out
    assert "匹配结果:" in output
    assert "推荐票档: [5] 看台 899元 [可选]" in output
    assert "执行结果：成功" in output
    bot.dismiss_startup_popups.assert_called_once()
    bot.driver.quit.assert_called_once()
