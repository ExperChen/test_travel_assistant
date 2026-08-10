"""FastAPI 应用装配。

    uvicorn app.main:app --host 127.0.0.1 --port 8000

⚠️ **必须 `--workers 1`**：checkpointer（MemorySaver）、SSE 事件缓冲、配额记账
都是进程内的，多 worker 下 `/answer` 可能落到没有该 thread 的进程上。
上多副本前要先把这三样换成共享存储（架构文档 §4.4 / §8.2）。
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
from app.services.trip_service import TripService
from app.tools.registry import close_clients

log = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level, json_output=settings.log_json)
    service = TripService()
    app.state.trip_service = service
    # 没有它，用户关掉页面的行程会永远卡在 waiting_input（架构文档 §4.3）
    service.start_sweeper()
    log.info(
        "服务启动",
        extra={
            "auth": settings.auth_enabled,
            "model": settings.llm_model,
            "interrupt_timeout_s": settings.interrupt_timeout_s,
        },
    )
    try:
        yield
    finally:
        await app.state.trip_service.aclose()
        await close_clients()
        log.info("服务已停止")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Better Travel Assistant",
        version="0.1.0",
        description="往返机票 + 目的地酒店 + 热门景点 + 逐日路径规划（目的地限中国大陆）",
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
        allow_headers=["Content-Type", "X-API-Key", "X-Profile-Id", "Last-Event-ID"],
    )

    install_error_handlers(app)
    app.include_router(routes_health.router)
    app.include_router(routes_trips.router, prefix="/api/v1")
    app.include_router(routes_profile.router, prefix="/api/v1")
    return app


app = create_app()
