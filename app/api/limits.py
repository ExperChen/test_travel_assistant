"""限流器。

单独一个模块是为了避免循环引用：路由要用 `@limiter.limit(...)` 装饰端点，
而 `main` 又要 import 路由。
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

__all__ = ["limiter"]

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_read],
)
"""默认按读接口限流；创建接口在路由上单独挂更严的 `rate_limit_create`——
每次创建烧 5 次 SerpAPI，按 60/分钟放行一分钟就能打掉月额度。"""
