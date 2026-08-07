"""Tool 注册表与统一装饰器。

Tool 层的职责边界（架构文档 §2）：参数映射 + 结果裁剪，不做决策。
**裁剪是硬性要求**：Google Flights 单次响应可达数百 KB，高德 polyline 单条路线
上万字符。原始响应只进日志和缓存，进 LLM 上下文的必须是裁剪后的结构，
否则 token 成本和幻觉风险同时爆炸。
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.core.exceptions import AppError, UpstreamError
from app.core.logging import get_logger
from app.providers.amap_client import AmapClient
from app.providers.serpapi_client import SerpApiClient

log = get_logger(__name__)

__all__ = [
    "ToolSpec",
    "tool",
    "get_tool",
    "all_specs",
    "as_langchain_tools",
    "serpapi_client",
    "amap_client",
    "override_clients",
    "reset_clients",
    "close_clients",
]

ProviderTag = Literal["serpapi", "amap"]

REGISTRY: dict[str, ToolSpec] = {}


@dataclass(frozen=True)
class ToolSpec:
    """一个 Tool 的完整描述。`parameters` 是给 LLM 看的 JSON Schema。"""

    name: str
    description: str
    parameters: dict[str, Any]
    provider: ProviderTag
    fn: Callable[..., Awaitable[Any]]
    llm_facing: bool = True
    """是否绑给 Agent。

    路径规划四件套收的是带坐标系标注的 GeoPoint 而不是裸经纬度——这是防止
    WGS-84 坐标被静默当成 GCJ-02 的唯一保险。它们由确定性的 route_planner 调用，
    不需要也不应该暴露给 LLM。
    """


def _result_size(result: Any) -> int:
    if isinstance(result, list | tuple):
        return len(result)
    return 1 if result is not None else 0


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    provider: ProviderTag,
    llm_facing: bool = True,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """注册一个 Tool，并包上统一的日志与错误归一。

    重试、缓存、熔断都在 Provider 层做过了，这里不重复。
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except AppError as exc:
                log.warning(
                    "tool failed",
                    extra={"tool": name, "code": exc.code, "err": exc.message},
                )
                raise
            except Exception as exc:  # noqa: BLE001 —— 兜底，保证上层只见 AppError
                log.exception("tool crashed", extra={"tool": name})
                raise UpstreamError(f"{name} 执行失败：{exc}", tool=name) from exc

            log.info(
                "tool ok",
                extra={
                    "tool": name,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "results": _result_size(result),
                },
            )
            return result

        REGISTRY[name] = ToolSpec(
            name, description, parameters, provider, wrapper, llm_facing=llm_facing
        )
        return wrapper

    return decorator


def get_tool(name: str) -> ToolSpec:
    if name not in REGISTRY:
        raise KeyError(f"未注册的 Tool：{name}（已注册：{sorted(REGISTRY)}）")
    return REGISTRY[name]


def all_specs() -> list[ToolSpec]:
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def as_langchain_tools(names: list[str] | None = None) -> list[Any]:
    """转成 LangChain StructuredTool，供 Agent 绑定。

    不指定 names 时只导出 llm_facing 的 Tool。延迟导入：地基层与单元测试不依赖 langchain。
    """
    from langchain_core.tools import StructuredTool

    specs = [get_tool(n) for n in names] if names else [s for s in all_specs() if s.llm_facing]
    return [
        StructuredTool.from_function(
            coroutine=spec.fn,
            name=spec.name,
            description=spec.description,
            args_schema=None,
        )
        for spec in specs
    ]


# ---------------------------------------------------------------- 客户端
# 用可替换的模块级单例而不是 lru_cache：测试需要注入自己的客户端，
# 否则每个用例都得依赖开发机 .env 里的真实 key。
_serpapi: SerpApiClient | None = None
_amap: AmapClient | None = None


def serpapi_client() -> SerpApiClient:
    global _serpapi
    if _serpapi is None:
        _serpapi = SerpApiClient()
    return _serpapi


def amap_client() -> AmapClient:
    global _amap
    if _amap is None:
        _amap = AmapClient()
    return _amap


def override_clients(
    *, serpapi: SerpApiClient | None = None, amap: AmapClient | None = None
) -> None:
    """测试专用：替换全局客户端。"""
    global _serpapi, _amap
    if serpapi is not None:
        _serpapi = serpapi
    if amap is not None:
        _amap = amap


def reset_clients() -> None:
    """测试专用：丢弃全局客户端（不关连接池，测试里用的是 respx 假传输）。"""
    global _serpapi, _amap
    _serpapi = None
    _amap = None


async def close_clients() -> None:
    """应用关闭时调用，释放 httpx 连接池。"""
    global _serpapi, _amap
    if _serpapi is not None:
        await _serpapi.aclose()
        _serpapi = None
    if _amap is not None:
        await _amap.aclose()
        _amap = None
