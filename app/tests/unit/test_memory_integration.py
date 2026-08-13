"""记忆接入参数解析的行为。

核心不变量：**记忆只填空，绝不覆盖用户这次说的话。**

原来这里还有一半在验 L3（去过的景点只降权、绝不硬过滤）——那套打分随
`attraction_agent` 一起删了。景点现在由模型自己挑，"去过"这件事没有了
写入点，也没有了消费点。
"""

from __future__ import annotations

from datetime import date

from app.agents.prompt_parser import Extraction, resolve
from app.models.memory import MemorySnapshot, Profile, preference_payload
from app.models.trip import TripRequest

TODAY = date(2026, 8, 7)


def _confident(**values) -> MemorySnapshot:
    """攒够三次采样，让偏好越过高置信线。"""
    profile = Profile(profile_id="u1")
    for _ in range(3):
        profile = profile.observe_all(values, on=TODAY)
    return MemorySnapshot(profile=profile)


def _once(**values) -> MemorySnapshot:
    return MemorySnapshot(profile=Profile(profile_id="u1").observe_all(values, on=TODAY))


class TestMemoryFillsOnlyGaps:
    def test_fills_a_missing_field(self):
        draft = resolve("去成都", Extraction(destination_city="成都"),
                        today=TODAY, memory=_confident(departure_city="北京"))
        field = next(f for f in draft.fields if f.key == "departure_city")
        assert field.value == "北京"
        assert field.origin == "memory"

    def test_never_overrides_what_the_user_said(self):
        """冲突是有价值的信号（去更新记忆），不是需要"纠正"的错误。"""
        draft = resolve(
            "从上海去成都",
            Extraction(departure_city="上海", destination_city="成都"),
            today=TODAY, memory=_confident(departure_city="北京"),
        )
        field = next(f for f in draft.fields if f.key == "departure_city")
        assert field.value == "上海"
        assert field.origin == "prompt"

    def test_low_confidence_suggests_but_does_not_fill(self):
        """说过一次就当成习惯，比不记还糟——只能建议。"""
        draft = resolve("去成都", Extraction(destination_city="成都"),
                        today=TODAY, memory=_once(departure_city="北京"))
        assert "出发地" in draft.missing
        assert any("北京" in q for q in draft.questions)
        assert not any(f.key == "departure_city" for f in draft.fields)

    def test_optional_field_from_memory(self):
        draft = resolve("9月5号从北京去成都玩5天",
                        Extraction(departure_city="北京", destination_city="成都",
                                   outbound_date_text="9月5号", travel_days=5),
                        today=TODAY, memory=_confident(transport="driving"))
        assert draft.request.transport == "driving"
        assert next(f for f in draft.fields if f.key == "transport").origin == "memory"

    def test_budget_bucket_becomes_the_upper_bound(self):
        draft = resolve("9月5号从北京去成都玩5天",
                        Extraction(departure_city="北京", destination_city="成都",
                                   outbound_date_text="9月5号", travel_days=5),
                        today=TODAY, memory=_confident(budget_per_night="300_600"))
        assert draft.request.budget_per_night == 600

    def test_open_ended_budget_bucket_is_not_filled(self):
        """"不限"档等于没填，别硬塞一个上限进去。"""
        draft = resolve("9月5号从北京去成都玩5天",
                        Extraction(departure_city="北京", destination_city="成都",
                                   outbound_date_text="9月5号", travel_days=5),
                        today=TODAY, memory=_confident(budget_per_night="any"))
        assert draft.request.budget_per_night is None

    def test_no_memory_behaves_exactly_as_before(self):
        with_none = resolve("去成都", Extraction(destination_city="成都"), today=TODAY)
        with_empty = resolve("去成都", Extraction(destination_city="成都"),
                             today=TODAY, memory=MemorySnapshot())
        assert [f.model_dump() for f in with_none.fields] == \
               [f.model_dump() for f in with_empty.fields]
        assert with_none.missing == with_empty.missing


class TestPreferencePayload:
    def test_budget_is_stored_as_a_bucket(self):
        """去三亚和去县城的预算不是一回事，只有档位跨行程稳定。"""
        payload = preference_payload(TripRequest(
            departure_city="北京", destination_city="成都",
            outbound_date=date(2026, 9, 5), return_date=date(2026, 9, 10),
            budget_per_night=450,
        ))
        assert payload["budget_per_night"] == "300_600"

    def test_destination_and_dates_are_not_remembered(self):
        payload = preference_payload(TripRequest(
            departure_city="北京", destination_city="成都",
            outbound_date=date(2026, 9, 5), return_date=date(2026, 9, 10),
            must_visit=["都江堰"],
        ))
        assert "destination_city" not in payload
        assert "outbound_date" not in payload
        assert "must_visit" not in payload
        assert payload["departure_city"] == "北京"
