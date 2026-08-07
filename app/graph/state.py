"""LangGraph 全局状态（架构文档 §4.1）。

并行分支写状态的唯一安全做法：各分支只写自己的 key，共享的累加字段用
`Annotated[list, add]` 声明 reducer。否则两个分支同时返回 warnings 时会互相覆盖。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Literal, TypedDict

from app.models.attraction import AttractionBranch
from app.models.common import CityRef, LocaleCtx, QuotaCounter
from app.models.errors import ApiError, PlanWarning
from app.models.events import InterruptQuestion
from app.models.flight import FlightBranch
from app.models.hotel import HotelBranch
from app.models.route import Itinerary
from app.models.trip import TripPlan, TripRequest, TripStatus

__all__ = ["TripState", "Phase", "initial_state", "to_plan"]

Phase = Literal[
    "intake",
    "resolve_city",
    "flight",
    "attraction",
    "hotel",
    "planning",
    "summarizing",
    "done",
    "failed",
]

# 两条分支并行时会在同一个 superstep 里各写一次 phase/status，默认的
# LastValue 通道会直接报 InvalidUpdateError。这两个 reducer 定义"谁赢"：
# 进度取更靠后的那个，失败永远压过一切。
_PHASE_RANK: dict[str, int] = {
    "intake": 0,
    "resolve_city": 1,
    "flight": 2,
    "attraction": 2,
    "hotel": 3,
    "planning": 4,
    "summarizing": 5,
    "done": 6,
    "failed": 99,
}


def latest_phase(current: Phase | None, incoming: Phase) -> Phase:
    if current is None:
        return incoming
    return incoming if _PHASE_RANK[incoming] >= _PHASE_RANK[current] else current


def worst_status(current: TripStatus | None, incoming: TripStatus) -> TripStatus:
    """一条分支失败了，整次规划就是失败的——哪怕另一条跑完了。"""
    if current == "failed" or incoming == "failed":
        return "failed"
    return incoming


class TripState(TypedDict, total=False):
    # ---- 输入：intake 归一后写入，全程只读 ----
    trip_id: str
    request: TripRequest
    locale: LocaleCtx

    # ---- 目的地 ----
    dest_city: CityRef

    # ---- 各分支产出 ----
    flight: FlightBranch
    attractions: AttractionBranch
    hotel: HotelBranch
    itinerary: Itinerary | None
    summary: str | None

    # ---- 控制面 ----
    phase: Annotated[Phase, latest_phase]
    status: Annotated[TripStatus, worst_status]
    pending: list[InterruptQuestion]
    """当前挂起的问题。

    是列表而不是单个：航班分支与「景点→酒店」分支并行跑，两边可能在同一个
    superstep 里各自中断一次，用户会同时看到两个问题。"""

    # 只有这两个字段会被多个分支同时写，必须带 reducer
    warnings: Annotated[list[PlanWarning], add]
    errors: Annotated[list[ApiError], add]

    quota: QuotaCounter


def to_plan(state: TripState) -> TripPlan:
    """图状态 → 对外契约。

    `pending` 不进 TripPlan：挂起的问题走 SSE 的 `question` 事件推给前端，
    在快照里再塞一份只会让两处不同步。
    """
    errors = state.get("errors") or []
    attractions = state.get("attractions")
    return TripPlan(
        trip_id=state["trip_id"],
        status=state.get("status", "running"),
        request=state["request"],
        locale=state.get("locale") or LocaleCtx(),
        destination=state.get("dest_city"),
        flights=state.get("flight"),
        hotel=state.get("hotel"),
        attractions=attractions.selected if attractions else [],
        itinerary=state.get("itinerary"),
        summary=state.get("summary"),
        warnings=state.get("warnings") or [],
        error=errors[0] if errors else None,
        quota=state.get("quota") or QuotaCounter(),
    )


def initial_state(trip_id: str, request: TripRequest) -> TripState:
    return TripState(
        trip_id=trip_id,
        request=request,
        phase="intake",
        status="running",
        pending=[],
        warnings=[],
        errors=[],
        quota=QuotaCounter(),
    )
