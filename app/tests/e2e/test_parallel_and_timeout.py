"""并行分支与中断超时（架构文档 §4.2 / §4.3）。

航班分支和「景点→酒店」分支并行跑，两边可能在同一个 superstep 里各自中断一次。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import httpx
import pytest
import respx

from app.config import settings
from app.core.exceptions import AnswerMismatch
from app.graph.builder import TripRunner
from app.graph.state import initial_state
from app.models.errors import ErrorCode
from app.models.trip import TripRequest
from app.tests.e2e._mocks import (
    OUTBOUND_TOKEN,
    SERP_URL,
    autocomplete_payload,
    mock_downstream,
    outbound_payload,
    pending_of,
    return_payload,
)

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)
BEIJING = [("PEK", "首都国际机场"), ("PKX", "大兴国际机场")]
HANGZHOU = [("HGH", "萧山国际机场")]


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "北京",
        "destination_city": "杭州",
        "outbound_date": OUTBOUND,
        "return_date": RETURN,
    }
    return TripRequest(**{**base, **kw})


def setup(*, departure_airports=BEIJING, arrival_airports=HANGZHOU):
    """出发地两个机场（航班会中断），酒店三个候选（酒店也会中断）。

    图的 superstep 划分：
        S1: flight_departure ‖ attraction_search
        S2: flight_arrival   ‖ hotel_search
        S3: flight_search
    想让两个中断**同时**挂起，得让它们落在同一步——所以要么两地机场都有歧义
    （S1 与 S2 各一次），要么给到达地两个机场，让 flight_arrival 和 hotel_search
    在 S2 撞上。
    """
    mock_downstream()
    respx.get(SERP_URL, params__contains={"q": "北京"}).mock(
        return_value=httpx.Response(200, json=autocomplete_payload("北京", departure_airports))
    )
    respx.get(SERP_URL, params__contains={"q": "杭州"}).mock(
        return_value=httpx.Response(200, json=autocomplete_payload("杭州", arrival_airports))
    )
    respx.get(SERP_URL, params__contains={"departure_token": OUTBOUND_TOKEN}).mock(
        return_value=httpx.Response(200, json=return_payload(RETURN))
    )
    respx.get(SERP_URL, params__contains={"engine": "google_flights"}).mock(
        return_value=httpx.Response(200, json=outbound_payload(OUTBOUND, count=1))
    )


class TestParallelExecution:
    @respx.mock
    async def test_local_branch_progresses_while_the_flight_branch_waits(self):
        setup()

        state = await TripRunner().start(make_request())

        # 航班停在选机场，但景点分支已经跑完了——这就是并行的直接证据
        assert pending_of(state, "flight.") is not None
        assert state["attractions"].selected

    @respx.mock
    async def test_both_branches_can_pause_at_the_same_time(self):
        # 出发地不歧义 → flight_arrival 与 hotel_search 双双落在 S2 并各自中断
        setup(
            departure_airports=[("PEK", "首都国际机场")],
            arrival_airports=[("HGH", "萧山国际机场"), ("SHA", "虹桥国际机场")],
        )

        runner = TripRunner()
        state = await runner.start(make_request(), trip_id="trp_both")

        ids = {q.id for q in state["pending"]}
        assert ids == {"flight.arrival_airport", "hotel.selection"}
        assert state["status"] == "waiting_input"

    @respx.mock
    async def test_answering_every_pending_question_at_once_completes_the_trip(self):
        setup()

        runner = TripRunner()
        first = await runner.start(make_request(), trip_id="trp_all")
        answers = {q.id: q.default for q in first["pending"]}
        state = await runner.resume("trp_all", answers)

        while state["status"] == "waiting_input":
            state = await runner.resume(
                "trp_all", {q.id: q.default for q in state["pending"]}
            )

        assert state["status"] == "done"
        assert state["itinerary"] is not None

    @respx.mock
    async def test_answering_one_leaves_the_other_pending(self):
        setup(
            departure_airports=[("PEK", "首都国际机场")],
            arrival_airports=[("HGH", "萧山国际机场"), ("SHA", "虹桥国际机场")],
        )

        runner = TripRunner()
        first = await runner.start(make_request(), trip_id="trp_one")
        assert len(first["pending"]) == 2

        state = await runner.resume("trp_one", {"hotel.selection": "1"})

        # 只回答了酒店，航班那个还挂着——langgraph 要求按 interrupt id 定向恢复，
        # 恢复错分支会让用户的选择落到别的问题上
        assert state["status"] == "waiting_input"
        assert {q.id for q in state["pending"]} == {"flight.arrival_airport"}
        assert state["hotel"].selected is not None

    @respx.mock
    async def test_unknown_question_id_is_rejected(self):
        setup()

        runner = TripRunner()
        await runner.start(make_request(), trip_id="trp_bad")

        # API 层要把这个翻译成 409 ANSWER_MISMATCH，而不是静默恢复错的分支
        with pytest.raises(AnswerMismatch):
            await runner.resume("trp_bad", {"hotel.selection": "1"})


class TestJoinRunsOnce:
    """汇合节点的重复触发防护。

    LangGraph 的汇合语义是「每有一条上游在某个 superstep 完成就触发一次」——
    两条分支长度不一致时，汇合节点必然被触发多次（换成静态边也一样）。
    实测踩过的后果：route_planner 跑两遍，第一遍在航班还没跑完时用缺失的落地时刻
    算出一份**错的**行程，还白花了一整套高德路径查询和一次 LLM 调用。
    """

    @respx.mock
    async def test_route_planner_and_summarize_each_do_work_once(self):
        setup(departure_airports=[("PEK", "首都国际机场")])

        runner = TripRunner()
        counts: Counter[str] = Counter()
        async for node, patch in runner.iter_run(
            "trp_once", initial_state("trp_once", make_request(auto_select=True))
        ):
            if patch:  # 空补丁 = 被守卫挡下的重复触发
                counts[node] += 1
        state = await runner.finalize("trp_once")

        assert counts["route_planner"] == 1
        assert counts["summarize"] == 1
        assert state["itinerary"] is not None

    @respx.mock
    async def test_no_duplicate_warnings(self):
        setup(departure_airports=[("PEK", "首都国际机场")])

        state = await TripRunner().start(make_request(auto_select=True))

        codes = [w.code for w in state["warnings"]]
        assert len(codes) == len(set(codes)), f"警告重复了：{codes}"

    @respx.mock
    async def test_itinerary_uses_the_real_landing_time(self):
        setup(departure_airports=[("PEK", "首都国际机场")])

        state = await TripRunner().start(make_request(auto_select=True))

        # 汇合被过早触发时航班数据还没到，会退化成"按中午 12:00 落地"的假设——
        # 那正是行程算错的信号
        assert "ARRIVAL_TIME_ASSUMED" not in {w.code for w in state["warnings"]}
        first_day = state["itinerary"].days[0]
        assert first_day.items[0].start_time == state["flight"].arrive_at


class TestJoinGuard:
    @respx.mock
    async def test_a_failed_branch_stops_the_join(self):
        mock_downstream()
        # 出发城市查不到机场 → 航班分支失败；景点/酒店分支照样能跑完
        respx.get(SERP_URL, params__contains={"engine": "google_flights_autocomplete"}).mock(
            return_value=httpx.Response(200, json={"suggestions": []})
        )

        state = await TripRunner().start(make_request(auto_select=True))

        assert state["status"] == "failed"
        assert state["errors"][0].code == ErrorCode.CITY_NOT_FOUND
        # 一条分支挂了就别再花额度编排一份用不上的行程
        assert state.get("itinerary") is None


class TestInterruptTimeout:
    @respx.mock
    async def test_expired_questions_are_auto_answered_with_their_defaults(
        self, monkeypatch
    ):
        # 让问题一诞生就过期
        monkeypatch.setattr(settings, "interrupt_timeout_s", 0)
        setup()

        runner = TripRunner()
        first = await runner.start(make_request(), trip_id="trp_timeout")
        assert first["status"] == "waiting_input"
        expected = {q.id: q.default for q in first["pending"]}

        state = await runner.resume_expired("trp_timeout")

        assert state is not None
        assert state["flight"].params.departure_airport_id == expected.get(
            "flight.departure_airport", "PEK"
        )
        codes = {w.code for w in state["warnings"]}
        assert "ANSWER_TIMED_OUT" in codes

    @respx.mock
    async def test_timeout_warning_names_the_question(self, monkeypatch):
        monkeypatch.setattr(settings, "interrupt_timeout_s", 0)
        setup()

        runner = TripRunner()
        await runner.start(make_request(), trip_id="trp_timeout_msg")
        state = await runner.resume_expired("trp_timeout_msg")

        message = next(w.message for w in state["warnings"] if w.code == "ANSWER_TIMED_OUT")
        assert "自动选择" in message

    @respx.mock
    async def test_nothing_expired_is_a_no_op(self):
        setup()  # 默认 10 分钟超时，刚创建不会过期

        runner = TripRunner()
        await runner.start(make_request(), trip_id="trp_fresh")

        assert await runner.resume_expired("trp_fresh") is None

    @respx.mock
    async def test_a_finished_trip_has_nothing_to_expire(self):
        setup(departure_airports=[("PEK", "首都国际机场")])

        runner = TripRunner()
        await runner.start(make_request(auto_select=True), trip_id="trp_done")

        assert await runner.resume_expired("trp_done") is None
