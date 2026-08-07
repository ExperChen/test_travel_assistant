"""航班分支端到端：机场歧义中断、方案选择中断、兜底重试、返程时刻。

这里是全流程里唯一花 SerpAPI 额度的地方（免费额度 250/月），
所以"什么时候发请求、发几次"本身就是要测的行为。
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from app.graph.builder import TripRunner
from app.models.errors import ErrorCode
from app.models.trip import TripRequest
from app.tests.e2e._mocks import (
    OUTBOUND_TOKEN,
    SERP_URL,
    autocomplete_payload,
    empty_flights_payload,
    hotels_payload,
    mock_downstream,
    outbound_payload,
    pending_of,
    return_payload,
)

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)

BEIJING_AIRPORTS = [("PEK", "首都国际机场"), ("PKX", "大兴国际机场")]
HANGZHOU_AIRPORTS = [("HGH", "萧山国际机场")]


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "北京",
        "destination_city": "杭州",
        "outbound_date": OUTBOUND,
        "return_date": RETURN,
    }
    return TripRequest(**{**base, **kw})


def quiet_downstream():
    """下游只给一个酒店候选。

    并行下任何一个中断都会暂停整张图——酒店分支若也中断，航班分支就跑不完，
    本文件想测的航班行为反而看不到了。
    """
    mock_downstream(hotels=hotels_payload(count=1, with_ads=False))

def mock_autocomplete(city: str, airports: list[tuple[str, str]]):
    return respx.get(SERP_URL, params__contains={"q": city}).mock(
        return_value=httpx.Response(200, json=autocomplete_payload(city, airports))
    )


def mock_return_leg(hour: int = 18):
    return respx.get(SERP_URL, params__contains={"departure_token": OUTBOUND_TOKEN}).mock(
        return_value=httpx.Response(200, json=return_payload(RETURN, hour))
    )


def mock_outbound(*payloads: dict):
    """去程搜索。多个 payload 时按调用顺序返回，用来测兜底链。"""
    return respx.get(SERP_URL, params__contains={"engine": "google_flights"}).mock(
        side_effect=[httpx.Response(200, json=p) for p in payloads]
    )


class TestAirportInterrupt:
    @respx.mock
    async def test_ambiguous_departure_city_pauses_for_the_user(self):
        quiet_downstream()
        mock_autocomplete("北京", BEIJING_AIRPORTS)
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)

        state = await TripRunner().start(make_request())

        assert state["status"] == "waiting_input"
        pending = pending_of(state, "flight.")
        assert pending.id == "flight.departure_airport"
        assert [o.key for o in pending.options] == ["PEK", "PKX"]
        assert pending.default == "PEK"
        assert "大兴国际机场" in pending.options[1].label

    @respx.mock
    async def test_resume_applies_the_choice_and_continues(self):
        quiet_downstream()
        mock_autocomplete("北京", BEIJING_AIRPORTS)
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        # 用户会选 PKX，所以航段的出发机场也得是 PKX
        mock_outbound(outbound_payload(OUTBOUND, count=1, dep_id="PKX"))

        runner = TripRunner()
        started = await runner.start(make_request(), trip_id="trp_test")
        assert started["status"] == "waiting_input"

        resumed = await runner.resume("trp_test", {"flight.departure_airport": "PKX"})

        assert resumed["flight"].params.departure_airport_id == "PKX"
        # 航班分支已走完（下一个中断点是酒店的事，不归本用例管）
        assert resumed["flight"].selected is not None

    @respx.mock
    async def test_single_airport_city_never_interrupts(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=1))

        state = await TripRunner().start(make_request())

        assert state["flight"].selected is not None
        pending = pending_of(state, "flight.")
        assert pending is None or not pending.id.startswith("flight.")

    @respx.mock
    async def test_iata_input_skips_autocomplete(self):
        quiet_downstream()
        departure = mock_autocomplete("PEK", BEIJING_AIRPORTS)
        arrival = mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=1))

        state = await TripRunner().start(make_request(departure_city="PEK"))

        # 用户直接给了三字码就不必再补全，省一次额度
        assert departure.call_count == 0
        assert arrival.call_count == 1
        assert state["flight"].params.departure_airport_id == "PEK"

    @respx.mock
    async def test_auto_select_picks_the_first_airport_with_a_warning(self):
        quiet_downstream()
        mock_autocomplete("北京", BEIJING_AIRPORTS)
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=1))

        state = await TripRunner().start(make_request(auto_select=True))

        assert state["status"] == "done"
        assert state["flight"].params.departure_airport_id == "PEK"
        assert "AIRPORT_AUTO_PICKED" in {w.code for w in state["warnings"]}


class TestItineraryInterrupt:
    @respx.mock
    async def test_multiple_candidates_pause_for_the_user(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=2))

        state = await TripRunner().start(make_request())

        pending = pending_of(state, "flight.")
        assert pending.id == "flight.itinerary"
        assert [o.key for o in pending.options] == ["1", "2"]
        assert "直飞" in pending.options[0].label

    @respx.mock
    async def test_each_option_says_which_flight_and_when(self):
        """原来只印「方案1 · 1200 · 2h30m · 直飞」——价格和时长一样时无从下手。

        "几点起飞"才是选航班最关键的信息：早班机还是红眼，直接决定第一天
        还能不能玩。
        """
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=2))

        state = await TripRunner().start(make_request())

        for option in pending_of(state, "flight.").options:
            assert "CA1" in option.label, option.label            # 航班号
            assert "PEK" in option.label and "HGH" in option.label  # 起讫机场
            assert ":" in option.label                              # 起降时刻
            assert "往返 ¥" in option.label                          # 价格是往返总价

    @respx.mock
    async def test_two_options_at_the_same_price_are_still_distinguishable(self):
        # 同价同时长的两班，只有起飞时刻能区分
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=2))

        state = await TripRunner().start(make_request())
        labels = [o.label for o in pending_of(state, "flight.").options]

        assert len(set(labels)) == len(labels)

    @respx.mock
    async def test_resume_selects_the_named_itinerary(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=2))

        runner = TripRunner()
        await runner.start(make_request(), trip_id="trp_pick")
        state = await runner.resume("trp_pick", {"flight.itinerary": "2"})

        assert state["flight"].selected_index == 1
        assert state["flight"].selected.price == 1500

    @respx.mock
    async def test_quota_accumulates_across_the_pause(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        mock_outbound(outbound_payload(OUTBOUND, count=2))

        runner = TripRunner()
        first = await runner.start(make_request(), trip_id="trp_quota")
        before = first["quota"].serpapi
        final = await runner.resume("trp_quota", {"flight.itinerary": "1"})

        # 一次规划烧了多少额度是按 trip 算的，不能被 resume 重置
        assert final["quota"].serpapi > before
        # 恢复时节点整个重放，去程搜索必须命中本地缓存而不是再花一次额度
        assert final["quota"].cache_hits > 0


class TestFallbackChain:
    @respx.mock
    async def test_loosening_travel_class_rescues_an_empty_search(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        outbound = mock_outbound(
            empty_flights_payload(),  # business 舱搜不到
            outbound_payload(OUTBOUND, count=1),  # 放宽舱位后有结果
        )

        state = await TripRunner().start(
            make_request(travel_class="business", auto_select=True)
        )

        assert state["status"] == "done"
        assert outbound.call_count == 2
        assert state["flight"].params.travel_class is None
        notes = [w.message for w in state["warnings"] if w.code == "FLIGHT_FALLBACK"]
        assert notes and "放宽" in notes[0]

    @respx.mock
    async def test_falls_back_to_an_alternate_city_airport(self):
        quiet_downstream()
        mock_autocomplete("北京", BEIJING_AIRPORTS)
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg()
        outbound = mock_outbound(
            empty_flights_payload(),  # PEK + economy
            empty_flights_payload(),  # PEK + 全舱位
            # 换到大兴才有结果——航段的出发机场必须跟着换成 PKX，
            # 真实接口不会在查 PKX 时返回 PEK 的航班
            outbound_payload(OUTBOUND, count=1, dep_id="PKX"),
        )

        state = await TripRunner().start(make_request(auto_select=True))

        assert state["status"] == "done"
        assert outbound.call_count == 3
        # 杭州只有一个机场，所以跳过换到达机场，直接换出发机场
        assert state["flight"].params.departure_airport_id == "PKX"
        notes = [w.message for w in state["warnings"] if w.code == "FLIGHT_FALLBACK"]
        assert notes and "PKX" in notes[0]

    @respx.mock
    async def test_gives_up_with_a_useful_error_instead_of_moving_the_dates(self):
        quiet_downstream()
        mock_autocomplete("北京", BEIJING_AIRPORTS)
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        outbound = mock_outbound(*[empty_flights_payload()] * 4)

        state = await TripRunner().start(make_request(auto_select=True))

        assert state["status"] == "failed"
        assert state["errors"][0].code == ErrorCode.NO_FLIGHTS
        # 悄悄挪动出行日期会改掉整个行程，那是用户才能拍板的事
        assert "改期" not in state["errors"][0].message
        assert outbound.call_count <= 4  # 兜底次数必须有上限


class TestReturnLeg:
    @respx.mock
    async def test_return_time_comes_from_the_token_query_not_the_outbound(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        mock_return_leg(hour=18)
        mock_outbound(outbound_payload(OUTBOUND, count=1))

        state = await TripRunner().start(make_request())
        branch = state["flight"]

        # best_flights 里只有去程；离开目的地的时刻必须来自 departure_token 那次查询
        assert branch.arrive_at.date() == OUTBOUND
        assert branch.depart_at.date() == RETURN
        assert branch.depart_at.hour == 18
        assert branch.depart_at > branch.arrive_at

    @respx.mock
    async def test_missing_return_data_falls_back_conservatively(self):
        quiet_downstream()
        mock_autocomplete("北京", [("PEK", "首都国际机场")])
        mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
        respx.get(SERP_URL, params__contains={"departure_token": OUTBOUND_TOKEN}).mock(
            return_value=httpx.Response(200, json=empty_flights_payload())
        )
        mock_outbound(outbound_payload(OUTBOUND, count=1))

        state = await TripRunner().start(make_request())
        branch = state["flight"]

        # 宁可保守假设上午起飞（末日几乎排不进景点），也不能让行程排到飞机起飞之后
        assert branch.depart_at.date() == RETURN
        assert branch.depart_at.hour == 9
        assert "RETURN_TIME_ESTIMATED" in {w.code for w in state["warnings"]}


class TestFlightFailures:
    @respx.mock
    async def test_city_with_no_airports_fails_before_searching(self):
        quiet_downstream()
        respx.get(SERP_URL, params__contains={"engine": "google_flights_autocomplete"}).mock(
            return_value=httpx.Response(200, json={"suggestions": []})
        )
        search = mock_outbound(outbound_payload(OUTBOUND))

        state = await TripRunner().start(make_request())

        assert state["errors"][0].code == ErrorCode.CITY_NOT_FOUND
        assert search.call_count == 0

    @respx.mock
    async def test_quota_exhausted_surfaces_clearly(self):
        quiet_downstream()
        respx.get(SERP_URL, params__contains={"engine": "google_flights_autocomplete"}).mock(
            return_value=httpx.Response(
                200, json={"error": "Your account has run out of searches."}
            )
        )

        state = await TripRunner().start(make_request())

        # 额度耗尽必须显式报出来，不能被当成"这个城市没有机场"
        assert state["errors"][0].code == ErrorCode.QUOTA_EXCEEDED


@pytest.mark.parametrize("answer,expected", [("PKX", "PKX"), ("2", "PKX"), ("pkx", "PKX")])
@respx.mock
async def test_answer_accepts_iata_or_index_in_any_case(answer, expected):
    quiet_downstream()
    mock_autocomplete("北京", BEIJING_AIRPORTS)
    mock_autocomplete("杭州", HANGZHOU_AIRPORTS)
    mock_return_leg()
    mock_outbound(outbound_payload(OUTBOUND, count=1))

    runner = TripRunner()
    await runner.start(make_request(), trip_id=f"trp_{answer}")
    state = await runner.resume(f"trp_{answer}", {"flight.departure_airport": answer})

    assert state["flight"].params.departure_airport_id == expected
