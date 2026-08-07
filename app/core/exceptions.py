"""应用异常。

约定：Provider 层只抛这些异常，绝不把 httpx / serpapi 的原始异常泄露到上层；
API 层捕获 AppError 后直接 `exc.to_api_error()` 序列化返回。
"""

from __future__ import annotations

from app.models.errors import ApiError, ErrorCode

__all__ = [
    "AppError",
    "InvalidParams",
    "UpstreamError",
    "UpstreamTimeout",
    "QuotaExceeded",
    "NotFoundError",
    "AnswerMismatch",
]


class AppError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, *, code: ErrorCode | None = None, **details):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details

    def to_api_error(self) -> ApiError:
        return ApiError.of(self.code, self.message, **self.details)


class InvalidParams(AppError):
    code = ErrorCode.INVALID_PARAMS


class UpstreamError(AppError):
    """provider 返回了错误（5xx、业务错误码等）。"""

    code = ErrorCode.UPSTREAM_ERROR


class UpstreamTimeout(UpstreamError):
    code = ErrorCode.UPSTREAM_TIMEOUT


class QuotaExceeded(AppError):
    """SerpAPI 额度耗尽 / 高德触发限流。必须显式暴露，不能静默失败。"""

    code = ErrorCode.QUOTA_EXCEEDED


class NotFoundError(AppError):
    code = ErrorCode.TRIP_NOT_FOUND


class AnswerMismatch(AppError):
    code = ErrorCode.ANSWER_MISMATCH
