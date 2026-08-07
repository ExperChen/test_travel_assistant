"""行程编排算法测试（纯函数，完全离线）。

这一层是确定性优化，给定输入必须给出确定输出——所以断言写得很具体。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.agents.route_planner import (
    MIN_VISIT_MINUTES,
    assign_days,
    estimate_minutes,
    order_within_day,
    parse_open_window,
    schedule_day,
    split_day_trips,
)
from app.core.dates import DayWindow
from app.models.attraction import Attraction
from app.models.common import GeoPoint

HOTEL = GeoPoint.gcj02(120.20, 30.25)
DAY = date(2026, 8, 10)


def spot(
    name: str,
    lng: float = 120.21,
    lat: float = 30.26,
    *,
    stay: int = 120,
    opentime: str = "",
    must: bool = False,
    score: float = 0.5,
    cost: float | None = None,
) -> Attraction:
    return Attraction(
        poi_id=name,
        name=name,
        location=GeoPoint.gcj02(lng, lat),
        suggested_duration_min=stay,
        opentime_today=opentime,
        must_visit=must,
        score=score,
        ticket_cost=cost,
    )


def window(start: time, end: time, *, index: int = 1) -> DayWindow:
    return DayWindow(
        day_index=index,
        day=DAY,
        start=datetime.combine(DAY, start),
        end=datetime.combine(DAY, end),
        kind="full",
    )


class TestParseOpenWindow:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("08:00-18:00", (time(8, 0), time(18, 0))),
            ("07:00-18:30", (time(7, 0), time(18, 30))),
            ("09:00-17:00(4月1日-10月31日)", (time(9, 0), time(17, 0))),
            ("08：00－20：00", (time(8, 0), time(20, 0))),
        ],
    )
    def test_parses_ranges(self, text, expected):
        assert parse_open_window(text) == expected

    @pytest.mark.parametrize("text", ["全天开放", "全年 全天开放", "24小时营业"])
    def test_all_day_means_no_constraint(self, text):
        assert parse_open_window(text) is None

    @pytest.mark.parametrize("text", ["", "详见景区公告", "旺季/淡季不同"])
    def test_unparsable_means_no_constraint(self, text):
        # 解析不出来就按不限制处理——宁可排上，也不要把景点误杀
        assert parse_open_window(text) is None

    def test_midnight_close_is_normalised(self):
        assert parse_open_window("10:00-24:00") == (time(10, 0), time(23, 59))

    def test_rejects_impossible_clock_values(self):
        assert parse_open_window("99:00-88:00") is None


class TestEstimateMinutes:
    def test_nearby_points_still_cost_the_floor(self):
        a = GeoPoint.gcj02(120.200, 30.250)
        b = GeoPoint.gcj02(120.201, 30.250)
        assert estimate_minutes(a, b) == 10  # 再近也要 10 分钟

    def test_further_is_longer(self):
        near = estimate_minutes(HOTEL, GeoPoint.gcj02(120.22, 30.25))
        far = estimate_minutes(HOTEL, GeoPoint.gcj02(120.40, 30.25))
        assert far > near

    def test_symmetric(self):
        a, b = HOTEL, GeoPoint.gcj02(120.30, 30.30)
        assert estimate_minutes(a, b) == estimate_minutes(b, a)


class TestAssignDays:
    def test_same_direction_lands_on_the_same_day(self):
        # 三个在北边、三个在南边，分 2 天应该正好一边一天
        north = [spot(f"北{i}", 120.20 + i * 0.005, 30.35) for i in range(3)]
        south = [spot(f"南{i}", 120.20 + i * 0.005, 30.15) for i in range(3)]

        days = assign_days(north + south, HOTEL, 2)

        assert sorted(len(d) for d in days) == [3, 3]
        groups = {frozenset(a.name for a in d) for d in days}
        assert groups == {
            frozenset({"北0", "北1", "北2"}),
            frozenset({"南0", "南1", "南2"}),
        }

    def test_every_attraction_is_assigned_exactly_once(self):
        spots = [spot(f"S{i}", 120.20 + i * 0.01, 30.25 + (i % 3) * 0.01) for i in range(9)]
        for day_count in (1, 2, 3, 4):
            days = assign_days(spots, HOTEL, day_count)
            names = [a.name for d in days for a in d]
            assert sorted(names) == sorted(a.name for a in spots)

    def test_must_visit_sorts_first_within_its_day(self):
        spots = [spot("普通", score=0.9), spot("必去", score=0.1, must=True)]
        days = assign_days(spots, HOTEL, 1)
        assert days[0][0].name == "必去"

    def test_no_attractions(self):
        assert assign_days([], HOTEL, 3) == [[], [], []]

    def test_zero_days(self):
        assert assign_days([spot("A")], HOTEL, 0) == []


class TestOrderWithinDay:
    def test_visits_the_near_one_first(self):
        far = spot("远", 120.40, 30.25)
        near = spot("近", 120.21, 30.25)
        assert [a.name for a in order_within_day([far, near], HOTEL)] == ["近", "远"]

    def test_avoids_zigzag_on_a_line(self):
        # 沿一条线摆开，乱序输入；正确顺序应当是单调推进而不是来回跳
        spots = [
            spot("D", 120.26, 30.25),
            spot("B", 120.22, 30.25),
            spot("A", 120.21, 30.25),
            spot("C", 120.24, 30.25),
        ]
        assert [a.name for a in order_within_day(spots, HOTEL)] == ["A", "B", "C", "D"]

    def test_single_and_empty(self):
        assert order_within_day([], HOTEL) == []
        assert len(order_within_day([spot("A")], HOTEL)) == 1


class TestMustVisitGoesFirst:
    """TSP 纯按地理位置重排，会把 `assign_days` 定好的必去优先级整个抹掉。

    实测都江堰被甩到当天第 4 位，前 3 个先把时间窗吃光，它就整批进了
    `leftover`——用户点名要去的地方就这么没了。
    """

    def test_a_far_must_visit_still_comes_first(self):
        far_must = spot("必去但远", 120.40, 30.25, must=True)
        near = spot("近", 120.21, 30.25)

        ordered = order_within_day([near, far_must], HOTEL)

        assert ordered[0].name == "必去但远"

    def test_the_rest_are_still_route_optimised(self):
        must = spot("必去", 120.21, 30.25, must=True)
        spots = [
            spot("D", 120.26, 30.25),
            spot("B", 120.22, 30.25),
            must,
            spot("C", 120.24, 30.25),
        ]

        ordered = [a.name for a in order_within_day(spots, HOTEL)]

        assert ordered[0] == "必去"
        assert ordered[1:] == ["B", "C", "D"]  # 其余仍单调推进，不来回跳

    def test_several_must_visits_are_ordered_among_themselves(self):
        far = spot("必去远", 120.40, 30.25, must=True)
        near = spot("必去近", 120.21, 30.25, must=True)

        ordered = [a.name for a in order_within_day([far, near], HOTEL)]

        assert ordered == ["必去近", "必去远"]

    def test_no_must_visit_leaves_behaviour_unchanged(self):
        spots = [spot("D", 120.26, 30.25), spot("A", 120.21, 30.25), spot("C", 120.24, 30.25)]

        assert [a.name for a in order_within_day(spots, HOTEL)] == ["A", "C", "D"]

    def test_a_must_visit_is_not_crowded_out_of_the_window(self):
        """把必去景点排在最前，时间窗就轮不到被别人吃光。"""
        spots = [
            spot("填满1", stay=240),
            spot("填满2", 120.22, 30.26, stay=240),
            spot("必去", 120.40, 30.25, stay=120, must=True),
        ]

        planned, leftover = schedule_day(
            order_within_day(spots, HOTEL), window(time(9, 0), time(17, 0)), HOTEL
        )

        assert "必去" in [i.name for i in planned]
        assert "必去" not in [a.name for a in leftover]


class TestScheduleDay:
    def test_fills_the_window_in_order(self):
        spots = [spot("A", stay=120), spot("B", 120.22, 30.26, stay=120)]
        items, leftover = schedule_day(spots, window(time(9, 0), time(21, 0)), HOTEL)

        assert [i.name for i in items] == ["A", "B"]
        assert not leftover
        assert items[0].start_time > datetime.combine(DAY, time(9, 0))  # 先要通勤
        assert items[1].start_time >= items[0].end_time

    def test_overflow_goes_to_leftover(self):
        spots = [spot(f"S{i}", 120.21 + i * 0.01, 30.26, stay=180) for i in range(5)]
        items, leftover = schedule_day(spots, window(time(9, 0), time(15, 0)), HOTEL)

        assert items and leftover
        assert len(items) + len(leftover) == 5
        # 排上的顺序保持不变，塞不下的按原顺序顺延
        assert [i.name for i in items] == [a.name for a in spots[: len(items)]]

    def test_never_schedules_past_the_window(self):
        spots = [spot(f"S{i}", 120.21 + i * 0.01, 30.26, stay=120) for i in range(6)]
        end = datetime.combine(DAY, time(18, 0))
        items, _ = schedule_day(spots, window(time(9, 0), time(18, 0)), HOTEL)

        assert all(i.end_time <= end for i in items)

    def test_waits_for_opening_time(self):
        late = spot("晚开门", opentime="14:00-20:00", stay=60)
        items, _ = schedule_day([late], window(time(9, 0), time(21, 0)), HOTEL)

        assert items[0].start_time == datetime.combine(DAY, time(14, 0))

    def test_closed_attraction_is_dropped(self):
        early = spot("早闭园", opentime="06:00-08:00", stay=60)
        items, leftover = schedule_day([early], window(time(9, 0), time(21, 0)), HOTEL)

        assert not items
        assert [a.name for a in leftover] == ["早闭园"]

    def test_visit_is_truncated_at_closing_time(self):
        closing = spot("17点关门", opentime="09:00-17:00", stay=300)
        items, _ = schedule_day([closing], window(time(14, 0), time(21, 0)), HOTEL)

        assert items[0].end_time == datetime.combine(DAY, time(17, 0))

    def test_too_short_a_visit_is_not_worth_scheduling(self):
        # 只剩不到半小时，排上去只是让行程好看
        spots = [spot("A", stay=120), spot("B", 120.22, 30.26, stay=120)]
        items, leftover = schedule_day(spots, window(time(9, 0), time(11, 20)), HOTEL)

        assert [a.name for a in leftover] == ["B"]
        assert all(
            (i.end_time - i.start_time).total_seconds() / 60 >= MIN_VISIT_MINUTES for i in items
        )

    def test_unusable_window_schedules_nothing(self):
        zero = window(time(9, 0), time(9, 0))
        items, leftover = schedule_day([spot("A")], zero, HOTEL)

        assert not items
        assert len(leftover) == 1

    def test_ticket_cost_is_carried_onto_the_item(self):
        items, _ = schedule_day(
            [spot("收费景点", cost=40.0)], window(time(9, 0), time(21, 0)), HOTEL
        )
        assert items[0].ticket_cost_cny == 40.0

    def test_injected_travel_times_are_respected(self):
        # 第二轮精修会用实测时长重跑排期
        spots = [spot("A", stay=60), spot("B", 120.22, 30.26, stay=60)]
        items, _ = schedule_day(
            spots, window(time(9, 0), time(21, 0)), HOTEL, travel_minutes=lambda a, b: 90
        )
        assert items[0].start_time == datetime.combine(DAY, time(10, 30))


class TestSplitDayTrips:
    """远郊景点前置筛选。

    成都实测：知名度排序把安仁古镇（50km）、青城后山（65km）排得很靠前，
    塞进市内行程会得到「通勤 8.8 小时、游玩 2.9 小时」这种没法用的安排。
    """

    def test_far_attractions_are_separated(self):
        near = spot("市内", 120.21, 30.26)
        far = spot("远郊", 120.80, 30.26)  # 直线约 57km

        kept, day_trips = split_day_trips([near, far], HOTEL)

        assert [a.name for a in kept] == ["市内"]
        assert [a.name for a in day_trips] == ["远郊"]

    def test_must_visit_is_never_filtered_out(self):
        # 用户明确点名了，再远也得去
        far = spot("远郊必去", 120.90, 30.26, must=True)

        kept, day_trips = split_day_trips([far, spot("市内")], HOTEL)

        assert "远郊必去" in [a.name for a in kept]
        assert not day_trips

    def test_everything_far_keeps_everything(self):
        # 全是远郊的小城市：宁可排得累，也别给一份空行程
        far = [spot(f"远{i}", 120.9 + i * 0.01, 30.26) for i in range(3)]

        kept, day_trips = split_day_trips(far, HOTEL)

        assert len(kept) == 3
        assert not day_trips

    def test_radius_is_configurable(self):
        # 必须搭一个市内景点，否则筛空后会触发「全筛掉就全保留」的兜底
        pool = [spot("中距离", 120.40, 30.26), spot("市内", 120.21, 30.26)]  # 约 19km / 1km

        assert not split_day_trips(pool, HOTEL, radius_m=30_000)[1]
        assert [a.name for a in split_day_trips(pool, HOTEL, radius_m=10_000)[1]] == ["中距离"]

    def test_empty_input(self):
        assert split_day_trips([], HOTEL) == ([], [])
