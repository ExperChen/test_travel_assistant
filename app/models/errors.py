"""统一错误契约（架构文档 §7.3）。

技术信息进日志，`user_message` 进用户界面——两者永远分开，
避免把 provider 的英文报错或内部参数直接甩给用户。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = ["ErrorCode", "ApiError", "PlanWarning"]


class ErrorCode(StrEnum):
    DESTINATION_UNSUPPORTED = "DESTINATION_UNSUPPORTED"
    DESTINATION_TOO_BROAD = "DESTINATION_TOO_BROAD"
    CITY_NOT_FOUND = "CITY_NOT_FOUND"
    NO_FLIGHTS = "NO_FLIGHTS"
    NO_HOTELS = "NO_HOTELS"
    NO_ATTRACTIONS = "NO_ATTRACTIONS"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INVALID_PARAMS = "INVALID_PARAMS"
    ANSWER_MISMATCH = "ANSWER_MISMATCH"
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"


_DEFAULT_USER_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.DESTINATION_UNSUPPORTED: "当前版本的景点与路线规划仅支持中国大陆城市。",
    ErrorCode.DESTINATION_TOO_BROAD: "请具体到城市，比如「杭州」而不是「浙江」。",
    ErrorCode.CITY_NOT_FOUND: "没找到这个城市，换个说法再试试？",
    ErrorCode.NO_FLIGHTS: "这个日期没搜到合适的航班，建议前后调整 3 天或换一个机场。",
    ErrorCode.NO_HOTELS: "该城市暂时查不到可预订的酒店。",
    ErrorCode.NO_ATTRACTIONS: "没能在该城市找到足够的景点，换个城市或放宽筛选试试。",
    ErrorCode.UPSTREAM_TIMEOUT: "数据源响应较慢，正在重试…",
    ErrorCode.UPSTREAM_ERROR: "数据源暂时不可用，请稍后再试。",
    ErrorCode.QUOTA_EXCEEDED: "今日查询次数已用完，请稍后再来。",
    ErrorCode.INVALID_PARAMS: "提交的信息有误，请检查后重试。",
    ErrorCode.ANSWER_MISMATCH: "请从给出的选项中选择。",
    ErrorCode.TRIP_NOT_FOUND: "找不到这次行程，可能已经过期了。",
    ErrorCode.UNAUTHORIZED: "身份校验失败。",
    ErrorCode.RATE_LIMITED: "操作太频繁了，请稍后再试。",
    ErrorCode.INTERNAL: "服务开小差了，请稍后再试。",
}

_RETRIABLE = frozenset(
    {
        ErrorCode.UPSTREAM_TIMEOUT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.RATE_LIMITED,
    }
)


class ApiError(BaseModel):
    code: ErrorCode
    message: str = Field(description="技术错误信息，进日志，不展示给用户")
    user_message: str = Field(default="", description="面向用户的中文提示")
    retriable: bool = False
    details: dict = Field(default_factory=dict)

    @classmethod
    def of(cls, code: ErrorCode, message: str, **details) -> ApiError:
        return cls(
            code=code,
            message=message,
            user_message=_DEFAULT_USER_MESSAGE.get(code, _DEFAULT_USER_MESSAGE[ErrorCode.INTERNAL]),
            retriable=code in _RETRIABLE,
            details=details,
        )


class PlanWarning(BaseModel):
    """降级/兜底记录：不阻断流程，但必须让用户知道结果是怎么来的。"""

    code: str
    message: str
    stage: str = ""

    @classmethod
    def of(cls, code: str, message: str, stage: str = "") -> PlanWarning:
        return cls(code=code, message=message, stage=stage)
