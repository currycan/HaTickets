# -*- coding: UTF-8 -*-
"""
Unified logging framework for HaTickets mobile module.

Usage:
    from logger import get_logger
    logger = get_logger(__name__)
    logger.info("消息内容")
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

# Asia/Shanghai timezone (UTC+8)
_TZ_SHANGHAI = timezone(timedelta(hours=8))

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hatickets_mobile.log")

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ShanghaiFormatter(logging.Formatter):
    """Logging formatter that uses Asia/Shanghai timezone for timestamps."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=_TZ_SHANGHAI)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime(_DATE_FORMAT)


def _build_console_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(_ShanghaiFormatter(fmt=_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
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
