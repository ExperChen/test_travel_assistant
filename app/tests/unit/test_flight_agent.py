"""航班分支的纯函数测试（不碰网络）。"""

from __future__ import annotations

from app.agents.flight_agent import (
    auto_pick_airport,
    flatten_airports,
    looks_like_iata,
    match_airport,
)
from app.models.flight import (
    Airport,
    AirportTime,
    CitySuggestion,
    FlightItinerary,
    FlightLeg,
)


def city(name: str, city_id: str, *airports: Airport) -> CitySuggestion:
    return CitySuggestion(name=name, id=city_id, type="city", airports=list(airports))


def airport(code: str, name: str, city_id: str, distance: str = "") -> Airport:
    return Airport(id=code, name=name, city_id=city_id, distance=distance)


# 实测 2026-08-06 的真实返回：「上海」命中三个城市，后两个是德国地名的中译
SHANGHAI = [
    city(
        "中国上海市", "/m/06wjf",
        airport("PVG", "上海浦东国际机场", "/m/06wjf", "20英里"),
        airport("SHA", "上海虹桥国际机场", "/m/06wjf", "8英里"),
    ),
    city("上海市", "/m/06wjf"),
    city(
        "德国上海德", "/m/02q0yzg",
        airport("NUE", "纽伦堡机场", "/m/05bkf", "32英里"),
        airport("ZAQ", "Nuremberg Hbf", "/m/05bkf", "35英里"),
    ),
    city(
        "德国上海因巴赫", "/m/02z2zgt",
        airport("FRA", "法兰克福机场", "/m/02z0j", "35英里"),
        airport("HHN", "Hahn Airport", "/m/02z0j", "24英里"),
        airport("ZRB", "Frankfurt Hbf", "/m/02z0j", "40英里"),
    ),
]


class TestFlattenAirports:
    """只收锚点城市名下的机场。

    autocomplete 是按**子串**命中的：「德国上海德」「德国上海因巴赫」都含「上海」
    二字。把所有建议拍平合并，用户就会在"上海有哪些机场"里看到法兰克福。
    """

    def test_foreign_cities_matched_by_substring_are_dropped(self):
        result = flatten_airports(SHANGHAI)

        assert {a.id for a in result} == {"PVG", "SHA"}

    def test_rail_stations_are_not_airports(self):
        # ZAQ / ZRB 是 Nuremberg Hbf / Frankfurt Hbf，进不了航班搜索，
        # 只会占掉用户的选择位
        result = flatten_airports(
            [city("测试市", "/m/x",
                  airport("XXX", "测试国际机场", "/m/x"),
                  airport("ZZZ", "Test Hbf", "/m/x"))]
        )

        assert {a.id for a in result} == {"XXX"}

    def test_closest_to_downtown_comes_first(self):
        # auto_select 取第一个；离市区近的通勤成本更低，没航班时还有兜底换机场
        result = flatten_airports(SHANGHAI)

        assert [a.id for a in result] == ["SHA", "PVG"]

    def test_mixed_distance_units_are_normalised(self):
        result = flatten_airports(
            [city("测试市", "/m/x",
                  airport("AAA", "远机场", "/m/x", "20公里"),
                  airport("BBB", "近机场", "/m/x", "8英里"))]
        )

        # 8 英里 ≈ 12.9 公里 < 20 公里；按数字裸比会排反
        assert [a.id for a in result] == ["BBB", "AAA"]

    def test_airports_without_a_distance_go_last(self):
        result = flatten_airports(
            [city("测试市", "/m/x",
                  airport("AAA", "未知距离机场", "/m/x"),
                  airport("BBB", "近机场", "/m/x", "8英里"))]
        )

        assert [a.id for a in result] == ["BBB", "AAA"]

    def test_the_first_suggestion_with_airports_wins(self):
        # 真实返回里第一条常常是没有机场的纯城市条目
        result = flatten_airports(
            [city("空条目", "/m/a"),
             city("真城市", "/m/b", airport("BBB", "机场", "/m/b"))]
        )

        assert [a.id for a in result] == ["BBB"]

    def test_no_airports_anywhere_returns_empty(self):
        # 上层据此报 CITY_NOT_FOUND，**不要**退回未过滤的全集
        assert flatten_airports([city("空", "/m/a"), city("也空", "/m/b")]) == []

    def test_empty_suggestions(self):
        assert flatten_airports([]) == []

    def test_duplicate_codes_are_collapsed(self):
        result = flatten_airports(
            [city("测试市", "/m/x",
                  airport("AAA", "机场", "/m/x", "5英里"),
                  airport("AAA", "机场（重复）", "/m/x", "5英里"))]
        )

        assert len(result) == 1


