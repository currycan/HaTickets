# -*- coding: UTF-8 -*-
"""fix-plan #26 Step 4：``--non-interactive`` 标志 + ``is_actionable=False`` fallback。

放在独立文件，避免与 ``test_mobile_prompt_runner.py`` 中的 ``with \\`` 块互相影响。
"""

from __future__ import annotations


from mobile.prompt_parser import ParseResult, PromptIntent
from mobile.prompt_runner import (
    UNACTIONABLE_EXIT_CODE,
    _handle_unactionable_apply,
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
