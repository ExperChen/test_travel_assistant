"""机票模拟数据生成器。

最重要的一条断言不是"字段齐不齐"，而是**真实解析器能不能吃下它**——
模拟数据一旦和真实格式漂移，线上换回真接口就会炸，而这正是模拟层最难发现的
失败方式。所以这里直接把生成的 payload 喂给 `FlightItinerary` / `CitySuggestion`。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.flight import CitySuggestion, FlightItinerary
from app.providers.mock import FlightMockGenerator
from app.providers.mock.airports import AIRPORTS, by_iata, distance_km, find_city
from app.providers.mock.flights import BASE_FARE_CNY, PER_KM_CNY, PRICE_JITTER

OUT = "2026-09-05"
RET = "2026-09-10"


def gen(seed: int = 42) -> FlightMockGenerator:
    return FlightMockGenerator(seed=seed)


class TestAirportTable:
    def test_iata_codes_are_unique(self):
        codes = [a.iata for a in AIRPORTS]
        assert len(codes) == len(set(codes))

    def test_multi_airport_cities_are_preserved(self):
        """一城多场是 autocomplete 存在的意义，也是换机场兜底的前提。"""
        from app.providers.mock.airports import airports_of_city

        for city in ("北京", "上海", "成都"):
            assert len(airports_of_city(city)) >= 2, city

    @pytest.mark.parametrize(
        ("query", "expected"),
        [("PEK", "北京"), ("成都", "成都"), ("蓉城", "成都"),
         ("杭州市", "杭州"), ("去南京", "南京"), ("ctu", "成都")],
    )
    def test_city_lookup(self, query, expected):
        assert find_city(query) == expected

    def test_longest_city_name_wins(self):
        """「南京」不能被「京」抢走。"""
        assert find_city("南京") == "南京"

    def test_unknown_query(self):
        assert find_city("不存在的地方xyz") is None
        assert find_city("") is None

    def test_distances_are_plausible(self):
        """粗校准：几条已知航线的距离应当落在合理区间。"""
        assert 100 < distance_km(by_iata("PEK"), by_iata("TSN")) < 160
        assert 1000 < distance_km(by_iata("PEK"), by_iata("PVG")) < 1200
        assert 2200 < distance_km(by_iata("PEK"), by_iata("URC")) < 2600


class TestAutocomplete:
    def test_matches_the_documented_shape(self):
        payload = gen().autocomplete("成都")
        assert payload["search_metadata"]["status"] == "Success"
        suggestion = payload["suggestions"][0]
        assert suggestion["type"] == "City"
        assert {a["id"] for a in suggestion["airports"]} == {"TFU", "CTU"}
        assert all(a["distance"].endswith(" km") for a in suggestion["airports"])

    def test_parses_into_the_real_model(self):
        payload = gen().autocomplete("北京")
        parsed = [CitySuggestion.model_validate(s) for s in payload["suggestions"]]
        assert [a.id for a in parsed[0].airports] == ["PEK", "PKX"]

    def test_missing_hl_returns_empty_for_chinese(self):
        """如实还原真实接口的坑：不传 hl 时中文城市名一律返回空。

        不还原它，模拟环境就永远发现不了"漏传 hl"这个会让整条链路第一步就死的问题。
        """
        assert gen().autocomplete("成都", hl="")["suggestions"] == []
        # 英文/三字码不受影响
        assert gen().autocomplete("PEK", hl="")["suggestions"]

    def test_unknown_city_is_empty_not_an_error(self):
        assert gen().autocomplete("不存在的地方xyz")["suggestions"] == []


class TestSearchShape:
    def test_parses_into_the_real_model(self):
        payload = gen().search(
            departure_id="PEK", arrival_id="CTU", outbound_date=OUT, return_date=RET
        )
        for raw in payload["best_flights"] + payload["other_flights"]:
            it = FlightItinerary.model_validate(raw)
            assert it.flights
            assert it.total_duration > 0
            assert it.flies_route("PEK", "CTU")

    def test_round_trip_carries_a_departure_token(self):
        payload = gen().search(
            departure_id="PEK", arrival_id="CTU", outbound_date=OUT,
            return_date=RET, trip_type=1,
        )
        assert all(it["departure_token"] for it in payload["best_flights"])
        assert all(it["type"] == "Round trip" for it in payload["best_flights"])

    def test_one_way_has_no_token(self):
        payload = gen().search(
            departure_id="PEK", arrival_id="CTU", outbound_date=OUT, trip_type=2
        )
        assert all("departure_token" not in it for it in payload["best_flights"])
        assert all(it["type"] == "One way" for it in payload["best_flights"])

    def test_best_flights_are_sorted_by_price(self):
        payload = gen().search(departure_id="PEK", arrival_id="CTU", outbound_date=OUT)
        prices = [it["price"] or 1e9 for it in payload["best_flights"]]
        assert prices == sorted(prices)

    @pytest.mark.parametrize(
        ("departure_id", "arrival_id"),
        [("PEK", "XXX"), ("XXX", "CTU"), ("PEK", "PEK"), ("", "CTU")],
    )
    def test_invalid_routes_return_empty(self, departure_id, arrival_id):
        """空结果**必须造得出来**——`search_with_fallback` 的 4 次兜底全靠它触发。"""
        payload = gen().search(
            departure_id=departure_id, arrival_id=arrival_id, outbound_date=OUT
        )
        assert payload["best_flights"] == []
        assert payload["other_flights"] == []


class TestReturnLeg:
    def _outbound_then_return(self, seed: int = 7):
        g = gen(seed)
        out = g.search(departure_id="PEK", arrival_id="CTU",
                       outbound_date=OUT, return_date=RET, trip_type=1)
        token = out["best_flights"][0]["departure_token"]
        back = g.search(departure_id="PEK", arrival_id="CTU",
                        outbound_date=OUT, return_date=RET,
                        trip_type=1, departure_token=token)
        return out, back

    def test_direction_is_reversed(self):
        """带 token 查回来的是返程，航段方向相反（文档 §3.2 行为 2）。"""
        _, back = self._outbound_then_return()
        it = FlightItinerary.model_validate(back["best_flights"][0])
        assert it.flies_route("CTU", "PEK")

    def test_departs_on_the_return_date(self):
        _, back = self._outbound_then_return()
        it = FlightItinerary.model_validate(back["best_flights"][0])
        assert it.departs_at.date() == date(2026, 9, 10)

    def test_return_leg_does_not_emit_another_token(self):
        """否则会变成无限套娃，而真实接口的返程列表里也没有 token。"""
        _, back = self._outbound_then_return()
        assert all("departure_token" not in it for it in back["best_flights"])


class TestPricing:
    def _sample(self, departure_id: str, arrival_id: str, n: int = 200) -> list[float]:
        out: list[float] = []
        for seed in range(n):
            payload = FlightMockGenerator(seed=seed).search(
                departure_id=departure_id, arrival_id=arrival_id,
                outbound_date=OUT, trip_type=2, best_count=1, other_count=0,
            )
            out += [
                it["price"]
                for it in payload["best_flights"]
                if it["price"] is not None and not it["layovers"]
            ]
        return out

    def test_jitter_stays_within_20_percent(self):
        """**这是本次的核心需求**：票价围绕基准价上下 20% 波动。"""
        origin, dest = by_iata("PEK"), by_iata("CTU")
        base = BASE_FARE_CNY + PER_KM_CNY * distance_km(origin, dest)
        prices = self._sample("PEK", "CTU")

        assert prices
        ratios = [p / base for p in prices]
        assert min(ratios) >= 1 - PRICE_JITTER - 0.01
        assert max(ratios) <= 1 + PRICE_JITTER + 0.01

    def test_jitter_actually_spans_the_range(self):
        """别只波动 1%——那等于没波动。"""
        prices = self._sample("PEK", "CTU")
        assert max(prices) / min(prices) > 1.3

    def test_price_scales_with_distance(self):
        """PEK→TSN 和 PEK→URC 不能同价。"""
        short = sum(self._sample("PEK", "TSN", 60)) / 60
        long = sum(self._sample("PEK", "URC", 60)) / 60
        assert long > short * 3

    def test_class_multiplier(self):
        cheap = gen(3).search(departure_id="PEK", arrival_id="CTU",
                              outbound_date=OUT, trip_type=2, travel_class=1)
        posh = gen(3).search(departure_id="PEK", arrival_id="CTU",
                             outbound_date=OUT, trip_type=2, travel_class=3)
        assert posh["best_flights"][0]["price"] > cheap["best_flights"][0]["price"]
        assert posh["best_flights"][0]["flights"][0]["travel_class"] == "Business"

    def test_round_trip_costs_about_double(self):
        one = gen(5).search(departure_id="PEK", arrival_id="CTU",
                            outbound_date=OUT, trip_type=2)
        both = gen(5).search(departure_id="PEK", arrival_id="CTU",
                             outbound_date=OUT, return_date=RET, trip_type=1)
        assert both["best_flights"][0]["price"] == pytest.approx(
            one["best_flights"][0]["price"] * 2, rel=0.05
        )

    def test_null_prices_occur(self):
        """真实接口会有"价格暂无"的条目，下游 price_text() 专门处理过它。"""
        nulls = 0
        for seed in range(300):
            payload = FlightMockGenerator(seed=seed).search(
                departure_id="PEK", arrival_id="PVG", outbound_date=OUT, trip_type=2
            )
            nulls += sum(
                1 for it in payload["best_flights"] + payload["other_flights"]
                if it["price"] is None
            )
        assert nulls > 0

    def test_unseeded_generators_differ(self):
        """默认真随机——同一航线两次查价不该恒等。"""
        prices = {
            FlightMockGenerator().search(
                departure_id="PEK", arrival_id="CTU", outbound_date=OUT, trip_type=2
            )["best_flights"][0]["price"]
            for _ in range(12)
        }
        assert len(prices) > 1

    def test_seeded_generators_are_reproducible(self):
        a = gen(99).search(departure_id="PEK", arrival_id="CTU", outbound_date=OUT)
        b = gen(99).search(departure_id="PEK", arrival_id="CTU", outbound_date=OUT)
        assert a == b


class TestRealism:
    def test_airlines_match_their_hubs(self):
        """从成都飞出去应当能看到川航，而不是一色的随机航司。"""
        payload = gen(1).search(departure_id="CTU", arrival_id="PEK", outbound_date=OUT)
        airlines = {
            leg["airline"]
            for it in payload["best_flights"] + payload["other_flights"]
            for leg in it["flights"]
        }
        assert {"四川航空", "中国国际航空"} & airlines

    def test_flight_number_prefix_matches_the_airline(self):
        payload = gen(2).search(departure_id="PEK", arrival_id="CAN", outbound_date=OUT)
        for it in payload["best_flights"]:
            for leg in it["flights"]:
                assert leg["flight_number"][:2].strip() in leg["airline_logo"]

    def test_long_haul_uses_wide_body(self):
        payload = gen(4).search(departure_id="PEK", arrival_id="URC", outbound_date=OUT)
        planes = {leg["airplane"] for it in payload["best_flights"] for leg in it["flights"]}
        assert any(k in p for p in planes for k in ("330", "350", "777", "787", "A321"))

    def test_connections_only_on_long_routes(self):
        """短途不该出现中转——北京飞天津经西安是明显的假数据。"""
        for seed in range(20):
            payload = FlightMockGenerator(seed=seed).search(
                departure_id="PEK", arrival_id="TSN", outbound_date=OUT
            )
            assert all(not it["layovers"] for it in payload["best_flights"])

    def test_connections_do_not_detour_absurdly(self):
        origin, dest = by_iata("HRB"), by_iata("SYX")
        direct = distance_km(origin, dest)
        for seed in range(30):
            payload = FlightMockGenerator(seed=seed).search(
                departure_id="HRB", arrival_id="SYX", outbound_date=OUT
            )
            for it in payload["best_flights"] + payload["other_flights"]:
                if not it["layovers"]:
                    continue
                hub = by_iata(it["layovers"][0]["id"])
                flown = distance_km(origin, hub) + distance_km(hub, dest)
                assert flown <= direct * 1.6 + 400

    def test_durations_are_consistent_with_the_legs(self):
        payload = gen(6).search(departure_id="HRB", arrival_id="SYX", outbound_date=OUT)
        for it in payload["best_flights"] + payload["other_flights"]:
            legs = sum(leg["duration"] for leg in it["flights"])
            stops = sum(lo["duration"] for lo in it["layovers"])
            assert it["total_duration"] == legs + stops
