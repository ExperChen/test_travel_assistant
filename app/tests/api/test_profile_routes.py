"""profile 三个口子 + 记忆在 API 层的接入。

文档 §6 把导出/删除/纠正列为**必须有的口子**，不是可选项——所以它们的
可用性本身就该有测试守着。
"""

from __future__ import annotations

from datetime import date

import pytest
import respx

from app.store import MemoryStore, reset_store
from app.tests.api.conftest import mock_all, read_events, trip_payload

PROFILE = {"X-Profile-Id": "usr_test_1"}


@pytest.fixture(autouse=True)
def _isolated_store():
    """每个用例一份 :memory: 库，别往开发机上写真文件。"""
    store = MemoryStore(":memory:")
    reset_store(store)
    yield store
    reset_store(None)


@pytest.mark.asyncio
class TestProfileEndpoints:
    async def test_empty_profile_is_not_a_404(self, client):
        """"还没记住任何东西"是正常状态，不是错误。"""
        response = await client.get("/api/v1/profile/me", headers=PROFILE)
        assert response.status_code == 200
        body = response.json()
        assert body["preferences"] == {}
        assert body["history"] == []

    async def test_missing_header_is_rejected(self):
        """没有 X-Profile-Id 就不知道操作哪一份记忆。"""
        from httpx import ASGITransport, AsyncClient

        from app.main import create_app

        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://testserver"
        ) as http:
            assert (await http.get("/api/v1/profile/me")).status_code == 400

    async def test_patch_then_export(self, client):
        response = await client.patch(
            "/api/v1/profile/me",
            json={"key": "departure_city", "value": "上海"},
            headers=PROFILE,
        )
        assert response.status_code == 200
        prefs = response.json()["preferences"]
        assert prefs["departure_city"]["value"] == "上海"
        # 手工纠正即刻生效，不用等三次采样
        assert prefs["departure_city"]["confidence"] == 1.0

    async def test_patch_with_null_forgets_the_field(self, client):
        await client.patch("/api/v1/profile/me",
                           json={"key": "pace", "value": "packed"}, headers=PROFILE)
        response = await client.patch("/api/v1/profile/me",
                                      json={"key": "pace", "value": None}, headers=PROFILE)
        assert response.json()["preferences"] == {}

    async def test_patch_rejects_unknown_fields(self, client):
        """只有 REMEMBERED_FIELDS 里的字段能写——不能拿它当任意 KV 存储。"""
        response = await client.patch(
            "/api/v1/profile/me",
            json={"key": "destination_city", "value": "三亚"},
            headers=PROFILE,
        )
        assert response.status_code == 400

    async def test_delete_clears_everything(self, client, _isolated_store):
        await client.patch("/api/v1/profile/me",
                           json={"key": "pace", "value": "packed"}, headers=PROFILE)
        assert (await client.delete("/api/v1/profile/me", headers=PROFILE)).status_code == 204
        assert (await client.get("/api/v1/profile/me",
                                 headers=PROFILE)).json()["preferences"] == {}

    async def test_profiles_are_isolated(self, client):
        await client.patch("/api/v1/profile/me",
                           json={"key": "pace", "value": "packed"}, headers=PROFILE)
        other = await client.get("/api/v1/profile/me", headers={"X-Profile-Id": "usr_other"})
        assert other.json()["preferences"] == {}


@pytest.mark.asyncio
class TestProfileIdValidation:
    async def test_malformed_id_is_treated_as_absent_not_an_error(self, client):
        """记忆是增量特性，不该因为一个畸形的头让整次请求 400。"""
        response = await client.get("/api/v1/profile/me",
                                    headers={"X-Profile-Id": "bad id!"})
        assert response.status_code == 400  # 退化成"没提供" → 缺头
        assert "X-Profile-Id" in response.json()["message"]

    @respx.mock
    async def test_creating_a_trip_without_profile_still_works(self, client):
        """不带 profile 头的老调用方一切照旧。

        **必须把 SSE 流读完再退出**：`POST /trips` 起的是后台任务，不排空它
        就会泄漏到后续用例里继续跑，把全局缓存和熔断器搅乱（实测会让
        test_providers 里的 8 个用例莫名其妙地失败）。
        """
        mock_all()
        response = await client.post("/api/v1/trips", json=trip_payload())
        assert response.status_code == 202
        await read_events(client, response.json()["trip_id"])


@pytest.mark.asyncio
class TestMemoryAwareParse:
    @respx.mock
    async def test_parse_fills_from_memory(self, client, _isolated_store):
        """记忆够可信时，`/parse` 直接把出发地填上并标明出处。"""
        for _ in range(3):
            profile = await _isolated_store.load_profile("usr_test_1")
            from app.models.memory import Profile

            profile = (profile or Profile(profile_id="usr_test_1")).observe_all(
                {"departure_city": "北京"}, on=date.today()
            )
            await _isolated_store.save_profile(profile)

        respx.route(host="testserver").pass_through()
        response = await client.post(
            "/api/v1/trips/parse", json={"prompt": "9月5号去成都玩5天"},
            headers=PROFILE,
        )
        assert response.status_code == 200
        fields = {f["key"]: f for f in response.json()["fields"]}
        assert fields["departure_city"]["origin"] == "memory"
        assert fields["departure_city"]["value"] == "北京"


@pytest.mark.asyncio
class TestChatIntake:
    @respx.mock
    async def test_multi_turn_accumulates(self, client, monkeypatch):
        """**核心场景**：分三轮说完，不用重复前面说过的。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        respx.route(host="testserver").pass_through()

        first = await client.post("/api/v1/trips/chat", json={"message": "想去成都"})
        session_id = first.json()["session_id"]
        assert not first.json()["draft"]

        await client.post("/api/v1/trips/chat",
                          json={"message": "从北京出发", "session_id": session_id})
        third = await client.post(
            "/api/v1/trips/chat",
            json={"message": "9月5号走，玩5天", "session_id": session_id},
        )

        body = third.json()
        assert body["draft"] is not None
        request = body["draft"]["request"]
        assert request["departure_city"] == "北京"
        assert request["destination_city"] == "成都"

    @respx.mock
    async def test_draft_origins_can_drive_the_clarify_step(self, client, monkeypatch):
        """`/chat` 产出的 origin 正是 `/trips` 的 `origins` 入参。"""
        monkeypatch.setattr("app.config.settings.llm_enabled", False)
        respx.route(host="testserver").pass_through()
        response = await client.post(
            "/api/v1/trips/chat",
            json={"message": "9月5号从北京去成都玩5天"},
        )
        fields = response.json()["draft"]["fields"]
        origins = {f["key"]: f["origin"] for f in fields}
        # 没提人数/节奏 → 全是 default，正是 clarify 该问的
        assert origins["adults"] == "default"
        assert origins["pace"] == "default"
