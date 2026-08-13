"""机票模拟数据生成器。

响应结构**逐字段对齐** `docs/architecture/serpapi-usage-and-mocking.md` §3.1 / §3.2，
也就是 SerpAPI `google_flights_autocomplete` 与 `google_flights` 的真实格式。

## 票价怎么来的

不是拍脑袋给个常数——那样 PEK→PVG 和 PEK→URC 会同价，一眼就假。模型是：

    基准价 = (起步价 + 每公里单价 × 航线距离) × 舱位系数 × 航段系数
    最终价 = 基准价 × random.uniform(0.8, 1.2)      ← 上下 20% 波动

波动用**可注入的 `Random`**：默认真随机（每次查价都不同，贴近真实），
传 `seed=` 则完全可复现——测试必须能断言具体数字。

## 三个必须还原的真实行为

1. **往返搜索（type=1）的 `best_flights` 里只有去程。** 要拿返程必须带上选定
   去程的 `departure_token` 再查一次——这是第二次额度消耗，代码里
   `fetch_return_departure()` 就是干这个的。
2. **带 token 查回来的航段方向是反的**（目的地 → 出发地）。
3. **`price` 偶尔为 `null`**，表示"价格暂无"而不是免费。真实接口就这样，
   下游 `price_text()` 专门处理过这种情况。
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, time, timedelta
from typing import Any

from app.providers.mock.airlines import MockAirline, aircraft_for, airlines_for_route
from app.providers.mock.airports import (
    MockAirport,
    airports_of_city,
    by_iata,
    distance_km,
    find_city,
)

__all__ = [
    "FlightMockGenerator",
    "PRICE_JITTER",
    "BASE_FARE_CNY",
    "PER_KM_CNY",
    "CLASS_MULTIPLIER",
]

PRICE_JITTER = 0.20
"""票价随机波动幅度：基准价的 ±20%。"""

BASE_FARE_CNY = 180.0
"""起步价：机场建设费 + 燃油附加 + 最低票面，与航程无关的那部分。"""

PER_KM_CNY = 0.62
"""每公里单价（经济舱）。国内实际成交价大致落在 0.4~0.8 元/公里。"""

CLASS_MULTIPLIER: dict[str, float] = {
    "economy": 1.0,
    "premium_economy": 1.55,
    "business": 2.90,
    "first": 4.60,
}

_CRUISE_KMH = 800.0
"""巡航速度。真实航程时间还要加上滑行/爬升/下降的固定开销。"""

_GROUND_MINUTES = 35
"""滑行 + 爬升 + 下降的固定开销（分钟）。"""

_LEGROOM = ("29 in", "30 in", "31 in", "32 in")

_EXTENSIONS_FULL = (
    "机上有 Wi-Fi（收费）",
    "座椅电源 / USB 接口",
    "点播娱乐系统",
    "含餐食",
)
_EXTENSIONS_LOW_COST = ("餐食需另行购买", "座椅间距较小")

# 一天里合理的出发时刻。刻意包含早班机和晚班机——它们直接决定首日/末日
# 还能不能安排行程——落地太晚首日就废了。
_DEPARTURE_HOURS = (7, 8, 10, 12, 14, 16, 18, 20, 22)

_TIME_FMT = "%Y-%m-%d %H:%M"


def _fmt(moment: datetime) -> str:
    return moment.strftime(_TIME_FMT)


class FlightMockGenerator:
    """生成 SerpAPI 格式的航班响应。

    `seed` 为 None 时票价真随机（同一航线两次查价不同，贴近真实）；
    给定 seed 则完全可复现。
    """

    def __init__(self, *, seed: int | None = None, jitter: float = PRICE_JITTER):
        self._seed = seed
        self._jitter = jitter
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------ 补全
    def autocomplete(self, q: str, *, hl: str = "zh-CN") -> dict[str, Any]:
        """`engine=google_flights_autocomplete` 的响应。

        ⚠️ 真实接口在**不传 hl 时对中文城市名一律返回空**（文档 §3.1）。
        这里如实还原这个坑——否则模拟环境永远发现不了漏传 hl 的问题。
        """
        text = (q or "").strip()
        if not text:
            return self._empty_suggestions()

        city = find_city(text)
        if city is None:
            return self._empty_suggestions()
        if not hl and any("一" <= ch <= "鿿" for ch in text):
            return self._empty_suggestions()

        airports = airports_of_city(city)
        return {
            "search_metadata": {
                "id": self._metadata_id("ac", text),
                "status": "Success",
                "total_time_taken": round(self._rng.uniform(0.4, 1.6), 2),
            },
            "search_parameters": {
                "engine": "google_flights_autocomplete",
                "q": text,
                "hl": hl,
            },
            "suggestions": [
                {
                    "type": "City",
                    "name": city,
                    "id": f"/m/{hashlib.md5(city.encode()).hexdigest()[:7]}",
                    "description": f"{city}，中国",
                    "airports": [
                        {
                            "name": a.name,
                            "id": a.iata,
                            "city": a.city,
                            "distance": a.distance_text,
                        }
                        for a in airports
                    ],
                }
            ],
        }

    def _empty_suggestions(self) -> dict[str, Any]:
        return {"search_metadata": {"status": "Success"}, "suggestions": []}

    # ------------------------------------------------------------------ 搜索
    def search(
        self,
        *,
        departure_id: str,
        arrival_id: str,
        outbound_date: str | date,
        return_date: str | date | None = None,
        trip_type: int = 1,
        adults: int = 1,
        children: int = 0,
        travel_class: int = 1,
        currency: str = "CNY",
        departure_token: str = "",
        best_count: int = 3,
        other_count: int = 3,
    ) -> dict[str, Any]:
        """`engine=google_flights` 的响应。

        `trip_type`：**1 = 往返，2 = 单程**（与官方文档写反的那个坑，见 §3.2）。
        带 `departure_token` 时返回的是**返程**列表，航段方向相反。
        """
        origin, dest = by_iata(departure_id), by_iata(arrival_id)
        if origin is None or dest is None or origin.iata == dest.iata:
            return self._empty_flights(departure_id, arrival_id)

        is_return_leg = bool(departure_token)
        if is_return_leg:
            # 返程：方向反过来，日期用 return_date
            origin, dest = dest, origin
            fly_on = _coerce(return_date) or _coerce(outbound_date)
        else:
            fly_on = _coerce(outbound_date)
        if fly_on is None:
            return self._empty_flights(departure_id, arrival_id)

        km = distance_km(origin, dest)
        pool = airlines_for_route(origin.city, dest.city)
        class_name = _CLASS_NAME.get(travel_class, "Economy")
        klass = _CLASS_KEY.get(travel_class, "economy")
        round_trip = trip_type == 1 and not is_return_leg

        total = max(best_count, 0) + max(other_count, 0)
        itineraries = [
            self._itinerary(
                origin, dest, fly_on, km, pool[i % len(pool)],
                index=i, klass=klass, class_name=class_name,
                round_trip=trip_type == 1,
                emit_token=round_trip,
            )
            for i in range(total)
        ]
        # best_flights 按性价比排（真实接口也是推荐在前），other 保持原序
        best = sorted(itineraries[:best_count], key=lambda it: it["price"] or 1e9)

        return {
            "search_metadata": {
                "id": self._metadata_id("fs", f"{origin.iata}{dest.iata}{fly_on}"),
                "status": "Success",
                "total_time_taken": round(self._rng.uniform(1.2, 3.8), 2),
            },
            "search_parameters": _search_parameters(
                departure_id=origin.iata if is_return_leg else departure_id,
                arrival_id=dest.iata if is_return_leg else arrival_id,
                outbound_date=_coerce(outbound_date),
                return_date=_coerce(return_date),
                trip_type=trip_type,
                adults=adults,
                children=children,
                travel_class=travel_class,
                currency=currency,
                departure_token=departure_token,
            ),
            "best_flights": best,
            "other_flights": itineraries[best_count:],
        }

    def _empty_flights(self, departure_id: str, arrival_id: str) -> dict[str, Any]:
        """空结果。**必须能造出来**——`search_with_fallback` 的 4 次兜底重试
        全靠它触发，只会返回成功响应的模拟层等于把那条链旁路掉了。"""
        return {
            "search_metadata": {"status": "Success"},
            "search_parameters": {
                "engine": "google_flights",
                "departure_id": departure_id,
                "arrival_id": arrival_id,
            },
            "best_flights": [],
            "other_flights": [],
        }

    # ------------------------------------------------------------- 单条行程
    def _itinerary(
        self,
        origin: MockAirport,
        dest: MockAirport,
        fly_on: date,
        km: float,
        airline: MockAirline,
        *,
        index: int,
        klass: str,
        class_name: str,
        round_trip: bool,
        emit_token: bool,
    ) -> dict[str, Any]:
        # 长航线才安排中转，且不是每条都中转——直飞永远排在前面
        with_stop = km >= 1800 and index >= 2 and self._rng.random() < 0.55

        depart_hour = _DEPARTURE_HOURS[index % len(_DEPARTURE_HOURS)]
        depart_minute = self._rng.choice((0, 5, 15, 30, 45))
        depart_at = datetime.combine(fly_on, time(depart_hour, depart_minute))

        legs, layovers, total_minutes = (
            self._connecting(origin, dest, depart_at, km, airline, klass, class_name)
            if with_stop
            else self._direct(origin, dest, depart_at, km, airline, klass, class_name)
        )

        price = self._price(km, klass, round_trip=round_trip, stops=len(layovers))

        itinerary: dict[str, Any] = {
            "flights": legs,
            "layovers": layovers,
            "total_duration": total_minutes,
            "carbon_emissions": self._carbon(km, len(legs)),
            "price": price,
            "type": "Round trip" if round_trip else "One way",
            "airline_logo": airline.logo,
        }
        if emit_token:
            # 只有往返的**去程**才带 token——拿它再查一次才有返程（§3.2 行为 1）
            itinerary["departure_token"] = self._token(origin, dest, fly_on, index)
        return itinerary

    def _direct(self, origin, dest, depart_at, km, airline, klass, class_name):
        minutes = self._duration(km)
        arrive_at = depart_at + timedelta(minutes=minutes)
        leg = self._leg(origin, dest, depart_at, arrive_at, minutes, km, airline, klass, class_name)
        return [leg], [], minutes

    def _connecting(self, origin, dest, depart_at, km, airline, klass, class_name):
        """两段中转。中转点取航司枢纽或航程中点附近的机场。"""
        hub = self._pick_hub(origin, dest)
        km1 = distance_km(origin, hub)
        km2 = distance_km(hub, dest)

        minutes1 = self._duration(km1)
        arrive1 = depart_at + timedelta(minutes=minutes1)
        layover_min = self._rng.choice((65, 90, 115, 140, 185))
        depart2 = arrive1 + timedelta(minutes=layover_min)
        minutes2 = self._duration(km2)
        arrive2 = depart2 + timedelta(minutes=minutes2)

        legs = [
            self._leg(origin, hub, depart_at, arrive1, minutes1, km1, airline, klass, class_name),
            self._leg(hub, dest, depart2, arrive2, minutes2, km2, airline, klass, class_name),
        ]
        layovers = [
            {
                "duration": layover_min,
                "name": hub.name,
                "id": hub.iata,
                "overnight": depart2.date() != arrive1.date(),
            }
        ]
        return legs, layovers, minutes1 + layover_min + minutes2

    def _pick_hub(self, origin: MockAirport, dest: MockAirport) -> MockAirport:
        """挑一个不在两端城市、且不会绕太远的中转机场。"""
        direct = distance_km(origin, dest)
        candidates = [
            a
            for a in (by_iata(c) for c in ("PEK", "PVG", "CAN", "CTU", "XIY", "KMG", "WUH"))
            if a is not None and a.city not in (origin.city, dest.city)
            # 绕行不超过 1.6 倍直飞距离，否则会出现"北京飞天津经乌鲁木齐"
            and distance_km(origin, a) + distance_km(a, dest) <= direct * 1.6 + 400
        ]
        return self._rng.choice(candidates) if candidates else origin

    def _leg(
        self, origin, dest, depart_at, arrive_at, minutes, km, airline, klass, class_name
    ) -> dict[str, Any]:
        low, high = airline.number_range
        return {
            "departure_airport": {
                "name": origin.name,
                "id": origin.iata,
                "time": _fmt(depart_at),
            },
            "arrival_airport": {
                "name": dest.name,
                "id": dest.iata,
                "time": _fmt(arrive_at),
            },
            "duration": minutes,
            "airplane": aircraft_for(airline, km),
            "airline": airline.name,
            "airline_logo": airline.logo,
            "travel_class": class_name,
            "flight_number": f"{airline.code} {self._rng.randint(low, high)}",
            "legroom": "29 in" if airline.low_cost else self._rng.choice(_LEGROOM),
            "overnight": arrive_at.date() != depart_at.date(),
            "often_delayed_by_over_30_min": self._rng.random() < 0.12,
            "extensions": list(
                _EXTENSIONS_LOW_COST if airline.low_cost else _EXTENSIONS_FULL
            ),
        }

    # ---------------------------------------------------------------- 价格
    def _price(self, km: float, klass: str, *, round_trip: bool, stops: int) -> float | None:
        """基准价 × ±20% 随机波动。

        约 4% 的概率返回 `None`——真实接口确实会有"价格暂无"的条目，
        下游 `price_text()` 专门处理过它。不造这种数据，那条分支就永远测不到。
        """
        if self._rng.random() < 0.04:
            return None

        base = (BASE_FARE_CNY + PER_KM_CNY * km) * CLASS_MULTIPLIER.get(klass, 1.0)
        if stops:
            base *= 0.88  # 中转便宜些，这也是用户选它的理由
        if round_trip:
            base *= 2.0  # 往返查询里 price 是往返总价

        low, high = 1 - self._jitter, 1 + self._jitter
        return round(base * self._rng.uniform(low, high), 0)

    def _duration(self, km: float) -> int:
        return int(round(km / _CRUISE_KMH * 60 + _GROUND_MINUTES))

    def _carbon(self, km: float, legs: int) -> dict[str, int]:
        """碳排放，克。中转多飞一段，排放更高。"""
        typical = int(km * 115 * 1000 / 1000)  # ≈115 g/客公里
        this = int(typical * self._rng.uniform(0.9, 1.15) * (1.12 if legs > 1 else 1.0))
        diff = round((this - typical) / typical * 100) if typical else 0
        return {
            "this_flight": this,
            "typical_for_this_route": typical,
            "difference_percent": diff,
        }

    # ---------------------------------------------------------------- 杂项
    def _token(self, origin: MockAirport, dest: MockAirport, fly_on: date, index: int) -> str:
        """departure_token：必须能唯一定位到"哪条去程"，否则返程会配错。"""
        raw = f"{origin.iata}-{dest.iata}-{fly_on.isoformat()}-{index}"
        return hashlib.sha1(raw.encode()).hexdigest()[:22] + "=="

    def _metadata_id(self, prefix: str, payload: str) -> str:
        return f"{prefix}_{hashlib.md5(payload.encode()).hexdigest()[:16]}"


_CLASS_NAME: dict[int, str] = {
    1: "Economy",
    2: "Premium economy",
    3: "Business",
    4: "First",
}
_CLASS_KEY: dict[int, str] = {
    1: "economy",
    2: "premium_economy",
    3: "business",
    4: "first",
}


def _coerce(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _search_parameters(**kw: Any) -> dict[str, Any]:
    """回显请求参数。真实响应里有这一块，缺了会让录像看起来不像真的。"""
    params: dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": kw["departure_id"],
        "arrival_id": kw["arrival_id"],
        "type": kw["trip_type"],
        "adults": kw["adults"],
        "currency": kw["currency"],
        "hl": "zh-CN",
    }
    if (outbound := kw.get("outbound_date")) is not None:
        params["outbound_date"] = outbound.isoformat()
    if (ret := kw.get("return_date")) is not None and kw["trip_type"] == 1:
        params["return_date"] = ret.isoformat()
    if kw.get("children"):
        params["children"] = kw["children"]
    if kw.get("travel_class", 1) != 1:
        params["travel_class"] = kw["travel_class"]
    if kw.get("departure_token"):
        params["departure_token"] = kw["departure_token"]
    return params
