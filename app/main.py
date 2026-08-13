"""FastAPI 应用装配。

    uvicorn app.main:app --host 127.0.0.1 --port 8000

规划本身不在这里——它是 CLI 上的自主 agent（`main.py`）。HTTP 侧只剩
参数收集（`/trips/chat`、`/trips/parse`）和长期记忆（`/profile/*`）。

⚠️ **仍建议 `--workers 1`**：intake 的会话存储和配额记账都是进程内的，
多 worker 下同一段对话可能落到不同进程上，累积的槽位就断了。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.errors import install_error_handlers
from app.api.limits import limiter
from app.api.v1 import routes_health, routes_profile, routes_trips
from app.config import settings
from app.core.logging import get_logger, setup_logging
from app.tools.registry import close_clients

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level, json_output=settings.log_json)
    log.info(
        "服务启动",
        extra={"auth": settings.auth_enabled, "model": settings.llm_model},
    )
    try:
        yield
    finally:
        await close_clients()
        log.info("服务已停止")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Better Travel Assistant",
        version="0.1.0",
        description="行程需求收集与长期记忆（目的地限中国大陆）。规划由 CLI 上的自主 agent 承担",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # 不用 "*"：带 X-API-Key 的请求配上通配来源等于把 key 暴露给任意站点
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key", "X-Profile-Id"],
    )

    install_error_handlers(app)
    app.include_router(routes_health.router)
    app.include_router(routes_trips.router, prefix="/api/v1")
    app.include_router(routes_profile.router, prefix="/api/v1")
    return app


app = create_app()
