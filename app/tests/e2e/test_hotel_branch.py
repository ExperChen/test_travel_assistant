"""酒店分支端到端：锚点、重排、中断、高德降级。"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import respx

from app.agents.hotel_agent import ASK_N, TOP_N
from app.graph.builder import TripRunner
from app.models.trip import TripRequest
from app.tests.e2e._mocks import (
    DISTANCE_URL,
    REGEO_URL,
    distance_payload,
    empty_hotels_payload,
    hotels_payload,
    mock_amap,
    mock_flights,
    mock_hotels,
    outbound_payload,
    pending_of,
    regeo_payload,
)

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "PEK",  # 用 IATA 跳过机场补全，让用例聚焦在酒店上
        "destination_city": "杭州",
        "outbound_date": OUTBOUND,
        "return_date": RETURN,
        "auto_select": True,
    }
    return TripRequest(**{**base, **kw})


def setup(*, hotels: dict | None = None, distances: list[int] | None = None):
    """本文件聚焦酒店，所以只给一个航班方案——否则会先停在航班的中断点上。"""
    mock_amap(distances=distances)
    mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
    return mock_hotels(hotels)


class TestSearchAndRerank:
    @respx.mock
    async def test_picks_the_best_by_combined_score(self):
        setup(distances=[3600, 300, 900, 1500])  # 广告酒店最远

        state = await TripRunner().start(make_request())
        branch = state["hotel"]

        assert state["status"] == "done"
        # 上游给了 4 家，少于 TOP_N=8，全部保留
        assert len(branch.candidates) == 4
        assert branch.selected is not None
        assert branch.candidates == sorted(branch.candidates, key=lambda c: -c.score)

    @respx.mock
    async def test_commute_is_attached_from_a_single_distance_call(self):
        distance = respx.get(DISTANCE_URL).mock(
            return_value=httpx.Response(200, json=distance_payload([600, 900, 1200, 1500]))
        )
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels()

        state = await TripRunner().start(make_request())

        # 一次批量测距覆盖所有候选，而不是每家酒店调一次路径规划
        assert distance.call_count == 1
        assert all(
            c.commute_to_centroid_min is not None for c in state["hotel"].candidates
        )

    @respx.mock
    async def test_hotel_coordinates_are_converted_before_hitting_amap(self):
        distance = respx.get(DISTANCE_URL).mock(
            return_value=httpx.Response(200, json=distance_payload([600, 900, 1200, 1500]))
        )
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels()

        await TripRunner().start(make_request())

        # Google 给的是 WGS-84，不转换就会静默偏几百米
        origins = distance.calls.last.request.url.params["origins"]
        first_lng = float(origins.split("|")[0].split(",")[0])
        assert first_lng > 120.15

    @respx.mock
    async def test_query_uses_the_attraction_business_area(self):
        search = setup()

        await TripRunner().start(make_request())

        q = search.calls.last.request.url.params["q"]
        # mock 的景点没有 business_area，退回「城市+酒店」
        assert q == "杭州市酒店"

    @respx.mock
    async def test_budget_maps_to_max_price(self):
        search = setup()

        await TripRunner().start(make_request(budget_per_night=800))

        assert search.calls.last.request.url.params["max_price"] == "800"

    @respx.mock
    async def test_no_budget_means_no_price_ceiling(self):
        search = setup()

        await TripRunner().start(make_request())

        # 瞎设上限是空结果最常见的原因
        assert "max_price" not in search.calls.last.request.url.params

    @respx.mock
    async def test_ads_participate_in_the_candidate_pool(self):
        setup(distances=[300, 900, 1200, 1500])  # 广告酒店离景点最近

        state = await TripRunner().start(make_request())

        # 广告位常常更便宜，不能因为是广告就丢掉（文档 §7.11）
        assert any(c.is_ad for c in state["hotel"].candidates)


class TestAmapFallback:
    @respx.mock
    async def test_empty_google_results_fall_back_to_amap(self):
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels(empty_hotels_payload())

        state = await TripRunner().start(make_request())
        branch = state["hotel"]

        assert state["status"] == "done"
        assert branch.candidates
        # 降级来源有坐标有评分，但没有房价——路径规划只需要坐标，所以不阻断主流程
        assert all(c.price_unavailable for c in branch.candidates)
        assert all(c.location is not None for c in branch.candidates)
        assert branch.candidates[0].total_price is None

    @respx.mock
    async def test_fallback_warns_the_user_about_missing_prices(self):
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels(empty_hotels_payload())

        state = await TripRunner().start(make_request())

        codes = {w.code for w in state["warnings"]}
        assert "HOTEL_PRICE_UNAVAILABLE" in codes
        message = next(w.message for w in state["warnings"] if w.code == "HOTEL_PRICE_UNAVAILABLE")
        # 必须说清是"查不到价格"，不能让用户以为免费
        assert "价格" in message or "房价" in message


class TestCandidateCount:
    """候选要多（给用户看），选项要少（给用户选）。"""

    @respx.mock
    async def test_more_candidates_are_kept_than_are_asked_about(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)

        branch = (await TripRunner().start(make_request()))["hotel"]

        # 不额外花额度：attach_commute 一次 distance_batch 覆盖 100 个起点
        assert len(branch.candidates) == TOP_N
        assert len(branch.candidates) > ASK_N

    @respx.mock
    async def test_every_candidate_carries_a_location_for_the_map(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)

        branch = (await TripRunner().start(make_request()))["hotel"]

        located = [c for c in branch.candidates if c.location is not None]
        assert located, "至少要有带坐标的候选，否则前端没法打点"
        # 坐标系必须显式带着（架构 §9.1）。SerpAPI 给的是 WGS-84，原样保留，
        # 进高德接口前才 as_gcj02()——裸浮点数传来传去迟早会串
        assert all(c.location.crs == "WGS84" for c in located)

    @respx.mock
    async def test_addresses_are_filled_in_from_the_map(self):
        # Google Hotels 不返回门牌号，广告位连 nearby_places 都没有——实测成都
        # 8 家候选里 7 家位置信息一片空白，只能靠逆地理编码补
        regeo = respx.get(REGEO_URL).mock(
            return_value=httpx.Response(200, json=regeo_payload())
        )
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)

        branch = (await TripRunner().start(make_request()))["hotel"]

        assert all(c.address for c in branch.candidates)
        # 一次批量调用覆盖全部候选，不是每家一次
        assert regeo.call_count == 1

    @respx.mock
    async def test_a_failed_lookup_does_not_break_the_trip(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)
        respx.get(REGEO_URL).mock(return_value=httpx.Response(500))

        state = await TripRunner().start(make_request())

        # 补不到地址不致命：用户还有周边地标和通勤时长可看
        assert state["status"] == "done"
        assert len(state["hotel"].candidates) == TOP_N

    @respx.mock
    async def test_a_thin_market_is_called_out(self):
        setup(hotels=hotels_payload(count=1, with_ads=False))

        state = await TripRunner().start(make_request())

        # 说清是"这地方就这么多"，而不是我们挑剩的
        assert "HOTEL_FEW_CANDIDATES" in {w.code for w in state["warnings"]}

    @respx.mock
    async def test_a_healthy_market_says_nothing(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)

        state = await TripRunner().start(make_request())

        assert "HOTEL_FEW_CANDIDATES" not in {w.code for w in state["warnings"]}


class TestHotelInterrupt:
    @respx.mock
    async def test_only_the_top_few_become_options(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)

        state = await TripRunner().start(make_request(auto_select=False))

        assert len(pending_of(state, "hotel.").options) == ASK_N

    @respx.mock
    async def test_an_out_of_range_answer_falls_back_to_the_first(self):
        setup(hotels=hotels_payload(count=12), distances=[600] * 20)
        runner = TripRunner()
        await runner.start(make_request(auto_select=False), trip_id="trp_oob")

        # 只列了 ASK_N 个选项，回答 "7" 属于越界——不能让它落到第 7 家上
        state = await runner.resume("trp_oob", {"hotel.selection": "7"})

        assert state["hotel"].selected_index == 0

    @respx.mock
    async def test_multiple_candidates_pause_for_the_user(self):
        setup()

        state = await TripRunner().start(make_request(auto_select=False))

        pending = pending_of(state, "hotel.")
        assert pending.id == "hotel.selection"
        # 候选保留 TOP_N=8 家，但**最多只问 ASK_N 个**——选项再多就不是"选择"
        # 而是"翻页"。挂起态下 state 里还没有 hotel 补丁，只能断言上界。
        assert 0 < len(pending.options) <= ASK_N
        # 每个选项都要带价格与通勤，用户才有得选
        for option in pending.options:
            assert "¥" in option.label or "价格暂无" in option.label
            assert "到景点重心" in option.label

    @respx.mock
    async def test_every_option_quotes_the_same_price_basis(self):
        """ads 只有单晚价、organic 才有 total_rate。

        只印其中一个，用户就没法比：实测成都那次第 1 行印「总价 ¥301」、
        第 2 行印「¥190/晚」，看着像 301 比 190 贵，其实前者每晚才 ¥100。
        """
        setup(hotels=hotels_payload(count=6), distances=[600] * 20)

        state = await TripRunner().start(make_request(auto_select=False))

        for option in pending_of(state, "hotel.").options:
            if "价格暂无" in option.label:
                continue
            assert "/晚" in option.label, option.label
            assert "共" in option.label, option.label

    @respx.mock
    async def test_the_options_are_not_all_ads(self):
        # 广告位是 Google 的付费展位，不是"最合适的酒店"。实测成都 8 家候选
        # 里 6 家是广告，前 4 名全被占满，用户等于没得选。
        setup(hotels=hotels_payload(count=2), distances=[600] * 20)

        state = await TripRunner().start(make_request(auto_select=False))

        labels = [o.label for o in pending_of(state, "hotel.").options]
        assert any("广告" not in label for label in labels), labels

    @respx.mock
    async def test_the_answer_maps_back_to_the_right_hotel(self):
        """选项顺序不等于候选顺序——`pick_options` 会把广告位换掉。

        不把序号映回 `top` 的真实下标，用户选的第 2 个会落到别的酒店上。
        """
        setup(hotels=hotels_payload(count=6), distances=[600] * 20)
        runner = TripRunner()
        started = await runner.start(make_request(auto_select=False), trip_id="trp_map")
        options = pending_of(started, "hotel.").options
        expected = options[1].detail["name"]

        state = await runner.resume("trp_map", {"hotel.selection": "2"})

        assert state["hotel"].selected.name == expected

    @respx.mock
    async def test_resume_selects_the_named_hotel(self):
        setup()

        runner = TripRunner()
        started = await runner.start(make_request(auto_select=False), trip_id="trp_hotel")
        expected = pending_of(started, "hotel.").options[1].detail["name"]

        state = await runner.resume("trp_hotel", {"hotel.selection": "2"})

        assert state["status"] == "done"
        assert state["hotel"].selected_index == 1
        assert state["hotel"].selected.name == expected

    @respx.mock
    async def test_auto_select_takes_the_top_ranked_with_a_warning(self):
        setup()

        state = await TripRunner().start(make_request())

        assert state["hotel"].selected_index == 0
        assert "HOTEL_AUTO_PICKED" in {w.code for w in state["warnings"]}


class TestDegradedPaths:
    @respx.mock
    async def test_distance_failure_degrades_ranking_instead_of_failing(self):
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels()
        respx.get(DISTANCE_URL).mock(return_value=httpx.Response(500))

        state = await TripRunner().start(make_request())

        # 拿不到通勤时长不致命，退化成只按价格+评分排
        assert state["status"] == "done"
        assert state["hotel"].candidates
        assert "HOTEL_COMMUTE_UNKNOWN" in {w.code for w in state["warnings"]}

    @respx.mock
    async def test_single_candidate_never_interrupts(self):
        mock_amap()
        mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))
        mock_hotels(hotels_payload(count=1, with_ads=False))

        state = await TripRunner().start(make_request(auto_select=False))

        assert state["status"] == "done"
        assert not state["pending"]
