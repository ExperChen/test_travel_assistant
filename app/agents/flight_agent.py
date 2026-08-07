"""机场解析与航班搜索（架构文档 §5.1）。

**这里没有 LLM 循环，是刻意的。** `docs/flight-agent/flight-react-agent-design.md`
描述的 Thought/Action 循环是为对话式交互设计的：Agent 要逐轮追问出发地、目的地、
日期、人数、舱位。但 D2 决策选了「表单起手」——这些参数在进入本模块时已经齐全，
ReAct 循环剩下的工作只有「机场有歧义时问用户」和「搜不到时换个条件重试」，
两者都是确定性逻辑。用 LLM 跑这段只会带来延迟、成本和不确定性，换不来任何东西。

对话式模式（用户直接说"我想订张去东京的票"）真要做时，ReAct 循环应当加在本模块
**之上**，负责把自然语言熬成 FlightSearchParams，然后仍然复用这里的函数。
"""

from __future__ import annotations

import re
from datetime import datetime, time

from app.core.logging import get_logger
from app.models.errors import PlanWarning
from app.models.flight import Airport, CitySuggestion, FlightSearchParams, FlightSearchResults
from app.tools.serpapi_flights import flights_autocomplete, flights_search

log = get_logger(__name__)

__all__ = [
    "looks_like_iata",
    "flatten_airports",
    "resolve_airports",
    "auto_pick_airport",
    "match_airport",
    "search_with_fallback",
    "fetch_return_departure",
    "MAX_SEARCH_ATTEMPTS",
    "FALLBACK_RETURN_TIME",
]

_IATA_RE = re.compile(r"^[A-Za-z]{3}$")

MAX_SEARCH_ATTEMPTS = 4
"""兜底重试上限。每次尝试烧 1 次 SerpAPI 额度（月配额 250），不能无限试。"""


def looks_like_iata(text: str) -> bool:
    """用户直接填了三字码就不必再走补全，省一次额度。"""
    return bool(_IATA_RE.match((text or "").strip()))


_RAIL_TERMS = ("hbf", "railway", "rail station", "火车站", "地铁站", "客运站")
"""Google 的机场列表里混着地面交通枢纽，`ZAQ`(Nuremberg Hbf) / `ZRB`(Frankfurt Hbf)
就是。它们进不了航班搜索，只会占掉用户的选择位。"""


def _is_rail_station(airport: Airport) -> bool:
    name = airport.name.lower()
    return any(term in name for term in _RAIL_TERMS)


def flatten_airports(suggestions: list[CitySuggestion]) -> list[Airport]:
    """取**第一个带机场的建议**作为锚点城市，只收属于它的机场。

    **不能把所有建议的机场拍平合并**——autocomplete 返回的是多个城市建议，
    其中不乏子串误命中：实测「上海」会返回「德国上海德」（纽伦堡 NUE/ZAQ）和
    「德国上海因巴赫」（法兰克福 FRA/HHN/ZRB），一共 7 个候选里 5 个在德国；
    「北京」更夸张，11 个里跟着芝加哥 ORD、圣路易斯 STL、法戈 FAR。
    这些地名是德文/英文地名的中译，恰好包含「上海」「北京」二字。

    判据用 `city_id`（Google 的实体 ID）等值比较，不做名字模糊匹配——
    锚点建议的 `id` 和它名下机场的 `city_id` 是同一个值。
    """
    anchor = next((c for c in suggestions if any(a.id for a in c.airports)), None)
    if anchor is None:
        return []

    seen: set[str] = set()
    out: list[Airport] = []
    for airport in anchor.airports:
        # anchor.id 为空时退回"只要是锚点建议名下的就算"，不放宽到其它建议
        if anchor.id and airport.city_id and airport.city_id != anchor.id:
            continue
        if not airport.id or airport.id in seen or _is_rail_station(airport):
            continue
        seen.add(airport.id)
        out.append(airport)
    # 近的排前面：auto_select 取第一个，离市区近的机场通勤成本更低。
    # 万一它没有合适航班，search_with_fallback 会自动换到后面的机场。
    out.sort(key=_distance_km)
    return out


