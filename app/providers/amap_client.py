"""高德 Web 服务 API 客户端（POI 搜索 / 路径规划 / 距离测量）。

两个必须知道的前提：
1. Key 类型必须是「Web 服务 API」，JS/Android/iOS 类型的 Key 会直接返回 10001。
2. v3 系列用 `status/info/infocode` 信封，v4 骑行用 `errcode/errmsg/data`，两种都要处理。
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

__all__ = ["AmapClient"]

# https://developer.amap.com/api/webservice/guide/tools/info
_INFOCODE_QUOTA = {"10003", "10044", "10045"}  # 日/并发额度超限
_INFOCODE_RETRIABLE = {"10002", "10004", "10020", "10021", "10022", "20800"}
_INFOCODE_FATAL = {
    "10001": "AMAP_KEY 不合法或不是「Web 服务 API」类型的 Key",
    "10005": "IP 白名单出错，请在高德控制台放开服务器 IP",
    "10008": "MD5 安全码未通过验证",
    "10009": "请求 key 与绑定平台不符",
}


def _is_retriable_http(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


def _describe(exc: BaseException) -> str:
    """`str(httpx.ReadTimeout())` 是空串——只打 str 会得到 `err=` 这种没用的日志。"""
    return f"{type(exc).__name__}: {exc}".rstrip(": ")


class AmapClient:
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
            "amap",
            failure_threshold=self._cfg.breaker_failure_threshold,
            reset_after_s=self._cfg.breaker_reset_after_s,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._cfg.amap_base_url,
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
    async def get(self, path: str, params: dict[str, Any], *, ttl_s: int) -> dict:
        """发起 GET 请求。`path` 形如 `/v5/place/text`、`/v3/direction/driving`。"""
        self._cfg.require("amap_key")

        key = make_key(f"amap:{path}", params)
        if (hit := cache.get(key)) is not None:
            record_call("amap", cached=True)
            log.info("amap cache hit", extra={"path": path, "cache_hit": True})
            return hit

        try:
            self._breaker.check()
        except CircuitOpenError as exc:
            raise UpstreamError(str(exc), provider="amap", circuit_open=True) from exc

        # None 值会被 httpx 编成 "None" 字符串发出去，必须先剔掉
        query = {k: v for k, v in params.items() if v is not None and v != ""}
        query.update(key=self._cfg.amap_key, output="json")

        async def _call() -> dict:
            resp = await self.client.get(path, params=query)
            resp.raise_for_status()
            return resp.json()

        try:
            data = await retry_async(
                _call,
                delays=self._cfg.retry_delays_s,
                should_retry=_is_retriable_http,
                on_retry=lambda n, exc, wait: log.warning(
                    "amap retry",
                    extra={"attempt": n, "wait_s": round(wait, 2), "err": _describe(exc)},
                ),
            )
        except httpx.TimeoutException as exc:
            self._breaker.record_failure()
            raise UpstreamTimeout(
                f"高德超时：{_describe(exc)}", provider="amap", path=path
            ) from exc
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure()
            raise UpstreamError(
                f"高德 HTTP {exc.response.status_code}",
                provider="amap",
                path=path,
                status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            self._breaker.record_failure()
            raise UpstreamError(f"高德请求失败：{exc}", provider="amap", path=path) from exc

        self._raise_on_payload_error(data, path)
        self._breaker.record_success()
        record_call("amap")

        cache.set(key, data, ttl_s)
        log.info("amap ok", extra={"path": path, "cache_hit": False, "quota_used": 1})
        return data

    # ------------------------------------------------------------------
    def _raise_on_payload_error(self, data: dict, path: str) -> None:
        # v4（骑行）信封
        if "errcode" in data:
            if str(data.get("errcode")) == "0":
                return
            self._breaker.record_failure()
            raise UpstreamError(
                f"高德 v4 错误 errcode={data.get('errcode')} {data.get('errmsg', '')}",
                provider="amap",
                path=path,
            )

        # v3/v5 信封
        if str(data.get("status")) == "1":
            return

        infocode = str(data.get("infocode", ""))
        info = str(data.get("info", ""))

        if infocode in _INFOCODE_QUOTA:
            # 额度问题不是故障，不触发熔断，但必须让上层立刻停手
            raise QuotaExceeded(
                f"高德额度超限 infocode={infocode} {info}", provider="amap", path=path
            )

        self._breaker.record_failure()
        if hint := _INFOCODE_FATAL.get(infocode):
            raise UpstreamError(
                f"高德配置错误 infocode={infocode}：{hint}", provider="amap", path=path
            )
        raise UpstreamError(
            f"高德错误 infocode={infocode} info={info}",
            provider="amap",
            path=path,
            retriable_code=infocode in _INFOCODE_RETRIABLE,
        )
