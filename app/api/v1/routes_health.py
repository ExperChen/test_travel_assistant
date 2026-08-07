"""健康检查。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.core.cache import cache

router = APIRouter(tags=["ops"])


def _llm_status() -> str:
    """`disabled` 是正常状态，不是故障——关掉模型时行程说明走确定性模板。"""
    if not settings.llm_enabled:
        return "disabled"
    return "configured" if settings.active_llm_key else "missing_key"


@router.get("/health")
async def health() -> dict:
    """探活。

    **不真发上游请求**——那会白烧 SerpAPI 额度（免费版只有 250 次/月）。
    这里只报告"密钥配没配、缓存有多大"，真实可用性由业务调用的日志体现。
    """
    return {
        "status": "ok",
        "providers": {
            "serpapi": "configured" if settings.serpapi_key else "missing_key",
            "amap": "configured" if settings.amap_key else "missing_key",
            "llm": _llm_status(),
        },
        "auth": "enabled" if settings.auth_enabled else "disabled",
        "cache": {
            "entries": len(cache),
            "hits": cache.stats.hits,
            "misses": cache.stats.misses,
            "hit_ratio": round(cache.stats.hit_ratio, 3),
        },
    }