_DISTANCE_RE = re.compile(r"([\d.]+)")


def _distance_km(airport: Airport) -> float:
    """把 '20英里' / '8 mi' / '32公里' 归一成公里；解析不出来的排到最后。"""
    text = airport.distance.strip().lower()
    if not (m := _DISTANCE_RE.search(text)):
        return float("inf")
    try:
        value = float(m.group(1))
    except ValueError:
        return float("inf")
    is_mile = "英里" in text or "mi" in text
    return value * 1.609 if is_mile else value


async def resolve_airports(city_or_iata: str, *, client=None) -> list[Airport]:
    """城市名 → 候选机场列表；已经是 IATA 三字码则直接返回单条。"""
    text = (city_or_iata or "").strip()
    if looks_like_iata(text):
        code = text.upper()
        return [Airport(name=code, id=code)]

    suggestions = await flights_autocomplete(text, client=client)
    return flatten_airports(suggestions)


def auto_pick_airport(
    options: list[Airport], *, role: str, auto_select: bool
) -> tuple[Airport | None, list[PlanWarning]]:
    """能自动定下来就定，定不下来返回 None 交给中断问用户。

    返回 None 表示"需要用户选"，不是错误。
    """
    if not options:
        return None, []

    if len(options) == 1:
        return options[0], []

    if auto_select:
        return options[0], [
            PlanWarning.of(
                "AIRPORT_AUTO_PICKED",
                f"{role}有 {len(options)} 个机场，已自动选择 {options[0].id}（{options[0].name}）",
                stage="flight",
            )
        ]

    return None, []


def match_airport(options: list[Airport], answer: str) -> Airport | None:
    """把用户的回答（IATA 代码或序号）映射回机场对象（数据规范 §3.2）。"""
    text = (answer or "").strip()
    if not text:
        return None

    for airport in options:
        if airport.id.upper() == text.upper():
            return airport

    if text.isdigit():
        index = int(text) - 1  # 展示给用户的序号是 1-based
        if 0 <= index < len(options):
            return options[index]
    return None


async def search_with_fallback(
    params: FlightSearchParams,
    *,
    arrival_options: list[Airport] | None = None,
    departure_options: list[Airport] | None = None,
    currency: str | None = None,
    hl: str | None = None,
    client=None,
) -> tuple[FlightSearchResults, FlightSearchParams, list[PlanWarning]]:
    """搜航班；搜空了就按固定顺序放宽条件重试。

    重试链的取舍（与上游文档 §7 的建议有出入，理由写在这里）：

    1. **放宽舱位** —— 严格是原条件的超集，不会给出用户没要的东西，最安全；
    2. **换同城备选机场** —— 仍在同一个城市，属于用户会自己做的调整；
    3. 到此为止。

    上游文档还建议「日期 ±3 天」，这里**故意不做**：悄悄挪动用户的出行日期会
    改掉行程本身（酒店、请假、同行人都对不上），属于用户才能拍板的事。搜不到时
    应当带着建议报 NO_FLIGHTS，由用户决定要不要改期。
    """
    warnings: list[PlanWarning] = []
    attempts: list[tuple[FlightSearchParams, str]] = [(params, "")]

    if params.travel_class:
        attempts.append(
            (
                params.model_copy(update={"travel_class": None}),
                f"{params.travel_class} 舱没有结果，已放宽为搜索全部舱位",
            )
        )

    for alt in (arrival_options or [])[1:]:
        if alt.id != params.arrival_airport_id:
            attempts.append(
                (
                    params.model_copy(
                        update={"arrival_airport_id": alt.id, "arrival_airport": alt}
                    ),
                    f"原到达机场没有结果，已改用同城的 {alt.id}（{alt.name}）",
                )
            )
            break

    for alt in (departure_options or [])[1:]:
        if alt.id != params.departure_airport_id:
            attempts.append(
                (
                    params.model_copy(
                        update={"departure_airport_id": alt.id, "departure_airport": alt}
                    ),
                    f"原出发机场没有结果，已改用同城的 {alt.id}（{alt.name}）",
                )
            )
            break

    results = FlightSearchResults()
    used = params
    for attempt, (candidate_params, note) in enumerate(attempts[:MAX_SEARCH_ATTEMPTS]):
        results = await flights_search(
            departure_id=candidate_params.departure_airport_id or "",
            arrival_id=candidate_params.arrival_airport_id or "",
            outbound_date=candidate_params.departure_date,  # type: ignore[arg-type]
            return_date=candidate_params.return_date,
            is_round_trip=bool(candidate_params.is_round_trip),
            passengers=candidate_params.passengers,
            children=candidate_params.children,
            travel_class=candidate_params.travel_class,
            currency=currency,
            hl=hl,
            client=client,
        )
        used = candidate_params
        if not results.is_empty:
            if note:
                warnings.append(PlanWarning.of("FLIGHT_FALLBACK", note, stage="flight"))
            return results, used, warnings

        next_note = attempts[attempt + 1][1] if attempt + 1 < len(attempts) else "无"
        log.info("航班搜索为空，准备兜底", extra={"attempt": attempt + 1, "next": next_note})

    return results, used, warnings


