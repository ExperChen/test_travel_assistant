"""行程与路线模型（架构文档 §7.2）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.attraction import Attraction
from app.models.common import GeoPoint

__all__ = [
    "TransportMode",
    "ItemKind",
    "RouteLeg",
    "DistanceResult",
    "DayItem",
    "DayPlan",
    "Itinerary",
]

TransportMode = Literal["transit", "driving", "walking"]
ItemKind = Literal["airport", "hotel", "attraction", "meal"]


class RouteLeg(BaseModel):
    """两个行程点之间的一段交通。

    `from_ref` / `to_ref` 由 route_planner 填——路径工具只认坐标，不认行程点 id。
    """

    from_ref: str = ""
    to_ref: str = ""
    mode: TransportMode
    distance_m: int = 0
    duration_min: int = 0
    cost_cny: float | None = Field(default=None, description="公交票价 / 驾车过路费")
    taxi_cost_cny: float | None = None
    detail: str = Field(default="", description="如「地铁2号线 → 换乘10号线，步行 850m」")
    restriction: str = Field(default="", description="驾车限行提示")


class DistanceResult(BaseModel):
    """批量距离测量的单条结果。

    高德对单个起点也可能失败（在海上/矿区/境外），这时 distance/duration 无意义，
    必须用 `ok` 判断后再参与排序，否则会把不可达的点排到最前面。
    """

    origin_index: int = Field(description="对应入参 origins 的下标，0-based")
    distance_m: int | None = None
    duration_s: int | None = None
    error_code: str = ""
    error_info: str = ""

    @property
    def ok(self) -> bool:
        return self.distance_m is not None and not self.error_code

    @property
    def duration_min(self) -> int | None:
        return None if self.duration_s is None else round(self.duration_s / 60)


class DayItem(BaseModel):
    kind: ItemKind
    ref_id: str
    name: str
    location: GeoPoint
    start_time: datetime
    end_time: datetime
    ticket_cost_cny: float | None = Field(
        default=None, description="景点门票参考价，用于汇总总花费"
    )

    @property
    def duration_min(self) -> int:
        return max(0, int((self.end_time - self.start_time).total_seconds() // 60))


class DayPlan(BaseModel):
    day_index: int
    day: date
    window_start: datetime
    window_end: datetime
    items: list[DayItem] = Field(default_factory=list)
    legs: list[RouteLeg] = Field(default_factory=list)

    @property
    def total_commute_min(self) -> int:
        return sum(leg.duration_min for leg in self.legs)

    @property
    def is_empty(self) -> bool:
        return not self.items


class Itinerary(BaseModel):
    days: list[DayPlan] = Field(default_factory=list)
    unscheduled: list[Attraction] = Field(
        default_factory=list, description="时间窗塞不下的景点，作为备选回传"
    )

    @property
    def total_commute_min(self) -> int:
        return sum(d.total_commute_min for d in self.days)

    @property
    def total_transport_cost_cny(self) -> float:
        return sum(leg.cost_cny or 0.0 for d in self.days for leg in d.legs)

    @property
    def total_ticket_cost_cny(self) -> float:
        # 同一景点若跨天出现（如分两次逛的大景区）只计一次门票
        seen: dict[str, float] = {}
        for day in self.days:
            for item in day.items:
                if item.kind == "attraction" and item.ticket_cost_cny:
                    seen[item.ref_id] = item.ticket_cost_cny
        return sum(seen.values())

    def totals(self) -> dict[str, float]:
        return {
            "commute_min": self.total_commute_min,
            "transport_cost_cny": round(self.total_transport_cost_cny, 2),
            "ticket_cost_cny": round(self.total_ticket_cost_cny, 2),
        }
