"""Tool 层契约测试。

fixture 是从 `docs/` 里摘录的真实响应快照。这一层要守住三件事：
1. 字段缺失 / 为 null 时不能抛异常（vacation rental 没有 hotel_class，POI 可能没坐标）；
2. polyline、原始大 JSON 绝不能进入返回值——那是 token 灾难；
3. 参数映射正确，尤其是"发出去之前就该拦下的非法参数"不许真的发请求。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.exceptions import InvalidParams
from app.core.metrics import track_quota
from app.models.common import GeoPoint
from app.tools import registry
from app.tools.amap_poi import poi_around, poi_detail, poi_keyword
from app.tools.amap_route import (
    direction_driving,
    direction_transit,
    direction_walking,
    distance_batch,
)
from app.tools.serpapi_flights import flights_autocomplete, flights_search
from app.tools.serpapi_hotels import hotels_autocomplete, hotels_search

SERP_URL = "https://serpapi.com/search.json"
AMAP_BASE = "https://restapi.amap.com"


# ------------------------------------------------------------------ 注册表
class TestRegistry:
    def test_all_tools_are_registered(self):
        names = {spec.name for spec in registry.all_specs()}
        assert names == {
            "flights_autocomplete",
            "flights_search",
            "hotels_autocomplete",
            "hotels_search",
            "poi_keyword",
            "poi_around",
            "poi_detail",
            "district_lookup",
            "regeo_batch",
            "distance_batch",
            "direction_transit",
            "direction_driving",
            "direction_walking",
        }

    def test_geopoint_tools_are_not_exposed_to_the_llm(self):
        # 它们收 GeoPoint（带坐标系标注），裸经纬度进来就可能把 WGS-84 当 GCJ-02 用
        hidden = {s.name for s in registry.all_specs() if not s.llm_facing}
        assert hidden == {
            "regeo_batch",
            "distance_batch",
            "direction_transit",
            "direction_driving",
            "direction_walking",
        }

    def test_every_spec_has_a_usable_description_and_schema(self):
        for spec in registry.all_specs():
            assert len(spec.description) > 20, spec.name
            assert spec.parameters["type"] == "object", spec.name
            assert spec.parameters["properties"], spec.name
            assert spec.provider in ("serpapi", "amap"), spec.name

    def test_unknown_tool_raises(self):
        with pytest.raises(KeyError):
            registry.get_tool("nope")


class TestQuotaAccounting:
    """SerpAPI 只有 250 次/月，必须能回答'这次规划烧了几次'。"""

    @respx.mock
    async def test_real_calls_and_cache_hits_are_counted_separately(
        self, serp, amap, load_fixture
    ):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_autocomplete"))
        )
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )

        with track_quota() as quota:
            await flights_autocomplete("New York", client=serp)
            await flights_autocomplete("New York", client=serp)  # 同参数 → 命中缓存
            await poi_keyword("景点", region="杭州市", client=amap)

        assert quota.serpapi == 1
        assert quota.amap == 1
        assert quota.cache_hits == 1

    @respx.mock
    async def test_calls_outside_a_tracking_context_are_ignored(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_autocomplete"))
        )
        # 没有活动的 counter 时不该炸，也不该泄漏到下一次统计
        await flights_autocomplete("New York", client=serp)

        with track_quota() as quota:
            pass
        assert quota.serpapi == 0


# -------------------------------------------------------------------- 航班
class TestFlightsAutocomplete:
    @respx.mock
    async def test_parses_city_and_airports(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_autocomplete"))
        )

        suggestions = await flights_autocomplete("New York", client=serp)

        assert len(suggestions) == 1
        city = suggestions[0]
        assert city.name == "New York"
        assert [a.id for a in city.airports] == ["JFK", "EWR", "LGA"]
        assert city.airports[0].label == (
            "[JFK] John F. Kennedy International Airport - 距市中心 14 mi"
        )

    @respx.mock
    async def test_sends_the_right_engine(self, serp, load_fixture):
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_autocomplete"))
        )
        await flights_autocomplete("  北京  ", client=serp)

        params = route.calls.last.request.url.params
        assert params["engine"] == "google_flights_autocomplete"
        assert params["q"] == "北京"

    @respx.mock
    async def test_always_sends_hl(self, serp, load_fixture):
        """回归保护：不传 hl 时中文城市名一律返回空。

        实测「成都」「杭州」「北京」不带 hl 全部落空，带上 hl=zh-CN 立刻有结果。
        目的地限中国大陆意味着用户几乎必然用中文输入，这个参数不能再丢。
        """
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_autocomplete"))
        )
        await flights_autocomplete("成都", client=serp)

        assert route.calls.last.request.url.params["hl"] == "zh-CN"

    @respx.mock
    async def test_empty_payload_is_not_an_error(self, serp):
        respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={}))
        assert await flights_autocomplete("nowhere", client=serp) == []

    async def test_blank_query_never_hits_the_network(self, serp):
        with pytest.raises(InvalidParams):
            await flights_autocomplete("   ", client=serp)


class TestFlightsSearch:
    @respx.mock
    async def test_parses_itineraries(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_search"))
        )

        results = await flights_search(
            departure_id="JFK",
            arrival_id="HND",
            outbound_date="2026-08-10",
            return_date="2026-08-16",
            is_round_trip=True,
            passengers=1,
            travel_class="economy",
            client=serp,
        )

        assert len(results.best_flights) == 2
        assert len(results.other_flights) == 1

        direct = results.best_flights[0]
        assert direct.price == 1820
        assert direct.stops == 0
        assert direct.total_duration == 855
        assert direct.flights[0].flight_number == "NH 107"
        assert direct.carbon_emissions.is_better_than_typical

        connecting = results.best_flights[1]
        assert connecting.stops == 1
        assert connecting.layovers[0].id == "SFO"

    @respx.mock
    async def test_arrival_and_departure_times_feed_the_planner(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_search"))
        )
        results = await flights_search(
            departure_id="JFK",
            arrival_id="HND",
            outbound_date="2026-08-10",
            return_date="2026-08-16",
            is_round_trip=True,
            client=serp,
        )

        itinerary = results.best_flights[0]
        assert itinerary.departs_at.isoformat() == "2026-08-10T08:30:00"
        assert itinerary.arrives_at.isoformat() == "2026-08-11T12:45:00"
        assert itinerary.arrival_airport_id == "HND"

    @respx.mock
    async def test_null_price_survives(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_search"))
        )
        results = await flights_search(
            departure_id="JFK",
            arrival_id="HND",
            outbound_date="2026-08-10",
            return_date="2026-08-16",
            is_round_trip=True,
            client=serp,
        )
        # 文档 §7：price 为 null 要标成"价格暂无"，不能崩
        assert results.other_flights[0].price is None

    @respx.mock
    async def test_request_parameter_mapping(self, serp, load_fixture):
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_search"))
        )
        await flights_search(
            departure_id="pek",
            arrival_id="hgh",
            outbound_date="2026-08-10",
            return_date="2026-08-16",
            is_round_trip=True,
            passengers=2,
            children=1,
            travel_class="business",
            client=serp,
        )

        params = route.calls.last.request.url.params
        assert params["engine"] == "google_flights"
        assert params["departure_id"] == "PEK"
        assert params["type"] == "1"  # 1=往返
        assert params["adults"] == "2"
        assert params["children"] == "1"
        assert params["travel_class"] == "3"
        assert params["currency"] == "CNY"

    @respx.mock
    async def test_one_way_omits_return_date(self, serp, load_fixture):
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("flights_search"))
        )
        await flights_search(
            departure_id="PEK",
            arrival_id="HGH",
            outbound_date="2026-08-10",
            is_round_trip=False,
            client=serp,
        )
        params = route.calls.last.request.url.params
        assert params["type"] == "2"  # 2=单程
        assert "return_date" not in params

    @respx.mock
    async def test_round_trip_without_return_date_never_hits_the_network(self, serp):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(InvalidParams):
            await flights_search(
                departure_id="PEK",
                arrival_id="HGH",
                outbound_date="2026-08-10",
                is_round_trip=True,
                client=serp,
            )
        # 这个请求必然返回空结果，白烧一次额度
        assert route.call_count == 0

    @respx.mock
    async def test_return_before_departure_is_rejected_locally(self, serp):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(InvalidParams):
            await flights_search(
                departure_id="PEK",
                arrival_id="HGH",
                outbound_date="2026-08-16",
                return_date="2026-08-10",
                is_round_trip=True,
                client=serp,
            )
        assert route.call_count == 0


# -------------------------------------------------------------------- 酒店
class TestHotelsAutocomplete:
    @respx.mock
    async def test_classifies_suggestions(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_autocomplete"))
        )

        suggestions = await hotels_autocomplete("day inn", client=serp)

        assert len(suggestions) == 3
        brand, single, search_term = suggestions
        # 有 property_token 才是具体门店，才能走单店模式（文档 §4.2）
        assert not brand.is_single_property
        assert single.is_single_property
        assert single.location == "4400 Connecticut Ave NW, Washington"
        assert not search_term.is_single_property
        assert search_term.autocomplete_suggestion == "day inn hotel near me"

    @respx.mock
    async def test_locale_is_passed_through(self, serp, load_fixture):
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_autocomplete"))
        )
        await hotels_autocomplete("杭州 西湖", client=serp)

        params = route.calls.last.request.url.params
        assert params["engine"] == "google_hotels_autocomplete"
        assert (params["gl"], params["hl"], params["currency"]) == ("cn", "zh-CN", "CNY")


class TestHotelsSearch:
    async def _search(self, client, **kw):
        return await hotels_search(
            check_in_date="2026-08-10",
            check_out_date="2026-08-16",
            q="Bali Resorts",
            client=client,
            **kw,
        )

    @respx.mock
    async def test_ads_and_properties_are_merged(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )

        candidates = await self._search(serp)

        assert len(candidates) == 4
        # 广告位常常更便宜，不能过滤掉，但要打标签（文档 §7.11）
        assert [c.is_ad for c in candidates] == [True, True, False, False]
        assert candidates[0].source == "Booking.com"

    @respx.mock
    async def test_nearby_places_carry_the_location_information(self, serp, load_fixture):
        """**Google Hotels 不返回门牌号地址**——`properties[]` 里根本没有 address
        字段。周边地标 + 到那里的耗时是它给出的唯一"这地方在哪儿"的信息。"""
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )

        candidates = await self._search(serp)
        with_nearby = next(c for c in candidates if c.nearby_places)

        assert not with_nearby.address  # 证实：这个来源确实给不出地址
        place = with_nearby.nearby_places[0]
        assert place.name == "I Gusti Ngurah Rai International Airport"
        # transportations 可能有多条，取第一条（Google 按由近及远排）
        assert place.mode == "Taxi"
        assert place.duration == "1 hr 9 min"

    @respx.mock
    async def test_nearby_place_label_is_human_readable(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )

        candidates = await self._search(serp)
        place = next(c for c in candidates if c.nearby_places).nearby_places[0]

        assert place.label.startswith("I Gusti")
        assert "打车" in place.label  # Taxi → 中文

    @respx.mock
    async def test_a_hotel_without_nearby_places_is_fine(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )

        candidates = await self._search(serp)

        assert any(not c.nearby_places for c in candidates)  # 前提：确实有这种
        assert all(isinstance(c.nearby_places, list) for c in candidates)

    @respx.mock
    async def test_hotel_class_from_both_shapes(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        candidates = await self._search(serp)

        assert candidates[1].hotel_class == 4  # ads 里是 int
        assert candidates[3].hotel_class == 5  # properties 里是 "5-star hotel"

    @respx.mock
    async def test_vacation_rental_without_hotel_class_does_not_crash(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        candidates = await self._search(serp)

        rental = candidates[2]
        assert rental.kind == "vacation rental"
        assert rental.hotel_class is None
        assert rental.total_price == 114

    @respx.mock
    async def test_coordinates_are_tagged_wgs84(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        candidates = await self._search(serp)

        # Google 给的是 WGS-84；标错坐标系会让后续路径规划静默偏几百米
        assert all(c.location.crs == "WGS84" for c in candidates if c.location)

    @respx.mock
    async def test_amenities_are_trimmed(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        candidates = await self._search(serp)
        assert all(len(c.amenities) <= 6 for c in candidates)

    @respx.mock
    async def test_ads_price_lands_on_nightly_not_total(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        candidates = await self._search(serp)

        ad = candidates[0]
        assert ad.nightly_price == 70
        assert ad.total_price is None  # ads 没有 total_rate，不要凭空造一个

    @respx.mock
    async def test_total_rate_keeps_before_tax_breakdown(self, serp, load_fixture):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("hotels_search"))
        )
        hotel = (await self._search(serp))[3]
        assert hotel.total_rate.extracted_lowest == 990
        assert hotel.total_rate.extracted_before_taxes_fees == 825

    @respx.mock
    async def test_children_ages_mismatch_is_rejected_locally(self, serp):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(InvalidParams):
            await self._search(serp, children=2, children_ages=[5])
        assert route.call_count == 0


# --------------------------------------------------------------------- POI
class TestPoiKeyword:
    @respx.mock
    async def test_parses_attractions(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )

        pois = await poi_keyword("景点", region="杭州市", client=amap)

        # 第 4 条没有坐标，无法参与路径规划，必须被丢掉而不是留个空壳
        assert [p.name for p in pois] == ["西湖风景名胜区", "太子湾公园", "雷峰塔景区"]
        assert all(p.location.crs == "GCJ02" for p in pois)

    @respx.mock
    async def test_free_and_paid_tickets(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        pois = await poi_keyword("景点", region="杭州市", client=amap)

        # cost 字段可能是"免费"这种中文，直接 float() 会炸
        assert pois[0].ticket_cost == 0.0
        assert pois[2].ticket_cost == 40.0

    @respx.mock
    async def test_business_fields(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        west_lake = (await poi_keyword("景点", region="杭州市", client=amap))[0]

        assert west_lake.rating == 4.9
        assert west_lake.tel == "0571-87179613"
        assert west_lake.opentime_today == "全天开放"
        assert west_lake.business_area == "西湖景区"
        assert west_lake.photos == ["https://store.is.autonavi.com/showpic/xihu.jpg"]

    @respx.mock
    async def test_entrance_overrides_centre_for_routing(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        pois = await poi_keyword("景点", region="杭州市", client=amap)

        # 大景区中心点常落在湖里山里，有入口坐标就该用入口
        assert pois[0].entrance is not None
        assert pois[0].routing_point == pois[0].entrance
        assert pois[1].entrance is None
        assert pois[1].routing_point == pois[1].location

    @respx.mock
    async def test_request_parameter_mapping(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        await poi_keyword("景点", region="杭州市", client=amap)

        params = route.calls.last.request.url.params
        assert params["keywords"] == "景点"
        assert params["region"] == "杭州市"
        assert params["city_limit"] == "true"
        # 不带 business 就拿不到 rating/cost/opentime（文档 §11.4）
        assert "business" in params["show_fields"]
        assert params["types"].startswith("110000")

    @respx.mock
    async def test_city_limit_is_dropped_without_region(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        await poi_keyword("西湖", client=amap)
        assert "city_limit" not in route.calls.last.request.url.params

    async def test_multiple_keywords_rejected(self, amap):
        # 高德一次只认一个关键词（文档 §11.6）
        with pytest.raises(InvalidParams):
            await poi_keyword("西湖,灵隐寺", client=amap)

    async def test_paging_limit_enforced_locally(self, amap):
        # page_size × page_num > 200 会返回空，不如提前报错
        with pytest.raises(InvalidParams):
            await poi_keyword("景点", page_size=25, page_num=9, client=amap)

    async def test_page_size_bounds(self, amap):
        with pytest.raises(InvalidParams):
            await poi_keyword("景点", page_size=50, client=amap)


class TestPoiAround:
    @respx.mock
    async def test_request_parameter_mapping(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v5/place/around").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        await poi_around(120.156209, 30.274648, radius=3000, client=amap)

        params = route.calls.last.request.url.params
        assert params["location"] == "120.156209,30.274648"
        assert params["radius"] == "3000"
        assert params["sortrule"] == "distance"

    async def test_radius_bounds(self, amap):
        with pytest.raises(InvalidParams):
            await poi_around(120.15, 30.27, radius=60000, client=amap)


class TestPoiDetail:
    @respx.mock
    async def test_ids_are_joined(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v5/place/detail").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_poi_keyword"))
        )
        await poi_detail(["B0FFFABGHR", "B0FFFAB8G6"], client=amap)

        params = route.calls.last.request.url.params
        assert params["id"] == "B0FFFABGHR,B0FFFAB8G6"
        assert "navi" in params["show_fields"]

    async def test_too_many_ids_rejected(self, amap):
        with pytest.raises(InvalidParams):
            await poi_detail([f"ID{i}" for i in range(21)], client=amap)

    async def test_empty_ids_rejected(self, amap):
        with pytest.raises(InvalidParams):
            await poi_detail(["", "  "], client=amap)


# ---------------------------------------------------------------- 路径规划
class TestDistanceBatch:
    @respx.mock
    async def test_parses_results_and_flags_failures(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v3/distance").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_distance"))
        )
        origins = [
            GeoPoint.gcj02(116.48, 39.99),
            GeoPoint.gcj02(116.31, 39.99),
            GeoPoint.gcj02(116.43, 39.90),
        ]

        results = await distance_batch(origins, GeoPoint.gcj02(116.40, 39.91), client=amap)

        assert [r.origin_index for r in results] == [0, 1, 2]
        assert results[0].distance_m == 12050
        assert results[0].duration_min == 45
        # 第三个点不可达：必须能被识别出来，否则会被排到"最近"的位置
        assert not results[2].ok
        assert results[2].error_code == "1"
        assert results[2].distance_m is None

    @respx.mock
    async def test_auto_chunks_above_one_hundred_origins(self, amap):
        def _payload(count: int, offset: int) -> dict:
            return {
                "status": "1",
                "info": "OK",
                "results": [
                    {"origin_id": str(i + 1), "distance": str(1000 + offset + i), "duration": "600"}
                    for i in range(count)
                ],
            }

        route = respx.get(f"{AMAP_BASE}/v3/distance").mock(
            side_effect=[
                httpx.Response(200, json=_payload(100, 0)),
                httpx.Response(200, json=_payload(50, 100)),
            ]
        )
        origins = [GeoPoint.gcj02(116.0 + i * 0.001, 39.9) for i in range(150)]

        results = await distance_batch(origins, GeoPoint.gcj02(116.40, 39.91), client=amap)

        assert route.call_count == 2
        assert len(results) == 150
        # 分批后的下标换算最容易写错，这里逐一核对
        assert [r.origin_index for r in results] == list(range(150))
        assert results[100].distance_m == 1100

    async def test_empty_origins_rejected(self, amap):
        with pytest.raises(InvalidParams):
            await distance_batch([], GeoPoint.gcj02(116.4, 39.9), client=amap)

    async def test_invalid_mode_rejected(self, amap):
        with pytest.raises(InvalidParams):
            await distance_batch(
                [GeoPoint.gcj02(116.4, 39.9)], GeoPoint.gcj02(116.5, 39.9), mode=2, client=amap
            )

    @respx.mock
    async def test_wgs84_origins_are_converted_before_sending(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v3/distance").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_distance"))
        )
        # 酒店坐标来自 Google，是 WGS-84；不转换就会静默偏移几百米
        await distance_batch(
            [GeoPoint.wgs84(116.404, 39.915)], GeoPoint.gcj02(116.40, 39.91), client=amap
        )

        sent = route.calls.last.request.url.params["origins"]
        assert sent != "116.404000,39.915000"
        lng, lat = (float(x) for x in sent.split(","))
        assert lng > 116.404 and lat > 39.915


class TestDirectionTransit:
    """公交换乘：**v3 与 v5 两版都要吃得下。**

    主链路默认走 v5，但 v3 仍受支持。两版的响应结构不同——v5 把时长票价挪进了
    `cost` 对象——所以每条断言都得在两版上各跑一遍，否则切版本时会静默出错。
    """

    URLS = {
        "v3": f"{AMAP_BASE}/v3/direction/transit/integrated",
        "v5": f"{AMAP_BASE}/v5/direction/transit/integrated",
    }

    def _mock(self, version: str, load_fixture):
        """按版本挂 mock。v5 的 fixture 从 v3 的改造而来，只动结构不动数值，
        这样两版的断言可以完全一致——数值不同就说明解析错了。"""
        payload = load_fixture("amap_transit")
        if version == "v5":
            payload = _as_v5(payload)
        return respx.get(self.URLS[version]).mock(
            return_value=httpx.Response(200, json=payload)
        )

    @pytest.mark.parametrize("version", ["v3", "v5"])
    @respx.mock
    async def test_parses_best_transit(self, amap, load_fixture, monkeypatch, version):
        monkeypatch.setattr("app.config.settings.amap_route_version", version)
        self._mock(version, load_fixture)

        leg = await direction_transit(
            GeoPoint.gcj02(116.4815, 39.9905),
            GeoPoint.gcj02(116.3151, 39.9995),
            city="010",
            client=amap,
        )

        assert leg is not None
        assert leg.mode == "transit"
        # 两版必须给出**完全相同**的解析结果
        assert leg.duration_min == 65
        assert leg.cost_cny == 6.0
        assert leg.taxi_cost_cny == 82.0
        assert leg.detail == "地铁15号线 → 地铁10号线（步行 1850 米）"

    @pytest.mark.parametrize("version", ["v3", "v5"])
    @respx.mock
    async def test_no_polyline_leaks_into_the_result(
        self, amap, load_fixture, monkeypatch, version
    ):
        monkeypatch.setattr("app.config.settings.amap_route_version", version)
        self._mock(version, load_fixture)
        leg = await direction_transit(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.31, 39.99),
            city="010", client=amap,
        )
        assert "polyline" not in leg.model_dump_json()

    @respx.mock
    async def test_v3_request_shape(self, amap, load_fixture, monkeypatch):
        from datetime import datetime

        monkeypatch.setattr("app.config.settings.amap_route_version", "v3")
        route = self._mock("v3", load_fixture)
        await direction_transit(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.31, 39.99),
            city="010", depart_at=datetime(2026, 8, 10, 8, 30), client=amap,
        )

        params = route.calls.last.request.url.params
        assert params["city"] == "010"
        assert params["date"] == "2026-8-10"  # 高德要的是不补零的格式
        assert params["time"] == "08:30"
        assert params["extensions"] == "all"

    @respx.mock
    async def test_v5_request_shape(self, amap, load_fixture, monkeypatch):
        """v5 的入参和 v3 不是一回事，三处都会静默出错。"""
        from datetime import datetime

        monkeypatch.setattr("app.config.settings.amap_route_version", "v5")
        route = self._mock("v5", load_fixture)
        await direction_transit(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.31, 39.99),
            city="010", depart_at=datetime(2026, 8, 10, 8, 30), client=amap,
        )

        params = route.calls.last.request.url.params
        assert params["city1"] == "010"       # 不是 city
        assert params["city2"] == "010"
        assert params["time"] == "8-30"       # 不是 08:30
        # **不传 show_fields=cost 就一个数都拿不到**
        assert "cost" in params["show_fields"]

    @pytest.mark.parametrize("version", ["v3", "v5"])
    @respx.mock
    async def test_no_route_returns_none_instead_of_raising(
        self, amap, monkeypatch, version
    ):
        monkeypatch.setattr("app.config.settings.amap_route_version", version)
        respx.get(self.URLS[version]).mock(
            return_value=httpx.Response(200, json={"status": "1", "route": {"transits": []}})
        )
        leg = await direction_transit(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.31, 39.99),
            city="010", client=amap,
        )
        # 郊区景点常常没有公交方案，这是正常结果，planner 会降级到驾车。
        # v5 的 strategy=6（地铁图模式）在很多 OD 上也是这个形态。
        assert leg is None

    async def test_city_is_required(self, amap):
        with pytest.raises(InvalidParams):
            await direction_transit(
                GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.31, 39.99), city="", client=amap
            )


def _as_v5(payload: dict) -> dict:
    """把 v3 的公交响应改造成 v5 的结构，**数值一个不动**。

    v3: transits[].duration / .cost      route.taxi_cost
    v5: transits[].cost.duration / .transit_fee   route.cost.taxi_fee
    """
    import copy

    out = copy.deepcopy(payload)
    route = out.get("route") or {}
    if "taxi_cost" in route:
        route["cost"] = {"taxi_fee": route.pop("taxi_cost")}
    for transit in route.get("transits") or []:
        transit["cost"] = {
            "duration": transit.pop("duration", "0"),
            "transit_fee": transit.pop("cost", None),
        }
    return out


class TestDirectionDriving:
    @respx.mock
    async def test_parses_first_path(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v3/direction/driving").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_driving"))
        )

        leg = await direction_driving(
            GeoPoint.gcj02(116.4815, 39.9905), GeoPoint.gcj02(116.4344, 39.9082), client=amap
        )

        assert leg.mode == "driving"
        assert leg.distance_m == 21000
        assert leg.duration_min == 40
        assert leg.cost_cny == 5.0
        assert leg.taxi_cost_cny == 78.0
        assert leg.restriction == "途经限行路段"

    @respx.mock
    async def test_no_turn_by_turn_directions(self, amap, load_fixture):
        """驾车只报时间，不产出路线。

        原来 detail 是前 3 条转向指引拼起来的（「沿人民南路向南行驶；右转进入
        天府大道…」）——真上路会开导航 App，三步指引既到不了目的地，
        又把输出塞满噪音。
        """
        respx.get(f"{AMAP_BASE}/v3/direction/driving").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_driving"))
        )
        leg = await direction_driving(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.43, 39.90), client=amap
        )

        assert leg.detail == ""
        # 响应里确实有 steps，是我们主动不要的，不是上游没给
        assert load_fixture("amap_driving")["route"]["paths"][0]["steps"]
        assert "polyline" not in leg.model_dump_json()

    @respx.mock
    async def test_time_and_money_still_come_through(self, amap, load_fixture):
        # 去掉的只是路线；排期要时长，决策要过路费和限行
        respx.get(f"{AMAP_BASE}/v3/direction/driving").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_driving"))
        )
        leg = await direction_driving(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.43, 39.90), client=amap
        )

        assert leg.duration_min == 40
        assert leg.cost_cny == 5.0
        assert leg.taxi_cost_cny == 78.0
        assert leg.restriction == "途经限行路段"

    @respx.mock
    async def test_uses_multi_route_strategy_and_all_extensions(self, amap, load_fixture):
        route = respx.get(f"{AMAP_BASE}/v3/direction/driving").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_driving"))
        )
        await direction_driving(
            GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.43, 39.90), client=amap
        )

        params = route.calls.last.request.url.params
        # strategy=10 才返回多条备选；extensions=all 才有过路费/打车费/限行
        assert params["strategy"] == "10"
        assert params["extensions"] == "all"

    @respx.mock
    async def test_empty_paths_returns_none(self, amap):
        respx.get(f"{AMAP_BASE}/v3/direction/driving").mock(
            return_value=httpx.Response(200, json={"status": "1", "route": {"paths": []}})
        )
        assert (
            await direction_driving(
                GeoPoint.gcj02(116.48, 39.99), GeoPoint.gcj02(116.43, 39.90), client=amap
            )
            is None
        )


class TestDirectionWalking:
    @respx.mock
    async def test_parses_path(self, amap, load_fixture):
        respx.get(f"{AMAP_BASE}/v3/direction/walking").mock(
            return_value=httpx.Response(200, json=load_fixture("amap_walking"))
        )

        leg = await direction_walking(
            GeoPoint.gcj02(116.434307, 39.90909), GeoPoint.gcj02(116.434446, 39.90816), client=amap
        )

        assert leg.mode == "walking"
        assert leg.distance_m == 147
        assert leg.duration_min == 2
        assert leg.detail == "步行 147 米"
        assert "polyline" not in leg.model_dump_json()
