"""行程编排端到端：时间窗、逐日分配、真实路线、放不下的备选。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import respx

from app.graph.builder import TripRunner
from app.models.trip import TripRequest
from app.tests.e2e._mocks import (
    DRIVING_URL,
    TRANSIT_URL,
    WALKING_URL,
    driving_payload,
    hotels_payload,
    mock_amap,
    mock_flights,
    mock_hotels,
    outbound_payload,
    poi_payload,
    return_payload,
    transit_payload,
)

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "PEK",
        "destination_city": "杭州",
        "outbound_date": OUTBOUND,
        "return_date": RETURN,
        "auto_select": True,
    }
    return TripRequest(**{**base, **kw})


def setup(*, return_hour: int = 18, pois: dict | None = None, hotels: dict | None = None):
    mock_amap(pois=pois)
    mock_flights(
        OUTBOUND,
        RETURN,
        outbound_result=outbound_payload(OUTBOUND, count=1),
        return_result=return_payload(RETURN, return_hour),
    )
    mock_hotels(hotels)


class TestItineraryShape:
    @respx.mock
    async def test_produces_one_plan_per_usable_day(self):
        setup()

        state = await TripRunner().start(make_request())
        itinerary = state["itinerary"]

        assert state["status"] == "done"
        # 落地日 → 返程日共 4 天；返程 18:00 起飞，末日仍有可用时间
        assert len(itinerary.days) == 4
        assert [d.day_index for d in itinerary.days] == [1, 2, 3, 4]

    @respx.mock
    async def test_first_day_starts_after_landing_and_check_in(self):
        setup()

        state = await TripRunner().start(make_request())
        first = state["itinerary"].days[0]

        kinds = [i.kind for i in first.items]
        assert kinds[0] == "airport"  # 从下飞机那一刻开始读得通
        assert kinds[1] == "hotel"
        arrive_at = state["flight"].arrive_at
        assert first.items[0].start_time == arrive_at
        assert first.items[1].start_time > arrive_at  # 机场到酒店要通勤

    @respx.mock
    async def test_every_scheduled_item_fits_inside_its_window(self):
        setup()

        for day in (await TripRunner().start(make_request()))["itinerary"].days:
            for item in day.items:
                if item.kind == "attraction":
                    assert item.start_time >= day.window_start
                    assert item.end_time <= day.window_end

    @respx.mock
    async def test_each_day_returns_to_the_hotel(self):
        setup()

        for day in (await TripRunner().start(make_request()))["itinerary"].days:
            if any(i.kind == "attraction" for i in day.items):
                assert day.items[-1].kind == "hotel"
                assert day.legs[-1].to_ref == "hotel"

    @respx.mock
    async def test_legs_connect_consecutive_items(self):
        setup()

        day = next(
            d
            for d in (await TripRunner().start(make_request()))["itinerary"].days
            if len([i for i in d.items if i.kind == "attraction"]) >= 2
        )
        attraction_refs = [i.ref_id for i in day.items if i.kind == "attraction"]
        # 第一段从酒店出发，最后一段回到酒店，中间首尾相接
        assert day.legs[0].from_ref == "hotel"
        assert [leg.to_ref for leg in day.legs] == [*attraction_refs, "hotel"]

    @respx.mock
    async def test_totals_are_aggregated(self):
        setup()

        totals = (await TripRunner().start(make_request()))["itinerary"].totals()

        assert totals["commute_min"] > 0
        assert totals["ticket_cost_cny"] >= 0


class TestTransportMode:
    @respx.mock
    async def test_transit_is_used_for_longer_hops(self):
        transit = respx.get(TRANSIT_URL)
        setup()

        await TripRunner().start(make_request(transport="transit"))

        assert transit.call_count > 0
        # 跨城市的公交查询必须带 citycode，否则高德直接报错
        assert transit.calls.last.request.url.params["city"] == "0571"

    @respx.mock
    async def test_driving_mode_never_calls_transit(self):
        transit = respx.get(TRANSIT_URL)
        driving = respx.get(DRIVING_URL)
        setup()

        await TripRunner().start(make_request(transport="driving"))

        assert transit.call_count == 0
        assert driving.call_count > 0

    @respx.mock
    async def test_short_hops_walk_instead_of_querying_transit(self):
        # 景点全部挤在酒店（120.15, 30.24）旁边 200m 内
        close_pois = {
            "status": "1",
            "info": "OK",
            "count": "3",
            "pois": [
                {
                    "name": f"近景点{i}",
                    "id": f"near-{i}",
                    "location": f"{120.1500 + i * 0.001:.6f},30.240000",
                    "typecode": "110000",
                    "business": {"rating": "4.8", "opentime_today": "08:00-20:00"},
                }
                for i in range(3)
            ],
        }
        # handle 要在 setup() 之前取：respx 按 pattern 去重，之后再 respx.get(同一个 URL)
        # 会把已注册的响应重置掉
        walking = respx.get(WALKING_URL)
        transit = respx.get(TRANSIT_URL)
        # 只留一家酒店，确保选中的就是景点旁边那家（默认候选里的广告酒店在 15km 外）
        setup(pois=close_pois, hotels=hotels_payload(count=1, with_ads=False))

        await TripRunner().start(make_request(transport="transit"))

        # 相隔几百米还去查公交换乘，既费额度又给出可笑的方案
        assert walking.call_count > 0
        assert transit.call_count == 0


class TestTimeWindowPressure:
    @respx.mock
    async def test_early_return_flight_leaves_no_room_on_the_last_day(self):
        setup(return_hour=8)  # 早上 8 点返程

        state = await TripRunner().start(make_request())
        itinerary = state["itinerary"]

        # 扣掉值机 buffer 与通勤后返程日无可用时间：那天整个不该出现在行程里，
        # 但必须告诉用户为什么少了一天
        assert all(day.day != RETURN for day in itinerary.days)
        assert "DAYS_WITHOUT_TIME" in {w.code for w in state["warnings"]}

    @respx.mock
    async def test_a_must_visit_survives_a_crowded_schedule(self):
        """用户点名的景点掉进备选，是最糟的失败方式。

        他会拿到一份看起来正常的行程，直到出发前才发现没安排。
        """
        crowded = {
            "status": "1",
            "info": "OK",
            "count": "20",
            "pois": [
                {
                    "name": f"景点{i}",
                    "id": f"spot-{i}",
                    "location": f"{120.10 + i * 0.02:.6f},{30.20 + i * 0.02:.6f}",
                    "typecode": "110000",
                    "business": {"rating": "4.9", "opentime_today": "09:00-21:00"},
                }
                for i in range(20)
            ],
        }
        setup(pois=crowded)

        state = await TripRunner().start(make_request(must_visit=["景点0"]))
        itinerary = state["itinerary"]
        # 关键字精检返回的是独立 POI，名字不一定等于用户输入，按标志找
        must = {a.name for a in state["attractions"].selected if a.must_visit}

        assert must, "前提：确实有必去景点入选"
        assert itinerary.unscheduled, "前提：这一批确实排不下，否则本用例没验证到东西"
        assert not (must & {a.name for a in itinerary.unscheduled})
        assert must <= {i.name for d in itinerary.days for i in d.items}

    @respx.mock
    async def test_a_stranded_must_visit_is_called_out(self):
        # 排不进去可以（营业时间/返程航班是硬约束），但必须明说
        setup(pois=poi_payload(3))

        state = await TripRunner().start(make_request(must_visit=["景点0"]))
        stranded = [a for a in state["itinerary"].unscheduled if a.must_visit]

        codes = {w.code for w in state["warnings"]}
        assert bool(stranded) == ("MUST_VISIT_IMPOSSIBLE" in codes)

    @respx.mock
    async def test_overflow_attractions_land_in_unscheduled(self):
        many = {
            "status": "1",
            "info": "OK",
            "count": "20",
            "pois": [
                {
                    "name": f"景点{i}",
                    "id": f"spot-{i}",
                    "location": f"{120.10 + i * 0.02:.6f},{30.20 + i * 0.02:.6f}",
                    "typecode": "110000",
                    "business": {"rating": "4.8", "opentime_today": "09:00-17:00"},
                }
                for i in range(20)
            ],
        }
        setup(pois=many)

        state = await TripRunner().start(make_request())
        itinerary = state["itinerary"]

        scheduled = sum(1 for d in itinerary.days for i in d.items if i.kind == "attraction")
        # 排不下的必须进备选而不是被悄悄丢掉
        assert itinerary.unscheduled
        assert scheduled + len(itinerary.unscheduled) == len(state["attractions"].selected)
        assert "ATTRACTIONS_UNSCHEDULED" in {w.code for w in state["warnings"]}

    @respx.mock
    async def test_opening_hours_are_respected(self):
        late_open = {
            "status": "1",
            "info": "OK",
            "count": "1",
            "pois": [
                {
                    "name": "下午才开门",
                    "id": "late-1",
                    "location": "120.210000,30.260000",
                    "typecode": "110000",
                    "business": {"rating": "4.8", "opentime_today": "14:00-20:00"},
                }
            ],
        }
        setup(pois=late_open)

        state = await TripRunner().start(make_request())

        for day in state["itinerary"].days:
            for item in day.items:
                if item.kind == "attraction":
                    assert item.start_time.hour >= 14
                    assert item.end_time.hour <= 20


class TestReturnLegFitsTheWindow:
    """回程腿必须纳入当日时间窗校验。

    真实运行踩到的：成都行程把「青城后山 18:54-20:54」排了进去（结束时刻确实在
    21:00 窗口内），然后回程公交 232 分钟，**凌晨 00:46 才到酒店**。
    只校验景点结束时间是不够的。
    """

    @respx.mock
    async def test_a_stop_with_a_long_way_back_is_not_scheduled(self):
        setup()
        # 去程很快，回程极慢——正是远郊景点的形态
        respx.get(TRANSIT_URL).mock(
            side_effect=lambda request: httpx.Response(
                200,
                json=transit_payload(duration_s=600)
                if "destination=120.15" not in str(request.url)
                else transit_payload(duration_s=20000),
            )
        )

        state = await TripRunner().start(make_request())

        for day in state["itinerary"].days:
            if not day.items:
                continue
            # 每天最后一件事必须是回到酒店，且不能越过当日时间窗
            assert day.items[-1].end_time <= day.window_end

    @respx.mock
    async def test_nobody_gets_home_after_the_window_closes(self):
        setup()

        state = await TripRunner().start(make_request())

        for day in state["itinerary"].days:
            for item in day.items:
                assert item.end_time <= day.window_end, (
                    f"第 {day.day_index} 天 {item.name} 结束于 {item.end_time}，"
                    f"超出时间窗 {day.window_end}"
                )

    @respx.mock
    async def test_returning_home_never_crosses_midnight(self):
        setup()

        state = await TripRunner().start(make_request())

        for day in state["itinerary"].days:
            for item in day.items:
                # 跨零点说明回程腿没被校验（实测出现过 00:46 回店）
                assert item.end_time.date() == day.day


class TestDegradedRouting:
    @respx.mock
    async def test_transit_with_no_route_falls_back_to_driving(self):
        # 覆盖必须写在 setup() 之后（respx 按 pattern 去重，后写的赢），
        # 而重新取 driving 的 handle 时必须把 payload 一并给上，否则会被重置成空响应
        setup()
        transit = respx.get(TRANSIT_URL).mock(
            return_value=httpx.Response(200, json={"status": "1", "route": {"transits": []}})
        )
        driving = respx.get(DRIVING_URL).mock(
            return_value=httpx.Response(200, json=driving_payload())
        )

        state = await TripRunner().start(make_request(transport="transit"))

        # 郊区景点常常没有公交方案，这不是错误
        assert transit.call_count > 0
        assert driving.call_count > 0
        assert state["status"] == "done"

    @respx.mock
    async def test_no_route_at_all_still_produces_an_estimated_leg(self):
        setup()
        empty = {"status": "1", "route": {"paths": [], "transits": []}}
        respx.get(TRANSIT_URL).mock(return_value=httpx.Response(200, json=empty))
        respx.get(DRIVING_URL).mock(return_value=httpx.Response(200, json=empty))
        respx.get(WALKING_URL).mock(return_value=httpx.Response(200, json=empty))

        state = await TripRunner().start(make_request())
        legs = [leg for d in state["itinerary"].days for leg in d.legs]

        assert state["status"] == "done"
        assert legs
        # 估算出来的段要如实标注，不能冒充实测结果
        assert any("估算" in leg.detail for leg in legs)


@respx.mock
async def test_itinerary_carries_no_polyline_into_the_state():
    setup()

    state = await TripRunner().start(make_request())

    # polyline 单条上万字符，进了状态就会进 LLM 上下文
    assert "polyline" not in state["itinerary"].model_dump_json()


@respx.mock
async def test_items_are_chronological_within_a_day():
    setup()

    for day in (await TripRunner().start(make_request()))["itinerary"].days:
        times = [i.start_time for i in day.items]
        assert times == sorted(times)
        assert all(isinstance(t, datetime) for t in times)


class TestRoutingFailureIsolation:
    """一段路线查不动不能毁掉整份行程。

    深圳实测：某几段公交换乘查询反复超时，重试耗尽后异常一路上抛，
    把已经排好的 4 天行程整个作废了。
    """

    @respx.mock
    async def test_timeout_on_one_leg_degrades_to_an_estimate(self):
        setup()
        respx.get(TRANSIT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        respx.get(DRIVING_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

        state = await TripRunner().start(make_request())

        assert state["status"] == "done"
        assert state["itinerary"] is not None
        legs = [leg for d in state["itinerary"].days for leg in d.legs]
        assert legs
        # 估算出来的段要如实标注是查询失败，不能冒充实测
        assert any("查询失败" in leg.detail for leg in legs)

    @respx.mock
    async def test_amap_outage_still_produces_a_usable_itinerary(self):
        setup()
        for url in (TRANSIT_URL, DRIVING_URL, WALKING_URL):
            respx.get(url).mock(return_value=httpx.Response(500))

        state = await TripRunner().start(make_request())

        assert state["status"] == "done"
        scheduled = sum(
            1 for d in state["itinerary"].days for i in d.items if i.kind == "attraction"
        )
        assert scheduled > 0
