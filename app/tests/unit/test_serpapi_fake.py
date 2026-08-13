"""模拟客户端与切换开关。

关注三件事：
1. **四个 engine 全覆盖**——漏一个就是一个隐藏的真实调用漏点；
2. **缓存与配额计数被保留**——否则模拟环境测不出配额问题，而配额是第一约束；
3. **假数据能被真实解析器吃下**——格式漂移是模拟层最难发现的失败方式。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.cache import cache
from app.core.exceptions import QuotaExceeded, UpstreamError
from app.core.metrics import track_quota
from app.models.hotel import HotelCandidate
from app.providers.mock.hotels import HotelMockGenerator
from app.providers.serpapi_fake import FakeSerpApiClient
from app.tools import registry

IN, OUT = "2026-09-05", "2026-09-10"

FLIGHTS_AC = {"engine": "google_flights_autocomplete", "q": "成都", "hl": "zh-CN"}
FLIGHTS = {
    "engine": "google_flights", "departure_id": "PEK", "arrival_id": "CTU",
    "outbound_date": IN, "return_date": OUT, "type": 1, "adults": 1,
}
HOTELS = {
    "engine": "google_hotels", "q": "成都市酒店",
    "check_in_date": IN, "check_out_date": OUT, "adults": 2,
}
HOTELS_AC = {"engine": "google_hotels_autocomplete", "q": "西湖"}


@pytest.mark.asyncio
class TestDispatch:
    @pytest.mark.parametrize(
        ("params", "key"),
        [(FLIGHTS_AC, "suggestions"), (FLIGHTS, "best_flights"),
         (HOTELS, "properties"), (HOTELS_AC, "suggestions")],
    )
    async def test_all_four_engines_are_covered(self, params, key):
        """漏一个 engine 就是一个隐藏的真实调用漏点。"""
        payload = await FakeSerpApiClient(seed=1).search(params)
        assert key in payload
        assert payload["search_metadata"]["status"] == "Success"

    async def test_unknown_engine_raises_apperror(self):
        """和真实客户端一样对外只抛 AppError，且要点明是模拟层没覆盖。"""
        with pytest.raises(UpstreamError, match="模拟层未覆盖"):
            await FakeSerpApiClient().search({"engine": "google_scholar", "q": "x"})

    async def test_records_the_requests_it_saw(self):
        client = FakeSerpApiClient(seed=1)
        await client.search(FLIGHTS_AC)
        assert client.calls[0]["q"] == "成都"

    async def test_injected_failure_propagates(self):
        """兜底与降级路径要能被验证——比让假客户端假装网络故障直接。"""
        boom = QuotaExceeded("额度耗尽", provider="serpapi")
        with pytest.raises(QuotaExceeded):
            await FakeSerpApiClient(fail_with=boom).search(FLIGHTS)

    async def test_aclose_is_a_noop(self):
        await FakeSerpApiClient().aclose()  # 同接口，不该抛


@pytest.mark.asyncio
class TestCacheAndQuota:
    async def test_second_identical_call_hits_the_cache(self):
        """不保留缓存的话，中断重放的行为会和线上不一致。"""
        cache.clear()
        client = FakeSerpApiClient(seed=1)
        with track_quota() as quota:
            first = await client.search(FLIGHTS)
            second = await client.search(FLIGHTS)
        assert first == second
        assert quota.serpapi == 1
        assert quota.cache_hits == 1

    async def test_quota_reflects_what_a_real_run_would_cost(self):
        """**模拟模式下配额照记。** 测不出配额问题的模拟层没有意义。"""
        cache.clear()
        client = FakeSerpApiClient(seed=1)
        with track_quota() as quota:
            await client.search(FLIGHTS_AC)
            await client.search(FLIGHTS)
            await client.search(HOTELS)
        assert quota.serpapi == 3

    async def test_different_params_are_cached_separately(self):
        cache.clear()
        client = FakeSerpApiClient(seed=1)
        with track_quota() as quota:
            await client.search(FLIGHTS)
            await client.search({**FLIGHTS, "arrival_id": "HGH"})
        assert quota.serpapi == 2


class TestRegistrySwitch:
    def test_mock_flag_swaps_the_client(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.serpapi_mock", True)
        registry.reset_clients()
        try:
            assert type(registry.serpapi_client()).__name__ == "FakeSerpApiClient"
        finally:
            registry.reset_clients()

    def test_default_is_the_real_client(self, monkeypatch):
        """**默认必须是真客户端。** 悄悄跑在假数据上是最坏的失败方式。"""
        monkeypatch.setattr("app.config.settings.serpapi_mock", False)
        registry.reset_clients()
        try:
            assert type(registry.serpapi_client()).__name__ == "SerpApiClient"
        finally:
            registry.reset_clients()

    def test_mock_needs_no_api_key(self, monkeypatch):
        """模拟模式的意义之一就是不需要凭据。"""
        monkeypatch.setattr("app.config.settings.serpapi_mock", True)
        monkeypatch.setattr("app.config.settings.serpapi_key", "")
        registry.reset_clients()
        try:
            assert registry.serpapi_client() is not None
        finally:
            registry.reset_clients()


class TestHotelMock:
    def gen(self, seed: int = 42) -> HotelMockGenerator:
        return HotelMockGenerator(seed=seed)

    def _search(self, **kw):
        base = {"q": "成都市天府广场附近酒店", "check_in_date": IN, "check_out_date": OUT}
        return self.gen(kw.pop("seed", 42)).search(**{**base, **kw})

    def test_parses_into_the_real_model(self):
        from app.tools.serpapi_hotels import _parse_ad, _parse_property

        payload = self._search()
        for raw in payload["properties"]:
            assert isinstance(_parse_property(raw), HotelCandidate)
        for raw in payload["ads"]:
            assert isinstance(_parse_ad(raw), HotelCandidate)

    def test_coordinates_are_wgs84(self):
        """给错坐标系会绕过 GeoPoint 的转换，线上换真接口就偏 300~600m。"""
        from app.tools.serpapi_hotels import _parse_property

        candidate = _parse_property(self._search()["properties"][0])
        assert candidate.location is not None
        assert candidate.location.crs == "WGS84"

    def test_properties_carry_no_address(self):
        """Google Hotels 不返回门牌号——地址是事后用高德逆地理编码补的。"""
        assert all("address" not in p for p in self._search()["properties"])

    def test_ads_have_nightly_price_only(self):
        for ad in self._search()["ads"]:
            assert "extracted_price" in ad
            assert "total_rate" not in ad

    def test_ads_ignore_max_price(self):
        """真实接口的 ads 不守价格筛选——drop_over_budget() 正是为它而生。

        不还原这个"不听话"，那段本地二次筛选的逻辑就等于没被验证。
        """
        payload = self._search(max_price=300)
        assert all(p["rate_per_night"]["extracted_lowest"] <= 300
                   for p in payload["properties"])
        assert any(a["extracted_price"] > 300 for a in payload["ads"])

    def test_total_rate_is_not_a_clean_multiple(self):
        """`total_rate` 含税含费，`rate_per_night` 是起价——两者不是整数倍关系。"""
        nights = (date.fromisoformat(OUT) - date.fromisoformat(IN)).days
        loose = 0
        for p in self._search()["properties"]:
            nightly = p["rate_per_night"]["extracted_lowest"]
            total = p["total_rate"]["extracted_lowest"]
            loose += abs(total - nightly * nights) > 1
        assert loose == len(self._search()["properties"])

    def test_price_scales_with_star_and_city(self):
        cheap = HotelMockGenerator(seed=3).search(
            q="兰州市酒店", check_in_date=IN, check_out_date=OUT)
        posh = HotelMockGenerator(seed=3).search(
            q="上海市酒店", check_in_date=IN, check_out_date=OUT)
        avg = lambda r: sum(  # noqa: E731
            p["rate_per_night"]["extracted_lowest"] for p in r["properties"]
        ) / len(r["properties"])
        assert avg(posh) > avg(cheap)

    def test_area_is_echoed_back(self):
        """商圈来自查询，结果才像是响应了输入。"""
        assert "天府广场" in self._search()["properties"][0]["name"]
        assert "市天府广场" not in self._search()["properties"][0]["name"]

    def test_unknown_city_returns_empty(self):
        """空结果**必须造得出来**——酒店降级到高德那条路径全靠它触发。"""
        payload = self._search(q="不存在的地方xyz酒店")
        assert payload["properties"] == []
        assert payload["ads"] == []

    def test_bad_dates_return_empty(self):
        assert self._search(check_out_date=IN)["properties"] == []

    def test_vacation_rentals_may_lack_hotel_class(self):
        payload = self._search(vacation_rentals=True)
        assert all(p["type"] == "vacation rental" for p in payload["properties"])
        assert all("extracted_hotel_class" not in p for p in payload["properties"])

    def test_seeded_is_reproducible(self):
        assert self._search(seed=9) == self._search(seed=9)
