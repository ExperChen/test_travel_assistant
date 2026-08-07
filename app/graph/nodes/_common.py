"""节点共用的小工具。"""

from __future__ import annotations

from typing import Literal

from app.graph.state import TripState
from app.models.errors import ApiError, ErrorCode, PlanWarning

__all__ = ["fail", "warn", "continue_or_fail"]


def fail(code: ErrorCode, message: str, **details) -> dict:
    """把节点变成终态失败。

    节点不抛异常而是返回错误状态——LangGraph 不会替我们兜异常，
    而且 SSE 的 error 事件需要的正是这个结构化对象。
    """
    return {
        "errors": [ApiError.of(code, message, **details)],
        "status": "failed",
        "phase": "failed",
    }


def warn(code: str, message: str, stage: str = "") -> list[PlanWarning]:
    """降级记录。返回列表是因为 state 里 warnings 带的是 add reducer。"""
    return [PlanWarning.of(code, message, stage)]


def continue_or_fail(state: TripState) -> Literal["continue", "failed"]:
    """条件边：任一节点写入了 errors 就直接收尾，不再往下跑。"""
    return "failed" if state.get("errors") else "continue"
