"""API 测试脚手架。

respx 是全局拦截 httpx 传输的，而测试客户端本身也走 httpx——所以必须给
`testserver` 开一条 pass_through，否则调自己的接口会被当成"未 mock 的外部请求"。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.api.deps import set_service
from app.main import create_app
from app.models.trip import TripRequest
from app.services.trip_service import TripService
from app.tests.e2e._mocks import mock_downstream, mock_flights, outbound_payload

OUTBOUND = date.today() + timedelta(days=30)
RETURN = date.today() + timedelta(days=33)

BASE = "http://testserver"


def trip_payload(**kw) -> dict:
    base = {
        "departure_city": "PEK",
        "destination_city": "杭州",
        "outbound_date": OUTBOUND.isoformat(),
        "return_date": RETURN.isoformat(),
        "auto_select": True,
    }
    return {**base, **kw}


def make_request(**kw) -> TripRequest:
    return TripRequest(**trip_payload(**kw))


def mock_all(*, itineraries: int = 1, hotels: dict | None = None) -> None:
    """一套能跑通全流程的上游响应；本地接口放行。"""
    respx.route(host="testserver").pass_through()
    mock_downstream(hotels=hotels)
    mock_flights(
        OUTBOUND, RETURN, outbound_result=outbound_payload(OUTBOUND, count=itineraries)
    )


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """限流计数是模块级的，所有用例共用 127.0.0.1——不重置就会互相把对方限掉。"""
    from app.api.limits import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    service = TripService()
    app.state.trip_service = service
    set_service(service)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE, timeout=30.0
        ) as http:
            yield http
    finally:
        await service.aclose()
        set_service(None)


STOP_AT = frozenset({"done", "error", "question"})
"""读到哪种事件就收工。

`question` 也算——挂起等用户回答时服务端**不会**发终止事件（真实 SSE 客户端
要保持连接等后续），但测试得停下来去调 /answer。
"""


async def read_events(
    http: AsyncClient,
    trip_id: str,
    *,
    last_event_id: str = "",
    stop_at: frozenset[str] = STOP_AT,
    timeout: float = 5.0,
) -> list[dict]:
    """把一条 SSE 流读到停止条件为止，返回 [{id, event, data}]。

    两处必须绕开的坑：

    1. 以 `data:` 行作为一帧的结束，而不是等空行——httpx 的 `aiter_lines` 会把
       末尾那个分隔空行缓冲住，不等下一个事件到来就不吐出来，靠空行成帧会在
       最后一个事件上永远卡住。
    2. 整体套一层超时——`ASGITransport` 不会向应用发送 `http.disconnect`，
       停在非终止事件（比如 `question`，此时服务端要继续保持连接）上关闭响应，
       客户端侧会一直等生成器结束。这是测试传输层的限制，不是服务端的问题。
    """
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    events: list[dict] = []

    async def pump() -> None:
        current: dict = {}
        async with http.stream(
            "GET", f"/api/v1/trips/{trip_id}/stream", headers=headers
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                key, _, value = line.partition(":")
                current[key.strip()] = value.strip()
                if key.strip() == "data":
                    events.append(current)
                    if current.get("event") in stop_at:
                        return
                    current = {}

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        pass
    return events


def event_types(events: list[dict]) -> list[str]:
    return [e.get("event", "") for e in events]


async def wait_for_pending(service, trip_id: str, timeout: float = 5.0) -> list[dict]:
    """等到行程挂起等待回答，返回通道里的 question 事件负载。

    **为什么不走 SSE**：httpx 的 `ASGITransport` 会缓冲完整响应体才返回，
    对"永不终止"的流（挂起等回答时服务端正是要保持连接）一个字节也读不到。
    终止型的流（done/error）用 `read_events` 测没问题，挂起态只能直接查通道。
    真实的 SSE 传输没有这个限制。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        channel = service.bus.get(trip_id)
        if channel is not None:
            questions = [e for e in channel.replay(0) if e.type == "question"]
            if questions and not service._tasks.get(trip_id):
                return [q.data for q in questions]
        await asyncio.sleep(0.02)
    raise AssertionError(f"{trip_id} 没有在 {timeout}s 内挂起等待回答")


async def drain(http: AsyncClient, service, trip_id: str, timeout: float = 5.0) -> dict:
    """等后台任务跑完并返回最终快照。

    测试结束前必须走一遍：respx 随测试函数退出就解除拦截，还在飞的后台请求
    会逃逸到真实网络。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not service._tasks.get(trip_id):
            break
        await asyncio.sleep(0.02)
    return (await http.get(f"/api/v1/trips/{trip_id}")).json()


async def wait_for_status(
    http: AsyncClient, service, trip_id: str, status: str, timeout: float = 8.0
) -> dict:
    """等行程走到某个终态并返回快照。

    和 `drain` 的区别：清扫任务是过一会儿才起新后台任务的，`drain` 看到
    `_tasks` 一时为空就会提前收工。这里盯的是状态本身。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not service._tasks.get(trip_id):
            plan = (await http.get(f"/api/v1/trips/{trip_id}")).json()
            if plan.get("status") == status:
                return plan
        await asyncio.sleep(0.02)
    raise AssertionError(f"{trip_id} 没有在 {timeout}s 内变成 {status}")


__all__ = [
    "BASE",
    "OUTBOUND",
    "RETURN",
    "client",
    "event_types",
    "httpx",
    "make_request",
    "mock_all",
    "read_events",
    "trip_payload",
    "wait_for_status",
]
