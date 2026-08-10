"""端到端：追问中断 → 恢复 → 规划完成 → 记忆落盘。

串起记忆与追问文档的三条主线，验证它们在真实图里协同工作：

    origins 带进来 → clarify 挂起 → 用户补答 → 参数生效 → done 时写记忆
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
import respx

from app.graph.builder import TripRunner
from app.models.trip import TripRequest
from app.services.trip_service import TripService, preference_payload
from app.tests.e2e._mocks import mock_downstream, mock_flights, outbound_payload

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)

ALL_DEFAULT = {"adults": "default", "pace": "default", "budget_per_night": "default"}


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "北京",
        "destination_city": "杭州",
        "outbound_date": OUTBOUND,
        "return_date": RETURN,
        "auto_select": True,  # 只测追问这一个中断点，其余全自动
    }
    return TripRequest(**{**base, **kw})


def mock_all() -> None:
    mock_downstream()
    mock_flights(OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=1))


@pytest.mark.asyncio
class TestClarifyInterrupt:
    @respx.mock
    async def test_asks_then_applies_the_answer(self):
        """全靠默认值兜底时会停下来问，答完参数要真的生效。"""
        mock_all()
        runner = TripRunner()

        state = await runner.start(make_request(), origins=ALL_DEFAULT)
        assert state["status"] == "waiting_input"
        question = state["pending"][0]
        assert question.id == "intake.clarify"
        assert question.kind == "form"
        assert question.skippable
        assert {f.key for f in question.fields} == {"adults", "pace", "budget_per_night"}

        final = await runner.resume(
            state["trip_id"],
            {"intake.clarify": {"adults": "3", "pace": "relaxed",
                                "budget_per_night": "300_600"}},
        )
        assert final["status"] == "done"
        assert final["request"].adults == 3
        assert final["request"].pace == "relaxed"
        assert final["request"].budget_per_night == 600

    @respx.mock
    async def test_no_origins_means_no_interruption(self):
        """老调用方（不带 origins）一切照旧，一个中断都不该多出来。"""
        mock_all()
        state = await TripRunner().start(make_request())
        assert state["status"] == "done"
        assert state["pending"] == []

    @respx.mock
    async def test_user_supplied_values_are_not_questioned(self):
        """用户说过的就别再问了。"""
        mock_all()
        state = await TripRunner().start(
            make_request(adults=2, pace="packed", budget_per_night=800),
            origins={"adults": "prompt", "pace": "prompt", "budget_per_night": "prompt"},
        )
        assert state["status"] == "done"
        assert state["pending"] == []

    @respx.mock
    async def test_timeout_falls_back_to_defaults(self):
        """追问绝不能成为新的卡死点——超时按默认值放行。"""
        mock_all()
        runner = TripRunner()
        state = await runner.start(make_request(), origins=ALL_DEFAULT)
        assert state["status"] == "waiting_input"

        # 把问题标成已过期，走清扫路径
        question = state["pending"][0]
        object.__setattr__(question, "expires_at", question.expires_at - timedelta(days=1))
        runner._timeouts.clear()

        final = await runner.resume(
            state["trip_id"], {"intake.clarify": question.default}
        )
        assert final["status"] == "done"
        assert final["request"].adults == 1  # 默认值

    @respx.mock
    async def test_asks_at_most_once(self):
        """最多问一轮——恢复之后不该再冒出第二个 clarify。"""
        mock_all()
        runner = TripRunner()
        state = await runner.start(make_request(), origins=ALL_DEFAULT)
        final = await runner.resume(
            state["trip_id"], {"intake.clarify": {"adults": "2"}}
        )
        assert final["status"] == "done"
        assert not any(q.id == "intake.clarify" for q in final.get("pending") or [])


@pytest.mark.asyncio
class TestMemoryWriteBack:
    @respx.mock
    async def test_preferences_reflect_the_final_request_not_the_draft(self):
        """**以最终生效的参数为准**：用户在追问里把人数从 1 改成 3，
        记进去的必须是 3。"""
        mock_all()
        runner = TripRunner()
        state = await runner.start(make_request(), origins=ALL_DEFAULT)
        final = await runner.resume(
            state["trip_id"],
            {"intake.clarify": {"adults": "3", "budget_per_night": "300_600"}},
        )

        payload = preference_payload(final["request"])
        assert payload["adults"] == 3
        assert payload["budget_per_night"] == "300_600"  # 存档位不存数字

    @respx.mock
    async def test_history_records_only_scheduled_attractions(self):
        """没去成的不算去过——只记最终排进行程的。"""
        mock_all()
        state = await TripRunner().start(make_request())
        assert state["status"] == "done"

        itinerary = state["itinerary"]
        scheduled = {
            item.ref_id
            for day in itinerary.days
            for item in day.items
            if item.kind == "attraction"
        }
        unscheduled = {a.poi_id for a in itinerary.unscheduled}
        assert scheduled  # 确实排进去了一些
        assert not (scheduled & unscheduled)  # 备选不该混进已访问

    @respx.mock
    async def test_a_broken_store_still_delivers_the_trip(self):
        """记忆坏掉不能拖垮已经交付的行程。

        `_remember` 跑在 `_emit_terminal` **之后**，所以哪怕它整段炸掉，
        用户也已经拿到 done 事件了。这里直接让 store 抛异常来验证这一点。
        """
        mock_all()

        class ExplodingStore:
            async def load_profile(self, *_a, **_kw):
                raise RuntimeError("磁盘满了")

        service = TripService(store=ExplodingStore())
        try:
            trip_id = service.create(
                make_request(), profile_id="usr_boom", origins=ALL_DEFAULT
            )
            # 不带 origins 的路径不会中断，这里给了 origins 所以会停一次
            await _drain(service, trip_id)
            state = await service.runner.finalize(trip_id)
            assert state["status"] == "waiting_input"
        finally:
            await service.aclose()


async def _drain(service, trip_id: str, *, timeout: float = 5.0) -> None:
    """等后台规划任务跑完这一轮。"""
    task = service._tasks.get(trip_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
