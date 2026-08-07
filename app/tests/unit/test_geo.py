"""坐标转换与几何基元测试。

这一层错了后面全错且不报错（只是路线偏 300~600m），所以断言写得比别处密。
"""

from __future__ import annotations

import math

import pytest

from app.core import geo

# 天安门附近，WGS-84 与其对应的 GCJ-02（业界通用参考点）
TIANANMEN_WGS = (116.404, 39.915)
TIANANMEN_GCJ = (116.41024, 39.91640)

TOKYO = (139.767, 35.681)


class TestOutOfChina:
    def test_beijing_is_in_china(self):
        assert not geo.out_of_china(*TIANANMEN_WGS)

    def test_tokyo_is_out_of_china(self):
        assert geo.out_of_china(*TOKYO)

    def test_out_of_china_points_are_untouched(self):
        # 境外不做偏移，否则日本/韩国的坐标会被平白挪走
        assert geo.wgs84_to_gcj02(*TOKYO) == TOKYO
        assert geo.gcj02_to_wgs84(*TOKYO) == TOKYO


class TestWgs84ToGcj02:
    def test_matches_reference_point(self):
        lng, lat = geo.wgs84_to_gcj02(*TIANANMEN_WGS)
        assert lng == pytest.approx(TIANANMEN_GCJ[0], abs=2e-4)
        assert lat == pytest.approx(TIANANMEN_GCJ[1], abs=2e-4)

    def test_offset_magnitude_is_a_few_hundred_metres(self):
        gcj = geo.wgs84_to_gcj02(*TIANANMEN_WGS)
        offset_m = geo.haversine_m(TIANANMEN_WGS, gcj)
        # 北京地区 GCJ-02 偏移量的已知量级；正是"不转换就静默偏移"的那几百米
        assert 300 < offset_m < 800

    def test_offset_direction_is_north_east_in_beijing(self):
        lng, lat = geo.wgs84_to_gcj02(*TIANANMEN_WGS)
        assert lng > TIANANMEN_WGS[0]
        assert lat > TIANANMEN_WGS[1]


class TestRoundTrip:
    @pytest.mark.parametrize(
        "point",
        [
            (116.404, 39.915),  # 北京
            (121.4737, 31.2304),  # 上海
            (120.155, 30.2741),  # 杭州
            (113.2644, 23.1291),  # 广州
            (87.6168, 43.8256),  # 乌鲁木齐（西部边缘）
        ],
    )
    def test_wgs_gcj_wgs_is_lossless(self, point):
        gcj = geo.wgs84_to_gcj02(*point)
        back = geo.gcj02_to_wgs84(*gcj)
        # 迭代求逆后残差应远小于 1mm（1e-9 度 ≈ 0.1mm）
        assert back[0] == pytest.approx(point[0], abs=1e-9)
        assert back[1] == pytest.approx(point[1], abs=1e-9)

    def test_inverse_is_much_better_than_naive_approximation(self):
        gcj = geo.wgs84_to_gcj02(*TIANANMEN_WGS)
        naive = (2 * gcj[0] - geo.wgs84_to_gcj02(*gcj)[0], 2 * gcj[1] - geo.wgs84_to_gcj02(*gcj)[1])
        iterative = geo.gcj02_to_wgs84(*gcj)
        assert geo.haversine_m(iterative, TIANANMEN_WGS) < geo.haversine_m(naive, TIANANMEN_WGS)


class TestFormatting:
    def test_to_amap_uses_lng_lat_order_with_six_decimals(self):
        assert geo.to_amap(116.397428, 39.90923) == "116.397428,39.909230"

    def test_parse_amap_round_trip(self):
        assert geo.parse_amap("116.397428,39.909230") == (116.397428, 39.909230)


class TestHaversine:
    def test_zero_distance(self):
        assert geo.haversine_m(TIANANMEN_WGS, TIANANMEN_WGS) == pytest.approx(0, abs=1e-6)

    def test_known_distance_beijing_to_shanghai(self):
        d_km = geo.haversine_m((116.404, 39.915), (121.4737, 31.2304)) / 1000
        assert 1060 < d_km < 1090  # 北京—上海直线距离约 1070km

    def test_is_symmetric(self):
        a, b = (116.404, 39.915), (121.4737, 31.2304)
        assert geo.haversine_m(a, b) == pytest.approx(geo.haversine_m(b, a))


