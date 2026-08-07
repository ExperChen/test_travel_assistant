"""酒店 Tool：Google Hotels Autocomplete + Search。

Tool 描述与参数 schema 对齐 `docs/hotel/serpapi-google-hotels-api.md` §6.1。
"""

from __future__ import annotations

import re
from datetime import date

from app.config import settings
from app.core.dates import coerce_date
from app.core.exceptions import InvalidParams
from app.core.logging import get_logger
from app.models.common import GeoPoint
from app.models.hotel import (
    HotelCandidate,
    HotelSearchParams,
    HotelSuggestion,
    NearbyPlace,
    Rate,
)
from app.providers.serpapi_client import SerpApiClient
from app.tools.registry import serpapi_client, tool

log = get_logger(__name__)

__all__ = ["hotels_autocomplete", "hotels_search", "MAX_HOTEL_RESULTS", "MAX_AMENITIES"]

MAX_HOTEL_RESULTS = 10
MAX_AMENITIES = 6

_HOTEL_CLASS_RE = re.compile(r"(\d)")


def _hotel_class(raw: dict) -> int | None:
    """星级：优先用 extracted_hotel_class（int），否则从 '5-star hotel' 里抠数字。"""
    if (extracted := raw.get("extracted_hotel_class")) is not None:
        try:
            return int(extracted)
        except (TypeError, ValueError):
            pass
    if m := _HOTEL_CLASS_RE.search(str(raw.get("hotel_class") or "")):
        return int(m.group(1))
    return None


def _rate(raw: dict | None) -> Rate | None:
    return Rate.model_validate(raw) if raw else None


MAX_NEARBY = 3
"""周边地标只留最近的几个，多了就成噪音了。"""


def _nearby(raw: dict) -> list[NearbyPlace]:
    """`nearby_places[].transportations[]` 只取第一条（Google 按由近及远给）。"""
    out: list[NearbyPlace] = []
    for place in (raw.get("nearby_places") or [])[:MAX_NEARBY]:
        trip = (place.get("transportations") or [{}])[0]
        out.append(
            NearbyPlace(
                name=place.get("name") or "",
                mode=trip.get("type") or "",
                duration=trip.get("duration") or "",
            )
        )
    return [p for p in out if p.name]


def _parse_property(raw: dict) -> HotelCandidate:
    return HotelCandidate(
        nearby_places=_nearby(raw),
        location_rating=raw.get("location_rating"),
        name=raw.get("name") or "",
        kind="vacation rental" if raw.get("type") == "vacation rental" else "hotel",
        property_token=raw.get("property_token") or "",
        hotel_class=_hotel_class(raw),
        overall_rating=raw.get("overall_rating"),
        reviews=raw.get("reviews"),
        total_rate=_rate(raw.get("total_rate")),
        rate_per_night=_rate(raw.get("rate_per_night")),
        # gps_coordinates 是 WGS-84，GeoPoint 会记住这一点，进高德前自动转换
        location=GeoPoint.from_google(raw.get("gps_coordinates")),
        amenities=(raw.get("amenities") or [])[:MAX_AMENITIES],
        thumbnail=raw.get("thumbnail") or "",
        deal_description=raw.get("deal_description") or "",
        link=raw.get("link") or "",
    )


def _parse_ad(raw: dict) -> HotelCandidate:
    # ads 是"价格胶囊卡片"，只有单晚价，没有 total_rate（文档 §4.3）
    price = raw.get("extracted_price")
    return HotelCandidate(
        nearby_places=_nearby(raw),
        location_rating=raw.get("location_rating"),
        name=raw.get("name") or "",
        kind="hotel",
        property_token=raw.get("property_token") or "",
        is_ad=True,
        source=raw.get("source") or "",
        hotel_class=_hotel_class(raw),
        overall_rating=raw.get("overall_rating"),
        reviews=raw.get("reviews"),
        rate_per_night=Rate(lowest=raw.get("price") or "", extracted_lowest=price)
        if price is not None or raw.get("price")
        else None,
        location=GeoPoint.from_google(raw.get("gps_coordinates")),
        amenities=(raw.get("amenities") or [])[:MAX_AMENITIES],
        thumbnail=raw.get("thumbnail") or "",
        link=raw.get("link") or "",
    )


def _safe(parse, raw: dict, what: str) -> HotelCandidate | None:
    try:
        return parse(raw)
    except Exception:  # noqa: BLE001 —— 单条脏数据不该让整次搜索失败
        log.warning(f"跳过无法解析的{what}", extra={"raw": str(raw)[:200]})
        return None


