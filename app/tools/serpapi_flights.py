"""航班 Tool：Google Flights Autocomplete + Search。

Tool 描述与参数 schema 对齐 `docs/flight-agent/flight-react-agent-design.md` §3。
"""

from __future__ import annotations

from datetime import date

from app.config import settings
from app.core.dates import coerce_date
from app.core.exceptions import InvalidParams
from app.core.logging import get_logger
from app.models.flight import (
    CitySuggestion,
    FlightItinerary,
    FlightSearchParams,
    FlightSearchResults,
    TravelClass,
)
from app.providers.serpapi_client import SerpApiClient
from app.tools.registry import serpapi_client, tool

log = get_logger(__name__)

__all__ = ["flights_autocomplete", "flights_search", "MAX_CANDIDATES_PER_GROUP"]

MAX_CANDIDATES_PER_GROUP = 3
"""best_flights / other_flights 各留 3 条——再多 LLM 也读不完，只会烧 token。"""


def _parse_suggestions(payload: dict) -> list[CitySuggestion]:
    out: list[CitySuggestion] = []
    for raw in payload.get("suggestions") or []:
        try:
            out.append(CitySuggestion.model_validate(raw))
        except Exception:  # noqa: BLE001 —— 单条脏数据不该让整次搜索失败
            log.warning("跳过无法解析的机场建议", extra={"raw_name": str(raw)[:120]})
    return out


def _parse_itineraries(items: list | None) -> list[FlightItinerary]:
    out: list[FlightItinerary] = []
    for raw in (items or [])[:MAX_CANDIDATES_PER_GROUP]:
        try:
            out.append(FlightItinerary.model_validate(raw))
        except Exception:  # noqa: BLE001
            log.warning("跳过无法解析的航班组合", extra={"raw": str(raw)[:200]})
    return out


@tool(
    name="flights_autocomplete",
    provider="serpapi",
    description=(
        "当用户提供出发地或目的地的城市名/关键词（而不是明确的 IATA 三字码）时调用，"
        "返回匹配的城市及其下属机场列表，每个机场含 IATA 代码、机场全称、所在城市、距市中心距离。"
        "用户用序号或 IATA 代码确认后，才能进入航班搜索。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "城市名或机场名的部分/全部，如 '北京'、'Tokyo'、'JFK'",
            }
        },
        "required": ["q"],
    },
)
async def flights_autocomplete(
    q: str, *, hl: str | None = None, client: SerpApiClient | None = None
) -> list[CitySuggestion]:
    if not q or not q.strip():
        raise InvalidParams("机场查询关键词不能为空")

    payload = await (client or serpapi_client()).search(
        {
            "engine": "google_flights_autocomplete",
            "q": q.strip(),
            # **不传 hl 时中文城市名一律返回空**——实测「成都」「杭州」「北京」
            # 全部落空，加上 hl=zh-CN 立刻返回 TFU/CTU。目的地限中国大陆意味着
            # 用户几乎必然用中文输入，漏了这个参数整条链路会在第一步就死。
            "hl": hl or settings.default_hl,
        }
    )
    return _parse_suggestions(payload)


@tool(
    name="flights_search",
    provider="serpapi",
    description=(
        "在出发/到达机场 IATA、日期、往返标志、乘客数都确定后调用，搜索航班组合。"
        "返回 best_flights（Google 推荐）与 other_flights（其他），各最多 3 条；"
        "每条含总价、总时长（分钟）、中转次数、各航段（航班号/航司/机型/起降时间/舱位）、碳排放。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "departure_id": {"type": "string", "description": "出发机场 IATA，如 PEK"},
            "arrival_id": {"type": "string", "description": "到达机场 IATA，如 HGH"},
            "outbound_date": {"type": "string", "description": "出发日期 YYYY-MM-DD"},
            "return_date": {"type": "string", "description": "返回日期 YYYY-MM-DD，仅往返需要"},
            "is_round_trip": {"type": "boolean", "description": "true 时必须同时给 return_date"},
            "passengers": {"type": "integer", "description": "成人数，默认 1"},
            "children": {"type": "integer", "description": "儿童数，默认 0"},
            "travel_class": {
                "type": "string",
                "enum": ["economy", "premium_economy", "business", "first"],
                "description": "舱位偏好，默认 economy",
            },
            "departure_token": {
                "type": "string",
                "description": "选定某个去程方案后，用它的 departure_token 再查一次，"
                "返回的才是该去程对应的**返程**航班列表",
            },
        },
        "required": ["departure_id", "arrival_id", "outbound_date", "is_round_trip", "passengers"],
    },
)
async def flights_search(
    departure_id: str,
    arrival_id: str,
    outbound_date: str | date,
    is_round_trip: bool,
    passengers: int = 1,
    return_date: str | date | None = None,
    children: int = 0,
    travel_class: TravelClass | None = None,
    departure_token: str = "",
    *,
    currency: str | None = None,
    hl: str | None = None,
    gl: str | None = None,
    client: SerpApiClient | None = None,
) -> FlightSearchResults:
    try:
        params = FlightSearchParams(
            departure_airport_id=departure_id,
            arrival_airport_id=arrival_id,
            departure_date=coerce_date(outbound_date),
            return_date=coerce_date(return_date) if return_date else None,
            is_round_trip=is_round_trip,
            passengers=passengers,
            children=children,
            travel_class=travel_class,
        )
    except ValueError as exc:
        raise InvalidParams(str(exc)) from exc

    if not params.is_ready:
        # 与其发一个必然返回空结果的请求白烧额度，不如在这里就说清楚缺什么
        raise InvalidParams(
            "航班参数不完整或自相矛盾（往返必须有 return_date，且不得早于出发日期）"
        )

    query = params.to_serpapi(
        currency=currency or settings.default_currency,
        hl=hl or settings.default_hl,
        gl=gl if gl is not None else settings.serpapi_flights_gl,
    )
    if departure_token:
        # 带上 token 后返回的是该去程对应的返程航班，字段结构完全相同
        query["departure_token"] = departure_token

    payload = await (client or serpapi_client()).search(query)

    results = FlightSearchResults(
        best_flights=_parse_itineraries(payload.get("best_flights")),
        other_flights=_parse_itineraries(payload.get("other_flights")),
    )
    # 查返程时航段方向是反的（目的地 → 出发地），按去程校验会全部误杀
    if not departure_token:
        results = _drop_off_route(results, departure_id, arrival_id)
    return results


def _drop_off_route(
    results: FlightSearchResults, departure_id: str, arrival_id: str
) -> FlightSearchResults:
    """扔掉不是本次查询这条航线的行程。

    没有这道校验，一张"从别的城市出发"的机票会一路走到行程里，而它的落地时间
    还会被 route_planner 当成首日时间窗的起点——错得又贵又不显眼。
    """

    def keep(items: list[FlightItinerary]) -> list[FlightItinerary]:
        good = [it for it in items if it.flies_route(departure_id, arrival_id)]
        if dropped := len(items) - len(good):
            log.warning(
                "丢弃航线不符的方案",
                extra={"route": f"{departure_id}→{arrival_id}", "dropped": dropped},
            )
        return good

    return FlightSearchResults(
        best_flights=keep(results.best_flights),
        other_flights=keep(results.other_flights),
    )
