"""记忆接入解析与打分的行为。

核心不变量只有两条，两条都在这里守着：

    记忆只填空，绝不覆盖用户这次说的话
    L3 只降权，绝不硬过滤
"""

from __future__ import annotations

from datetime import date

import pytest

from app.agents.attraction_agent import score_attractions
from app.agents.prompt_parser import Extraction, resolve
from app.models.attraction import Attraction
from app.models.common import GeoPoint
from app.models.memory import MemorySnapshot, Profile
from app.models.trip import TripRequest
from app.services.trip_service import preference_payload

TODAY = date(2026, 8, 7)
ANCHOR = GeoPoint.gcj02(104.07, 30.67)


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
                        today=TODAY, memory=_confident(pace="relaxed"))
        assert draft.request.pace == "relaxed"
        assert next(f for f in draft.fields if f.key == "pace").origin == "memory"

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


def _poi(poi_id: str, name: str, *, must: bool = False) -> Attraction:
    return Attraction(
        poi_id=poi_id, name=name, location=ANCHOR,
        typecode="110200", rating=4.5, recall_rank=0, must_visit=must,
    )


class TestVisitDecayInScoring:
    def test_recent_visit_is_downranked_not_removed(self):
        """去过西湖不代表不想再去——降权可以，删掉不行。"""
        pool = [_poi("B001", "都江堰"), _poi("B002", "青城山")]
        memory = MemorySnapshot(visited={"B001": date(2026, 6, 1)})

        scored = score_attractions(pool, ANCHOR, memory=memory, today=TODAY)
        assert {a.poi_id for a in scored} == {"B001", "B002"}  # 一个都没少
        by_id = {a.poi_id: a for a in scored}
        assert by_id["B001"].score < by_id["B002"].score

    def test_the_downrank_is_explained(self):
        """用户看到熟悉的景点排得靠后，得知道是为什么。"""
        memory = MemorySnapshot(visited={"B001": date(2026, 6, 1)})
        scored = score_attractions([_poi("B001", "都江堰")], ANCHOR,
                                   memory=memory, today=TODAY)
        assert "2026-06" in scored[0].visited_note

    def test_must_visit_is_exempt(self):
        """用户点名要去，去过一百次也照排。"""
        memory = MemorySnapshot(visited={"B001": date(2026, 6, 1)})
        scored = score_attractions([_poi("B001", "都江堰", must=True)], ANCHOR,
                                   memory=memory, today=TODAY)
        assert scored[0].visited_note == ""
        no_memory = score_attractions([_poi("B001", "都江堰", must=True)], ANCHOR,
                                      today=TODAY)
        assert scored[0].score == no_memory[0].score

    @pytest.mark.parametrize(
        ("visited_on", "factor"),
        [(date(2026, 6, 1), 0.5), (date(2025, 6, 1), 0.8), (date(2019, 1, 1), 1.0)],
    )
    def test_decay_bands(self, visited_on, factor):
        base = score_attractions([_poi("B001", "都江堰")], ANCHOR, today=TODAY)[0].score
        decayed = score_attractions(
            [_poi("B001", "都江堰")], ANCHOR,
            memory=MemorySnapshot(visited={"B001": visited_on}), today=TODAY,
        )[0].score
        assert decayed == pytest.approx(round(base * factor, 4), abs=1e-3)

    def test_no_memory_leaves_scores_untouched(self):
        pool = [_poi("B001", "都江堰")]
        assert score_attractions(pool, ANCHOR, today=TODAY)[0].score == \
               score_attractions(pool, ANCHOR, memory=MemorySnapshot(), today=TODAY)[0].score
