# -*- coding: UTF-8 -*-
"""Resolve Damai item metadata from an item URL or itemId."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


DM_APP_KEY = "12574478"
DM_JSV = "2.7.5"
DM_ITEM_DETAIL_API = "mtop.damai.item.detail.getdetail"
DM_ITEM_DETAIL_VERSION = "1.0"
DM_ITEM_DETAIL_HOST = "https://mtop.damai.cn"
DM_CHANNEL = "damai@damaih5_h5"
DM_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Mobile Safari/537.36"
)


class DamaiItemResolveError(RuntimeError):
    """Raised when Damai item metadata cannot be resolved."""


def extract_item_id(value: str) -> str:
    """Extract the Damai itemId from a URL or raw numeric string."""
    if not isinstance(value, str) or len(value.strip()) == 0:
        raise ValueError("item 链接或 itemId 不能为空")

    candidate = value.strip()
    if candidate.isdigit():
        return candidate

    parsed = urlparse(candidate)
    query = parse_qs(parsed.query)
    for key in ("itemId", "itemid", "id"):
        values = query.get(key)
        if values and values[0].isdigit():
            return values[0]

    for pattern in (
        r"(?:itemId|itemid|id)=([0-9]{6,})",
        r"/([0-9]{6,})(?:[/?#]|$)",
    ):
        match = re.search(pattern, candidate)
        if match:
            return match.group(1)

    raise ValueError(f"无法从输入中提取 itemId: {value}")


def normalize_text(text: Optional[str]) -> str:
    """Normalize text for loose matching across Damai UI variants."""
    if not text:
        return ""

    normalized = re.sub(r"[\s\u00a0]+", "", text)
    normalized = re.sub(r"[【】\[\]（）()·•,:：\-—_~]+", "", normalized)
    return normalized.lower()


def city_keyword(city_name: Optional[str]) -> str:
    """Normalize city text for config and search matching."""
    if not city_name:
        return ""
    return re.sub(r"(特别行政区|自治州|地区|盟|市)$", "", city_name.strip())


def build_search_keyword(
    item_name: Optional[str], item_name_display: Optional[str] = None
) -> str:
    """Build a search keyword that works better in the Damai app search page."""
    candidates = [item_name or "", item_name_display or ""]
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        text = re.sub(r"^[【\[][^】\]]+[】\]]", "", text).strip()
        text = re.sub(r"^[^0-9A-Za-z\u4e00-\u9fff]*", "", text)
        if text:
            return text

    raise ValueError("无法根据演出名称生成搜索关键词")


@dataclass
class DamaiItemDetail:
    item_id: str
    item_name: str
    item_name_display: str
    city_name: str
    venue_name: str
    venue_city_name: str
    show_time: str
    price_range: str
    raw_data: dict

    @property
    def search_keyword(self) -> str:
        return build_search_keyword(self.item_name, self.item_name_display)

    @property
    def city_keyword(self) -> str:
        return city_keyword(self.city_name)

    @property
    def normalized_dates(self) -> list:
        """从 ``show_time`` 中抽取所有可识别的 ``MM.DD`` 候选，统一格式。"""
        try:
            from mobile.date_utils import normalize_date
        except ImportError:  # pragma: no cover - 运行环境兼容
            from date_utils import normalize_date  # type: ignore[no-redef]

        seen: list = []
        if not self.show_time:
            return seen
        # show_time 可能是 "2026-04-06"、"2026.04.06" 或 "2026-04-06 ~ 2026-04-08"
        for chunk in re.split(r"\s*[~～至到\-]\s*", self.show_time):
            candidate = normalize_date(chunk)
            if candidate and candidate not in seen:
                seen.append(candidate)
        if not seen:
            # 兜底：直接对完整 show_time 跑一次正则
            candidate = normalize_date(self.show_time)
            if candidate:
                seen.append(candidate)
        return seen


class DamaiItemResolver:
    """Resolve Damai item metadata through the official mobile mtop endpoint."""

    def __init__(self, timeout: float = 10):
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))

    def _referer_for_item(self, item_id: str, item_url: Optional[str]) -> str:
        if item_url:
            return item_url
        return f"https://m.damai.cn/shows/item.html?itemId={item_id}"

    def _request(self, url: str, referer: str) -> str:
        request = Request(
            url, headers={"User-Agent": DM_USER_AGENT, "Referer": referer}
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def _prime_token(self, item_id: str, referer: str, data: str) -> str:
        params = {
            "jsv": DM_JSV,
            "appKey": DM_APP_KEY,
            "t": "0",
            "sign": "x",
            "type": "json",
            "timeout": "10000",
            "valueType": "string",
            "forceAntiCreep": "true",
            "dataType": "json",
            "data": data,
        }
        url = f"{DM_ITEM_DETAIL_HOST}/h5/{DM_ITEM_DETAIL_API}/{DM_ITEM_DETAIL_VERSION}/?{urlencode(params)}"
        self._request(url, referer)

        for cookie in self.cookie_jar:
            if cookie.name == "_m_h5_tk":
                return cookie.value.split("_", 1)[0]

        raise DamaiItemResolveError("无法从大麦接口响应中获取 `_m_h5_tk`")

    def fetch_item_detail(
        self, item_url: Optional[str] = None, item_id: Optional[str] = None
    ) -> DamaiItemDetail:
        resolved_item_id = extract_item_id(item_id or item_url or "")
        referer = self._referer_for_item(resolved_item_id, item_url)
        data_obj = {
            "itemId": resolved_item_id,
            "platform": "8",
            "comboChannel": "2",
            "dmChannel": DM_CHANNEL,
        }
        data = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)

        token = self._prime_token(resolved_item_id, referer, data)
        current_ms = str(int(time.time() * 1000))
        sign_payload = f"{token}&{current_ms}&{DM_APP_KEY}&{data}"
        sign = hashlib.md5(sign_payload.encode("utf-8")).hexdigest()

        params = {
            "jsv": DM_JSV,
            "appKey": DM_APP_KEY,
            "t": current_ms,
            "sign": sign,
            "type": "json",
            "timeout": "10000",
            "valueType": "string",
            "forceAntiCreep": "true",
            "dataType": "json",
            "data": data,
        }
        url = f"{DM_ITEM_DETAIL_HOST}/h5/{DM_ITEM_DETAIL_API}/{DM_ITEM_DETAIL_VERSION}/?{urlencode(params)}"
        body = self._request(url, referer)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DamaiItemResolveError(
                f"大麦详情接口返回了不可解析内容: {exc}"
            ) from exc

        ret = payload.get("ret") or []
        if not any("SUCCESS" in entry for entry in ret):
            raise DamaiItemResolveError(f"大麦详情接口返回失败: {ret!r}")

        data_block = payload.get("data") or {}
        item = data_block.get("item") or {}
        venue = data_block.get("venue") or {}
        price = data_block.get("price") or {}

        item_name = item.get("itemName") or item.get("itemNameDisplay") or ""
        if not item_name:
            raise DamaiItemResolveError("大麦详情接口未返回有效的演出名称")

        return DamaiItemDetail(
            item_id=item.get("itemId") or resolved_item_id,
            item_name=item_name,
            item_name_display=item.get("itemNameDisplay") or item_name,
            city_name=item.get("cityName") or venue.get("venueCityName") or "",
            venue_name=venue.get("venueName") or "",
            venue_city_name=venue.get("venueCityName") or "",
            show_time=item.get("showTime") or "",
            price_range=price.get("range") or "",
            raw_data=data_block,
        )
