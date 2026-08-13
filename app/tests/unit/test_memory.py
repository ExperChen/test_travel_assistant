"""L2 偏好 / L3 履历的模型与存储。"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.memory import (
    HIGH_CONFIDENCE,
    MemorySnapshot,
    Profile,
    TripHistory,
    VisitedAttraction,
    bucket_to_budget,
    budget_bucket,
    recency_band,
)
from app.store import MemoryStore

TODAY = date(2026, 8, 7)


class TestConfidence:
    def test_first_observation_is_not_yet_trusted(self):
        """说过一次不算习惯——直接拿来填参数是过度自信。"""
        profile = Profile(profile_id="u1").observe_all({"travel_class": "business"}, on=TODAY)
        assert profile.preferences["travel_class"].confidence < HIGH_CONFIDENCE
        assert profile.confident_value("travel_class") is None

    def test_three_consistent_observations_earn_trust(self):
        """0.3 → 0.51 → 0.657，第三次越过 0.6 —— 说过三次才算习惯。"""
        profile = Profile(profile_id="u1")
        for _ in range(3):
            profile = profile.observe_all({"travel_class": "business"}, on=TODAY)
        assert profile.preferences["travel_class"].samples == 3
        assert profile.confident_value("travel_class") == "business"

    def test_changing_the_value_resets_samples_and_decays(self):
        """换了值：置信度打七折、采样归一、值换新。

        一个人换了出发城市，第 1 次是异常，第 3 次是搬家——所以要重新攒。
        """
        profile = Profile(profile_id="u1")
        for _ in range(4):
            profile = profile.observe_all({"departure_city": "北京"}, on=TODAY)
        before = profile.preferences["departure_city"].confidence

        profile = profile.observe_all({"departure_city": "上海"}, on=TODAY)
        after = profile.preferences["departure_city"]
        assert after.value == "上海"
        assert after.samples == 1
        assert after.confidence == pytest.approx(before * 0.7, rel=1e-3)

    def test_unknown_fields_are_never_recorded(self):
        """只记 REMEMBERED_FIELDS 里的字段，目的地/日期是噪音。"""
        profile = Profile(profile_id="u1").observe_all(
            {"destination_city": "成都", "outbound_date": "2026-09-05", "travel_class": "business"},
            on=TODAY,
        )
        assert set(profile.preferences) == {"travel_class"}


class TestChildrenAges:
    def test_ages_advance_with_elapsed_years(self):
        """存死数字会让去年 5 岁的孩子永远 5 岁。"""
        profile = Profile(profile_id="u1").observe_all(
            {"children_ages": [5, 8]}, on=date(2024, 3, 1)
        )
        grown = profile.advance_children_ages(date(2026, 8, 7))
        assert grown.preferences["children_ages"].value == [7, 10]

    def test_birthday_not_yet_reached_this_year(self):
        """8 月 7 日记的 5 岁，到次年 8 月 6 日还是 5 岁。"""
        profile = Profile(profile_id="u1").observe_all(
            {"children_ages": [5]}, on=date(2025, 8, 7)
        )
        assert profile.advance_children_ages(date(2026, 8, 6)).preferences[
            "children_ages"
        ].value == [5]

    def test_last_seen_is_not_touched_by_reading(self):
        """`last_seen` 是观测时间，不是读取时间——推进年龄不该改它。"""
        profile = Profile(profile_id="u1").observe_all(
            {"children_ages": [5]}, on=date(2024, 3, 1)
        )
        grown = profile.advance_children_ages(TODAY)
        assert grown.preferences["children_ages"].last_seen == date(2024, 3, 1)


class TestBudgetBucket:
    @pytest.mark.parametrize(
        ("amount", "bucket"),
        [(None, "any"), (200, "under_300"), (300, "under_300"),
         (450, "300_600"), (600, "300_600"), (900, "600_1000"), (2000, "over_1000")],
    )
    def test_bucketing(self, amount, bucket):
        assert budget_bucket(amount) == bucket

    def test_bucket_returns_upper_bound(self):
        """预算是"不超过多少"，取上界才不会把用户想住的酒店滤掉。"""
        assert bucket_to_budget("300_600") == 600

    def test_open_ended_buckets_mean_no_cap(self):
        assert bucket_to_budget("over_1000") is None
        assert bucket_to_budget("any") is None


class TestVisitDecay:
    def test_bands(self):
        assert recency_band(date(2026, 5, 1), TODAY) == "recent"
        assert recency_band(date(2025, 5, 1), TODAY) == "mid"
        assert recency_band(date(2020, 5, 1), TODAY) == "old"

    def test_decay_and_note(self):
        snap = MemorySnapshot(visited={"B001": date(2026, 5, 1), "B002": date(2019, 1, 1)})
        assert snap.decay_for("B001", TODAY) == 0.5
        assert snap.decay_for("B002", TODAY) == 1.0
        assert snap.decay_for("NEVER", TODAY) == 1.0
        assert "2026-05" in snap.visited_note("B001", TODAY)
        assert snap.visited_note("B002", TODAY) == ""


@pytest.mark.asyncio
class TestMemoryStore:
    async def test_roundtrip(self):
        store = MemoryStore(":memory:")
        profile = Profile(profile_id="u1").observe_all({"travel_class": "business"}, on=TODAY)
        assert await store.save_profile(profile)

        loaded = await store.load_profile("u1")
        assert loaded is not None
        assert loaded.preferences["travel_class"].value == "business"
        await store.aclose()

    async def test_missing_profile_is_none_not_an_error(self):
        store = MemoryStore(":memory:")
        assert await store.load_profile("nobody") is None
        await store.aclose()

    async def test_patch_sets_full_confidence(self):
        """手工纠正比采样三次更可信——换了城市不该等三次才生效。"""
        store = MemoryStore(":memory:")
        profile = await store.patch_preference("u1", "departure_city", "上海", on=TODAY)
        assert profile is not None
        assert profile.preferences["departure_city"].confidence == 1.0
        assert profile.confident_value("departure_city") == "上海"
        await store.aclose()

    async def test_forget_one_preference(self):
        store = MemoryStore(":memory:")
        await store.patch_preference("u1", "travel_class", "business", on=TODAY)
        await store.patch_preference("u1", "transport", "driving", on=TODAY)
        profile = await store.forget_preference("u1", "travel_class")
        assert profile is not None
        assert set(profile.preferences) == {"transport"}
        await store.aclose()

    async def test_delete_wipes_preferences_and_history(self):
        """用户有权让系统忘掉一切——包括履历，不只是偏好。"""
        store = MemoryStore(":memory:")
        await store.patch_preference("u1", "travel_class", "business", on=TODAY)
        await store.record_trip("u1", _history("trp_1", date(2026, 3, 1)))

        assert await store.delete_profile("u1")
        assert await store.load_profile("u1") is None
        assert await store.history("u1") == []
        await store.aclose()

    async def test_snapshot_keeps_the_most_recent_visit(self):
        """同一个景点去过多次，按**最近**那次算衰减。"""
        store = MemoryStore(":memory:")
        await store.record_trip("u1", _history("trp_old", date(2024, 1, 1)))
        await store.record_trip("u1", _history("trp_new", date(2026, 6, 1)))

        snap = await store.snapshot("u1", today=TODAY)
        assert snap.visited["B001"] == date(2026, 6, 1)
        assert snap.decay_for("B001", TODAY) == 0.5
        await store.aclose()

    async def test_snapshot_without_profile_id_is_empty(self):
        store = MemoryStore(":memory:")
        assert (await store.snapshot("")).is_empty
        await store.aclose()

    async def test_write_failure_never_raises(self, tmp_path):
        """记忆坏掉不能拖垮规划——写失败只返回 False，不抛。

        用"父级是个文件"制造真实的不可写路径：`mkdir` 会 NotADirectoryError。
        （别用不存在的盘符——Windows 上那种路径会被当成相对路径直接建出来。）
        """
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        store = MemoryStore(blocker / "memory.db")

        assert await store.save_profile(Profile(profile_id="u1")) is False
        assert await store.load_profile("u1") is None
        assert await store.history("u1") == []
        assert (await store.snapshot("u1")).is_empty


def _history(trip_id: str, end: date) -> TripHistory:
    return TripHistory(
        trip_id=trip_id,
        city="成都市",
        adcode="510100",
        start_date=end,
        end_date=end,
        attractions=[VisitedAttraction(poi_id="B001", name="都江堰景区")],
    )
