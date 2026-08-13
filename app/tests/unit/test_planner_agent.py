"""自主规划 Agent。

这条路径现在是**默认编排**，但它的保证和固定管线不一样——它保证的不是
"行程一定合法"，而是：

    额度不会被烧光 · 循环不会卡死 · 任何失败都能交出已有轨迹

上面三条是代码保证的，也就是这个文件要守的。行程本身合不合理由模型负责。
"""

from __future__ import annotations

import json

import pytest

from app.agents.planner_agent import AgentRun, PlannerAgent, llm_tool_specs
from app.core.metrics import track_quota


class FakeResponse:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeLLM:
    """按脚本逐轮返回。`bind_tools` 返回自身，好让 agent 的调用链走通。"""

    def __init__(self, *rounds):
        self._rounds = list(rounds)
        self.bound: list = []
        self.calls = 0

    def bind_tools(self, tools):
        self.bound = tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if not self._rounds:
            return FakeResponse("没词了")
        return self._rounds.pop(0)


def _call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"name": name, "args": args, "id": cid}


FINISH = FakeResponse(tool_calls=[_call("finish_plan", {"summary": "好了", "days": []})])


class TestToolExposure:
    def test_all_llm_facing_tools_are_offered(self):
        """**这是这次改动的核心**：模型能看到的不再只有 3 个工具。"""
        names = {s.name for s in llm_tool_specs()}
        # 数据查询
        assert {"district_lookup", "poi_keyword", "poi_around", "poi_detail"} <= names
        assert {"flights_search", "hotels_search"} <= names
        # 路径类——原本收 GeoPoint、模型用不了，靠 planner_tools 的封装补进来
        assert {"route_between", "distance_many", "address_of"} <= names

    def test_route_tools_take_plain_numbers(self):
        """封装层对模型收裸经纬度，坐标系约束仍在内部保留。"""
        spec = next(s for s in llm_tool_specs() if s.name == "route_between")
        props = spec.parameters["properties"]
        assert props["from_lng"]["type"] == "number"
        assert "GeoPoint" not in json.dumps(spec.parameters)

    def test_every_tool_has_a_schema(self):
        """没有 JSON Schema 的工具没法绑给模型。"""
        for spec in llm_tool_specs():
            assert spec.parameters.get("type") == "object"
            assert spec.description


@pytest.mark.asyncio
class TestBudgetGuards:
    async def test_serpapi_calls_are_capped(self):
        """**免费额度 250 次/月**，自主循环几十次就能打光。

        超上限后不是抛异常，而是告诉模型"用已有结果继续"——
        直接失败会让前面查到的数据全白费。
        """
        rounds = [
            FakeResponse(tool_calls=[_call("flights_autocomplete", {"q": f"城市{i}"})])
            for i in range(5)
        ]
        agent = PlannerAgent(llm=FakeLLM(*rounds, FINISH), serpapi_budget=2)
        run = await agent.run("随便排一个")

        refusals = [s for s in run.steps if "已达上限" in s.observation]
        assert len(refusals) == 3, "第 3 次起就该拒绝（前 2 次用掉了额度）"
        assert agent._serpapi_attempts == 2

    async def test_budget_counts_attempts_not_successes(self):
        """**按尝试次数计，不按实际消耗的配额。**

        失败的调用不会增加配额计数器。若按配额判断，模型反复调一个
        失败的接口就能无限循环——上限形同虚设。
        """
        rounds = [
            FakeResponse(tool_calls=[_call("hotels_autocomplete", {"q": f"x{i}"})])
            for i in range(4)
        ]
        agent = PlannerAgent(llm=FakeLLM(*rounds, FINISH), serpapi_budget=1)

        with track_quota() as quota:
            run = await agent.run("排一个")

        # 调用全部失败（没有 mock），配额计数器是 0
        assert quota.serpapi == 0
        # 但上限照样生效
        assert agent._serpapi_attempts == 1
        assert any("已达上限" in s.observation for s in run.steps)

    async def test_identical_calls_are_refused(self):
        """同样的参数不查第二次——重复查询是自主循环最常见的烧额度方式。"""
        same = _call("district_lookup", {"keywords": "深圳"})
        agent = PlannerAgent(
            llm=FakeLLM(FakeResponse(tool_calls=[same]),
                        FakeResponse(tool_calls=[same]), FINISH)
        )
        run = await agent.run("排一个")

        assert any("结果不会变" in s.observation for s in run.steps)

    async def test_step_limit_stops_the_loop(self):
        """模型可能陷入"查了又查"，必须有硬顶。"""
        forever = [
            FakeResponse(tool_calls=[_call("poi_keyword", {"keywords": f"景点{i}"})])
            for i in range(50)
        ]
        agent = PlannerAgent(llm=FakeLLM(*forever), max_steps=4)
        run = await agent.run("排一个")

        assert not run.finished
        assert "步数用尽" in run.stop_reason
        assert len(run.steps) == 4


@pytest.mark.asyncio
class TestFailureHandling:
    async def test_model_failure_returns_partial_trace(self):
        """**模型挂了也要把已有轨迹交出去。**

        前面几步查到的数据仍有价值，而且用户更想知道"走到哪一步断的"。
        上抛异常会把整个 CLI 打崩——实测踩过。
        """
        class Boom(FakeLLM):
            async def ainvoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(
                        tool_calls=[_call("district_lookup", {"keywords": "深圳"})])
                raise TimeoutError("模型超时")

        run = await PlannerAgent(llm=Boom()).run("排一个")

        assert not run.finished
        assert "模型调用失败" in run.stop_reason
        assert len(run.steps) == 1  # 第一步的结果保住了

    async def test_unknown_tool_is_reported_back(self):
        agent = PlannerAgent(
            llm=FakeLLM(FakeResponse(tool_calls=[_call("book_flight", {})]), FINISH))
        run = await agent.run("排一个")

        assert "没有名为 book_flight 的工具" in run.steps[0].observation

    async def test_bad_arguments_are_reported_back(self):
        """参数错了回灌给模型自己纠正，而不是崩掉。"""
        agent = PlannerAgent(
            llm=FakeLLM(
                FakeResponse(tool_calls=[_call("district_lookup", {"wrong": 1})]), FINISH))
        run = await agent.run("排一个")

        assert "参数不对" in run.steps[0].observation

    async def test_plain_answer_without_tools_ends_the_run(self):
        run = await PlannerAgent(llm=FakeLLM(FakeResponse("我直接回答"))).run("排一个")

        assert run.finished
        assert run.answer == "我直接回答"


@pytest.mark.asyncio
class TestFinish:
    async def test_finish_plan_delivers(self):
        run = await PlannerAgent(llm=FakeLLM(FINISH)).run("排一个")

        assert run.finished
        assert "好了" in run.answer
        assert run.stop_reason == "模型主动收尾"

    async def test_finish_tool_is_bound(self):
        llm = FakeLLM(FINISH)
        await PlannerAgent(llm=llm).run("排一个")

        names = {t["function"]["name"] for t in llm.bound}
        assert "finish_plan" in names
        assert "route_between" in names


class TestAgentRun:
    def test_tool_calls_excludes_finish(self):
        run = AgentRun()
        run.add("poi_keyword", {}, "ok")
        run.add("finish_plan", {}, "已交付")
        assert run.tool_calls == 1

    def test_observation_is_truncated(self):
        """单条观测无限长会把上下文撑爆。"""
        run = AgentRun()
        run.add("poi_keyword", {}, "x" * 9000)
        assert len(run.steps[0].observation) <= 2000
