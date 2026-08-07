"""Provider 层契约测试：请求怎么发出去、各类错误怎么归一。

用 respx 拦截 httpx，不产生任何真实调用（真实联调见 `-m live` 标记的用例）。
重点验证两件事：
1. 额度类错误必须显式抛 QuotaExceeded，不能静默当成"没结果"；
2. 缓存命中时不得再发请求——这是 250 次/月额度的第一道闸。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.core.exceptions import QuotaExceeded, UpstreamError, UpstreamTimeout
from app.providers.amap_client import AmapClient
from app.providers.serpapi_client import SerpApiClient

# cfg / _clean_cache 见 app/tests/conftest.py
SERP_URL = "https://serpapi.com/search.json"
AMAP_BASE = "https://restapi.amap.com"


# ---------------------------------------------------------------- SerpAPI
class TestSerpApiClient:
    @respx.mock
    async def test_injects_api_key_and_returns_payload(self, cfg):
        route = respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json={"suggestions": [{"name": "北京"}]})
        )
        client = SerpApiClient(config=cfg)

        data = await client.search({"engine": "google_flights_autocomplete", "q": "北京"})

        assert data["suggestions"][0]["name"] == "北京"
        sent = route.calls.last.request.url
        assert sent.params["api_key"] == "test-serp-key"
        assert sent.params["engine"] == "google_flights_autocomplete"
        await client.aclose()

    @respx.mock
    @pytest.mark.parametrize(
        "metadata",
        [
            {"total_time_taken": {"float": 2.6}},  # google_hotels 的形状
            {"total_time_taken": 1.9},  # autocomplete 直接给裸 float
            {"total_time_taken": None},
            {"total_time_taken": "慢"},
            {},
        ],
    )
    async def test_logging_never_breaks_on_metadata_shape(self, cfg, metadata):
        """曾经这里硬取 `.get("float")`，一行日志代码把整个请求打崩了。

        各引擎的 search_metadata 形状并不一致，可观测性代码没有资格弄挂业务。
        """
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(
                200, json={"suggestions": [], "search_metadata": metadata}
            )
        )
        client = SerpApiClient(config=cfg)

        assert await client.search({"engine": "google_flights_autocomplete"}) is not None
        await client.aclose()

    @respx.mock
    async def test_second_identical_call_is_served_from_cache(self, cfg):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
        client = SerpApiClient(config=cfg)
        params = {"engine": "google_hotels", "q": "杭州"}

        await client.search(params)
        await client.search(params)

        assert route.call_count == 1  # 第二次不许再烧额度
        await client.aclose()

    @respx.mock
    async def test_different_params_are_cached_separately(self, cfg):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
        client = SerpApiClient(config=cfg)

        await client.search({"engine": "google_hotels", "q": "杭州"})
        await client.search({"engine": "google_hotels", "q": "苏州"})

        assert route.call_count == 2
        await client.aclose()

    @respx.mock
    async def test_quota_exhausted_payload_raises_quota_exceeded(self, cfg):
        # SerpAPI 用 HTTP 200 + error 字段报额度耗尽，最容易被当成"没搜到"
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(
                200, json={"error": "Your account has run out of searches."}
            )
        )
        client = SerpApiClient(config=cfg)

        with pytest.raises(QuotaExceeded):
            await client.search({"engine": "google_flights"})
        await client.aclose()

    @respx.mock
    async def test_generic_error_payload_raises_upstream_error(self, cfg):
        respx.get(SERP_URL).mock(
            return_value=httpx.Response(200, json={"error": "Unsupported engine"})
        )
        client = SerpApiClient(config=cfg)

        with pytest.raises(UpstreamError):
            await client.search({"engine": "nope"})
        await client.aclose()

    @respx.mock
    async def test_http_429_raises_quota_exceeded_after_retries(self, cfg):
        route = respx.get(SERP_URL).mock(return_value=httpx.Response(429))
        client = SerpApiClient(config=cfg)

        with pytest.raises(QuotaExceeded):
            await client.search({"engine": "google_flights"})
        assert route.call_count == 3  # 首次 + 2 次退避重试
        await client.aclose()

    @respx.mock
    async def test_transient_500_then_success(self, cfg):
        route = respx.get(SERP_URL).mock(
            side_effect=[
                httpx.Response(500),
                httpx.Response(200, json={"ok": 1}),
            ]
        )
        client = SerpApiClient(config=cfg)

        assert await client.search({"engine": "google_flights"}) == {"ok": 1}
        assert route.call_count == 2
        await client.aclose()

    @respx.mock
    async def test_timeout_is_normalised(self, cfg):
        respx.get(SERP_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        client = SerpApiClient(config=cfg)

        with pytest.raises(UpstreamTimeout):
            await client.search({"engine": "google_flights"})
        await client.aclose()

    @respx.mock
    async def test_failures_eventually_open_the_circuit(self, cfg):
        respx.get(SERP_URL).mock(return_value=httpx.Response(500))
        client = SerpApiClient(config=cfg)

        for i in range(cfg.breaker_failure_threshold):
            with pytest.raises(UpstreamError):
                await client.search({"engine": "google_flights", "n": i})

        with pytest.raises(UpstreamError) as exc:
            await client.search({"engine": "google_flights", "n": "after"})
        assert exc.value.details.get("circuit_open") is True
        await client.aclose()

    async def test_missing_key_fails_fast(self):
        client = SerpApiClient(config=Settings(_env_file=None, serpapi_key=""))
        with pytest.raises(RuntimeError, match="SERPAPI_KEY"):
            await client.search({"engine": "google_flights"})


# ------------------------------------------------------------------- 高德
class TestAmapClient:
    @respx.mock
    async def test_injects_key_and_drops_empty_params(self, cfg):
        route = respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json={"status": "1", "pois": []})
        )
        client = AmapClient(config=cfg)

        await client.get(
            "/v5/place/text",
            {"keywords": "景点", "region": "杭州", "types": None, "sortrule": ""},
            ttl_s=60,
        )

        params = route.calls.last.request.url.params
        assert params["key"] == "test-amap-key"
        assert params["keywords"] == "景点"
        # None/"" 若不剔掉，httpx 会编成字面量 "None" 发给高德
        assert "types" not in params
        assert "sortrule" not in params
        await client.aclose()

    @respx.mock
    async def test_success_envelope(self, cfg):
        respx.get(f"{AMAP_BASE}/v3/distance").mock(
            return_value=httpx.Response(
                200, json={"status": "1", "info": "OK", "results": [{"distance": "1200"}]}
            )
        )
        client = AmapClient(config=cfg)

        data = await client.get("/v3/distance", {"origins": "1,1"}, ttl_s=60)
        assert data["results"][0]["distance"] == "1200"
        await client.aclose()

    @respx.mock
    async def test_invalid_key_gives_actionable_message(self, cfg):
        # 10001 最常见的原因是用了 JS/Android 类型的 Key，报错必须直说
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(
                200, json={"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}
            )
        )
        client = AmapClient(config=cfg)

        with pytest.raises(UpstreamError, match="Web 服务"):
            await client.get("/v5/place/text", {"keywords": "x"}, ttl_s=60)
        await client.aclose()

    @respx.mock
    async def test_daily_quota_raises_quota_exceeded(self, cfg):
        respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(
                200,
                json={"status": "0", "info": "DAILY_QUERY_OVER_LIMIT", "infocode": "10003"},
            )
        )
        client = AmapClient(config=cfg)

        with pytest.raises(QuotaExceeded):
            await client.get("/v5/place/text", {"keywords": "x"}, ttl_s=60)
        await client.aclose()

    @respx.mock
    async def test_v4_bicycling_envelope_is_understood(self, cfg):
        respx.get(f"{AMAP_BASE}/v4/direction/bicycling").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "data": {"paths": []}})
        )
        client = AmapClient(config=cfg)

        data = await client.get("/v4/direction/bicycling", {"origin": "1,1"}, ttl_s=60)
        assert data["data"] == {"paths": []}
        await client.aclose()

    @respx.mock
    async def test_v4_error_envelope(self, cfg):
        respx.get(f"{AMAP_BASE}/v4/direction/bicycling").mock(
            return_value=httpx.Response(200, json={"errcode": 30001, "errmsg": "参数错误"})
        )
        client = AmapClient(config=cfg)

        with pytest.raises(UpstreamError, match="30001"):
            await client.get("/v4/direction/bicycling", {"origin": "1,1"}, ttl_s=60)
        await client.aclose()

    @respx.mock
    async def test_response_is_cached(self, cfg):
        route = respx.get(f"{AMAP_BASE}/v5/place/text").mock(
            return_value=httpx.Response(200, json={"status": "1", "pois": []})
        )
        client = AmapClient(config=cfg)

        await client.get("/v5/place/text", {"keywords": "景点"}, ttl_s=600)
        await client.get("/v5/place/text", {"keywords": "景点"}, ttl_s=600)

        assert route.call_count == 1
        await client.aclose()

    async def test_missing_key_fails_fast(self):
        client = AmapClient(config=Settings(_env_file=None, amap_key=""))
        with pytest.raises(RuntimeError, match="AMAP_KEY"):
            await client.get("/v5/place/text", {}, ttl_s=60)
