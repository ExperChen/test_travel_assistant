"""LLM 客户端工厂。

这是全项目**唯一**构造 LLM 客户端的地方——`summarize()` 又支持注入客户端，
所以换供应商只影响这一个文件。

延迟导入各家 SDK：地基层（models / core / providers 的 HTTP 部分）不该因为
没装 LLM 依赖就 import 失败，单元测试也不需要它们。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

__all__ = ["get_llm", "reset_llm"]


@lru_cache(maxsize=4)
def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    timeout_s: float | None = None,
) -> Any:
    """返回一个 LangChain ChatModel。

    `timeout_s` 留空用全局默认（30s）。自主规划 Agent 要传更大的值——
    带十几个工具 schema 的请求 30 秒不够（实测直接 APITimeoutError）。
    """
    name = model or settings.llm_model
    temp = settings.llm_temperature if temperature is None else temperature
    timeout = settings.llm_timeout_s if timeout_s is None else timeout_s

    if settings.llm_provider == "gemini":
        return _gemini(name, temp, timeout)
    return _openai_compatible(name, temp, timeout)


def reset_llm() -> None:
    """配置变了（主要是测试里）就丢掉缓存的客户端。"""
    get_llm.cache_clear()


def _openai_compatible(model: str, temperature: float, timeout: float) -> Any:
    """DeepSeek / 百炼 / 智谱 / Kimi / 火山方舟 / 硅基流动 / OpenAI …

    这些厂商的接口都兼容 OpenAI，换家只是换 base_url + model + key。
    """
    settings.require("llm_api_key")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 langchain-openai，请先 pip install -r requirements.txt") from exc

    log.info("使用 OpenAI 兼容模型", extra={"model": model, "base_url": settings.llm_base_url})
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=timeout,
        max_retries=1,  # 重试留给上层的模板兜底，别在这里干等
    )


def _gemini(model: str, temperature: float, timeout: float) -> Any:
    settings.require("google_api_key")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 langchain-google-genai，请先 pip install -r requirements.txt"
        ) from exc

    log.info("使用 Gemini", extra={"model": model})
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        google_api_key=settings.google_api_key,
        timeout=timeout,
    )
