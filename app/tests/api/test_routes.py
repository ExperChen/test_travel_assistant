"""HTTP 契约测试（架构文档 §8）。"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
import respx

from app.config import settings
from app.models.errors import ErrorCode
from app.services.trip_service import TripService
from app.tests.api.conftest import (
    drain,
    event_types,
    mock_all,
    read_events,
    trip_payload,
    wait_for_pending,
    wait_for_status,
)
from app.tests.e2e._mocks import hotels_payload


class TestCreateTrip:
    @respx.mock
    async def test_accepts_and_returns_a_stream_url(self, client):
        mock_all()

        response = await client.post("/api/v1/trips", json=trip_payload())

        # 一次规划要几十秒且中途可能问用户，不能占着 HTTP 连接 → 202 + SSE
        assert response.status_code == 202
        body = response.json()
        assert body["trip_id"].startswith("trp_")
        assert body["stream_url"] == f"/api/v1/trips/{body['trip_id']}/stream"

    @respx.mock
    async def test_invalid_dates_are_rejected_with_a_readable_message(self, client):
        mock_all()

        response = await client.post(
            "/api/v1/trips",
            json=trip_payload(return_date=trip_payload()["outbound_date"]),
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == ErrorCode.INVALID_PARAMS
        # Pydantic 的原始报错结构对用户没意义，必须压成一句话
        assert body["user_message"]
        assert "ValidationError" not in body["user_message"]

    @respx.mock
    async def test_children_ages_mismatch_is_rejected(self, client):
        mock_all()

        response = await client.post(
            "/api/v1/trips", json=trip_payload(children=2, children_ages=[5])
        )

        assert response.status_code == 400
        assert response.json()["code"] == ErrorCode.INVALID_PARAMS


class TestGetTrip:
    @respx.mock
    async def test_returns_the_plan_snapshot(self, client):
        mock_all()
        trip_id = (await client.post("/api/v1/trips", json=trip_payload())).json()["trip_id"]
        await read_events(client, trip_id)

        response = await client.get(f"/api/v1/trips/{trip_id}")

        assert response.status_code == 200
        plan = response.json()
        assert plan["trip_id"] == trip_id
        assert plan["status"] == "done"
        assert plan["itinerary"] is not None
        assert plan["quota"]["serpapi"] > 0

    async def test_unknown_trip_is_404(self, client):
        response = await client.get("/api/v1/trips/trp_nope")

        assert response.status_code == 404
        assert response.json()["code"] == ErrorCode.TRIP_NOT_FOUND


class TestStream:
    @respx.mock
    async def test_emits_stage_partial_and_done(self, client):
        mock_all()
        trip_id = (await client.post("/api/v1/trips", json=trip_payload())).json()["trip_id"]

        events = await read_events(client, trip_id)
        kinds = event_types(events)

        assert kinds[-1] == "done"
        assert "stage" in kinds
        assert "partial" in kinds
        # 事件 id 必须单调递增，断线重连才有得续
        ids = [int(e["id"]) for e in events]
        assert ids == sorted(ids)

    @respx.mock
    async def test_reconnect_replays_only_the_missed_events(self, client):
        mock_all()
        trip_id = (await client.post("/api/v1/trips", json=trip_payload())).json()["trip_id"]
        full = await read_events(client, trip_id)
        cutoff = full[2]["id"]

        replayed = await read_events(client, trip_id, last_event_id=cutoff)

        assert [int(e["id"]) for e in replayed] == [
            int(e["id"]) for e in full if int(e["id"]) > int(cutoff)
        ]

    @respx.mock
    async def test_late_subscriber_still_gets_the_whole_history(self, client):
        mock_all()
        trip_id = (await client.post("/api/v1/trips", json=trip_payload())).json()["trip_id"]
        await read_events(client, trip_id)  # 跑完再订阅

        events = await read_events(client, trip_id)

        # 环形缓冲保留了历史，晚到的客户端不会看到一片空白
        assert event_types(events)[-1] == "done"

    @respx.mock
    async def test_failure_ends_the_stream_with_an_error_event(self, client):
        mock_all()
        respx.get(
            "https://serpapi.com/search.json",
            params__contains={"engine": "google_flights_autocomplete"},
        ).mock(return_value=respx.MockResponse(200, json={"suggestions": []}))

        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(departure_city="北京"))
        ).json()["trip_id"]

        events = await read_events(client, trip_id)
        assert event_types(events)[-1] == "error"


class TestAnswer:
    @respx.mock
    async def test_question_event_then_answer_completes_the_trip(self, client):
        mock_all(hotels=hotels_payload(count=3))
        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(auto_select=False))
        ).json()["trip_id"]

        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        question = (await wait_for_pending(service, trip_id))[0]

        response = await client.post(
            f"/api/v1/trips/{trip_id}/answer",
            json={"question_id": question["id"], "value": question["default"]},
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == [question["id"]]

        # 必须等后台任务跑完再结束：respx 随测试函数退出就解除拦截，
        # 还在飞的请求会逃逸到真实网络
        plan = await drain(client, service, trip_id)
        assert plan["status"] == "done"

    @respx.mock
    async def test_answering_an_unknown_question_is_409(self, client):
        mock_all(hotels=hotels_payload(count=3))
        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(auto_select=False))
        ).json()["trip_id"]
        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        await wait_for_pending(service, trip_id)

        # 挂起的是 hotel.selection，回答别的问题必须被拒——恢复错分支会让
        # 用户的选择落到不相干的地方
        response = await client.post(
            f"/api/v1/trips/{trip_id}/answer",
            json={"question_id": "flight.itinerary", "value": "1"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == ErrorCode.ANSWER_MISMATCH

    @respx.mock
    async def test_answering_an_unknown_trip_is_404(self, client):
        mock_all()

        response = await client.post(
            "/api/v1/trips/trp_nope/answer", json={"question_id": "x", "value": "1"}
        )

        assert response.status_code == 404

    @respx.mock
    async def test_multiple_answers_in_one_call(self, client):
        mock_all(hotels=hotels_payload(count=3))
        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(auto_select=False))
        ).json()["trip_id"]

        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        pending = await wait_for_pending(service, trip_id)
        answers = {q["id"]: q["default"] for q in pending}

        response = await client.post(
            f"/api/v1/trips/{trip_id}/answer", json={"answers": answers}
        )

        assert response.status_code == 200
        assert sorted(response.json()["accepted"]) == sorted(answers)
        await drain(client, service, trip_id)


class TestAuth:
    @respx.mock
    async def test_requests_are_rejected_without_the_key(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        mock_all()

        response = await client.post("/api/v1/trips", json=trip_payload())

        assert response.status_code == 401
        assert response.json()["code"] == ErrorCode.UNAUTHORIZED

    @respx.mock
    async def test_correct_key_passes(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        mock_all()

        response = await client.post(
            "/api/v1/trips", json=trip_payload(), headers={"X-API-Key": "s3cret"}
        )

        assert response.status_code == 202

    @respx.mock
    async def test_wrong_key_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "app_api_key", "s3cret")
        mock_all()

        response = await client.post(
            "/api/v1/trips", json=trip_payload(), headers={"X-API-Key": "wrong"}
        )

        assert response.status_code == 401

    @respx.mock
    async def test_auth_is_off_when_no_key_is_configured(self, client, monkeypatch):
        # 本地开发默认不设 APP_API_KEY
        monkeypatch.setattr(settings, "app_api_key", "")
        mock_all()

        response = await client.post("/api/v1/trips", json=trip_payload())

        assert response.status_code == 202


class TestHealth:
    async def test_reports_configuration_without_calling_upstreams(self, client):
        # 探活绝不能真发上游请求——SerpAPI 免费额度只有 250 次/月
        with respx.mock:
            respx.route(host="testserver").pass_through()
            response = await client.get("/health")
            outbound = [c for c in respx.calls if c.request.url.host != "testserver"]

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert not outbound

    async def test_reports_missing_keys(self, client, monkeypatch):
        monkeypatch.setattr(settings, "serpapi_key", "")
        with respx.mock:
            respx.route(host="testserver").pass_through()
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
            respx.route(host="testserver").pass_through()
            body = (await client.get("/health")).json()

        assert body["providers"]["llm"] == expected

    async def test_llm_switched_off_is_reported_as_such(self, client, monkeypatch):
        monkeypatch.setattr(settings, "llm_enabled", False)
        with respx.mock:
            respx.route(host="testserver").pass_through()
            body = (await client.get("/health")).json()

        # 不是故障：关掉模型时行程说明改走确定性模板
        assert body["providers"]["llm"] == "disabled"


class TestParsePrompt:
    """一句话 → TripRequest 草稿。不创建行程，不烧 SerpAPI 额度。"""

    @respx.mock
    async def test_returns_a_draft_the_client_can_confirm(self, client):
        respx.route(host="testserver").pass_through()
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
        respx.route(host="testserver").pass_through()
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
        assert origins["pace"] == "default"

    @respx.mock
    async def test_an_incomplete_prompt_reports_what_is_missing(self, client):
        respx.route(host="testserver").pass_through()

        body = (
            await client.post("/api/v1/trips/parse", json={"prompt": "想去杭州"})
        ).json()

        assert body["request"] is None
        assert body["missing"]
        assert body["questions"]

    @respx.mock
    async def test_the_draft_is_accepted_by_the_create_endpoint(self, client):
        """契约的关键：解析出来的 request 必须能原样提交。"""
        mock_all()
        outbound = date.today() + timedelta(days=30)
        draft = (
            await client.post(
                "/api/v1/trips/parse",
                json={"prompt": f"{outbound.month}月{outbound.day}号从北京去杭州玩4天"},
            )
        ).json()

        response = await client.post(
            "/api/v1/trips", json={**draft["request"], "auto_select": True}
        )

        assert response.status_code == 202
        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        await drain(client, service, response.json()["trip_id"])

    async def test_an_empty_prompt_is_rejected_by_validation(self, client):
        response = await client.post("/api/v1/trips/parse", json={"prompt": ""})

        # 和其他入参校验一样走 400 + INVALID_PARAMS 的统一错误信封
        assert response.status_code == 400
        assert response.json()["code"] == ErrorCode.INVALID_PARAMS

    @respx.mock
    async def test_parsing_never_touches_serpapi(self, client):
        # 解析只该动模型（测试里还是关掉的），一次上游请求都不该发
        with respx.mock:
            respx.route(host="testserver").pass_through()
            await client.post(
                "/api/v1/trips/parse", json={"prompt": "9月5号从北京去成都玩5天"}
            )
            outbound = [c for c in respx.calls if c.request.url.host != "testserver"]

        assert not outbound


class TestRateLimit:
    """创建接口必须比读接口限得严得多。

    **每次创建烧 5 次 SerpAPI**（免费额度 250 次/月）。按读接口的 60/分钟放行，
    一分钟就能把整月额度打掉 1.2 倍。
    """

    @respx.mock
    async def test_create_stops_accepting_past_the_configured_limit(self, client, monkeypatch):
        respx.route(host="testserver").pass_through()
        # 只验限流本身，把后台规划摘掉——省得为了测一条 429 跑十遍完整流水线
        monkeypatch.setattr(TripService, "_spawn", lambda self, trip_id, stream: None)
        limit = int(settings.rate_limit_create.split("/")[0])

        codes = [
            (await client.post("/api/v1/trips", json=trip_payload())).status_code
            for _ in range(limit + 2)
        ]

        assert codes[:limit] == [202] * limit
        assert codes[limit:] == [429, 429]
        assert limit < int(settings.rate_limit_read.split("/")[0])

    @respx.mock
    async def test_reads_are_not_blocked_by_the_create_limit(self, client):
        mock_all()
        trip_id = (await client.post("/api/v1/trips", json=trip_payload())).json()["trip_id"]
        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        await read_events(client, trip_id)

        # 轮询进度的客户端不该被"创建"那条更严的限流波及
        codes = [(await client.get(f"/api/v1/trips/{trip_id}")).status_code for _ in range(15)]

        assert set(codes) == {200}
        await drain(client, service, trip_id)


class TestSweeper:
    """超时清扫（架构文档 §4.3）。

    `expires_at` 只是个时间戳，没人来检查它就等于没有：用户看到"选哪个机场"
    直接关掉页面，这次行程会永远停在 waiting_input，checkpointer 里的 thread
    也永远不释放。
    """

    @respx.mock
    async def test_abandoned_trip_is_released_with_the_default_answer(self, client, monkeypatch):
        monkeypatch.setattr(settings, "interrupt_timeout_s", 0)  # 问题一诞生就过期
        mock_all(hotels=hotels_payload(count=3))
        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(auto_select=False))
        ).json()["trip_id"]

        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        await wait_for_pending(service, trip_id)  # 模拟用户看到问题后就此消失
        service.start_sweeper(interval_s=0.05)

        plan = await wait_for_status(client, service, trip_id, "done")
        assert "ANSWER_TIMED_OUT" in {w["code"] for w in plan["warnings"]}

    @respx.mock
    async def test_a_running_trip_is_left_alone(self, client):
        mock_all(hotels=hotels_payload(count=3))
        trip_id = (
            await client.post("/api/v1/trips", json=trip_payload(auto_select=False))
        ).json()["trip_id"]
        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        await wait_for_pending(service, trip_id)

        # 默认 10 分钟超时，刚挂起的问题不该被抢答——否则用户正在选就被替他选了
        assert await service.sweep_expired() == 0

        pending = await wait_for_pending(service, trip_id)
        await client.post(
            f"/api/v1/trips/{trip_id}/answer",
            json={"answers": {q["id"]: q["default"] for q in pending}},
        )
        await drain(client, service, trip_id)

    async def test_sweeper_is_started_only_once(self, client):
        service = client._transport.app.state.trip_service  # type: ignore[attr-defined]
        service.start_sweeper(interval_s=60)
        first = service._sweeper

        service.start_sweeper(interval_s=60)

        # 每次请求都调一次也不会堆出一堆循环
        assert service._sweeper is first


@respx.mock
async def test_concurrent_trips_do_not_share_state(client):
    mock_all()

    first, second = await asyncio.gather(
        client.post("/api/v1/trips", json=trip_payload()),
        client.post("/api/v1/trips", json=trip_payload(destination_city="杭州")),
    )
    a, b = first.json()["trip_id"], second.json()["trip_id"]
    assert a != b

    await read_events(client, a)
    await read_events(client, b)

    plan_a = (await client.get(f"/api/v1/trips/{a}")).json()
    plan_b = (await client.get(f"/api/v1/trips/{b}")).json()
    assert plan_a["trip_id"] == a
    assert plan_b["trip_id"] == b


@pytest.mark.parametrize("path", ["/api/v1/trips/trp_x", "/api/v1/trips/trp_x/stream"])
async def test_unknown_trip_paths_are_404(client, path):
    response = await client.get(path)
    assert response.status_code == 404
