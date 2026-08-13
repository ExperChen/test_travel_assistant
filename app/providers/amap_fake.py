"""`AmapClient` 的模拟替身。

与 [`serpapi_fake.FakeSerpApiClient`](serpapi_fake.py) 同构：接口一致
（`get()` / `aclose()`），切换点收在 `registry.amap_client()` 一处。

保留缓存与配额计数、不保留重试与熔断，理由同 SerpAPI 那份。

> **和 SerpAPI 的区别**：高德日配额 5000，不是稀缺资源，所以线上没有省额度的
> 压力。这里做模拟是为了**完全离线**——断网、无 key 也能跑通整条规划链，
> 演示和 CI 都用得上。
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.core.cache import cache, make_key
from app.core.exceptions import AppError, UpstreamError
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.providers.mock.amap import AmapMockGenerator

log = get_logger(__name__)

__all__ = ["FakeAmapClient"]


class FakeAmapClient:
    """按 path 分发到对应的模拟生成器。"""

    def __init__(
        self,
        *,
        config: Settings | None = None,
        seed: int | None = None,
        fail_with: AppError | None = None,
    ):
        self._cfg = config or settings
        self._gen = AmapMockGenerator(seed=seed)
        self._fail_with = fail_with
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def aclose(self) -> None:
        return None

    async def get(self, path: str, params: dict[str, Any], *, ttl_s: int) -> dict:
        self.calls.append((path, dict(params)))

        if self._fail_with is not None:
            raise self._fail_with

        # 与真实客户端一致：None 值不参与请求，也就不该进缓存键
        clean = {k: v for k, v in params.items() if v is not None}
        key = make_key(f"amap:{path}", clean)
        if (hit := cache.get(key)) is not None:
            record_call("amap", cached=True)
            return hit

        handler = _HANDLERS.get(path)
        if handler is None:
            raise UpstreamError(f"模拟层未覆盖的高德端点：{path!r}", provider="amap-mock")

        data = handler(self._gen, clean)
        record_call("amap")
        cache.set(key, data, ttl_s)
        log.info("amap(mock) ok", extra={"path": path, "mock": True})
        return data


_HANDLERS = {
    "/v3/config/district": lambda g, p: g.district(str(p.get("keywords") or "")),
    "/v5/place/text": lambda g, p: g.place_text(p),
    "/v5/place/around": lambda g, p: g.place_around(p),
    "/v5/place/detail": lambda g, p: g.place_detail(p),
    "/v3/geocode/regeo": lambda g, p: g.regeo(p),
    "/v3/distance": lambda g, p: g.distance(p),
    "/v3/direction/walking": lambda g, p: g.direction_walking(p),
    "/v3/direction/driving": lambda g, p: g.direction_driving(p),
    "/v3/direction/transit/integrated": lambda g, p: g.direction_transit(p),
    # v5 的入参名不同（city1/city2、show_fields），但模拟层只用到 origin/destination，
    # 所以可以直接复用同一个生成器
    "/v5/direction/transit/integrated": lambda g, p: g.direction_transit(p),
}
"""项目实际用到的 9 个端点全覆盖。未覆盖的抛 `UpstreamError` 并点明是模拟层的锅。"""
