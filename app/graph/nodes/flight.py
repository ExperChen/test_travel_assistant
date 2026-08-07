"""航班分支：解析出发机场 → 解析到达机场 → 搜索并选定方案。

拆成三个节点而不是一个，是因为 LangGraph 在 resume 时会**从头重放整个节点**。
一个节点里放两个 interrupt，重放语义会变得难以推理；拆开之后每个节点最多一个
中断点，resume 的对应关系一目了然。重放时的 API 调用由本地 TTL 缓存吸收，
不会重复消耗额度（quota 里记的是 cache_hits）。
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.agents.flight_agent import (
    auto_pick_airport,
    fetch_return_departure,
    match_airport,
    resolve_airports,
    search_with_fallback,
)
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.graph.nodes._common import fail
from app.graph.state import TripState
from app.models.errors import ErrorCode, PlanWarning
from app.models.events import InterruptQuestion, QuestionOption
from app.models.flight import Airport, FlightBranch, FlightSearchParams

log = get_logger(__name__)

__all__ = ["flight_departure", "flight_arrival", "flight_search", "MAX_CANDIDATES"]

MAX_CANDIDATES = 3


def _airport_options(airports: list[Airport]) -> list[QuestionOption]:
    return [
        QuestionOption(
            key=a.id,
            label=a.label,
            detail={"name": a.name, "city": a.city, "distance": a.distance},
        )
        for a in airports
    ]


async def _resolve_leg(
    state: TripState, *, role: str, city: str, question_id: str
) -> tuple[Airport | None, list[Airport], list[PlanWarning], dict | None]:
    """解析一端机场。返回 (选中的机场, 候选列表, 警告, 失败补丁)。"""
    request = state["request"]

    try:
        options = await resolve_airports(city)
    except AppError as exc:
        return None, [], [], fail(exc.code, exc.message, city=city)

    if not options:
        return (
            None,
            [],
            [],
            fail(
                ErrorCode.CITY_NOT_FOUND,
                f"没有找到{role}「{city}」对应的机场",
                city=city,
            ),
        )

    chosen, warnings = auto_pick_airport(options, role=role, auto_select=request.auto_select)

    if chosen is None:
        question = InterruptQuestion.build(
            question_id,
            f"{city}有 {len(options)} 个机场，{role}选哪个？",
            _airport_options(options),
            timeout_s=settings.interrupt_timeout_s,
        )
        # 执行到这里会挂起；resume 之后本节点从头重放，interrupt() 直接返回用户的答案
        answer = interrupt(question.model_dump(mode="json"))
        chosen = match_airport(options, str(answer))
        if chosen is None:
            # API 层会先按 pending.options 校验，走到这里说明是超时自动填的默认值
            chosen = options[0]
            warnings = warnings + [
                PlanWarning.of(
                    "AIRPORT_FALLBACK",
                    f"没能识别{role}机场的选择「{answer}」，已改用 {chosen.id}",
                    stage="flight",
                )
            ]

    return chosen, options, warnings, None


async def flight_departure(state: TripState) -> dict:
    request = state["request"]
    chosen, options, warnings, failure = await _resolve_leg(
        state,
        role="出发地",
        city=request.departure_city,
        question_id="flight.departure_airport",
    )
    if failure:
        return failure

    branch = state.get("flight") or FlightBranch()
    params = branch.params.model_copy(
        update={"departure_airport_id": chosen.id, "departure_airport": chosen}  # type: ignore[union-attr]
    )
    patch: dict = {
        "flight": branch.model_copy(update={"params": params, "departure_options": options}),
        "phase": "flight",
    }
    if warnings:
        patch["warnings"] = warnings
    return patch


async def flight_arrival(state: TripState) -> dict:
    request = state["request"]
    chosen, options, warnings, failure = await _resolve_leg(
        state,
        role="目的地",
        city=request.destination_city,
        question_id="flight.arrival_airport",
    )
    if failure:
        return failure

    branch = state["flight"]
    params = branch.params.model_copy(
        update={
            "arrival_airport_id": chosen.id,  # type: ignore[union-attr]
            "arrival_airport": chosen,
            "departure_date": request.outbound_date,
            "return_date": request.return_date,
            "is_round_trip": True,
            "passengers": request.adults,
            "children": request.children,
            "travel_class": request.travel_class,
        }
    )
    patch: dict = {
        "flight": branch.model_copy(
            update={"params": params, "arrival_options": options, "arrival_airport": chosen}
        )
    }
    if warnings:
        patch["warnings"] = warnings
    return patch


def _itinerary_options(candidates) -> list[QuestionOption]:
    """选项必须写清**哪一班、几点飞**。

    原来只印「方案1 · 1200 · 2h30m · 直飞」——价格和时长一样时用户完全无从下手，
    而"几点起飞"恰恰是选航班时最关键的信息（早班机还是红眼，直接决定第一天
    还能不能玩）。
    """
    options: list[QuestionOption] = []
    for i, it in enumerate(candidates, 1):
        price = f"往返 ¥{it.price:.0f}" if it.price is not None else "价格暂无"
        hours, minutes = divmod(it.total_duration, 60)
        stops = "直飞" if it.stops == 0 else f"中转 {it.stops} 次"

        legs = it.flights
        if legs:
            first, last = legs[0], legs[-1]
            # time 是 "YYYY-MM-DD HH:MM"，只取时刻——日期在问题标题里已经有了
            depart = first.departure_airport.time[11:] or first.departure_airport.time
            arrive = last.arrival_airport.time[11:] or last.arrival_airport.time
            numbers = "/".join(f.flight_number for f in legs if f.flight_number)
            airline = first.airline or ""
            route = (
                f"{first.departure_airport.id} {depart} → {last.arrival_airport.id} {arrive}"
            )
            head = f"{airline} {numbers}".strip() or f"方案{i}"
        else:
            head, route = f"方案{i}", ""

        parts = [head, route, price, f"{hours}h{minutes:02d}m", stops]
        options.append(
            QuestionOption(
                key=str(i),
                label=" · ".join(p for p in parts if p),
                detail=it.model_dump(mode="json"),
            )
        )
    return options


async def flight_search(state: TripState) -> dict:
    request = state["request"]
    branch = state["flight"]
    locale = state["locale"]
    params: FlightSearchParams = branch.params

    try:
        results, used_params, fallback_warnings = await search_with_fallback(
            params,
            arrival_options=branch.arrival_options,
            departure_options=branch.departure_options,
            currency=locale.currency,
            hl=locale.hl,
        )
    except AppError as exc:
        route = f"{params.departure_airport_id}→{params.arrival_airport_id}"
        return fail(exc.code, exc.message, route=route)

    if results.is_empty:
        return fail(
            ErrorCode.NO_FLIGHTS,
            f"{params.departure_airport_id}→{params.arrival_airport_id} "
            f"{params.departure_date} 起飞的往返航班没有结果（已尝试放宽舱位与同城备选机场）",
            departure=params.departure_airport_id,
            arrival=params.arrival_airport_id,
        )

    candidates = (results.best_flights or results.other_flights)[:MAX_CANDIDATES]
    warnings = list(fallback_warnings)

    if request.auto_select or len(candidates) == 1:
        index = 0
        if len(candidates) > 1:
            warnings.append(
                PlanWarning.of(
                    "FLIGHT_AUTO_PICKED",
                    f"共 {len(candidates)} 个航班方案，已自动选择推荐的第 1 个",
                    stage="flight",
                )
            )
    else:
        question = InterruptQuestion.build(
            "flight.itinerary",
            f"找到 {len(candidates)} 个往返方案，选哪个？",
            _itinerary_options(candidates),
            timeout_s=settings.interrupt_timeout_s,
        )
        answer = str(interrupt(question.model_dump(mode="json")))
        index = int(answer) - 1 if answer.isdigit() and 0 < int(answer) <= len(candidates) else 0

    selected = candidates[index]
    arrive_at = selected.arrives_at

    # best_flights 里只有去程；"离开目的地"的时刻要拿选定去程的 departure_token
    # 再查一次返程才有。这是 route_planner 末日时间窗的硬依赖。
    depart_at, return_warning = await fetch_return_departure(
        used_params,
        selected.departure_token,
        currency=locale.currency,
        hl=locale.hl,
    )
    if return_warning:
        warnings.append(return_warning)

    patch: dict = {
        "flight": branch.model_copy(
            update={
                "params": used_params,
                "candidates": candidates,
                "selected_index": index,
                "arrival_airport": used_params.arrival_airport,
                "arrive_at": arrive_at,
                "depart_at": depart_at,
            }
        ),
        "phase": "attraction",
    }
    if not arrive_at:
        warnings.append(
            PlanWarning.of(
                "FLIGHT_NO_ARRIVAL_TIME",
                "航班数据里没有可解析的落地时间，首日行程将按默认时段安排",
                stage="flight",
            )
        )
    if warnings:
        patch["warnings"] = warnings

    log.info(
        "航班已选定",
        extra={
            "route": f"{used_params.departure_airport_id}→{used_params.arrival_airport_id}",
            "candidates": len(candidates),
            "picked": index + 1,
            "price": selected.price,
        },
    )
    return patch