async def fetch_return_departure(
    params: FlightSearchParams,
    departure_token: str,
    *,
    currency: str | None = None,
    hl: str | None = None,
    client=None,
) -> tuple[datetime | None, PlanWarning | None]:
    """查出返程航班的起飞时间。

    **为什么需要单独一次查询**：SerpAPI 往返搜索返回的 `best_flights` 里只有**去程**
    航段。要拿到返程，必须带着选定去程的 `departure_token` 再查一次，返回的才是与之
    配对的返程列表。

    这个时间是 route_planner 的硬依赖——末日行程必须在返程起飞前收尾。拿不到时退回
    "返程日期 + 保守时刻"，宁可少排半天也不能把行程排到飞机起飞之后。
    """
    if not departure_token or not params.return_date:
        return _fallback_return(params)

    try:
        results = await flights_search(
            departure_id=params.departure_airport_id or "",
            arrival_id=params.arrival_airport_id or "",
            outbound_date=params.departure_date,  # type: ignore[arg-type]
            return_date=params.return_date,
            is_round_trip=True,
            passengers=params.passengers,
            children=params.children,
            travel_class=params.travel_class,
            departure_token=departure_token,
            currency=currency,
            hl=hl,
            client=client,
        )
    except Exception as exc:  # noqa: BLE001 —— 返程查不到不该让整次规划失败
        log.warning("返程航班查询失败，改用保守时刻", extra={"err": str(exc)})
        return _fallback_return(params)

    for itinerary in results.best_flights or results.other_flights:
        if (departs := itinerary.departs_at) is not None:
            return departs, None

    return _fallback_return(params)


FALLBACK_RETURN_TIME = time(9, 0)
"""拿不到真实返程时刻时的保守假设：当天上午 9 点起飞。

宁可保守——按 9 点算，末日几乎排不进景点；要是乐观地假设晚上起飞，而实际航班在
上午，用户就会拿到一份"飞机起飞后还在逛景点"的行程。
"""


def _fallback_return(params: FlightSearchParams) -> tuple[datetime | None, PlanWarning | None]:
    if not params.return_date:
        return None, None
    return (
        datetime.combine(params.return_date, FALLBACK_RETURN_TIME),
        PlanWarning.of(
            "RETURN_TIME_ESTIMATED",
            f"没能查到返程航班的确切起飞时刻，末日行程按 {params.return_date} "
            f"{FALLBACK_RETURN_TIME:%H:%M} 起飞保守安排",
            stage="flight",
        ),
    )
