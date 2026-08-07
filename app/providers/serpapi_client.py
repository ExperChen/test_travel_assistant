"""SerpAPI HTTP 客户端（Google Flights + Google Hotels）。

只负责发请求、解 JSON、把错误归一成 AppError，不含任何业务逻辑。
额度纪律见架构文档 §6.1：免费版 250 次/月，所以默认命中本地缓存就不发请求，
也绝不主动传 `no_cache=true`（SerpAPI 服务端 1h 缓存命中不扣额度）。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings, settings
from app.core.cache import cache, make_key
from app.core.exceptions import QuotaExceeded, UpstreamError, UpstreamTimeout
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.core.retry import CircuitBreaker, CircuitOpenError, retry_async

log = get_logger(__name__)

__all__ = ["SerpApiClient"]

_QUOTA_HINTS = ("run out of searches", "ran out of searches", "exceeded your", "plan limit")


def _took_seconds(data: dict) -> float | None:
    """从 search_metadata 里取本次耗时——**只用于日志，绝不允许抛异常**。

    各引擎的形状不一致：google_hotels 返回 `{"float": 2.6}`，
    google_flights_autocomplete 直接返回裸 float。曾经这里硬取 `.get("float")`，
    一行日志代码把整个请求打崩了。可观测性代码没有资格弄挂业务。
    """
    try:
        raw = (data.get("search_metadata") or {}).get("total_time_taken")
        if isinstance(raw, dict):
            raw = raw.get("float")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _error_detail(response: httpx.Response, limit: int = 200) -> str:
    """从错误响应里抠出可读的原因。解析失败就退回截断的正文。"""
    try:
        payload = response.json()
        if isinstance(payload, dict) and (err := payload.get("error")):
            return str(err)[:limit]
    except ValueError:
        pass
    return (response.text or "")[:limit].strip() or "（无响应正文）"


def _is_retriable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


def _describe(exc: BaseException) -> str:
    """`str(httpx.ReadTimeout())` 是空串——只打 str 会得到 `err=` 这种没用的日志。"""
    return f"{type(exc).__name__}: {exc}".rstrip(": ")


class SerpApiClient:
    def __init__(
        self,
        *,
        config: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._cfg = config or settings
        self._client = client
        self._owns_client = client is None
        self._breaker = CircuitBreaker(
            "serpapi",
            failure_threshold=self._cfg.breaker_failure_threshold,
            reset_after_s=self._cfg.breaker_reset_after_s,
        )

    # ------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._cfg.http_timeout_s, connect=self._cfg.http_connect_timeout_s
                ),
                headers={"User-Agent": "better-travel-assistant/0.1"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    async def search(self, params: dict[str, Any], *, ttl_s: int | None = None) -> dict:
        """调用 `https://serpapi.com/search.json`。

        `params` 里不要放 api_key，由本方法注入；缓存键也不含 key。
        """
        self._cfg.require("serpapi_key")
        ttl = self._cfg.cache_ttl_serpapi_s if ttl_s is None else ttl_s

        key = make_key(f"serpapi:{params.get('engine', '?')}", params)
        if (hit := cache.get(key)) is not None:
            record_call("serpapi", cached=True)
            log.info("serpapi cache hit", extra={"engine": params.get("engine"), "cache_hit": True})
            return hit

        try:
            self._breaker.check()
        except CircuitOpenError as exc:
            # Provider 层对外只抛 AppError，不泄露内部异常类型
            raise UpstreamError(str(exc), provider="serpapi", circuit_open=True) from exc

        query = {**params, "api_key": self._cfg.serpapi_key, "output": "json"}

        async def _call() -> dict:
            resp = await self.client.get(self._cfg.serpapi_base_url, params=query)
            resp.raise_for_status()
            return resp.json()

        try:
            data = await retry_async(
                _call,
                delays=self._cfg.retry_delays_s,
                should_retry=_is_retriable,
                on_retry=lambda n, exc, wait: log.warning(
                    "serpapi retry",
                    extra={"attempt": n, "wait_s": round(wait, 2), "err": _describe(exc)},
                ),
            )
        except httpx.TimeoutException as exc:
            self._breaker.record_failure()
            raise UpstreamTimeout(f"SerpAPI 超时：{exc}", provider="serpapi") from exc
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure()
            code = exc.response.status_code
            if code == 429:
                raise QuotaExceeded("SerpAPI 触发限流", provider="serpapi", status=code) from exc
            # 带上响应正文：SerpAPI 的 4xx 会在 error 字段里说清是哪个参数不对，
            # 只报一句 "HTTP 400" 等于把唯一的线索扔了（排查时只能手工重放请求）
            raise UpstreamError(
                f"SerpAPI HTTP {code}：{_error_detail(exc.response)}",
                provider="serpapi",
                status=code,
            ) from exc
        except httpx.HTTPError as exc:
            self._breaker.record_failure()
            raise UpstreamError(f"SerpAPI 请求失败：{exc}", provider="serpapi") from exc

        self._raise_on_payload_error(data)
        self._breaker.record_success()
        record_call("serpapi")

        cache.set(key, data, ttl)
        log.info(
            "serpapi ok",
            extra={
                "engine": params.get("engine"),
                "cache_hit": False,
                "quota_used": 1,
                "took_s": _took_seconds(data),
            },
        )
        return data

    # ------------------------------------------------------------------
    def _raise_on_payload_error(self, data: dict) -> None:
        """SerpAPI 经常用 HTTP 200 + `error` 字段报错，必须单独判。"""
        err = data.get("error")
        if not err:
            status = (data.get("search_metadata") or {}).get("status")
            if status and status.lower() == "error":
                self._breaker.record_failure()
                raise UpstreamError("SerpAPI 返回 status=Error", provider="serpapi")
            return

        message = str(err)
        lowered = message.lower()
        if any(hint in lowered for hint in _QUOTA_HINTS):
            # 额度耗尽不算 provider 故障，不该触发熔断
            raise QuotaExceeded(f"SerpAPI 额度耗尽：{message}", provider="serpapi")
        self._breaker.record_failure()
        raise UpstreamError(f"SerpAPI 返回错误：{message}", provider="serpapi")
