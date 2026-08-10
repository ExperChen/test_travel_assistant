"""依赖注入：鉴权与服务单例。"""

from __future__ import annotations

import re
import secrets

from fastapi import Header, Request

from app.config import settings
from app.core.exceptions import AppError
from app.models.errors import ErrorCode
from app.services.trip_service import TripService

__all__ = ["require_api_key", "get_service", "set_service", "get_profile_id", "PROFILE_HEADER"]

PROFILE_HEADER = "X-Profile-Id"

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

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


async def get_profile_id(x_profile_id: str = Header(default="")) -> str:
    """取"哪一份记忆"。缺失返回空串 = 不使用记忆。

    ⚠️ **这不是安全边界**（记忆与追问文档 §5 方案 A）。`X-Profile-Id` 由客户端
    自己生成（前端 localStorage 里的 UUID），任何人都能填别人的 id。鉴权仍然
    完全由 `X-API-Key` 负责；这个头只回答"读写哪一份偏好"。

    正因为它可猜，**记忆里绝不能存敏感信息**——出发城市、节奏这类无所谓，
    姓名/证件/支付信息一律不许进。

    格式限制在 `[A-Za-z0-9_-]{1,64}`：这个值会进 SQL 参数和日志，
    收窄字符集比事后转义可靠。不合法**不报错**，按"没提供"处理——
    记忆是增量特性，不该因为一个畸形的头让整次规划 400。
    """
    value = (x_profile_id or "").strip()
    if not value:
        return ""
    if not _PROFILE_ID_RE.match(value):
        return ""
    return value