class TestAutoPick:
    def test_single_option_needs_no_question(self):
        options = [airport("HGH", "杭州萧山国际机场", "/m/x")]

        chosen, warnings = auto_pick_airport(options, role="目的地", auto_select=False)

        assert chosen is not None
        assert not warnings

    def test_multiple_options_ask_the_user(self):
        options = [airport("SHA", "虹桥", "/m/x"), airport("PVG", "浦东", "/m/x")]

        chosen, _ = auto_pick_airport(options, role="目的地", auto_select=False)

        assert chosen is None  # None = 需要用户选，不是错误

    def test_auto_select_takes_the_first_and_says_so(self):
        options = [airport("SHA", "虹桥", "/m/x"), airport("PVG", "浦东", "/m/x")]

        chosen, warnings = auto_pick_airport(options, role="目的地", auto_select=True)

        assert chosen.id == "SHA"
        assert warnings[0].code == "AIRPORT_AUTO_PICKED"


class TestMatchAirport:
    OPTIONS = [airport("SHA", "虹桥", "/m/x"), airport("PVG", "浦东", "/m/x")]

    def test_matches_by_iata_case_insensitively(self):
        assert match_airport(self.OPTIONS, "pvg").id == "PVG"

    def test_matches_by_one_based_index(self):
        assert match_airport(self.OPTIONS, "2").id == "PVG"

    def test_unknown_answer_is_none(self):
        assert match_airport(self.OPTIONS, "XYZ") is None
        assert match_airport(self.OPTIONS, "9") is None
        assert match_airport(self.OPTIONS, "") is None


def test_iata_codes_skip_the_autocomplete_call():
    # 用户直接填三字码就不必再走补全，省一次 SerpAPI 额度
    assert looks_like_iata("PEK")
    assert looks_like_iata(" pek ")
    assert not looks_like_iata("北京")
    assert not looks_like_iata("PEKX")


def leg(dep: str, arr: str, number: str = "CA100") -> FlightLeg:
    return FlightLeg(
        departure_airport=AirportTime(id=dep, name=dep, time="2026-09-05 08:00"),
        arrival_airport=AirportTime(id=arr, name=arr, time="2026-09-05 10:30"),
        flight_number=number,
    )


class TestFliesRoute:
    """上游偶尔掺进不属于本次查询的航段。

    一张"从别的城市出发"的机票会一路走进行程，它的落地时间还会被
    route_planner 当成首日时间窗的起点——错得又贵又不显眼。
    """

    def test_a_nonstop_on_the_asked_route(self):
        it = FlightItinerary(flights=[leg("PEK", "CTU")])
        assert it.flies_route("PEK", "CTU")

    def test_a_connection_is_judged_by_its_ends(self):
        # 中转点是哪儿不重要，起讫点对得上就行
        it = FlightItinerary(flights=[leg("PEK", "WUX"), leg("WUX", "CTU")])
        assert it.flies_route("PEK", "CTU")

    def test_a_different_departure_is_rejected(self):
        it = FlightItinerary(flights=[leg("PKX", "CTU")])
        assert not it.flies_route("PEK", "CTU")

    def test_a_different_arrival_is_rejected(self):
        it = FlightItinerary(flights=[leg("PEK", "TFU")])
        assert not it.flies_route("PEK", "CTU")

    def test_case_and_padding_do_not_matter(self):
        it = FlightItinerary(flights=[leg("PEK", "CTU")])
        assert it.flies_route(" pek ", "ctu")

    def test_an_empty_itinerary_is_not_on_any_route(self):
        assert not FlightItinerary(flights=[]).flies_route("PEK", "CTU")