@tool(
    name="hotels_autocomplete",
    provider="serpapi",
    description=(
        "当用户给出模糊的酒店/区域关键词时调用，返回补全建议。"
        "建议分三类：带 property_token 的是具体门店（可直接查这一家的房价）；"
        "只有 kgmid 的是品牌；其余是搜索词建议。"
        "选中某条后，用它的 autocomplete_suggestion 作为 hotels_search 的 q。"
    ),
    parameters={
        "type": "object",
        "properties": {"q": {"type": "string", "description": "用户输入的酒店/区域关键词"}},
        "required": ["q"],
    },
)
async def hotels_autocomplete(
    q: str,
    *,
    gl: str | None = None,
    hl: str | None = None,
    currency: str | None = None,
    client: SerpApiClient | None = None,
) -> list[HotelSuggestion]:
    if not q or not q.strip():
        raise InvalidParams("酒店查询关键词不能为空")

    payload = await (client or serpapi_client()).search(
        {
            "engine": "google_hotels_autocomplete",
            "q": q.strip(),
            # gl/hl/currency 必须与后续 search 保持一致，否则价格币种对不上（文档 §7.4）
            "gl": gl or settings.default_gl,
            "hl": hl or settings.default_hl,
            "currency": currency or settings.default_currency,
        }
    )

    out: list[HotelSuggestion] = []
    for raw in payload.get("suggestions") or []:
        try:
            out.append(HotelSuggestion.model_validate(raw))
        except Exception:  # noqa: BLE001
            log.warning("跳过无法解析的酒店建议", extra={"raw": str(raw)[:200]})
    return out


@tool(
    name="hotels_search",
    provider="serpapi",
    description=(
        "搜索酒店。两种模式：(1) 列表搜索——传 q + 入住/离店日期；"
        "(2) 单店精准——额外传 property_token。无论哪种模式日期都必填（YYYY-MM-DD）。"
        "返回合并后的候选（广告位带 is_ad 标记，不要因为是广告就忽略，它们常常更便宜），"
        "每条含名称、星级、评分、评论数、总价 total_rate、单晚价、坐标、设施。"
        "总价看 total_rate，不要用单晚价乘晚数——后者通常是税前价。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "城市/区域/品牌/autocomplete_suggestion"},
            "property_token": {"type": "string", "description": "单店模式必填，来自补全或上次搜索"},
            "check_in_date": {"type": "string", "description": "入住日期 YYYY-MM-DD"},
            "check_out_date": {"type": "string", "description": "离店日期 YYYY-MM-DD"},
            "adults": {"type": "integer", "description": "成人数，默认 2"},
            "children": {"type": "integer", "description": "儿童数，默认 0"},
            "children_ages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "每个儿童的年龄（1-17），长度必须等于 children",
            },
            "sort_by": {
                "type": "integer",
                "enum": [3, 8, 13],
                "description": "3=最低价 8=最高评分 13=评论最多；不传=相关度",
            },
            "min_price": {"type": "integer", "description": "单晚最低价"},
            "max_price": {"type": "integer", "description": "单晚最高价"},
            "rating": {"type": "integer", "enum": [7, 8, 9], "description": "7=3.5+ 8=4.0+ 9=4.5+"},
            "hotel_class": {
                "type": "array",
                "items": {"type": "integer", "enum": [2, 3, 4, 5]},
                "description": "星级筛选，仅 hotels 模式有效",
            },
            "free_cancellation": {
                "type": "boolean",
                "description": "仅看可免费取消，仅 hotels 模式",
            },
            "vacation_rentals": {
                "type": "boolean",
                "description": "true=民宿模式，星级等筛选会失效",
            },
        },
        "required": ["check_in_date", "check_out_date"],
    },
)
async def hotels_search(
    check_in_date: str | date,
    check_out_date: str | date,
    q: str = "",
    property_token: str = "",
    adults: int = 2,
    children: int = 0,
    children_ages: list[int] | None = None,
    sort_by: int | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    rating: int | None = None,
    hotel_class: list[int] | None = None,
    free_cancellation: bool = False,
    vacation_rentals: bool = False,
    next_page_token: str = "",
    *,
    gl: str | None = None,
    hl: str | None = None,
    currency: str | None = None,
    client: SerpApiClient | None = None,
) -> list[HotelCandidate]:
    try:
        params = HotelSearchParams(
            q=q,
            property_token=property_token,
            check_in_date=coerce_date(check_in_date),
            check_out_date=coerce_date(check_out_date),
            adults=adults,
            children=children,
            children_ages=children_ages or [],
            sort_by=sort_by,
            min_price=min_price,
            max_price=max_price,
            rating=rating,
            hotel_class=hotel_class or [],
            free_cancellation=free_cancellation,
            vacation_rentals=vacation_rentals,
            next_page_token=next_page_token,
        )
    except ValueError as exc:
        raise InvalidParams(str(exc)) from exc

    payload = await (client or serpapi_client()).search(
        params.to_serpapi(
            gl=gl or settings.default_gl,
            hl=hl or settings.default_hl,
            currency=currency or settings.default_currency,
        )
    )

    candidates: list[HotelCandidate] = []
    for raw in payload.get("ads") or []:
        if (c := _safe(_parse_ad, raw, "广告酒店")) is not None:
            candidates.append(c)
    for raw in payload.get("properties") or []:
        if (c := _safe(_parse_property, raw, "酒店")) is not None:
            candidates.append(c)

    return candidates[:MAX_HOTEL_RESULTS]
