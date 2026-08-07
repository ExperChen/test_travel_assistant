"""依赖注入：鉴权与服务单例。"""

from __future__ import annotations

import secrets

from fastapi import Header, Request

from app.config import settings
from app.core.exceptions import AppError
from app.models.errors import ErrorCode
from app.services.trip_service import TripService

__all__ = ["require_api_key", "get_service", "set_service"]

_fallback: TripService | None = None


def get_service(request: Request) -> TripService:
    """进程内单例：checkpointer、事件缓冲、后台任务都挂在它上面。

    正常路径从 `app.state` 取（由 lifespan 建好）；`_fallback` 只为不走
    lifespan 的场景（脚本、单元测试）兜底。
    """
    service = getattr(request.app.state, "trip_service", None)
    if service is not None:
        return service

    global _fallback
    if _fallback is None:
        _fallback = TripService()
    return _fallback


def set_service(service: TripService | None) -> None:
    """测试用：注入自定义 service。"""
    global _fallback
    _fallback = service


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """`APP_API_KEY` 为空时关闭鉴权——仅限本地开发。

    用 compare_digest 而不是 `==`：字符串比较会在第一个不同的字节短路返回，
    时序差异足以让攻击者逐字节猜出 key。
    """
    if not settings.auth_enabled:
        return
    if not secrets.compare_digest(x_api_key, settings.app_api_key):
        raise AppError("X-API-Key 缺失或不正确", code=ErrorCode.UNAUTHORIZED)
