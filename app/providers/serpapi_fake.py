"""`SerpApiClient` 的模拟替身（见 `docs/architecture/serpapi-usage-and-mocking.md` §5 方案 A）。

**接口与 `SerpApiClient` 完全一致**（`search()` / `aclose()`），所以上层的
Tool、Agent、图节点一行都不用改——`registry.serpapi_client()` 换个实例就完成切换。

## 刻意保留的两件事

1. **TTL 缓存。** 不保留的话，LangGraph 中断重放时的行为会和线上不一致——
   线上重放命中缓存不烧额度，模拟环境每次都重新生成，两边的
   `quota.cache_hits` 对不上，这个指标就废了。
2. **配额计数。** 模拟模式下仍然记 `record_call("serpapi")`，
   于是"这次规划**如果**走真接口会烧几次"在模拟环境里依然准确。
   配额是本项目的第一约束，测不出配额问题的模拟层没有意义。

## 刻意**不**保留的

重试、熔断、HTTP 错误归一——模拟数据不会超时也不会 5xx。
需要验证这些路径时用 `fail_with=` 注入异常，比让假客户端假装网络故障更直接。

## 故意还原的"坏行为"

模拟层最容易犯的错是"只会成功"。真实环境里空结果、价格缺失、广告位不守
价格筛选都是常态，而它们恰恰是兜底逻辑的触发条件。这些行为都在
`app/providers/mock/` 的生成器里如实还原了，本类不做任何"美化"。
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.core.cache import cache, make_key
from app.core.exceptions import AppError, UpstreamError
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.providers.mock.flights import FlightMockGenerator
from app.providers.mock.hotels import HotelMockGenerator

log = get_logger(__name__)

__all__ = ["FakeSerpApiClient"]


class FakeSerpApiClient:
    """按 `engine` 分发到对应的模拟生成器。

    `seed=None`（默认）时票价/房价真随机，贴近"每次查价都不一样"的真实体验；
    给定 seed 则完全可复现——测试需要能断言具体数字。
    """

    def __init__(
        self,
        *,
        config: Settings | None = None,
        seed: int | None = None,
        fail_with: AppError | None = None,
    ):
        self._cfg = config or settings
        self._flights = FlightMockGenerator(seed=seed)
        self._hotels = HotelMockGenerator(seed=seed)
        self._fail_with = fail_with
        """注入一个异常，让每次调用都以它失败——用来验证兜底与降级路径。"""
        self.calls: list[dict[str, Any]] = []
        """收到过的请求参数，便于断言"发出去的到底是什么"。"""

    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        """没有连接池要释放，留着只为与真实客户端同接口。"""
        return None

    async def search(self, params: dict[str, Any], *, ttl_s: int | None = None) -> dict:
        engine = params.get("engine", "")
        self.calls.append(dict(params))

        if self._fail_with is not None:
            raise self._fail_with

        ttl = self._cfg.cache_ttl_serpapi_s if ttl_s is None else ttl_s
        key = make_key(f"serpapi:{engine}", params)
        if (hit := cache.get(key)) is not None:
            record_call("serpapi", cached=True)
            log.info("serpapi(mock) cache hit", extra={"engine": engine, "cache_hit": True})
            return hit

        handler = _HANDLERS.get(engine)
        if handler is None:
            # 和真实客户端一样对外只抛 AppError；同时明确点出是模拟层没覆盖，
            # 而不是上游出了问题——这个区分在排查时很值钱
            raise UpstreamError(
                f"模拟层未覆盖的 engine：{engine!r}", provider="serpapi-mock"
            )

        data = handler(self, params)
        record_call("serpapi")
        cache.set(key, data, ttl)
        log.info(
            "serpapi(mock) ok",
            extra={"engine": engine, "cache_hit": False, "quota_used": 1, "mock": True},
        )
        return data

    # ------------------------------------------------------------ 各 engine
    def _flights_autocomplete(self, params: dict[str, Any]) -> dict:
        return self._flights.autocomplete(
            params.get("q", ""), hl=params.get("hl", "")
        )

    def _flights_search(self, params: dict[str, Any]) -> dict:
        return self._flights.search(
            departure_id=params.get("departure_id", ""),
            arrival_id=params.get("arrival_id", ""),
            outbound_date=params.get("outbound_date"),
            return_date=params.get("return_date"),
            # ⚠️ 1=往返、2=单程（官方文档写反了，见 §3.2）
            trip_type=int(params.get("type", 1) or 1),
            adults=int(params.get("adults", 1) or 1),
            children=int(params.get("children", 0) or 0),
            travel_class=int(params.get("travel_class", 1) or 1),
            currency=params.get("currency", "CNY"),
            departure_token=params.get("departure_token", ""),
        )

    def _hotels_search(self, params: dict[str, Any]) -> dict:
        return self._hotels.search(
            q=params.get("q", ""),
            property_token=params.get("property_token", ""),
            check_in_date=params.get("check_in_date"),
            check_out_date=params.get("check_out_date"),
            adults=int(params.get("adults", 2) or 2),
            children=int(params.get("children", 0) or 0),
            max_price=_maybe_int(params.get("max_price")),
            min_price=_maybe_int(params.get("min_price")),
            hotel_class=_split_ints(params.get("hotel_class")),
            vacation_rentals=str(params.get("vacation_rentals", "")).lower() == "true",
            currency=params.get("currency", "CNY"),
        )

    def _hotels_autocomplete(self, params: dict[str, Any]) -> dict:
        return self._hotels.autocomplete(
            params.get("q", ""), currency=params.get("currency", "CNY")
        )


_HANDLERS = {
    "google_flights_autocomplete": FakeSerpApiClient._flights_autocomplete,
    "google_flights": FakeSerpApiClient._flights_search,
    "google_hotels": FakeSerpApiClient._hotels_search,
    "google_hotels_autocomplete": FakeSerpApiClient._hotels_autocomplete,
}
"""四个 engine 全覆盖。

`google_hotels_autocomplete` 目前服务路径上没人调用，但它已经在 Tool 注册表里——
不覆盖就是留一个隐藏的真实调用漏点（文档 §3.4）。
"""


def _maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_ints(value: Any) -> list[int]:
    """`hotel_class` 在请求里是 "4,5" 这样的逗号串。"""
    if not value:
        return []
    if isinstance(value, list):
        return [int(v) for v in value]
    return [int(part) for part in str(value).split(",") if part.strip().isdigit()]
