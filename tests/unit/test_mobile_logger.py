# -*- coding: UTF-8 -*-
"""Tests for mobile.logger module."""

import logging
from unittest.mock import Mock


from mobile.logger import (
    _ShanghaiColorFormatter,
    _ShanghaiFormatter,
    _supports_color,
    get_logger,
    log_event,
    _ANSI_COLORS,
    _ANSI_RESET,
    _DATE_FORMAT,
    _configured_loggers,
    _format_event_value,
)


# ---------------------------------------------------------------------------
# _ShanghaiFormatter
# ---------------------------------------------------------------------------


class TestShanghaiFormatter:
    def test_format_time_with_datefmt(self):
        fmt = _ShanghaiFormatter(fmt="%(message)s", datefmt="%H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = fmt.formatTime(record, datefmt="%H:%M:%S")
        # Should be a valid time string (HH:MM:SS)
        assert len(result) == 8
        assert result.count(":") == 2

    def test_format_time_without_datefmt(self):
        fmt = _ShanghaiFormatter(fmt="%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        result = fmt.formatTime(record)
        # Default format: YYYY-MM-DD HH:MM:SS
        assert len(result) == 19
        assert "-" in result


# ---------------------------------------------------------------------------
# _ShanghaiColorFormatter
# ---------------------------------------------------------------------------


class TestShanghaiColorFormatter:
    def test_no_color_returns_plain(self):
        fmt = _ShanghaiColorFormatter(
            fmt="%(message)s",
            datefmt=_DATE_FORMAT,
            enable_color=False,
        )
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="warn msg",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        assert "\033[" not in result
        assert "warn msg" in result

    def test_color_enabled_wraps_with_ansi(self):
        fmt = _ShanghaiColorFormatter(
            fmt="%(message)s",
            datefmt=_DATE_FORMAT,
            enable_color=True,
        )
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="colored",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        assert _ANSI_COLORS[logging.WARNING] in result
        assert _ANSI_RESET in result

    def test_color_enabled_unknown_level_no_ansi(self):
        fmt = _ShanghaiColorFormatter(
            fmt="%(message)s",
            datefmt=_DATE_FORMAT,
            enable_color=True,
        )
        record = logging.LogRecord(
            name="test",
            level=5,
            pathname="",
            lineno=0,
            msg="odd level",
            args=(),
            exc_info=None,
        )
        result = fmt.format(record)
        # Level 5 is not in _ANSI_COLORS, so no wrapping
        assert "\033[" not in result


# ---------------------------------------------------------------------------
# _supports_color
# ---------------------------------------------------------------------------


class TestSupportsColor:
    def test_no_color_env_returns_false(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        stream = Mock()
        stream.isatty.return_value = True
        assert _supports_color(stream) is False

    def test_clicolor_force_returns_true(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        stream = Mock()
        assert _supports_color(stream) is True

    def test_none_stream_returns_false(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        assert _supports_color(None) is False

    def test_stream_without_isatty_returns_false(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        stream = object()  # no isatty attribute
        assert _supports_color(stream) is False

    def test_non_tty_stream_returns_false(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        stream = Mock()
        stream.isatty.return_value = False
        assert _supports_color(stream) is False

    def test_dumb_terminal_returns_false(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        stream = Mock()
        stream.isatty.return_value = True
        assert _supports_color(stream) is False

    def test_normal_tty_returns_true(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        stream = Mock()
        stream.isatty.return_value = True
        assert _supports_color(stream) is True


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_logger_with_handlers(self):
        # Use a unique name to avoid collisions
        name = "test_logger_unique_12345"
        _configured_loggers.discard(name)
        try:
            lgr = get_logger(name)
            assert isinstance(lgr, logging.Logger)
            assert lgr.level == logging.DEBUG
            assert lgr.propagate is False
            # Should have console + file handlers
            assert len(lgr.handlers) >= 2
        finally:
            # Cleanup
            lgr.handlers.clear()
            _configured_loggers.discard(name)

    def test_idempotent_no_duplicate_handlers(self):
        name = "test_logger_idempotent_67890"
        _configured_loggers.discard(name)
        try:
            lgr1 = get_logger(name)
            handler_count = len(lgr1.handlers)
            lgr2 = get_logger(name)
            assert lgr1 is lgr2
            assert len(lgr2.handlers) == handler_count
        finally:
            lgr1.handlers.clear()
            _configured_loggers.discard(name)


# ---------------------------------------------------------------------------
# _format_event_value
# ---------------------------------------------------------------------------


class TestFormatEventValue:
    def test_none_renders_none(self):
        assert _format_event_value(None) == "none"

    def test_bool_renders_lowercase(self):
        assert _format_event_value(True) == "true"
        assert _format_event_value(False) == "false"

    def test_int_renders_plain(self):
        assert _format_event_value(42) == "42"

    def test_float_renders_plain(self):
        assert _format_event_value(1.25) == "1.25"

    def test_simple_string_unquoted(self):
        assert _format_event_value("ready") == "ready"

    def test_empty_string_quoted(self):
        assert _format_event_value("") == '""'

    def test_string_with_space_is_quoted(self):
        assert _format_event_value("立即 购票") == '"立即 购票"'

    def test_string_with_equals_is_quoted(self):
        assert _format_event_value("a=b") == '"a=b"'

    def test_string_with_double_quote_is_escaped(self):
        assert _format_event_value('say "hi"') == '"say \\"hi\\""'

    def test_multiline_collapsed(self):
        assert _format_event_value("line1\nline2") == '"line1 line2"'


# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------


class TestLogEvent:
    def _make_capturing_logger(self, name: str):
        lgr = logging.getLogger(name)
        lgr.handlers.clear()
        lgr.setLevel(logging.DEBUG)
        lgr.propagate = False
        captured = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                captured.append((record.levelno, record.getMessage()))

        lgr.addHandler(CaptureHandler())
        return lgr, captured

    def test_emits_event_at_info_by_default(self):
        lgr, captured = self._make_capturing_logger("test_log_event_default")
        log_event(lgr, "sale_ready", cta_text="立即购票", polls=12, duration_ms=480)
        assert len(captured) == 1
        level, msg = captured[0]
        assert level == logging.INFO
        assert msg.startswith("event=sale_ready ")
        # Plain CJK without whitespace stays unquoted (single-token field).
        assert "cta_text=立即购票" in msg
        assert "polls=12" in msg
        assert "duration_ms=480" in msg

    def test_explicit_level_respected(self):
        lgr, captured = self._make_capturing_logger("test_log_event_error")
        log_event(
            lgr,
            "error",
            level=logging.ERROR,
            type="SoldOutError",
            scene="price_select",
        )
        assert captured[0][0] == logging.ERROR
        assert "event=error" in captured[0][1]
        assert "type=SoldOutError" in captured[0][1]
        assert "scene=price_select" in captured[0][1]

    def test_no_fields_renders_event_only(self):
        lgr, captured = self._make_capturing_logger("test_log_event_bare")
        log_event(lgr, "boot")
        assert captured[0][1] == "event=boot"

    def test_none_field_renders_none_token(self):
        lgr, captured = self._make_capturing_logger("test_log_event_none")
        log_event(lgr, "boot", damai_version=None)
        assert "damai_version=none" in captured[0][1]

    def test_field_order_preserved(self):
        lgr, captured = self._make_capturing_logger("test_log_event_order")
        log_event(lgr, "session_selected", date="2026-05-30", city="北京", index=2)
        msg = captured[0][1]
        # Order: event → date → city → index
        assert msg.index("date=") < msg.index("city=") < msg.index("index=")
