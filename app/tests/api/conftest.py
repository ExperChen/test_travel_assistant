"""API 测试脚手架。

respx 是全局拦截 httpx 传输的，而测试客户端本身也走 httpx——所以必须给
`testserver` 开一条 pass_through，否则调自己的接口会被当成"未 mock 的外部请求"。

原来这里还有一大套 SSE/后台任务的辅助（`read_events` / `drain` /
`wait_for_pending`）。规划从固定管线换成自主 agent 之后，HTTP 侧不再有
"后台跑着的行程"这个概念，那些辅助连同 `TripService` 一起删了。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.main import create_app

BASE = "http://testserver"


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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=BASE, timeout=30.0
    ) as http:
        yield http


def local_only() -> None:
    """放行本地接口，其余一律拦下——用来断言"这条路径没碰上游"。"""
    respx.route(host="testserver").pass_through()


def outbound_calls() -> list:
    return [c for c in respx.calls if c.request.url.host != "testserver"]


__all__ = ["BASE", "client", "httpx", "local_only", "outbound_calls"]
