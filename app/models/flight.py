"""航班相关模型。

严格对齐 `docs/flight-agent/flight-data-specification.md` §2 的 TypeScript 定义，
只做等价翻译，不增删字段语义。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "TravelClass",
    "Airport",
    "CitySuggestion",
    "AirportTime",
    "FlightLeg",
    "Layover",
    "CarbonEmissions",
    "FlightItinerary",
    "FlightSearchResults",
    "FlightSearchParams",
    "FlightBranch",
]

TravelClass = Literal["economy", "premium_economy", "business", "first"]

_TRAVEL_CLASS_CODE: dict[TravelClass, int] = {
    "economy": 1,
    "premium_economy": 2,
    "business": 3,
    "first": 4,
}
"""SerpAPI google_flights 的 travel_class 实际收的是数字码 1~4。

注意：`docs/flight-agent/serpapi-google-flights-api.md` §4 把它写成了字符串枚举，
与真实接口不符。内部一律用字符串（可读、好校验），只在 to_serpapi() 出口处转成
数字码。这一处差异需要在联调（`-m live`）时确认，若真接受字符串再改回去。
"""

_AIRPORT_TIME_FMT = "%Y-%m-%d %H:%M"


class Airport(BaseModel):
    """Autocomplete 返回的 `airports[]` 元素。"""

    name: str
    id: str = Field(description="IATA 三字码，如 JFK —— 核心标识")
    city: str = ""
    city_id: str = ""
    distance: str = Field(default="", description="距市中心距离，原样保留如 '14 mi'")

    @field_validator("id")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @property
    def label(self) -> str:
        """展示格式：`[JFK] John F. Kennedy International Airport - 距市中心 14 mi`"""
        tail = f" - 距市中心 {self.distance}" if self.distance else ""
        return f"[{self.id}] {self.name}{tail}"


class CitySuggestion(BaseModel):
    """Autocomplete 返回的 `suggestions[]` 元素：一个城市 + 其下属机场。"""

    name: str
    id: str = ""
    type: str = ""
    description: str = ""
    airports: list[Airport] = Field(default_factory=list)


class AirportTime(BaseModel):
    """单段航班的起降机场 + **当地**时间。"""

    name: str = ""
    id: str = ""
    time: str = Field(default="", description="当地时间 'YYYY-MM-DD HH:mm'")

    @property
    def at(self) -> datetime | None:
        """解析成 naive datetime（当地时区，SerpAPI 不给时区偏移）。"""
        try:
            return datetime.strptime(self.time, _AIRPORT_TIME_FMT)
        except (ValueError, TypeError):
            return None


class FlightLeg(BaseModel):
    """单段航段。中转行程 = 多段。"""

    departure_airport: AirportTime
    arrival_airport: AirportTime
    duration: int = Field(default=0, description="本段飞行时长（分钟）")
    airplane: str = ""
    airline: str = ""
    airline_logo: str = ""
    travel_class: str = ""
    flight_number: str = ""
    legroom: str = ""
    ticket_also_sold_by: list[str] = Field(default_factory=list)
    overnight: bool = False
    often_delayed_by_over_30_min: bool = False
    extensions: list[str] = Field(default_factory=list)


class Layover(BaseModel):
    duration: int = Field(default=0, description="停留时长（分钟）")
    name: str = ""
    id: str = ""
    overnight: bool = False


class CarbonEmissions(BaseModel):
    this_flight: int = 0
    typical_for_this_route: int = 0
    difference_percent: int = 0

    @property
    def is_better_than_typical(self) -> bool:
        return self.difference_percent < 0


class FlightItinerary(BaseModel):
    """`best_flights` / `other_flights` 的元素。"""

    flights: list[FlightLeg] = Field(default_factory=list)
    layovers: list[Layover] = Field(default_factory=list)
    total_duration: int = Field(default=0, description="全程总时长（分钟）")
    carbon_emissions: CarbonEmissions | None = None
    price: float | None = Field(default=None, description="总价；为 None 表示'价格暂无'")
    type: str = ""
    airline_logo: str = ""
    departure_token: str = ""

    @property
    def stops(self) -> int:
        return len(self.layovers)

    @property
    def arrives_at(self) -> datetime | None:
        """去程落地时间——route_planner 首日时间窗的起点。"""
        return self.flights[-1].arrival_airport.at if self.flights else None

    def flies_route(self, departure_id: str, arrival_id: str) -> bool:
        """这条行程确实是从 `departure_id` 飞到 `arrival_id` 吗。

        上游偶尔会掺进不属于本次查询的航段（换机场重试、`departure_token`
        配错时尤其容易）。首段起飞机场和末段降落机场必须对得上——否则用户会
        拿到一张"从别的城市出发"的机票，而落地时间又被 route_planner 当成
        首日时间窗的起点，整份行程一起错。
        """
        if not self.flights:
            return False
        first, last = self.flights[0], self.flights[-1]
        return (
            first.departure_airport.id.upper() == departure_id.strip().upper()
            and last.arrival_airport.id.upper() == arrival_id.strip().upper()
        )

    @property
    def departs_at(self) -> datetime | None:
        return self.flights[0].departure_airport.at if self.flights else None

    @property
    def arrival_airport_id(self) -> str:
        return self.flights[-1].arrival_airport.id if self.flights else ""


class FlightSearchResults(BaseModel):
    best_flights: list[FlightItinerary] = Field(default_factory=list)
    other_flights: list[FlightItinerary] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.best_flights and not self.other_flights

    def all(self) -> list[FlightItinerary]:
        return [*self.best_flights, *self.other_flights]


class FlightSearchParams(BaseModel):
    """Agent Memory 里的航班参数集合，也是调用 search 工具的输入。"""

    departure_airport_id: str | None = None
    arrival_airport_id: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    is_round_trip: bool | None = None
    passengers: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    travel_class: TravelClass | None = None

    # 展示用的完整机场对象
    departure_airport: Airport | None = None
    arrival_airport: Airport | None = None

    @field_validator("departure_airport_id", "arrival_airport_id")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v

    @property
    def is_ready(self) -> bool:
        """对应数据规范 §5 的 isParamsReady——用代码判断，不交给 LLM。"""
        if not (self.departure_airport_id and self.arrival_airport_id and self.departure_date):
            return False
        if self.is_round_trip is None or self.passengers < 1:
            return False
        if self.is_round_trip:
            if not self.return_date:
                return False
            if self.departure_date > self.return_date:
                return False
        return True

    def to_serpapi(self, *, currency: str = "CNY", hl: str = "zh-CN", gl: str = "") -> dict:
        """转成 `engine=google_flights` 的请求参数。"""
        if not self.is_ready:
            raise ValueError("航班参数尚未收集完整，不能调用搜索接口")
        params: dict = {
            "engine": "google_flights",
            "departure_id": self.departure_airport_id,
            "arrival_id": self.arrival_airport_id,
            "outbound_date": self.departure_date.isoformat(),  # type: ignore[union-attr]
            # ⚠️ 1=往返、2=单程。`serpapi-google-flights-api.md` §4 把两者写反了
            # （"1=单程，2=往返"），照抄会得到 HTTP 400：
            #   `return_date` should not be set if `type` is not `1` (Round trip).
            # 已实测确认：type=1 带 return_date 返回往返价（¥2300），type=2 返回单程价（¥1150）。
            "type": 1 if self.is_round_trip else 2,
            "adults": self.passengers,
            "currency": currency,
            "hl": hl,
        }
        if self.is_round_trip and self.return_date:
            params["return_date"] = self.return_date.isoformat()
        if self.children:
            params["children"] = self.children
        if self.travel_class:
            params["travel_class"] = _TRAVEL_CLASS_CODE[self.travel_class]
        # gl 决定 Google 的销售地（point of sale），会实打实地换掉结果集。
        # 留空 = 不发，用 Google 默认站点。**这是权衡后的默认值，不是遗漏**，
        # 依据见 settings.serpapi_flights_gl 的说明与 §5.1 的对照实验。
        if gl:
            params["gl"] = gl
        return params


class FlightBranch(BaseModel):
    """flight 分支的产出。后两个时间字段是 route_planner 的硬依赖。"""

    params: FlightSearchParams = Field(default_factory=FlightSearchParams)
    departure_options: list[Airport] = Field(
        default_factory=list, description="Autocomplete 返回的候选，供中断问题与兜底换机场用"
    )
    arrival_options: list[Airport] = Field(default_factory=list)
    candidates: list[FlightItinerary] = Field(default_factory=list)
    selected_index: int | None = None
    arrival_airport: Airport | None = None
    arrive_at: datetime | None = Field(default=None, description="去程落地当地时间")
    depart_at: datetime | None = Field(default=None, description="返程起飞当地时间")

    @property
    def selected(self) -> FlightItinerary | None:
        if self.selected_index is None:
            return None
        if not 0 <= self.selected_index < len(self.candidates):
            return None
        return self.candidates[self.selected_index]

    @model_validator(mode="after")
    def _check_times(self) -> Self:
        if self.arrive_at and self.depart_at and self.depart_at < self.arrive_at:
            raise ValueError("返程起飞时间不能早于去程落地时间")
        return self
