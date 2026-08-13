"""高德模拟客户端。

最关键的断言是**几何自洽**：距离与时长必须由请求里的真实坐标算出来。
不自洽的话规划出来会是"相隔 50 公里、步行 5 分钟"这种行程，
而它的时间窗求解完全建立在这些数字上。
"""

from __future__ import annotations

import pytest

from app.core.cache import cache
from app.core.exceptions import UpstreamError
from app.core.geo import haversine_m
from app.core.metrics import track_quota
from app.models.common import GeoPoint
from app.providers.amap_fake import FakeAmapClient
from app.providers.mock.airports import city_center
from app.providers.mock.amap import DETOUR, AmapMockGenerator
from app.tools import registry

CHENGDU = city_center("成都")
TTL = 60


def gen(seed: int = 42) -> AmapMockGenerator:
    return AmapMockGenerator(seed=seed)


def _pt(lng: float, lat: float) -> str:
    return f"{lng:.6f},{lat:.6f}"


@pytest.mark.asyncio
class TestDispatch:
    @pytest.mark.parametrize(
        ("path", "params", "key"),
        [
            ("/v3/config/district", {"keywords": "成都"}, "districts"),
            ("/v5/place/text", {"region": "成都", "types": "110000"}, "pois"),
            ("/v5/place/around", {"location": "104.0668,30.5728"}, "pois"),
            ("/v5/place/detail", {"id": "B001"}, "pois"),
            ("/v3/geocode/regeo", {"location": "104.0668,30.5728"}, "regeocodes"),
            ("/v3/distance", {"origins": "104.0,30.5", "destination": "104.1,30.6"}, "results"),
            ("/v3/direction/walking",
             {"origin": "104.0,30.5", "destination": "104.01,30.51"}, "route"),
            ("/v3/direction/driving",
             {"origin": "104.0,30.5", "destination": "104.1,30.6"}, "route"),
            ("/v3/direction/transit/integrated",
             {"origin": "104.0,30.5", "destination": "104.1,30.6", "city": "028"}, "route"),
        ],
    )
    async def test_all_nine_endpoints_are_covered(self, path, params, key):
        """漏一个端点就是一个隐藏的真实调用漏点。"""
        cache.clear()
        payload = await FakeAmapClient(seed=1).get(path, params, ttl_s=TTL)
        assert payload["status"] == "1"  # ⚠️ 字符串 "1"，不是数字
        assert key in payload

    async def test_unknown_endpoint_raises_apperror(self):
        with pytest.raises(UpstreamError, match="模拟层未覆盖"):
            await FakeAmapClient().get("/v3/nonexistent", {}, ttl_s=TTL)

    async def test_cache_and_quota_are_preserved(self):
        cache.clear()
        client = FakeAmapClient(seed=1)
        params = {"keywords": "成都"}
        with track_quota() as quota:
            first = await client.get("/v3/config/district", params, ttl_s=TTL)
            second = await client.get("/v3/config/district", params, ttl_s=TTL)
        assert first == second
        assert quota.amap == 1
        assert quota.cache_hits == 1


class TestDistrict:
    def test_returns_adcode_and_citycode(self):
        d = gen().district("成都")["districts"][0]
        assert d["adcode"] == "510100"
        assert d["citycode"] == "028"
        assert d["level"] == "city"
        assert d["name"] == "成都市"

    def test_center_is_parseable(self):
        d = gen().district("杭州")["districts"][0]
        point = GeoPoint.from_amap(d["center"])
        assert point.crs == "GCJ02"  # 高德一律 GCJ-02

    def test_unknown_city_is_empty(self):
        assert gen().district("不存在的地方xyz")["districts"] == []


