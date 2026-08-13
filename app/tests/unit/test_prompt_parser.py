"""自然语言需求解析测试（不碰网络）。

模型部分用假客户端注入；日期部分全是确定性的，直接断言。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.agents.prompt_parser import (
    Extraction,
    extract_by_rules,
    parse_prompt,
    resolve,
)
from app.config import settings

TODAY = date(2026, 8, 6)  # 周四


class FakeLLM:
    """返回一段预设文本；`error` 不为 None 时抛出。"""

    def __init__(self, content: str = "{}", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return type("Response", (), {"content": self.content})()


def llm_returning(**fields) -> FakeLLM:
    return FakeLLM(json.dumps(fields, ensure_ascii=False))


class TestRuleExtraction:
    """模型不可用时的兜底。`LLM_ENABLED=false` 是受支持的运行模式。"""

    def test_pulls_the_common_shape_apart(self):
        e = extract_by_rules("9月5号从北京去成都玩5天，预算600一晚")

        assert e.departure_city == "北京"
        assert e.destination_city == "成都"
        assert e.outbound_date_text == "9月5号"
        assert e.travel_days == 5
        assert e.budget_per_night == 600

    def test_understands_chinese_numerals(self):
        assert extract_by_rules("去西安待四天").travel_days == 4
        assert extract_by_rules("两个大人去杭州").adults == 2

    def test_strips_the_city_suffix(self):
        # 「成都市」和「成都」要归一，否则和高德返回的名字对不上
        assert extract_by_rules("从北京市去成都市玩3天").destination_city == "成都"

    def test_picks_up_transport(self):
        assert extract_by_rules("去成都玩3天，自驾").transport == "driving"
        assert extract_by_rules("去成都玩3天，坐地铁").transport == "transit"

    def test_two_dates_become_outbound_and_return(self):
        e = extract_by_rules("9月5号去成都，9月9号回来")

        assert e.outbound_date_text == "9月5号"
        assert e.return_date_text == "9月9号"

    def test_says_nothing_rather_than_guessing(self):
        e = extract_by_rules("帮我看看")

        assert e.destination_city is None
        assert e.outbound_date_text is None


class TestResolve:
    """归一层：短语 → 绝对日期，缺什么说什么。纯函数。"""

    def test_builds_a_request_when_everything_is_there(self):
        draft = resolve(
            "…",
            Extraction(
                departure_city="北京", destination_city="成都",
                outbound_date_text="9月5号", travel_days=5,
            ),
            today=TODAY,
        )

        assert draft.ok
        assert draft.request.outbound_date == date(2026, 9, 5)
        # 用户口径：「玩 5 天」= 返程日 − 出发日 = 5，即 9/5 走 9/10 回
        assert draft.request.return_date == date(2026, 9, 10)
        assert draft.request.duration_days == 5
        # 行程横跨 6 个日历日（9/5~9/10），排期按这个数开时间窗
        assert draft.request.travel_days == 6

    def test_relative_dates_are_computed_here_not_by_the_model(self):
        draft = resolve(
            "…",
            Extraction(departure_city="上海", destination_city="西安",
                       outbound_date_text="下周三", travel_days=4),
            today=TODAY,
        )

        assert draft.request.outbound_date == date(2026, 8, 12)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("国庆", date(2026, 10, 1)), ("十一假期", date(2026, 10, 1)),
         ("元旦", date(2027, 1, 1)), ("五一", date(2027, 5, 1))],
    )
    def test_solar_holidays_resolve_to_the_first_day(self, text, expected):
        draft = resolve(
            "…",
            Extraction(departure_city="深圳", destination_city="西安",
                       outbound_date_text=text, travel_days=3),
            today=TODAY,
        )

        assert draft.request.outbound_date == expected

    def test_lunar_holidays_are_asked_about_instead_of_guessed(self):
        # 农历节日每年公历日期不同，猜错一天就是查错日子的机票
        draft = resolve(
            "…",
            Extraction(departure_city="深圳", destination_city="西安",
                       outbound_date_text="春节", travel_days=3),
            today=TODAY,
        )

        assert not draft.ok
        assert "出发日期" in draft.missing
        assert any("春节" in q for q in draft.questions)

    def test_missing_pieces_become_questions(self):
        draft = resolve("想去杭州", Extraction(destination_city="杭州"), today=TODAY)

        assert not draft.ok
        assert draft.missing == ["出发地", "出发日期"]
        assert len(draft.questions) == 2

    def test_ambiguous_dates_still_produce_a_request_but_ask(self):
        # 「下周日」两种读法差整整一周——值先用着，同时把问题抛出来
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="下周日", travel_days=3),
            today=TODAY,
        )

        assert draft.ok
        assert any("下周日" in q for q in draft.questions)

    def test_unparseable_date_asks_for_a_concrete_one(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="等天气凉快了", travel_days=3),
            today=TODAY,
        )

        assert not draft.ok
        assert any("等天气凉快了" in q for q in draft.questions)

    def test_no_duration_at_all_asks_how_long(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号"),
            today=TODAY,
        )

        assert not draft.ok
        assert "返程日期" in draft.missing

    def test_the_destination_does_not_become_a_must_visit(self):
        # 「想去杭州」里的杭州是目的地；混进 must_visit 会让 route_planner
        # 拿着城市名去景点池里强行匹配
        draft = resolve(
            "想去杭州玩3天",
            Extraction(departure_city="北京", destination_city="杭州",
                       outbound_date_text="9月5号", travel_days=3,
                       must_visit=["杭州", "西湖"]),
            today=TODAY,
        )

        assert draft.request.must_visit == ["西湖"]


class TestFieldProvenance:
    """每个值都要能说清是用户说的、我们推的，还是默认的。"""

    def test_marks_where_each_value_came_from(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号", travel_days=5),
            today=TODAY,
        )
        origins = {f.key: f.origin for f in draft.fields}

        assert origins["departure_city"] == "prompt"
        assert origins["return_date"] == "derived"  # 由「玩 5 天」推出来
        assert origins["transport"] == "default"

    def test_enum_values_are_shown_in_chinese(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号", travel_days=3,
                       travel_class="business", transport="driving"),
            today=TODAY,
        )
        shown = {f.key: f.value for f in draft.fields}

        assert shown["travel_class"] == "商务舱"
        assert shown["transport"] == "自驾"


class TestParsePrompt:
    async def test_uses_the_model_output(self):
        llm = llm_returning(
            departure_city="北京", destination_city="成都",
            outbound_date_text="9月5号", travel_days=5, budget_per_night=600,
        )

        draft = await parse_prompt("随便一句话", today=TODAY, llm=llm)

        assert draft.ok
        assert not draft.degraded
        assert draft.request.budget_per_night == 600

    async def test_strips_the_code_fence_models_love_to_add(self):
        llm = FakeLLM('```json\n{"destination_city": "成都"}\n```')

        draft = await parse_prompt("去成都", today=TODAY, llm=llm)

        assert any(f.key == "destination_city" for f in draft.fields)

    async def test_tolerates_chatter_around_the_json(self):
        llm = FakeLLM('好的，解析结果如下：{"destination_city": "成都"} 希望有帮助！')

        draft = await parse_prompt("去成都", today=TODAY, llm=llm)

        assert any(f.value == "成都" for f in draft.fields)

    async def test_a_broken_model_falls_back_to_rules(self):
        llm = FakeLLM(error=RuntimeError("模型挂了"))

        draft = await parse_prompt(
            "9月5号从北京去成都玩5天", today=TODAY, llm=llm
        )

        # 解析功能不能因为模型不可用就整个失效
        assert draft.ok
        assert draft.degraded
        assert draft.request.destination_city == "成都"

    async def test_garbage_from_the_model_falls_back_too(self):
        llm = FakeLLM("我不知道你在说什么")

        draft = await parse_prompt("9月5号从北京去成都玩5天", today=TODAY, llm=llm)

        assert draft.ok
        assert draft.degraded

    async def test_disabled_llm_never_builds_a_client(self, monkeypatch):
        import app.agents.prompt_parser as module

        monkeypatch.setattr(settings, "llm_enabled", False)
        monkeypatch.setattr(
            module, "_default_llm", lambda: pytest.fail("关掉了还去建 LLM 客户端")
        )

        draft = await parse_prompt("9月5号从北京去成都玩5天", today=TODAY)

        assert draft.ok
        assert draft.degraded

    async def test_an_empty_prompt_asks_rather_than_crashes(self):
        draft = await parse_prompt("   ", today=TODAY)

        assert not draft.ok
        assert draft.questions

    async def test_overlong_prompts_are_truncated(self):
        llm = llm_returning(destination_city="成都")

        await parse_prompt("啰嗦" * 5000, today=TODAY, llm=llm)

        sent = llm.calls[0][1]["content"]
        assert len(sent) <= 1000

    async def test_the_model_call_is_counted_against_the_quota(self):
        from app.core.metrics import track_quota

        with track_quota() as quota:
            await parse_prompt("去成都", today=TODAY, llm=llm_returning())

        assert quota.llm == 1


class TestSpecialRequests:
    """特殊需求走的是和 must_visit 一样的路子：规则认关键词，模型补自由文本。"""

    def test_rules_pick_up_the_common_ones(self):
        e = extract_by_rules("9月5号从北京去成都玩5天，带着老人，行李多，不早起")
        assert e.special_requests == ["行李多", "行动不便", "不早起"]

    def test_a_plain_request_has_none(self):
        assert extract_by_rules("9月5号从北京去成都玩5天").special_requests == []

    def test_reaches_the_request(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号", travel_days=5,
                       special_requests=["带着我妈，腿脚不太好"]),
            today=TODAY,
        )
        assert draft.request.special_requests == ["行动不便"]

    def test_free_text_survives_normalisation(self):
        """认不出来不等于该丢掉——模型能理解的比规则多。"""
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号", travel_days=5,
                       special_requests=["我对花粉过敏"]),
            today=TODAY,
        )
        assert draft.request.special_requests == ["我对花粉过敏"]

    def test_shows_up_as_a_field_with_provenance(self):
        draft = resolve(
            "…",
            Extraction(departure_city="北京", destination_city="成都",
                       outbound_date_text="9月5号", travel_days=5,
                       special_requests=["不早起"]),
            today=TODAY,
        )
        field = next(f for f in draft.fields if f.key == "special_requests")
        assert field.origin == "prompt"
        assert field.value == "不早起"
