# -*- coding: UTF-8 -*-
"""日期格式归一化工具。

NLP 模式下用户输入（"4月6号"）、API 返回（"2026-04-06"）、UI 显示（"04.06"）
三种格式并存，必须统一为 ``MM.DD`` 后再做匹配。仅依赖标准库，不引入外部依赖。
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["normalize_date"]


_PATTERNS = (
    # 4月6号 / 4 月 6 日 / 04月06日
    re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*[号日好]?"),
    # 2026-04-06 / 2026/04/06 / 2026.04.06
    re.compile(r"\d{4}[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})"),
    # 04-06 / 4/6 / 04.06
    re.compile(r"(?P<m>\d{1,2})[./-](?P<d>\d{1,2})"),
)


def normalize_date(raw: str) -> Optional[str]:
    """统一日期为 ``MM.DD`` 格式；解析失败返回 ``None``。

    支持格式：
    - ``4月6号`` / ``4 月 6 日`` / ``04月06日``
    - ``4/6`` / ``04-06`` / ``04.06``
    - ``2026-04-06`` / ``2026/04/06`` / ``2026.04.06``
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            month = int(match.group("m"))
            day = int(match.group("d"))
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}.{day:02d}"
    return None
