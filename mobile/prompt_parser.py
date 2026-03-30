# -*- coding: UTF-8 -*-
"""Natural-language prompt parsing for the mobile Damai workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:
    from mobile.item_resolver import normalize_text
except ImportError:
    from item_resolver import normalize_text


_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_KNOWN_CITY_TOKENS = (
    "北京", "上海", "深圳", "广州", "杭州", "成都", "重庆", "武汉", "南京", "西安",
    "苏州", "天津", "长沙", "郑州", "青岛", "宁波", "福州", "厦门", "南昌", "沈阳",
    "大连", "合肥", "无锡", "佛山", "东莞", "珠海", "昆明", "贵阳", "南宁", "长春",
    "哈尔滨", "太原", "石家庄", "济南", "兰州", "海口", "三亚", "乌鲁木齐", "呼和浩特",
)

_REQUEST_STOPWORDS = (
    "帮我", "帮忙", "抢票", "抢一张", "抢两张", "抢", "买", "订", "门票", "票", "演出票",
    "给我", "一下", "尽快", "尽量", "麻烦", "我要", "想要", "请", "能不能", "可以", "帮",
)

_SEAT_HINTS = ("内场", "看台", "VIP", "vip", "至尊", "前排", "后排", "看台区", "包厢")

_UNAVAILABLE_TAGS = {"无票", "缺货", "售罄", "已售罄", "不可选", "暂不可售"}


@dataclass
class PromptIntent:
    raw_prompt: str
    quantity: int = 1
    date: Optional[str] = None
    city: Optional[str] = None
    artist: Optional[str] = None
    search_keyword: Optional[str] = None
    candidate_keywords: list[str] = field(default_factory=list)
    price_hint: Optional[str] = None
    seat_hint: Optional[str] = None
    numeric_price_hint: Optional[int] = None
    notes: list[str] = field(default_factory=list)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_chinese_int(token: str) -> Optional[int]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[token]
    if len(token) == 2 and token[0] == "十" and token[1] in _CHINESE_DIGITS:
        return 10 + _CHINESE_DIGITS[token[1]]
    if len(token) == 2 and token[1] == "十" and token[0] in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[token[0]] * 10
    if len(token) == 3 and token[1] == "十" and token[0] in _CHINESE_DIGITS and token[2] in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[token[0]] * 10 + _CHINESE_DIGITS[token[2]]
    return None


def _parse_quantity(prompt: str) -> int:
    match = re.search(r"([0-9零一二两三四五六七八九十]+)\s*张", prompt)
    if not match:
        return 1

    value = _parse_chinese_int(match.group(1))
    return value if value and value > 0 else 1


def _parse_date(prompt: str) -> Optional[str]:
    patterns = (
        r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日好]?",
        r"(\d{1,2})[./-](\d{1,2})",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if not match:
            continue
        month = int(match.group(1))
        day = int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}.{day:02d}"
    return None


def _parse_city(prompt: str) -> Optional[str]:
    for city in _KNOWN_CITY_TOKENS:
        if city in prompt:
            return city
    match = re.search(r"([\u4e00-\u9fff]{2,4})站", prompt)
    if match:
        return match.group(1)
    return None


def _clean_prompt_for_keyword(prompt: str, removable_tokens: Optional[Iterable[str]] = None) -> str:
    cleaned = prompt
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日好]?", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}[./-]\d{1,2}", " ", cleaned)
    cleaned = re.sub(r"[0-9零一二两三四五六七八九十]+\s*张", " ", cleaned)
    for word in _REQUEST_STOPWORDS:
        cleaned = cleaned.replace(word, " ")
    for token in removable_tokens or ():
        if token:
            cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"\b\d+\s*元?\b", " ", cleaned)
    cleaned = cleaned.replace("的", " ")
    return _normalize_whitespace(cleaned)


def _parse_artist_and_keyword(prompt: str, removable_tokens: Optional[Iterable[str]] = None) -> tuple[Optional[str], Optional[str], list[str]]:
    cleaned = _clean_prompt_for_keyword(prompt, removable_tokens=removable_tokens)

    artist = None
    artist_match = re.search(r"([\u4e00-\u9fffA-Za-z0-9·•]+?)(?:的)?(?:演唱会|音乐会|演出|live|LIVE|巡演)", cleaned)
    if artist_match:
        artist = artist_match.group(1).strip(" ，,。！？")
    else:
        tail_artist = re.search(r"([\u4e00-\u9fffA-Za-z0-9·•]{2,12})", cleaned)
        if tail_artist:
            artist = tail_artist.group(1)

    candidates = []
    if artist:
        candidates.extend([f"{artist} 演唱会", artist])

    if cleaned:
        candidates.append(cleaned)

    deduped = []
    seen = set()
    for candidate in candidates:
        value = _normalize_whitespace(candidate)
        normalized = normalize_text(value)
        if not value or not normalized or normalized in seen:
            continue
        deduped.append(value)
        seen.add(normalized)

    return artist, (deduped[0] if deduped else None), deduped


def _parse_price_hints(prompt: str) -> tuple[Optional[str], Optional[str], Optional[int]]:
    seat_hint = None
    for token in _SEAT_HINTS:
        if token in prompt:
            seat_hint = token
            break

    numeric_price = None
    numeric_match = re.search(r"([1-9]\d{1,4})\s*元?", prompt)
    if numeric_match:
        numeric_price = int(numeric_match.group(1))

    if seat_hint and numeric_price:
        return f"{seat_hint}{numeric_price}元", seat_hint, numeric_price
    if numeric_price:
        return f"{numeric_price}元", None, numeric_price
    if seat_hint:
        return seat_hint, seat_hint, None
    return None, None, None


def parse_prompt(prompt: str) -> PromptIntent:
    if not isinstance(prompt, str) or len(prompt.strip()) == 0:
        raise ValueError("prompt 不能为空")

    normalized_prompt = _normalize_whitespace(prompt)
    parsed_date = _parse_date(normalized_prompt)
    parsed_city = _parse_city(normalized_prompt)
    price_hint, seat_hint, numeric_price = _parse_price_hints(normalized_prompt)
    removable_tokens = []
    if parsed_city:
        removable_tokens.extend([parsed_city, f"{parsed_city}站"])
    if seat_hint:
        removable_tokens.append(seat_hint)
    if price_hint:
        removable_tokens.append(price_hint)
    if numeric_price is not None:
        removable_tokens.extend([str(numeric_price), f"{numeric_price}元"])

    artist, keyword, candidate_keywords = _parse_artist_and_keyword(
        normalized_prompt,
        removable_tokens=removable_tokens,
    )

    intent = PromptIntent(
        raw_prompt=normalized_prompt,
        quantity=_parse_quantity(normalized_prompt),
        date=parsed_date,
        city=parsed_city,
        artist=artist,
        search_keyword=keyword,
        candidate_keywords=candidate_keywords,
        price_hint=price_hint,
        seat_hint=seat_hint,
        numeric_price_hint=numeric_price,
    )

    if not intent.search_keyword:
        raise ValueError("无法从提示词中提取搜索关键词")

    if not intent.date:
        intent.notes.append("提示词中未识别到明确日期，后续需要基于查询结果确认场次")

    if not intent.price_hint:
        intent.notes.append("提示词中未识别到明确票档偏好，后续会使用查询结果确认票档")

    return intent


def _extract_digits(text: str) -> Optional[int]:
    match = re.search(r"([1-9]\d{1,4})", text or "")
    if match:
        return int(match.group(1))
    return None


def is_price_option_available(option: dict) -> bool:
    tag = (option.get("tag") or "").strip()
    return tag not in _UNAVAILABLE_TAGS


def score_price_option(intent: PromptIntent, option: dict) -> int:
    text = option.get("text") or ""
    tag = option.get("tag") or ""
    normalized_text = normalize_text(text)
    score = 0

    if not is_price_option_available(option):
        score -= 1000

    if intent.price_hint and normalize_text(intent.price_hint) in normalized_text:
        score += 120

    if intent.seat_hint and normalize_text(intent.seat_hint) in normalized_text:
        score += 80

    if intent.numeric_price_hint is not None:
        digits = _extract_digits(text)
        if digits == intent.numeric_price_hint:
            score += 150
        elif digits is not None:
            score -= abs(digits - intent.numeric_price_hint)

    if tag in {"可预约", "预售", "可选"}:
        score += 10

    return score


def choose_price_option(intent: PromptIntent, options: Iterable[dict]) -> Optional[dict]:
    ranked = []
    for option in options:
        scored = dict(option)
        scored["score"] = score_price_option(intent, option)
        ranked.append(scored)

    if not ranked:
        return None

    ranked.sort(key=lambda item: item["score"], reverse=True)
    best = ranked[0]

    if intent.price_hint and best["score"] < 100:
        return None

    if not intent.price_hint and not is_price_option_available(best):
        return None

    return best
