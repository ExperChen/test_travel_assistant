"""行程请求与结果的顶层契约（架构文档 §7.1 / §7.2）。"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.dates import trip_day_count
from app.models.attraction import Attraction
from app.models.common import CityRef, LocaleCtx, QuotaCounter
from app.models.errors import ApiError, PlanWarning
from app.models.flight import FlightBranch, TravelClass
from app.models.hotel import HotelBranch
from app.models.route import Itinerary, TransportMode

__all__ = ["Pace", "TripStatus", "TripRequest", "TripPlan", "CostBreakdown"]

Pace = Literal["relaxed", "standard", "packed"]
TripStatus = Literal["running", "waiting_input", "done", "failed"]


class TripRequest(BaseModel):
    """前端表单提交的内容。目的地必须能解析到中国大陆城市（D1 决策）。"""

    departure_city: str = Field(min_length=1, description="城市名或 IATA 三字码")
    destination_city: str = Field(min_length=1)
    outbound_date: date
    return_date: date

    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    children_ages: list[int] = Field(default_factory=list)
    travel_class: TravelClass = "economy"

    budget_per_night: int | None = Field(
        default=None, ge=0, description="CNY，映射到 hotels 的 max_price"
    )
    hotel_class: list[Literal[2, 3, 4, 5]] = Field(default_factory=list)

    must_visit: list[str] = Field(default_factory=list, description="强制进入行程的景点名")
    avoid: list[str] = Field(default_factory=list)

    pace: Pace = "standard"
    transport: TransportMode = "transit"
    auto_select: bool = Field(
        default=False, description="true = 全自动，不产生任何中断问题"
    )

    @field_validator("departure_city", "destination_city")
    @classmethod
    def _strip(cls, v: str) -> str:
        # min_length 校验的是未去空格的原串，"   " 能混过去，必须在这里补一刀
        if not (stripped := v.strip()):
            raise ValueError("城市名不能为空")
        return stripped

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.return_date <= self.outbound_date:
            raise ValueError("return_date 必须晚于 outbound_date")
        if len(self.children_ages) != self.children:
            raise ValueError("children_ages 的个数必须等于 children")
        if any(not 1 <= a <= 17 for a in self.children_ages):
            raise ValueError("儿童年龄必须在 1~17 之间，1 岁以下填 1")
        return self

    @property
    def nights(self) -> int:
        return (self.return_date - self.outbound_date).days

    @property
    def duration_days(self) -> int:
        """**用户口径**的行程天数：返程日 − 出发日。1 号来、5 号回 = 4 天。

        和 `travel_days` 的区别：那个是行程横跨的**日历天数**（上例为 5，
        1/2/3/4/5 号都要排时间窗），排期算法用它；对用户说话一律用这个。
        数值上恒等于 `nights`——住几晚就是几天，两个名字留着是为了让调用点
        自己说清在讲哪件事。
        """
        return self.nights

    @property
    def travel_days(self) -> int:
        """行程横跨的日历天数（含落地日与返程日），排期用。

        ⚠️ 不是用户说的"N 天"，那个看 `duration_days`。
        """
        return trip_day_count(self.outbound_date, self.return_date)


class CostBreakdown(BaseModel):
    """预估花费：**只算机票和住宿**。

    不计市内交通——金额小、误差大（票价随换乘方案变，出租车更没准），
    混进总价只会拉低整个数字的可信度。也不计门票，那个数据本来就基本查不到。

    任一分项缺失时 `total_cny` 为 None，缺哪项写在 `missing` 里。
    **绝不把缺失当 0**：把「酒店没标价」算成「住宿 ¥0」，总价就成了谎报。
    """

    flight_cny: float | None = None
    hotel_cny: float | None = None
    nights: int = 0
    nightly_cny: float | None = Field(default=None, description="用于展示「4 晚 × ¥520」")
    missing: list[str] = Field(default_factory=list)

    @property
    def total_cny(self) -> float | None:
        if self.missing:
            return None
        return round((self.flight_cny or 0.0) + (self.hotel_cny or 0.0), 2)


class TripPlan(BaseModel):
    """一次规划的完整快照，`GET /trips/{id}` 与 SSE 的 `done` 事件都返回它。"""

    trip_id: str
    status: TripStatus = "running"
    request: TripRequest
    locale: LocaleCtx = Field(default_factory=LocaleCtx)

    destination: CityRef | None = None
    flights: FlightBranch | None = None
    hotel: HotelBranch | None = None
    attractions: list[Attraction] = Field(default_factory=list)
    itinerary: Itinerary | None = None
    summary: str | None = None

    warnings: list[PlanWarning] = Field(default_factory=list)
    error: ApiError | None = None
    quota: QuotaCounter = Field(default_factory=QuotaCounter)

    @property
    def is_terminal(self) -> bool:
        return self.status in ("done", "failed")

    @property
    def costs(self) -> CostBreakdown:
        """机票 + 住宿。SerpAPI 给的机票价已是往返总价、已含全部乘客，不要再乘。"""
        nights = self.request.nights
        breakdown = CostBreakdown(nights=nights)

        selected_flight = self.flights.selected if self.flights else None
        if selected_flight is not None and selected_flight.price is not None:
            breakdown.flight_cny = float(selected_flight.price)
        else:
            breakdown.missing.append("机票")

        hotel = self.hotel.selected if self.hotel else None
        if hotel is not None and hotel.total_price is not None:
            breakdown.hotel_cny = float(hotel.total_price)
            breakdown.nightly_cny = round(hotel.total_price / nights, 2) if nights else None
        elif hotel is not None and hotel.nightly_price is not None:
            breakdown.nightly_cny = float(hotel.nightly_price)
            breakdown.hotel_cny = round(hotel.nightly_price * nights, 2)
        else:
            breakdown.missing.append("住宿")

        return breakdown