class TestGeometryIsCoherent:
    """路径类端点的距离/时长必须由真实坐标算出。"""

    def test_distance_matches_the_coordinates(self):
        origin = (104.0668, 30.5728)
        dest = (104.1668, 30.6728)
        payload = gen().distance({
            "origins": _pt(*origin), "destination": _pt(*dest), "type": 1,
        })
        result = payload["results"][0]
        straight = haversine_m(origin, dest)
        assert int(result["distance"]) == pytest.approx(straight * DETOUR, rel=0.01)

    def test_straight_line_mode_has_no_detour(self):
        origin, dest = (104.0, 30.5), (104.1, 30.6)
        payload = gen().distance({
            "origins": _pt(*origin), "destination": _pt(*dest), "type": 0,
        })
        assert int(payload["results"][0]["distance"]) == pytest.approx(
            haversine_m(origin, dest), rel=0.01
        )

    @pytest.mark.parametrize(
        ("method", "mode", "lo", "hi"),
        [("direction_walking", "walking", 4.5, 5.5),
         ("direction_driving", "driving", 18.0, 29.0),
         ("direction_transit", "transit", 14.0, 23.0)],
    )
    def test_implied_speed_is_plausible(self, method, mode, lo, hi):
        """反推速度必须落在设定值附近——不然就是随机数冒充路线。"""
        origin, dest = (104.0668, 30.5728), (104.1668, 30.6728)
        payload = getattr(gen(), method)({
            "origin": _pt(*origin), "destination": _pt(*dest), "city": "028",
        })
        route = payload["route"]
        leg = (route.get("paths") or route.get("transits"))[0]
        km = int(leg["distance"] if "distance" in leg else route["distance"]) / 1000
        kmh = km / (int(leg["duration"]) / 3600)
        assert lo <= kmh <= hi, f"{mode} 反推速度 {kmh:.1f} km/h 不合理"

    def test_farther_apart_means_longer(self):
        near = gen().direction_driving({
            "origin": _pt(104.0668, 30.5728), "destination": _pt(104.08, 30.58)})
        far = gen().direction_driving({
            "origin": _pt(104.0668, 30.5728), "destination": _pt(104.40, 30.90)})
        assert int(far["route"]["paths"][0]["duration"]) > int(
            near["route"]["paths"][0]["duration"])

    def test_missing_coordinates_return_empty_route(self):
        assert gen().direction_driving({"origin": "", "destination": ""})["route"]["paths"] == []


class TestPoi:
    def test_attraction_search_uses_real_names(self):
        pois = gen().place_text({"region": "成都", "types": "110000"})["pois"]
        names = {p["name"] for p in pois}
        assert {"宽窄巷子", "杜甫草堂"} & names

    def test_keyword_search_results_match_the_keyword(self):
        """上游 looks_like_match() 靠名字相关性判定命中，不沾边会被整体判为未命中。"""
        pois = gen().place_text({"region": "成都", "keywords": "都江堰"})["pois"]
        assert pois
        assert all("都江堰" in p["name"] for p in pois)

    def test_hotel_type_switches_to_lodging(self):
        pois = gen().place_text({"region": "成都", "types": "100000"})["pois"]
        assert all(p["typecode"] == "100000" for p in pois)

    def test_coordinates_are_stable_for_the_same_poi(self):
        """同名 POI 每次都落在同一位置，否则召回与详情两次调用的坐标会飘。"""
        a = gen(1).place_text({"region": "成都", "types": "110000"})["pois"][0]
        b = gen(2).place_text({"region": "成都", "types": "110000"})["pois"][0]
        assert a["location"] == b["location"]

    def test_paging_returns_different_pois(self):
        p1 = gen().place_text({"region": "成都", "types": "110000", "page_size": 5, "page_num": 1})
        p2 = gen().place_text({"region": "成都", "types": "110000", "page_size": 5, "page_num": 2})
        assert {p["name"] for p in p1["pois"]}.isdisjoint({p["name"] for p in p2["pois"]})

    def test_day_trip_spots_are_actually_far(self):
        """远郊景点必须真的远——否则 split_day_trips() 那条分支永远测不到。"""
        pois = gen().place_text({"region": "成都", "types": "110000", "page_size": 25})["pois"]
        far = {p["name"]: p for p in pois if p["name"] in ("都江堰景区", "青城山")}
        assert far, "分类码检索应当能召回到远郊景点"
        for name, poi in far.items():
            point = GeoPoint.from_amap(poi["location"])
            km = haversine_m(point.coordinate, CHENGDU) / 1000
            assert km > 20, f"{name} 距市中心仅 {km:.1f} km，太近了"

    def test_around_search_carries_distance(self):
        pois = gen().place_around({"location": _pt(*CHENGDU), "radius": 5000})["pois"]
        assert all("distance" in p for p in pois)

    def test_detail_provides_an_entrance(self):
        poi = gen().place_detail({"id": "B001|B002"})["pois"][0]
        assert poi["navi"]["entr_location"]

    def test_regeo_returns_one_address_per_point(self):
        raw = "|".join([_pt(*CHENGDU), _pt(104.1, 30.6), _pt(104.2, 30.7)])
        out = gen().regeo({"location": raw})["regeocodes"]
        assert len(out) == 3
        assert all(a["formatted_address"] for a in out)


class TestRegistrySwitch:
    def test_mock_flag_swaps_the_client(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.amap_mock", True)
        registry.reset_clients()
        try:
            assert type(registry.amap_client()).__name__ == "FakeAmapClient"
        finally:
            registry.reset_clients()

    def test_default_is_the_real_client(self, monkeypatch):
        """默认必须是真客户端——悄悄跑在假数据上是最坏的失败方式。"""
        monkeypatch.setattr("app.config.settings.amap_mock", False)
        registry.reset_clients()
        try:
            assert type(registry.amap_client()).__name__ == "AmapClient"
        finally:
            registry.reset_clients()
