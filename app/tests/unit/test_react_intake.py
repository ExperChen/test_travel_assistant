"""ReAct 多轮参数收集。

重点覆盖两件事：
1. **累积**——前面轮次说过的不用再说（这是整个特性的目的）；
2. **不守规矩的模型输出不能让对话卡死**。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.agents.react_intake import (
    ReactIntakeAgent,
    SessionStore,
    parse_decision,
)
from app.config import settings
from app.models.intake import IntakeSession
from app.models.memory import MemorySnapshot, Profile

TODAY = date(2026, 8, 7)


class FakeLLM:
    """按脚本逐条返回。记录收到的 prompt，便于断言上下文里带了什么。"""

    def __init__(self, *replies: str):
        self._replies = list(replies)
        self.seen: list[str] = []

    async def ainvoke(self, messages):
        self.seen.append("\n".join(m["content"] for m in messages))
        text = self._replies.pop(0) if self._replies else "Thought: 没词了\nResponse: 嗯"
        return type("R", (), {"content": text})()


class TestParseDecision:
    def test_action(self):
        d = parse_decision(
            'Thought: 要算日期\nAction: resolve_date\nAction Input: {"text": "下周三"}'
        )
        assert d.action == "resolve_date"
        assert d.action_input == {"text": "下周三"}

    def test_response(self):
        d = parse_decision("Thought: 缺出发地\nResponse: 从哪个城市出发？")
        assert d.response == "从哪个城市出发？"
        assert not d.action

    def test_finish(self):
        d = parse_decision('Thought: 齐了\nFinish: {"destination_city": "成都"}')
        assert d.finish == {"destination_city": "成都"}

    def test_full_chinese_colons(self):
        """模型经常把英文冒号打成中文冒号。"""
        d = parse_decision("Thought：想想\nResponse：哪天出发？")
        assert d.response == "哪天出发？"

    def test_unparseable_becomes_a_reply_not_a_crash(self):
        """宁可把一句思考漏给用户，也不能让对话卡死在解析失败上。"""
        d = parse_decision("我就是不守格式")
        assert d.response == "我就是不守格式"

    def test_broken_finish_json_falls_through(self):
        """Finish 的 JSON 坏了 → 当作还没说完，继续对话而不是崩。"""
        d = parse_decision("Thought: 齐了\nFinish: {这不是JSON")
        assert d.finish is None

    def test_broken_action_input_still_dispatches(self):
        """参数坏了也要把 Action 发出去——让工具层报错并回灌给模型自我纠正。"""
        d = parse_decision("Thought: 调用\nAction: resolve_date\nAction Input: {坏的")
        assert d.action == "resolve_date"
        assert d.action_input == {}


@pytest.mark.asyncio
class TestAccumulation:
    async def test_slots_persist_across_turns(self, monkeypatch):
        """**这是整个特性的目的**：第一轮说目的地，第三轮才说日期，都不用重复。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        agent = ReactIntakeAgent()
        session = IntakeSession(session_id="s1")

        await agent.run(session, "想去成都", today=TODAY)
        assert session.collected.destination_city == "成都"

        await agent.run(session, "从北京出发", today=TODAY)
        assert session.collected.departure_city == "北京"
        assert session.collected.destination_city == "成都"  # 没被冲掉

        reply = await agent.run(session, "9月5号走，玩5天", today=TODAY)
        assert reply.done
        assert reply.draft.request.departure_city == "北京"
        assert reply.draft.request.destination_city == "成都"
        assert reply.draft.request.outbound_date == date(2026, 9, 5)

    async def test_later_turns_override_earlier_ones(self, monkeypatch):
        """「算了我还是从上海走」必须能改掉之前说的北京。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        agent = ReactIntakeAgent()
        session = IntakeSession(session_id="s1")

        await agent.run(session, "从北京出发", today=TODAY)
        await agent.run(session, "算了，从上海出发", today=TODAY)
        assert session.collected.departure_city == "上海"

    async def test_silence_does_not_wipe_a_slot(self, monkeypatch):
        """这一轮没提到 ≠ 要清空。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        agent = ReactIntakeAgent()
        session = IntakeSession(session_id="s1")

        await agent.run(session, "从北京出发", today=TODAY)
        await agent.run(session, "嗯嗯", today=TODAY)
        assert session.collected.departure_city == "北京"

    async def test_does_not_ask_the_same_slot_twice(self, monkeypatch):
        """问了没答说明用户不想说，再问就是查户口。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        agent = ReactIntakeAgent()
        session = IntakeSession(session_id="s1")

        first = await agent.run(session, "想去成都", today=TODAY)
        second = await agent.run(session, "不知道", today=TODAY)
        assert first.reply != second.reply


@pytest.mark.asyncio
class TestReactLoop:
    async def test_tool_call_then_reply(self):
        llm = FakeLLM(
            'Thought: 先算日期\nAction: resolve_date\nAction Input: {"text": "下周三"}',
            "Thought: 拿到了，还缺出发地\nResponse: 从哪个城市出发？",
        )
        agent = ReactIntakeAgent(llm=llm)
        session = IntakeSession(session_id="s1")

        reply = await agent.run(session, "下周三去成都", today=TODAY)
        assert reply.reply == "从哪个城市出发？"
        assert [s.action for s in reply.steps] == ["resolve_date", "Response"]
        assert "2026-08-12" in reply.steps[0].observation

    async def test_unknown_tool_is_reported_back_to_the_model(self):
        """报错回灌给模型让它自我纠正，比直接崩掉有用。"""
        llm = FakeLLM(
            "Thought: 乱调\nAction: book_flight\nAction Input: {}",
            "Thought: 那个工具不存在\nResponse: 哪天出发？",
        )
        agent = ReactIntakeAgent(llm=llm)
        reply = await agent.run(IntakeSession(session_id="s1"), "去成都", today=TODAY)
        assert "没有名为 book_flight 的工具" in reply.steps[0].observation
        assert reply.reply == "哪天出发？"

    async def test_step_budget_is_enforced(self):
        """步数用尽就带着已有信息收尾，而不是无限烧 token。"""
        llm = FakeLLM(*['Thought: 再来\nAction: resolve_date\nAction Input: {"text": "明天"}'] * 20)
        agent = ReactIntakeAgent(llm=llm, max_steps=3)
        reply = await agent.run(IntakeSession(session_id="s1"), "去成都", today=TODAY)
        assert len(reply.steps) == 3
        assert reply.missing  # 没收集齐，但也没卡住

    async def test_llm_failure_falls_back_to_rules(self):
        """模型抽风不能让对话断掉。"""

        class Boom:
            async def ainvoke(self, messages):
                raise RuntimeError("上游 500")

        agent = ReactIntakeAgent(llm=Boom())
        reply = await agent.run(
            IntakeSession(session_id="s1"), "9月5号从北京去成都玩5天", today=TODAY
        )
        assert reply.degraded
        assert reply.done
        assert "上游 500" in reply.degraded_reason

    async def test_timeout_says_which_knob_to_turn(self):
        """**降级原因必须可行动。**

        用户只看到「模型这轮没答上来」时无从下手——而实测最常见的降级原因
        就是超时。原因里得点名那个配置项。
        """

        class Timeout:
            async def ainvoke(self, messages):
                raise TimeoutError("timed out")

        reply = await ReactIntakeAgent(llm=Timeout()).run(
            IntakeSession(session_id="s1"), "去成都", today=TODAY
        )
        assert reply.degraded
        assert "超时" in reply.degraded_reason
        assert "INTAKE_LLM_TIMEOUT_S" in reply.degraded_reason

    async def test_disabled_llm_is_not_reported_as_a_failure(self, monkeypatch):
        """模型被关掉是配置，不是故障——两者混为一谈会让人白排查一轮。"""
        monkeypatch.setattr(settings, "llm_enabled", False)
        reply = await ReactIntakeAgent().run(
            IntakeSession(session_id="s1"), "去成都", today=TODAY
        )
        assert reply.degraded
        assert "LLM_ENABLED" in reply.degraded_reason

    async def test_intake_uses_its_own_longer_timeout(self, monkeypatch):
        """全局 30s 对 intake 太短：单次调用带着工具说明和对话历史，实测常在
        25~60 秒，超时会静默退回规则解析。"""
        seen: dict = {}

        def fake_get_llm(*, timeout_s=None, **kw):
            seen["timeout_s"] = timeout_s
            raise RuntimeError("不实际调用")

        # conftest 全局关掉了 LLM，这里得开回来才能走到建客户端那一步
        monkeypatch.setattr(settings, "llm_enabled", True)
        monkeypatch.setattr("app.providers.llm.get_llm", fake_get_llm)
        await ReactIntakeAgent().run(
            IntakeSession(session_id="s1"), "去成都", today=TODAY
        )
        assert seen["timeout_s"] == settings.intake_llm_timeout_s
        assert settings.intake_llm_timeout_s > settings.llm_timeout_s

    async def test_finish_normalises_common_field_aliases(self):
        """模型爱写 outbound_date 而不是 outbound_date_text，兜一下。"""
        llm = FakeLLM(
            "Thought: 齐了\nFinish: "
            '{"departure": "北京", "destination": "成都", '
            '"outbound_date": "9月5号", "days": 5}'
        )
        agent = ReactIntakeAgent(llm=llm)
        reply = await agent.run(IntakeSession(session_id="s1"), "...", today=TODAY)
        assert reply.done
        assert reply.draft.request.destination_city == "成都"
        assert reply.draft.request.outbound_date == date(2026, 9, 5)

    async def test_memory_is_visible_to_the_recall_tool(self):
        """记忆里有的不该再问——模型得能查到它。"""
        profile = Profile(profile_id="u1")
        for _ in range(3):
            profile = profile.observe_all({"departure_city": "北京"}, on=TODAY)

        llm = FakeLLM(
            "Thought: 先查记忆\nAction: recall_preference\n"
            'Action Input: {"field": "departure_city"}',
            "Thought: 记忆里有\nResponse: 还是从北京出发吧？",
        )
        agent = ReactIntakeAgent(llm=llm)
        reply = await agent.run(
            IntakeSession(session_id="s1"), "去成都",
            memory=MemorySnapshot(profile=profile), today=TODAY,
        )
        assert '"usable": true' in reply.steps[0].observation.lower()
        assert "北京" in reply.steps[0].observation


class TestSessionStore:
    def test_reuses_an_existing_session(self):
        store = SessionStore()
        first = store.get_or_create()
        assert store.get_or_create(first.session_id) is first

    def test_evicts_when_full(self):
        store = SessionStore(max_sessions=3)
        for _ in range(5):
            store.get_or_create()
        assert len(store) == 3