class TestBearing:
    ORIGIN = (116.400, 39.900)

    @pytest.mark.parametrize(
        "point,expected",
        [
            ((116.400, 40.000), 0),  # 正北
            ((116.500, 39.900), 90),  # 正东
            ((116.400, 39.800), 180),  # 正南
            ((116.300, 39.900), 270),  # 正西
        ],
    )
    def test_cardinal_directions(self, point, expected):
        assert geo.bearing_deg(self.ORIGIN, point) == pytest.approx(expected, abs=1.0)

    def test_range_is_zero_to_360(self):
        for lng in (116.3, 116.4, 116.5):
            for lat in (39.8, 39.9, 40.0):
                if (lng, lat) == self.ORIGIN:
                    continue
                assert 0 <= geo.bearing_deg(self.ORIGIN, (lng, lat)) < 360


class TestCentroid:
    def test_single_point(self):
        assert geo.centroid([(116.4, 39.9)]) == (116.4, 39.9)

    def test_symmetric_points_average_to_middle(self):
        pts = [(116.3, 39.8), (116.5, 40.0)]
        assert geo.centroid(pts) == pytest.approx((116.4, 39.9))

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            geo.centroid([])


class TestClusterByBearing:
    ORIGIN = (116.400, 39.900)

    def test_two_opposite_groups_split_cleanly(self):
        # 3 个点在北边、3 个点在南边，分 2 天应该正好一天一边
        north = [(116.39, 40.00), (116.40, 40.01), (116.41, 40.00)]
        south = [(116.39, 39.80), (116.40, 39.79), (116.41, 39.80)]
        points = north + south

        clusters = geo.cluster_by_bearing(points, self.ORIGIN, 2)

        assert sorted(len(c) for c in clusters) == [3, 3]
        groups = {frozenset(c) for c in clusters}
        assert groups == {frozenset({0, 1, 2}), frozenset({3, 4, 5})}

    def test_balanced_split_keeps_day_sizes_even(self):
        points = [
            (116.40, 40.00),
            (116.47, 39.97),
            (116.50, 39.90),
            (116.47, 39.83),
            (116.40, 39.80),
            (116.33, 39.83),
            (116.30, 39.90),
            (116.33, 39.97),
        ]
        clusters = geo.cluster_by_bearing(points, self.ORIGIN, 4)
        assert [len(c) for c in clusters] == [2, 2, 2, 2]

    def test_every_point_lands_in_exactly_one_cluster(self):
        points = [(116.4 + i * 0.01, 39.9 + (i % 3) * 0.01) for i in range(1, 10)]
        for k in (1, 2, 3, 4):
            clusters = geo.cluster_by_bearing(points, self.ORIGIN, k)
            flat = [i for c in clusters for i in c]
            assert sorted(flat) == list(range(len(points)))

    def test_more_days_than_attractions_yields_empty_days(self):
        points = [(116.41, 39.91), (116.39, 39.89)]
        clusters = geo.cluster_by_bearing(points, self.ORIGIN, 5)
        assert len(clusters) == 5
        assert sum(len(c) for c in clusters) == 2

    def test_no_points(self):
        assert geo.cluster_by_bearing([], self.ORIGIN, 3) == [[], [], []]

    def test_invalid_k(self):
        with pytest.raises(ValueError):
            geo.cluster_by_bearing([(116.4, 39.9)], self.ORIGIN, 0)

    def test_unbalanced_mode_cuts_at_largest_gaps(self):
        # 一簇 4 个点挤在北边，1 个孤点在南边；非均衡模式应尊重几何而不是拆散簇
        points = [
            (116.399, 40.000),
            (116.400, 40.001),
            (116.401, 40.000),
            (116.400, 39.999),
            (116.400, 39.800),
        ]
        clusters = geo.cluster_by_bearing(points, self.ORIGIN, 2, balanced=False)
        assert sorted(len(c) for c in clusters) == [1, 4]
        assert [4] in [sorted(c) for c in clusters]


def test_bearing_and_haversine_agree_on_a_right_triangle():
    """正东走一段再正北走一段，方位角应落在第一象限。"""
    origin = (116.400, 39.900)
    east_then_north = (116.500, 40.000)
    b = geo.bearing_deg(origin, east_then_north)
    assert 0 < b < 90
    assert geo.haversine_m(origin, east_then_north) > 0
    assert not math.isnan(b)
