# -*- coding: UTF-8 -*-
"""fix-plan #26 Step 4：``--non-interactive`` 标志 + ``is_actionable=False`` fallback。

放在独立文件，避免与 ``test_mobile_prompt_runner.py`` 中的 ``with \\`` 块互相影响。
"""

from __future__ import annotations


from mobile.prompt_parser import ParseResult, PromptIntent
from mobile.prompt_runner import (
    UNACTIONABLE_EXIT_CODE,
    _handle_unactionable_apply,
    _is_stdin_tty,
    _update_parse_result_post_discovery,
    parse_args,
)


def _unactionable_parse_result() -> ParseResult:
    intent = PromptIntent(raw_prompt="测试", search_keyword="x")
    return ParseResult(
        intent=intent,
        matched_item=None,  # → is_actionable=False
        diagnostics=["缺失：观演人姓名", "缺失：明确日期"],
        confidence=0.4,
    )


class TestNonInteractiveArg:
    def test_non_interactive_default_false(self):
        args = parse_args(["张杰演唱会"])
        assert args.non_interactive is False

    def test_non_interactive_flag_set(self):
        args = parse_args(["张杰演唱会", "--non-interactive"])
        assert args.non_interactive is True

    def test_unactionable_exit_code_constant(self):
        assert UNACTIONABLE_EXIT_CODE == 5


