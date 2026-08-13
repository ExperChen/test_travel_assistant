"""HTTP 契约测试（架构文档 §8）。

HTTP 侧现在只剩三样：探活、参数收集（chat / parse）、鉴权与限流。
创建行程 / SSE / 中断问答那一整套随固定管线删除——规划移到了 CLI 上的
自主 agent，它没有"逐节点推进的状态"可供订阅。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import respx

from app.config import settings
from app.models.errors import ErrorCode
from app.tests.api.conftest import local_only, outbound_calls


class TestAuth:
    @respx.mock
    async def test_requests_are_rejected_without_the_key(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        local_only()

        response = await client.post("/api/v1/trips/parse", json={"prompt": "想去成都"})

        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.UNAUTHORIZED

    @respx.mock
    async def test_correct_key_passes(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        local_only()

        response = await client.post(
            "/api/v1/trips/parse", json={"prompt": "想去成都"},
            headers={"X-API-Key": "s3cret"},
        )

        assert response.status_code == 200

    @respx.mock
    async def test_wrong_key_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        local_only()

        response = await client.post(
            "/api/v1/trips/parse", json={"prompt": "想去成都"},
            headers={"X-API-Key": "wrong"},
        )

        assert response.status_code == 401

    @respx.mock
    async def test_auth_is_off_when_no_key_is_configured(self, client, monkeypatch):
        # 本地开发默认不设 APP_API_KEY
        monkeypatch.setattr(settings, "app_api_key", "")
        local_only()

        response = await client.post("/api/v1/trips/parse", json={"prompt": "想去成都"})

        assert response.status_code == 200


class TestHealth:
    async def test_reports_configuration_without_calling_upstreams(self, client):
        # 探活绝不能真发上游请求——SerpAPI 免费额度只有 250 次/月
        with respx.mock:
            local_only()
            response = await client.get("/health")
            outbound = outbound_calls()

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert not outbound

    async def test_reports_missing_keys(self, client, monkeypatch):
        monkeypatch.setattr(settings, "serpapi_key", "")
        with respx.mock:
            local_only()
            body = (await client.get("/health")).json()

        assert body["providers"]["serpapi"] == "missing_key"

    @pytest.mark.parametrize(
        ("provider", "llm_key", "google_key", "expected"),
        [
            ("openai_compatible", "sk-x", "", "configured"),
            ("openai_compatible", "", "goog-x", "missing_key"),
            ("gemini", "", "goog-x", "configured"),
            ("gemini", "sk-x", "", "missing_key"),
        ],
    )
    async def test_llm_status_follows_the_active_provider(
        self, client, monkeypatch, provider, llm_key, google_key, expected
    ):
        # 两个分支读不同的环境变量，探活只盯一个的话另一半用户永远看到误报
        monkeypatch.setattr(settings, "llm_enabled", True)  # 测试全局默认关掉了模型
        monkeypatch.setattr(settings, "llm_provider", provider)
        monkeypatch.setattr(settings, "llm_api_key", llm_key)
        monkeypatch.setattr(settings, "google_api_key", google_key)
        with respx.mock:
            local_only()
            body = (await client.get("/health")).json()

        assert body["providers"]["llm"] == expected

    async def test_llm_switched_off_is_reported_as_such(self, client, monkeypatch):
        monkeypatch.setattr(settings, "llm_enabled", False)
        with respx.mock:
            local_only()
            body = (await client.get("/health")).json()

        # 不是故障：关掉模型时参数解析改走规则抽取
        assert body["providers"]["llm"] == "disabled"


class TestParsePrompt:
    """一句话 → TripRequest 草稿。不烧 SerpAPI 额度。"""

    @respx.mock
    async def test_returns_a_draft_the_client_can_confirm(self, client):
        local_only()
        outbound = date.today() + timedelta(days=30)

        response = await client.post(
            "/api/v1/trips/parse",
            json={"prompt": f"{outbound.month}月{outbound.day}号从北京去成都玩5天，预算600一晚"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["request"]["destination_city"] == "成都"
        assert body["request"]["budget_per_night"] == 600
        assert not body["missing"]

    @respx.mock
    async def test_every_field_says_where_it_came_from(self, client):
        local_only()
        outbound = date.today() + timedelta(days=30)

        body = (
            await client.post(
                "/api/v1/trips/parse",
                json={"prompt": f"{outbound.month}月{outbound.day}号从北京去成都玩5天"},
            )
        ).json()
        origins = {f["key"]: f["origin"] for f in body["fields"]}

        assert origins["destination_city"] == "prompt"
        assert origins["return_date"] == "derived"
        assert origins["transport"] == "default"

    @respx.mock
    async def test_special_requests_reach_the_draft(self, client):
        """特殊需求是**只能靠说**的字段，解析丢了它就再也没有第二次机会。"""
        local_only()
        outbound = date.today() + timedelta(days=30)

        body = (
            await client.post(
                "/api/v1/trips/parse",
                json={"prompt": f"{outbound.month}月{outbound.day}号从北京去成都玩5天，"
                                "带着老人，我们吃素"},
            )
        ).json()

        assert set(body["request"]["special_requests"]) == {"行动不便", "素食"}

    @respx.mock
    async def test_an_incomplete_prompt_reports_what_is_missing(self, client):
        local_only()

        body = (
            await client.post("/api/v1/trips/parse", json={"prompt": "想去杭州"})
        ).json()

        assert body["request"] is None
        assert body["missing"]
        assert body["questions"]

    async def test_an_empty_prompt_is_rejected_by_validation(self, client):
        response = await client.post("/api/v1/trips/parse", json={"prompt": ""})

        # 和其他入参校验一样走 400 + INVALID_PARAMS 的统一错误信封
        assert response.status_code == 400
        assert response.json()["code"] == ErrorCode.INVALID_PARAMS

    @respx.mock
    async def test_parsing_never_touches_serpapi(self, client):
        # 解析只该动模型（测试里还是关掉的），一次上游请求都不该发
        with respx.mock:
            local_only()
            await client.post(
                "/api/v1/trips/parse", json={"prompt": "9月5号从北京去成都玩5天"}
            )
            outbound = outbound_calls()

        assert not outbound


class TestRateLimit:
    """会调模型的接口必须比读接口限得严。

    `/parse` 和 `/chat` 每次都要打一轮 LLM，按读接口的 60/分钟放行，
    一分钟就能把模型账单顶上去。
    """

    @respx.mock
    async def test_parse_stops_accepting_past_the_configured_limit(self, client):
        local_only()
        limit = int(settings.rate_limit_create.split("/")[0])

        codes = [
            (
                await client.post("/api/v1/trips/parse", json={"prompt": "想去成都"})
            ).status_code
            for _ in range(limit + 2)
        ]

        assert codes[:limit] == [200] * limit
        assert codes[limit:] == [429, 429]
        assert limit < int(settings.rate_limit_read.split("/")[0])

    @respx.mock
    async def test_reads_are_not_blocked_by_the_create_limit(self, client):
        local_only()

        # 轮询探活的客户端不该被"创建"那条更严的限流波及
        codes = [(await client.get("/health")).status_code for _ in range(15)]

        assert set(codes) == {200}
