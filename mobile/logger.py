# -*- coding: UTF-8 -*-
"""
Unified logging framework for HaTickets mobile module.

Usage:
    from logger import get_logger, log_event
    logger = get_logger(__name__)
    logger.info("消息内容")
    log_event(logger, "sale_ready", cta_text="立即购票", polls=12, duration_ms=480)
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

# Asia/Shanghai timezone (UTC+8)
_TZ_SHANGHAI = timezone(timedelta(hours=8))

_LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hatickets_mobile.log"
)

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ANSI_RESET = "\033[0m"
_ANSI_COLORS = {
    logging.DEBUG: "\033[2m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}


class _ShanghaiFormatter(logging.Formatter):
    """Logging formatter that uses Asia/Shanghai timezone for timestamps."""

    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        dt = datetime.fromtimestamp(record.created, tz=_TZ_SHANGHAI)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime(_DATE_FORMAT)


class _ShanghaiColorFormatter(_ShanghaiFormatter):
    """Console formatter with optional ANSI colors based on log level."""

    def __init__(self, *args, enable_color: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_color = enable_color

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if not self.enable_color:
            return rendered

        color = _ANSI_COLORS.get(record.levelno)
        if not color:
            return rendered
        return f"{color}{rendered}{_ANSI_RESET}"


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE") == "1":
        return True
    if stream is None or not hasattr(stream, "isatty"):
        return False
    if not stream.isatty():
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def _build_console_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        _ShanghaiColorFormatter(
            fmt=_CONSOLE_FORMAT,
            datefmt=_DATE_FORMAT,
            enable_color=_supports_color(getattr(handler, "stream", None)),
        )
    )
    return handler


def _build_file_handler() -> logging.FileHandler:
    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_ShanghaiFormatter(fmt=_FILE_FORMAT, datefmt=_DATE_FORMAT))
    return handler


# Module-level flag to avoid adding duplicate handlers on repeated get_logger() calls.
_configured_loggers: Set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger that writes to console (INFO+) and file (DEBUG+).

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance with Shanghai-timezone timestamps.
    """
    logger = logging.getLogger(name)

    if name not in _configured_loggers:
        logger.setLevel(logging.DEBUG)
        logger.addHandler(_build_console_handler())
        logger.addHandler(_build_file_handler())
        # Prevent propagation to the root logger to avoid duplicate output.
        logger.propagate = False
        _configured_loggers.add(name)

    return logger


def _format_event_value(value) -> str:
    """Render a field value for structured event logs (no whitespace, quote-safe)."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Collapse whitespace so a single line stays parseable as `key=value` pairs.
    text = " ".join(text.split())
    if not text:
        return '""'
    if any(c in text for c in (" ", "\t", "=", '"')):
        text = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'
    return text


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields,
) -> None:
    """Emit a structured event log line.

    Output shape: ``event=<name> key1=value1 key2=value2``.  Values containing
    whitespace, ``=`` or quotes are quoted/escaped so the line stays grep-able
    and downstream-parseable.

    Args:
        logger: Target logger (typically obtained via :func:`get_logger`).
        event: Short snake_case event name (e.g. ``sale_ready``).
        level: Logging level (default :data:`logging.INFO`).
        **fields: Arbitrary key/value pairs serialized to the log line.
    """
    parts = [f"event={_format_event_value(event)}"]
    for key, value in fields.items():
        parts.append(f"{key}={_format_event_value(value)}")
    logger.log(level, " ".join(parts))
