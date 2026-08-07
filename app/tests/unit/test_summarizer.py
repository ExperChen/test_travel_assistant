"""行程说明生成测试（不碰网络）。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time

import pytest

from app.agents.summarizer import (
    MAX_SUMMARY_CHARS,
    SYSTEM_PROMPT,
    build_digest,
    render_fallback,
    strip_markup,
    summarize,
    truncate_at_sentence,
)
from app.config import settings
from app.core.metrics import track_quota
from app.models.attraction import Attraction
from app.models.common import CityRef, GeoPoint
from app.models.flight import FlightBranch, FlightItinerary
from app.models.hotel import HotelBranch, HotelCandidate, Rate
from app.models.route import DayItem, DayPlan, Itinerary, RouteLeg
from app.models.trip import TripPlan, TripRequest

DAY = date(2026, 9, 4)


def make_plan(*, with_hotel: bool = True, unscheduled: int = 0) -> TripPlan:
    point = GeoPoint.gcj02(120.15, 30.24)
    items = [
        DayItem(
            kind="attraction",
            ref_id=f"spot-{i}",
            name=f"景点{i}",
            location=point,
            start_time=datetime.combine(DAY, time(9 + i * 3, 0)),
            end_time=datetime.combine(DAY, time(11 + i * 3, 0)),
            ticket_cost_cny=40.0 if i else 0.0,
        )
        for i in range(2)
    ]
    itinerary = Itinerary(
        days=[
            DayPlan(
                day_index=1,
                day=DAY,
                window_start=datetime.combine(DAY, time(9, 0)),
                window_end=datetime.combine(DAY, time(21, 0)),
                items=items,
                legs=[RouteLeg(mode="transit", distance_m=5000, duration_min=25, cost_cny=4.0)],
            )
        ],
        unscheduled=[
            Attraction(poi_id=f"u{i}", name=f"备选{i}", location=point)
            for i in range(unscheduled)
        ],
    )
    return TripPlan(
        trip_id="trp_test",
        status="done",
        request=TripRequest(
            departure_city="北京",
            destination_city="杭州",
            outbound_date=DAY,
            return_date=date(2026, 9, 7),
        ),
        destination=CityRef(name="杭州市", adcode="330100", center=point),
        hotel=HotelBranch(
            candidates=[
                HotelCandidate(
                    name="测试大酒店",
                    location=point,
                    hotel_class=4,
                    overall_rating=4.6,
                    total_rate=Rate(lowest="¥1200", extracted_lowest=1200),
                    commute_to_centroid_min=15,
                )
            ],
            selected_index=0,
        )
        if with_hotel
        else None,
        itinerary=itinerary,
    )


class FakeLLM:
    def __init__(self, content: str = "这是一段行程说明。", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return type("Response", (), {"content": self.content})()


class TestStripMarkup:
    def test_removes_tags(self):
        assert strip_markup("<b>行程</b>说明") == "行程说明"

    def test_removes_script_blocks_with_their_content(self):
        assert strip_markup("前<script>alert(1)</script>后") == "前后"

    def test_removes_style_and_iframe(self):
        assert strip_markup("<style>a{}</style>正文<iframe src=x></iframe>") == "正文"

    def test_plain_text_is_untouched(self):
        assert strip_markup("**杭州** 4 天 - 西湖") == "**杭州** 4 天 - 西湖"

    def test_handles_empty(self):
        assert strip_markup("") == ""


class TestBuildDigest:
    def test_includes_the_essentials(self):
        digest = build_digest(make_plan())

        assert digest["目的地"] == "杭州市"
        assert digest["酒店"]["名称"] == "测试大酒店"
        assert len(digest["每日安排"]) == 1
        assert [s["名称"] for s in digest["每日安排"][0]["景点"]] == ["景点0", "景点1"]
        assert digest["行程强度"]["全程通勤分钟"] == 25

    def test_carries_no_polyline_or_raw_payloads(self):
        import json

        blob = json.dumps(build_digest(make_plan()), ensure_ascii=False, default=str)
        # 这些进上下文只会烧 token 和喂幻觉
        for forbidden in ("polyline", "property_token", "departure_token", "serpapi"):
            assert forbidden not in blob

    def test_missing_hotel_is_simply_absent(self):
        assert "酒店" not in build_digest(make_plan(with_hotel=False))

    def test_unscheduled_attractions_are_listed(self):
        digest = build_digest(make_plan(unscheduled=3))
        assert digest["未排入"] == ["备选0", "备选1", "备选2"]


class TestFallback:
    def test_mentions_route_days_and_spots(self):
        text = render_fallback(build_digest(make_plan()))

        assert "北京" in text and "杭州市" in text
        assert "景点0 → 景点1" in text

    def test_quotes_only_numbers_present_in_the_digest(self):
        text = render_fallback(build_digest(make_plan()))
        # 模板里的每个数字都来自 digest，不做任何推断
        assert "¥1200" in text
        assert "15 分钟" in text
        assert "25 分钟" in text

    def test_omits_price_when_unavailable(self):
        plan = make_plan()
        plan.hotel.candidates[0].total_rate = None
        text = render_fallback(build_digest(plan))
        assert "总价" not in text

    def test_survives_an_empty_itinerary(self):
        plan = make_plan()
        plan.itinerary = Itinerary()
        assert render_fallback(build_digest(plan))


class TestSummarize:
    async def test_uses_the_llm_output(self):
        llm = FakeLLM("杭州 4 天，西湖为主。")
        text, warning = await summarize(build_digest(make_plan()), llm=llm)

        assert text == "杭州 4 天，西湖为主。"
        assert warning is None
        assert llm.calls

    async def test_strips_html_from_the_model_output(self):
        # prompt 里禁止了 HTML，但提示不是约束——模型跑偏或被景点名注入照样会吐标签
        llm = FakeLLM("<img src=x onerror=alert(1)>杭州 4 天")
        text, _ = await summarize(build_digest(make_plan()), llm=llm)

        assert "<" not in text
        assert "杭州 4 天" in text

    async def test_truncates_overlong_output(self):
        llm = FakeLLM("啰" * 900)
        text, _ = await summarize(build_digest(make_plan()), llm=llm)
        assert len(text) == MAX_SUMMARY_CHARS

    async def test_llm_failure_falls_back_instead_of_raising(self):
        # 行程本身已经排好了，说明文案只是包装，绝不能反过来拖垮它
        llm = FakeLLM(error=RuntimeError("User location is not supported"))
        text, warning = await summarize(build_digest(make_plan()), llm=llm)

        assert warning is not None
        assert warning.code == "SUMMARY_FALLBACK"
        assert "杭州市" in text

    async def test_empty_model_output_falls_back(self):
        llm = FakeLLM("   ")
        text, warning = await summarize(build_digest(make_plan()), llm=llm)

        assert warning is not None
        assert text

    async def test_disabled_llm_never_builds_a_client(self, monkeypatch):
        import app.agents.summarizer as module

        monkeypatch.setattr(settings, "llm_enabled", False)
        monkeypatch.setattr(
            module, "_default_llm", lambda: pytest.fail("关掉了还去建 LLM 客户端")
        )

        text, warning = await summarize(build_digest(make_plan()))

        # 调不通 Gemini 的网络环境里，关掉它能省下每次 30 秒的超时等待
        assert warning is None
        assert "杭州市" in text

    async def test_explicit_client_wins_over_the_switch(self, monkeypatch):
        # 开关只管"要不要自己去建一个"；显式注入了就用它，否则测试没法验证 LLM 路径
        monkeypatch.setattr(settings, "llm_enabled", False)
        llm = FakeLLM("来自注入的客户端")

        text, _ = await summarize(build_digest(make_plan()), llm=llm)

        assert text == "来自注入的客户端"
        assert llm.calls

    async def test_prompt_forbids_fabrication(self):
        llm = FakeLLM()
        await summarize(build_digest(make_plan()), llm=llm)

        system = llm.calls[0][0]["content"]
        assert "禁止" in system
        assert "HTML" in system


class TestQuota:
    """模型调用要计入配额，否则报表永远显示「LLM 0 次」。"""

    async def test_a_successful_call_is_counted(self):
        with track_quota() as quota:
            await summarize(build_digest(make_plan()), llm=FakeLLM())

        assert quota.llm == 1

    async def test_a_failed_call_is_counted_too(self):
        # 失败的请求一样烧了 token，配额报表要如实反映
        with track_quota() as quota:
            _, warning = await summarize(
                build_digest(make_plan()), llm=FakeLLM(error=RuntimeError("boom"))
            )

        assert warning is not None
        assert quota.llm == 1

    async def test_the_template_path_costs_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_enabled", False)
        with track_quota() as quota:
            await summarize(build_digest(make_plan()))

        assert quota.llm == 0


@pytest.mark.parametrize("field", ["航班", "酒店"])
def test_digest_omits_branches_that_never_ran(field):
    plan = make_plan(with_hotel=False)
    plan.flights = None
    assert field not in build_digest(plan)


class TestTruncation:
    """摘要超长时按句截断。

    实测踩到的：硬切 400 字把最后一行停在「- **9/」，读起来像程序崩了。
    """

    def test_short_text_is_untouched(self):
        assert truncate_at_sentence("很短。") == "很短。"

    def test_cuts_at_the_last_complete_sentence(self):
        text = "第一句。" * 50 + "被截断的半句没有句号"
        out = truncate_at_sentence(text, limit=60)

        assert out.endswith("。")
        assert len(out) <= 60

    def test_falls_back_to_a_hard_cut_when_there_is_no_sentence_break(self):
        # 整段没有句号时只能硬切，但不能返回空
        out = truncate_at_sentence("啰" * 900, limit=100)
        assert 0 < len(out) <= 100

    async def test_overlong_model_output_ends_on_a_sentence(self):
        llm = FakeLLM("这是一句完整的话。" * 80)
        text, _ = await summarize(build_digest(make_plan()), llm=llm)

        assert len(text) <= MAX_SUMMARY_CHARS
        assert text.endswith("。")


class TestCosts:
    """预估花费 = 机票 + 住宿。市内交通与门票都不算。"""

    def _with_flight_and_hotel(self):
        plan = make_plan()
        plan.flights = FlightBranch(
            candidates=[FlightItinerary(price=2300.0)], selected_index=0
        )
        return plan

    def test_adds_up_flight_and_hotel_only(self):
        plan = self._with_flight_and_hotel()

        costs = plan.costs

        assert costs.flight_cny == 2300.0
        assert costs.hotel_cny == 1200.0  # make_plan 的酒店总价
        assert costs.total_cny == 3500.0  # 手算可验：交通费不在里面

    def test_transport_cost_is_excluded(self):
        plan = self._with_flight_and_hotel()
        transport = plan.itinerary.totals()["transport_cost_cny"]

        # 前提：数据里确实有交通费，否则这条断言是空的
        assert transport > 0
        assert plan.costs.total_cny == 3500.0

    def test_a_missing_price_is_not_counted_as_zero(self):
        # 把「酒店没标价」算成「住宿 ¥0」，总价就成了谎报
        plan = self._with_flight_and_hotel()
        plan.hotel = None

        costs = plan.costs

        assert costs.total_cny is None
        assert costs.missing == ["住宿"]

    def test_nightly_price_only_is_multiplied_by_nights(self):
        plan = self._with_flight_and_hotel()
        chosen = plan.hotel.selected
        chosen.total_rate = None
        chosen.rate_per_night = Rate(lowest="¥500", extracted_lowest=500.0)

        costs = plan.costs

        assert costs.nights == plan.request.nights
        assert costs.hotel_cny == 500.0 * plan.request.nights

    def test_the_digest_reports_costs_not_transport(self):
        digest = build_digest(self._with_flight_and_hotel())

        assert digest["预估花费"]["合计"] == 3500.0
        assert "transport_cost_cny" not in json.dumps(digest, ensure_ascii=False)

    def test_the_digest_omits_costs_when_incomplete(self):
        # 模型只能复述 digest 里有的东西；给半截数字不如不给
        plan = make_plan()  # 没有航班
        assert "预估花费" not in build_digest(plan)

    def test_a_room_rate_always_carries_the_number_of_nights(self):
        # "总价301元"没有参照系——住 3 晚和住 10 晚差别巨大
        hotel = build_digest(self._with_flight_and_hotel())["酒店"]

        assert hotel["住几晚"] == make_plan().request.nights
        assert hotel["每晚价"] == round(hotel["总价"] / hotel["住几晚"], 2)

    def test_the_prompt_forbids_a_total_without_its_parts(self):
        # 只写一项再给合计，读起来像算错了："机票1927元，合计2228元"
        assert "必须同时写出来" in SYSTEM_PROMPT

    def test_the_fallback_states_both_line_items_with_the_total(self):
        text = render_fallback(build_digest(self._with_flight_and_hotel()))

        assert "机票 ¥2300" in text
        assert "住宿 ¥1200" in text
        assert "¥3500" in text

    def test_the_fallback_room_rate_shows_nights(self):
        text = render_fallback(build_digest(self._with_flight_and_hotel()))

        assert "每晚 ¥400 × 3 晚" in text
        assert "共 ¥1200" in text

    def test_the_fallback_says_what_is_excluded(self):
        text = render_fallback(build_digest(self._with_flight_and_hotel()))

        assert "机票 ¥2300" in text
        assert "不含市内交通与门票" in text

    def test_commute_is_reported_as_intensity_not_money(self):
        # 通勤是时间不是钱，不该出现在费用区
        digest = build_digest(self._with_flight_and_hotel())

        assert digest["行程强度"]["全程通勤分钟"] == 25
        assert "通勤" not in json.dumps(digest["预估花费"], ensure_ascii=False)


class TestNoClockTimesForAttractions:
    """景点只给顺序，不给钟点。

    排期算法内部照样算精确时刻（要卡营业时间和航班窗口），但把
    「09:20-11:20」印给用户，等于把一个内部中间值说成承诺——路上一堵就全错位。
    航班时刻是例外：那是真实存在的，必须说。
    """

    def test_the_daily_plan_carries_order_not_times(self):
        digest = build_digest(make_plan())
        spots = digest["每日安排"][0]["景点"]

        assert [s["顺序"] for s in spots] == list(range(1, len(spots) + 1))
        assert all("时段" not in s for s in spots)
        assert not re.search(r"\d{1,2}:\d{2}", json.dumps(digest["每日安排"], ensure_ascii=False))

    def test_flight_times_are_still_there(self):
        # 景点不给钟点，但航班必须给——赶不上飞机可不是"顺序"问题
        plan = make_plan()
        plan.flights = FlightBranch(
            candidates=[FlightItinerary(price=1200.0)],
            selected_index=0,
            arrive_at=datetime(2026, 9, 4, 10, 30),
            depart_at=datetime(2026, 9, 7, 18, 0),
        )

        digest = build_digest(plan)

        assert digest["航班"]["去程落地"] == "09-04 10:30"
        assert digest["航班"]["返程起飞"] == "09-07 18:00"

    def test_the_fallback_lists_attractions_in_order(self):
        text = render_fallback(build_digest(make_plan()))

        assert "→" in text
        assert not re.search(r"\d{1,2}:\d{2}", text)

    def test_the_prompt_forbids_inventing_times(self):
        assert "先后顺序" in SYSTEM_PROMPT

    def test_the_itinerary_still_knows_the_real_times(self):
        # 只是不显示；排期依赖它们，接口也照常返回
        plan = make_plan()

        assert plan.itinerary.days[0].items[0].start_time is not None


class TestTicketsAreNotSurfaced:
    """门票一律不进文案。

    高德极少返回 `business.cost`，报一个只覆盖零星几个景点的合计比不报更误导。
    模型只能复述 digest 里有的东西，所以从 digest 里摘干净就等于让它彻底闭嘴。
    """

    def test_the_digest_carries_no_ticket_field(self):
        plan = make_plan()
        for day in plan.itinerary.days:
            for item in day.items:
                item.ticket_cost_cny = 120.0  # 有数据也不进 digest

        digest = build_digest(plan)

        assert all("门票" not in s for s in digest["每日安排"][0]["景点"])
        assert "门票" not in json.dumps(digest, ensure_ascii=False)
        assert "ticket_cost_cny" not in json.dumps(digest, ensure_ascii=False)

    def test_the_fallback_never_mentions_tickets(self):
        plan = make_plan()
        for day in plan.itinerary.days:
            for item in day.items:
                item.ticket_cost_cny = 137.0  # 挑个不会和酒店价/通勤数字撞的值

        text = render_fallback(build_digest(plan))

        assert "门票" not in text
        assert "137" not in text
        assert f"{plan.itinerary.totals()['ticket_cost_cny']:.0f}" not in text

    def test_the_totals_still_carry_the_data_for_the_api(self):
        # 只是不显示；TripPlan 照常返回，前端要用随时能取
        plan = make_plan()
        for day in plan.itinerary.days:
            for item in day.items:
                item.ticket_cost_cny = 137.0

        assert plan.itinerary.totals()["ticket_cost_cny"] > 0
