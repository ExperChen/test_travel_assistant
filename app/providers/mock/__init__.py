"""SerpAPI 模拟数据（见 `docs/architecture/serpapi-usage-and-mocking.md`）。

目前只覆盖机票。酒店（`google_hotels`）待补。

    from app.providers.mock import FlightMockGenerator

    gen = FlightMockGenerator(seed=42)          # 不传 seed 则票价真随机
    gen.autocomplete("成都")                     # google_flights_autocomplete
    gen.search(departure_id="PEK", arrival_id="CTU",
               outbound_date="2026-09-05", return_date="2026-09-10")
"""

from app.providers.mock.airlines import AIRLINES, MockAirline
from app.providers.mock.airports import AIRPORTS, CITIES, MockAirport, by_iata, find_city
from app.providers.mock.flights import (
    BASE_FARE_CNY,
    CLASS_MULTIPLIER,
    PER_KM_CNY,
    PRICE_JITTER,
    FlightMockGenerator,
)

__all__ = [
    "AIRLINES",
    "AIRPORTS",
    "CITIES",
    "BASE_FARE_CNY",
    "CLASS_MULTIPLIER",
    "PER_KM_CNY",
    "PRICE_JITTER",
    "FlightMockGenerator",
    "MockAirline",
    "MockAirport",
    "by_iata",
    "find_city",
]