class TestUnactionableFallback:
    def test_force_non_interactive_returns_false_and_writes_stderr(self, capsys):
        result = _handle_unactionable_apply(
            _unactionable_parse_result(), force_non_interactive=True
        )
        assert result is False
        captured = capsys.readouterr()
        assert "0.40" in captured.err
        assert "缺失：观演人姓名" in captured.err

    def test_non_tty_stdin_returns_false(self, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return False

        monkeypatch.setattr("mobile.prompt_runner.sys.stdin", FakeStdin())
        result = _handle_unactionable_apply(
            _unactionable_parse_result(), force_non_interactive=False
        )
        assert result is False

    def test_tty_user_accepts_returns_true(self, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True

        monkeypatch.setattr("mobile.prompt_runner.sys.stdin", FakeStdin())
        monkeypatch.setattr(
            "mobile.prompt_runner._prompt_yes_no", lambda *_a, **_kw: True
        )
        assert (
            _handle_unactionable_apply(
                _unactionable_parse_result(), force_non_interactive=False
            )
            is True
        )

    def test_tty_user_rejects_returns_false(self, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True

        monkeypatch.setattr("mobile.prompt_runner.sys.stdin", FakeStdin())
        monkeypatch.setattr(
            "mobile.prompt_runner._prompt_yes_no", lambda *_a, **_kw: False
        )
        assert (
            _handle_unactionable_apply(
                _unactionable_parse_result(), force_non_interactive=False
            )
            is False
        )


class TestUpdateParseResultPostDiscovery:
    """覆盖 _update_parse_result_post_discovery 的所有分支。"""

    def _result(self, date=None, confidence=0.5):
        intent = PromptIntent(raw_prompt="x", search_keyword="k", date=date)
        return ParseResult(
            intent=intent, matched_item=None, confidence=confidence, diagnostics=[]
        )

    def test_no_summary_clears_matched_item(self):
        r = self._result()
        _update_parse_result_post_discovery(r, {"summary": {}}, None)
        assert r.matched_item is None

    def test_intent_date_in_visible_dates(self):
        r = self._result(date="04.06")
        summary = {"dates": ["04.05", "04.06"], "title": "X"}
        _update_parse_result_post_discovery(r, {"summary": summary}, {"text": "880元"})
        assert r.matched_session == "04.06"
        assert r.matched_price == "880元"
        # confidence 应被 +0.05
        assert r.confidence > 0.5

    def test_single_visible_date_used(self):
        r = self._result(date=None)
        summary = {"dates": ["04.07"]}
        _update_parse_result_post_discovery(r, {"summary": summary}, None)
        assert r.matched_session == "04.07"
        # 没 chosen_price 应加 diagnostic
        assert any("无法从页面票档" in d for d in r.diagnostics)

    def test_multiple_dates_no_match_adds_diagnostic(self):
        r = self._result(date="04.06")
        summary = {"dates": ["04.04", "04.05"]}
        _update_parse_result_post_discovery(r, {"summary": summary}, None)
        assert r.matched_session is None
        assert any("未找到唯一匹配" in d for d in r.diagnostics)

    def test_no_dates_with_undefined_intent_date(self):
        r = self._result(date=None)
        summary = {"dates": []}
        _update_parse_result_post_discovery(r, {"summary": summary}, {"text": "580元"})
        # 没有可见日期且 intent.date 也无 → 走 else 分支，记录 diagnostic
        assert r.matched_session is None
        assert any("<未指定>" in d for d in r.diagnostics)


class TestIsStdinTty:
    def test_returns_bool_without_raising(self):
        # 仅确认调用安全；具体值取决于运行环境
        result = _is_stdin_tty()
        assert isinstance(result, bool)

    def test_attribute_error_path(self, monkeypatch):
        class NoIsattyStdin:
            pass

        monkeypatch.setattr("mobile.prompt_runner.sys.stdin", NoIsattyStdin())
        assert _is_stdin_tty() is False


class TestMainActionableCheck:
    """覆盖 main() 中 apply 模式下 is_actionable=False 走 UNACTIONABLE_EXIT_CODE 的分支。

    用 monkeypatch 替换核心依赖，避免触碰 ``with \\`` 风格的现有 main 测试。
    """

    def test_apply_mode_returns_unactionable_exit_code(self, monkeypatch):
        from mobile import prompt_runner

        # 准备 base_config_dict 与 Config 对象（最小可用）
        base_config_dict = {
            "serial": "ABC",
            "users": ["张志涛"],
            "city": "北京",
            "date": "04.06",
            "price": "580元",
            "price_index": 0,
            "keyword": "X",
            "if_commit_order": False,
            "probe_only": True,
            "auto_navigate": True,
        }

        from mobile.config import Config

        class FakeConfig:
            def __init__(self):
                self.city = "北京"
                self.date = "04.06"
                self.price = "580元"
                self.price_index = 0

            def to_dict(self):
                return {**base_config_dict}

        # 让 parse_prompt 返回置信度低 + 无 matched_item 的 ParseResult
        from mobile.prompt_parser import ParseResult

        class FakeIntent:
            raw_prompt = "x"
            quantity = 1
            quantity_explicit = False
            attendee_names = ["张志涛"]
            date = None
            city = None
            artist = None
            search_keyword = "x"
            candidate_keywords = ["x"]
            price_hint = None
            seat_hint = None
            numeric_price_hint = None
            numeric_price_min = None
            numeric_price_max = None
            notes = []

        fake_pr = ParseResult(
            intent=FakeIntent(),
            matched_item=None,
            diagnostics=["test diag"],
            confidence=0.4,
        )

        class FakeBot:
            driver = None

            def __init__(self, *_a, **_kw):
                pass

            def dismiss_startup_popups(self):
                pass

            def probe_current_page(self):
                return {"state": "detail_page"}

            def discover_target_event(self, *_a, **_kw):
                return {
                    "used_keyword": "x",
                    "search_results": [],
                    "page_probe": {"state": "detail_page"},
                }

            def inspect_current_target_event(self, _probe):
                return {
                    "title": "X",
                    "venue": "Y",
                    "state": "detail_page",
                    "reservation_mode": False,
                    "dates": ["04.04", "04.05"],
                    "price_options": [],
                }

        monkeypatch.setattr(
            prompt_runner, "_load_base_config_dict", lambda _p: base_config_dict
        )
        monkeypatch.setattr(prompt_runner, "parse_prompt", lambda _p: fake_pr)
        monkeypatch.setattr(
            prompt_runner, "_validate_prompt_requirements", lambda *_a, **_kw: None
        )
        monkeypatch.setattr(
            prompt_runner, "_auto_sync_device_config", lambda c, _m: (c, None)
        )
        monkeypatch.setattr(
            Config, "load_config", classmethod(lambda cls, _p: FakeConfig())
        )
        monkeypatch.setattr(prompt_runner, "DamaiBot", FakeBot)
        monkeypatch.setattr(
            prompt_runner, "choose_price_option", lambda *_a, **_kw: None
        )
        monkeypatch.setattr(prompt_runner, "_format_summary", lambda *_a, **_kw: "")

        # 让 stdin 非 TTY → fallback 直接 exit 5
        class FakeStdin:
            def isatty(self):
                return False

        monkeypatch.setattr(prompt_runner.sys, "stdin", FakeStdin())

        result = prompt_runner.main(["x", "--mode", "apply", "--non-interactive"])
        assert result == UNACTIONABLE_EXIT_CODE
