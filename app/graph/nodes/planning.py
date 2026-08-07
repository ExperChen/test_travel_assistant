"""route_planner：把航班、酒店、景点合成逐日行程。

依赖顺序上这是最后一环——它同时需要落地/返程时刻（航班）、住哪（酒店）、
去哪些地方（景点）。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.agents.route_planner import (
    DAY_TRIP_RADIUS_M,
    assign_days,
    build_itinerary,
    fetch_leg,
    split_day_trips,
)
from app.config import settings
from app.core.dates import build_day_windows
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.graph.nodes._common import fail
from app.graph.state import TripState
from app.models.common import GeoPoint
from app.models.errors import ErrorCode, PlanWarning
from app.models.route import DayItem, RouteLeg

log = get_logger(__name__)

__all__ = ["route_planner", "DEFAULT_AIRPORT_COMMUTE_MIN"]

DEFAULT_AIRPORT_COMMUTE_MIN = 60
"""查不到机场↔酒店路线时的兜底通勤时长。

宁可高估：估短了会把首日行程排在还没到酒店的时候，估长了只是少排半小时。
"""

FALLBACK_ARRIVE_TIME = time(12, 0)
"""连落地时刻都没有时，按中午落地保守处理（首日只排半天）。"""


async def _airport_commute(
    airport: GeoPoint | None,
    hotel: GeoPoint,
    *,
    mode,
    citycode: str,
) -> tuple[int, RouteLeg | None]:
    if airport is None:
        return DEFAULT_AIRPORT_COMMUTE_MIN, None
    try:
        leg = await fetch_leg(airport, hotel, mode=mode, citycode=citycode)
    except AppError as exc:
        log.warning("机场通勤查询失败", extra={"err": exc.message})
        return DEFAULT_AIRPORT_COMMUTE_MIN, None
    return leg.duration_min, leg


def _branches_ready(state: TripState) -> bool:
    """两条并行分支是否都已产出结果。"""
    flight = state.get("flight")
    hotel = state.get("hotel")
    return bool(flight and flight.selected and hotel and hotel.selected)


async def route_planner(state: TripState) -> dict:
    # 汇合节点的守卫：并行下另一条分支可能已经失败，而本分支的条件边看到的
    # 状态未必包含那次写入。已经失败就别再花额度编排一份用不上的行程。
    if state.get("errors"):
        log.info("上游已失败，跳过行程编排")
        return {}

    # LangGraph 的汇合语义是「每有一条上游在某个 superstep 完成就触发一次」，
    # 两条分支长度不一致时本节点必然被触发多次（静态边也一样）。没有这两道守卫：
    #   · 第一次触发时航班还没跑完 → 会用缺失的落地时刻算出一份错的行程；
    #   · 第二次触发 → 整套高德路径查询和 LLM 调用再花一遍。
    if state.get("itinerary"):
        log.info("行程已编排过，跳过重复触发")
        return {}
    if not _branches_ready(state):
        log.info("另一条分支还没跑完，等下一次触发")
        return {}

    request = state["request"]
    city = state["dest_city"]
    flight = state.get("flight")
    hotel_branch = state.get("hotel")
    attractions = state.get("attractions")

    hotel = hotel_branch.selected if hotel_branch else None
    if hotel is None or hotel.location is None:
        return fail(ErrorCode.NO_HOTELS, "没有选定的酒店，无法编排行程", city=city.name)

    selected = attractions.selected if attractions else []
    if not selected:
        return fail(ErrorCode.NO_ATTRACTIONS, "没有可编排的景点", city=city.name)

    warnings: list[PlanWarning] = []
    hotel_point = hotel.location.as_gcj02()

    # ---- 时间窗：首日从落地起算，末日在返程起飞前收尾 ----
    arrive_at = flight.arrive_at if flight else None
    depart_at = flight.depart_at if flight else None
    if arrive_at is None:
        arrive_at = datetime.combine(request.outbound_date, FALLBACK_ARRIVE_TIME)
        warnings.append(
            PlanWarning.of(
                "ARRIVAL_TIME_ASSUMED",
                f"没有落地时刻，首日按 {FALLBACK_ARRIVE_TIME:%H:%M} 到达保守安排",
                stage="planning",
            )
        )
    if depart_at is None:
        depart_at = datetime.combine(request.return_date, time(9, 0))

    # SerpAPI 只给机场 IATA 三字码，不给坐标。首末日时间窗只需要「机场到酒店要多久」，
    # 拿不到就用兜底值。要精确化的话，应当用 poi_keyword(f"{城市}机场") 补一次坐标。
    commute_min, arrival_leg = await _airport_commute(
        None, hotel_point, mode=request.transport, citycode=city.citycode
    )
    if arrival_leg is None:
        warnings.append(
            PlanWarning.of(
                "AIRPORT_COMMUTE_ESTIMATED",
                f"机场到酒店的通勤按 {commute_min} 分钟估算",
                stage="planning",
            )
        )

    windows = build_day_windows(
        arrive_at,
        depart_at,
        airport_to_hotel_min=commute_min,
        hotel_to_airport_min=commute_min,
        checkin_buffer_min=settings.checkin_buffer_min,
        predeparture_buffer_min=settings.predeparture_buffer_min,
        day_start=time(settings.day_start_hour, 0),
        day_end=time(settings.day_end_hour, 0),
        departure_day_start=time(settings.departure_day_start_hour, 30),
    )

    usable = [w for w in windows if w.is_usable]
    if not usable:
        return fail(
            ErrorCode.INVALID_PARAMS,
            "落地与返程之间没有可用于游览的时间",
            arrive_at=arrive_at.isoformat(),
            depart_at=depart_at.isoformat(),
        )

    # ---- 先把「不适合当日往返」的挑出去 ----
    # 知名度排序会把远郊景区排得很靠前（成都：安仁古镇 50km、青城后山 65km）。
    # 塞进市内行程会得到「通勤 8.8 小时、游玩 2.9 小时」这种没法用的安排。
    # 用直线距离筛，零额度。
    nearby, day_trips = split_day_trips(selected, hotel_point)
    if day_trips:
        names = "、".join(a.name for a in day_trips[:5])
        warnings.append(
            PlanWarning.of(
                "DAY_TRIP_ONLY",
                f"{len(day_trips)} 个景点距住处超过 {DAY_TRIP_RADIUS_M // 1000} 公里，"
                f"更适合单独安排一日游，已移入备选：{names}",
                stage="planning",
            )
        )

    # ---- 分天 + 编排 ----
    buckets = assign_days(nearby, hotel_point, len(usable))
    try:
        itinerary = await build_itinerary(
            usable,
            buckets,
            hotel_point,
            hotel.name,
            mode=request.transport,
            citycode=city.citycode,
        )
    except AppError as exc:
        return fail(exc.code, exc.message, city=city.name)

    # 远郊的那批也要出现在备选里，别让用户以为它们凭空消失了
    itinerary = itinerary.model_copy(
        update={"unscheduled": [*itinerary.unscheduled, *day_trips]}
    )

    itinerary = _prepend_arrival(itinerary, arrive_at, commute_min, hotel, hotel_point)

    # 用户点名的景点掉进备选，是最糟的失败方式——他会拿到一份看起来正常的行程，
    # 直到出发前才发现没安排。排不进去可以，但必须**明说**。
    if stranded := [a.name for a in itinerary.unscheduled if a.must_visit]:
        warnings.append(
            PlanWarning.of(
                "MUST_VISIT_IMPOSSIBLE",
                f"「{'、'.join(stranded)}」所需时间超出剩余行程（营业时间或返程航班所限），"
                "未能安排；可以考虑加天数或换出发/返程时刻",
                stage="planning",
            )
        )

    if itinerary.unscheduled:
        warnings.append(
            PlanWarning.of(
                "ATTRACTIONS_UNSCHEDULED",
                f"有 {len(itinerary.unscheduled)} 个景点没能排进时间窗，已作为备选列出",
                stage="planning",
            )
        )

    unusable_days = len(windows) - len(usable)
    if unusable_days:
        warnings.append(
            PlanWarning.of(
                "DAYS_WITHOUT_TIME",
                f"有 {unusable_days} 天因航班时刻没有可用游览时间",
                stage="planning",
            )
        )

    patch: dict = {"itinerary": itinerary, "phase": "summarizing"}
    if warnings:
        patch["warnings"] = warnings

    log.info(
        "行程已编排",
        extra={
            "days": len(itinerary.days),
            "scheduled": sum(
                1 for d in itinerary.days for i in d.items if i.kind == "attraction"
            ),
            "unscheduled": len(itinerary.unscheduled),
            "commute_min": itinerary.total_commute_min,
        },
    )
    return patch


def _prepend_arrival(itinerary, arrive_at, commute_min, hotel, hotel_point):
    """首日开头补上「落地 → 到店入住」，让行程从下飞机那一刻开始读得通。"""
    if not itinerary.days:
        return itinerary

    first = itinerary.days[0]
    if first.day != arrive_at.date():
        return itinerary

    check_in = arrive_at + timedelta(minutes=commute_min)
    arrival_items = [
        DayItem(
            kind="airport",
            ref_id="arrival",
            name="落地",
            location=hotel_point,  # 没有机场坐标，用酒店占位，仅用于时间轴展示
            start_time=arrive_at,
            end_time=arrive_at,
        ),
        DayItem(
            kind="hotel",
            ref_id="hotel",
            name=f"入住 {hotel.name}",
            location=hotel_point,
            start_time=check_in,
            end_time=first.window_start,
        ),
    ]
    updated = first.model_copy(update={"items": arrival_items + first.items})
    return itinerary.model_copy(update={"days": [updated, *itinerary.days[1:]]})