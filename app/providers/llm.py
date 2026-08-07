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
def get_llm(model: str | None = None, temperature: float | None = None) -> Any:
    """返回一个 LangChain ChatModel。

    调用点很少（只有 summarize），缓存实例即可。
    """
    name = model or settings.llm_model
    temp = settings.llm_temperature if temperature is None else temperature

    if settings.llm_provider == "gemini":
        return _gemini(name, temp)
    return _openai_compatible(name, temp)


def reset_llm() -> None:
    """配置变了（主要是测试里）就丢掉缓存的客户端。"""
    get_llm.cache_clear()


def _openai_compatible(model: str, temperature: float) -> Any:
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
        timeout=settings.llm_timeout_s,
        max_retries=1,  # 重试留给上层的模板兜底，别在这里干等
    )


def _gemini(model: str, temperature: float) -> Any:
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
        timeout=settings.llm_timeout_s,
    )
