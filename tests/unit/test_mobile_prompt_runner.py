"""Unit tests for mobile/prompt_runner.py."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from mobile.config import Config
from mobile.prompt_parser import parse_prompt
from mobile import prompt_runner


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


def _make_discovery():
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


def _make_summary():
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


def test_config_path_prefers_local_file(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")
    (mobile_dir / "config.local.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
        assert prompt_runner._config_path() == mobile_dir / "config.local.jsonc"


def test_config_path_falls_back_to_shared_config(tmp_path):
    mobile_dir = tmp_path / "mobile"
    mobile_dir.mkdir()
    (mobile_dir / "config.jsonc").write_text("{}", encoding="utf-8")

    with patch("mobile.prompt_runner._repo_root", return_value=tmp_path):
        assert prompt_runner._config_path() == mobile_dir / "config.jsonc"


def test_split_city_and_venue_handles_separator():
    assert prompt_runner._split_city_and_venue("上海市 · 浦发银行东方体育中心") == (
        "上海",
        "浦发银行东方体育中心",
    )


def test_split_city_and_venue_returns_raw_text_without_separator():
    assert prompt_runner._split_city_and_venue("国家体育场-鸟巢") == (None, "国家体育场-鸟巢")


def test_format_price_option_adds_tag_and_ocr_marker():
    option = {"index": 6, "text": "1280元", "tag": "可预约", "source": "ocr"}
    assert prompt_runner._format_price_option(option) == "[6] 1280元 [可预约] (OCR)"


def test_format_summary_includes_candidates_and_recommendation():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    discovery = _make_discovery()
    discovery["summary"] = _make_summary()

    text = prompt_runner._format_summary(intent, discovery, discovery["summary"]["price_options"][0])

    assert "提示词: 帮我买一张马思唯的上海 4 月 4 日的看台票 899" in text
    assert "- 可见场次: 04.04, 04.05" in text
    assert "[6] 内场 1299元 [可选] (OCR)" in text
    assert "- 搜索候选:" in text
    assert "- 推荐票档: [5] 看台 899元 [可选]" in text


def test_prompt_yes_no_and_choice(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert prompt_runner._prompt_yes_no("确认继续") is True

    replies = iter(["6"])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    result = prompt_runner._prompt_choice("请选择：", ["[5] 899元", "[6] 1299元"])

    output = capsys.readouterr().out
    assert "请选择：" in output
    assert result == "6"


def test_resolve_confirmed_date_prefers_prompt_date():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    summary = {"dates": ["04.04", "04.05"]}
    assert prompt_runner._resolve_confirmed_date(intent, summary, assume_yes=True) == "04.04"


def test_resolve_confirmed_date_uses_single_visible_date():
    intent = parse_prompt("帮我买一张马思唯的上海看台票 899")
    summary = {"dates": ["04.04"]}
    assert prompt_runner._resolve_confirmed_date(intent, summary, assume_yes=True) == "04.04"


def test_resolve_confirmed_date_raises_in_yes_mode_on_mismatch():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    summary = {"dates": ["04.05", "04.06"]}

    with pytest.raises(ValueError, match="日期"):
        prompt_runner._resolve_confirmed_date(intent, summary, assume_yes=True)


def test_resolve_confirmed_date_interactive_choice():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    summary = {"dates": ["04.05", "04.06"]}

    with patch("mobile.prompt_runner._prompt_choice", return_value="1"):
        assert prompt_runner._resolve_confirmed_date(intent, summary, assume_yes=False) == "04.06"


def test_resolve_confirmed_price_prefers_chosen_option():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票 899")
    chosen = {"index": 5, "text": "看台 899元", "tag": "可选"}
    summary = {"price_options": [chosen]}

    assert prompt_runner._resolve_confirmed_price(intent, summary, chosen, assume_yes=True) == chosen


def test_resolve_confirmed_price_uses_single_available_option():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的看台票")
    summary = {"price_options": [{"index": 5, "text": "看台 899元", "tag": "可选"}]}

    assert prompt_runner._resolve_confirmed_price(intent, summary, None, assume_yes=True)["index"] == 5


def test_resolve_confirmed_price_raises_in_yes_mode_when_ambiguous():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
    summary = {
        "price_options": [
            {"index": 5, "text": "看台 899元", "tag": "可选"},
            {"index": 6, "text": "内场 1299元", "tag": "可选"},
        ]
    }

    with pytest.raises(ValueError, match="票档偏好"):
        prompt_runner._resolve_confirmed_price(intent, summary, None, assume_yes=True)


def test_resolve_confirmed_price_interactive_choice():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
    summary = {
        "price_options": [
            {"index": 5, "text": "看台 899元", "tag": "可选"},
            {"index": 6, "text": "内场 1299元", "tag": "可选"},
        ]
    }

    with patch("mobile.prompt_runner._prompt_choice", return_value="6"):
        selected = prompt_runner._resolve_confirmed_price(intent, summary, None, assume_yes=False)

    assert selected["index"] == 6


def test_resolve_confirmed_price_rejects_unknown_index():
    intent = parse_prompt("帮我买一张马思唯的上海 4 月 4 日的票")
    summary = {
        "price_options": [
            {"index": 5, "text": "看台 899元", "tag": "可选"},
            {"index": 6, "text": "内场 1299元", "tag": "可选"},
        ]
    }

    with patch("mobile.prompt_runner._prompt_choice", return_value="7"), \
         pytest.raises(ValueError, match="无效的 price_index"):
        prompt_runner._resolve_confirmed_price(intent, summary, None, assume_yes=False)


def test_build_updated_config_for_probe_mode():
    base_config = _make_base_config().to_dict()
    intent = parse_prompt("帮我抢一张 4 月 6 号张杰的演唱会门票，1280元")
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


def test_parse_args_defaults_to_summary_mode():
    args = prompt_runner.parse_args(["帮我买票"])

    assert args.prompt == "帮我买票"
    assert args.mode == "summary"
    assert args.yes is False


def test_main_summary_mode_prints_summary_and_quits_driver(tmp_path, capsys):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=_make_summary()["price_options"][0]):
        assert prompt_runner.main(["帮我买一张马思唯的上海 4 月 4 日的看台票 899"]) == 0

    output = capsys.readouterr().out
    assert "匹配结果:" in output
    assert "推荐票档: [5] 看台 899元 [可选]" in output
    bot.dismiss_startup_popups.assert_called_once()
    bot.driver.quit.assert_called_once()


def test_main_apply_mode_writes_config_without_execution(tmp_path, capsys):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()
    selected_price = _make_summary()["price_options"][0]

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=selected_price), \
         patch("mobile.prompt_runner.save_config_dict") as save_config_dict:
        assert prompt_runner.main(
            ["帮我买一张马思唯的上海 4 月 4 日的看台票 899", "--mode", "apply", "--yes"]
        ) == 0

    saved_config = save_config_dict.call_args.args[0]
    assert saved_config["price"] == "看台 899元"
    assert saved_config["price_index"] == 5
    assert saved_config["probe_only"] is True
    assert saved_config["auto_navigate"] is True
    assert save_config_dict.call_args.args[1] == str(config_path)
    bot.run_with_retry.assert_not_called()
    assert f"已更新配置: {config_path}" in capsys.readouterr().out


def test_main_confirm_mode_executes_retry(tmp_path):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()
    bot.run_with_retry.return_value = True
    selected_price = _make_summary()["price_options"][0]

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=selected_price), \
         patch("mobile.prompt_runner.save_config_dict"):
        assert prompt_runner.main(
            ["帮我买一张马思唯的上海 4 月 4 日的看台票 899", "--mode", "confirm", "--yes"]
        ) == 0

    assert isinstance(bot.config, Config)
    assert bot.config.if_commit_order is False
    assert bot.item_detail is None
    bot.run_with_retry.assert_called_once_with(max_retries=1)


def test_main_returns_one_when_user_cancels_write(tmp_path):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()
    selected_price = _make_summary()["price_options"][0]

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=selected_price), \
         patch("mobile.prompt_runner._prompt_yes_no", return_value=False), \
         patch("mobile.prompt_runner.save_config_dict") as save_config_dict:
        assert prompt_runner.main(["帮我买一张马思唯的上海 4 月 4 日的看台票 899", "--mode", "apply"]) == 1

    save_config_dict.assert_not_called()


def test_main_returns_one_when_date_not_confirmed(tmp_path):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=_make_summary()["price_options"][0]), \
         patch("mobile.prompt_runner._resolve_confirmed_date", return_value=None):
        assert prompt_runner.main(["帮我买一张马思唯的上海 4 月 4 日的看台票 899", "--mode", "apply"]) == 1


def test_main_returns_one_when_price_not_confirmed(tmp_path):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = _make_discovery()
    bot.inspect_current_target_event.return_value = _make_summary()

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         patch("mobile.prompt_runner.choose_price_option", return_value=None), \
         patch("mobile.prompt_runner._resolve_confirmed_price", return_value=None):
        assert prompt_runner.main(["帮我买一张马思唯的上海 4 月 4 日的看台票 899", "--mode", "apply"]) == 1


def test_main_raises_when_no_event_is_discovered(tmp_path):
    config_path = tmp_path / "config.local.jsonc"
    base_config = _make_base_config()
    bot = Mock()
    bot.driver = Mock()
    bot.discover_target_event.return_value = None

    with patch("mobile.prompt_runner._config_path", return_value=config_path), \
         patch("mobile.prompt_runner.load_config_dict", return_value=base_config.to_dict()), \
         patch("mobile.prompt_runner.Config.load_config", return_value=base_config), \
         patch("mobile.prompt_runner.DamaiBot", return_value=bot), \
         pytest.raises(RuntimeError, match="未能根据提示词打开目标演出"):
        prompt_runner.main(["帮我买一张马思唯的上海 4 月 4 日的看台票 899"])

    bot.driver.quit.assert_called_once()
