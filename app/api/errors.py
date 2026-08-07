"""异常 → HTTP 响应的统一映射（架构文档 §7.3）。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.errors import ApiError, ErrorCode

log = get_logger(__name__)

__all__ = ["install_error_handlers", "STATUS_BY_CODE"]

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_PARAMS: 400,
    ErrorCode.DESTINATION_UNSUPPORTED: 400,
    ErrorCode.DESTINATION_TOO_BROAD: 400,
    ErrorCode.CITY_NOT_FOUND: 404,
    ErrorCode.TRIP_NOT_FOUND: 404,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.ANSWER_MISMATCH: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.UPSTREAM_TIMEOUT: 504,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.NO_FLIGHTS: 422,
    ErrorCode.NO_HOTELS: 422,
    ErrorCode.NO_ATTRACTIONS: 422,
    ErrorCode.INTERNAL: 500,
}


def _respond(error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=STATUS_BY_CODE.get(error.code, 500),
        content=error.model_dump(mode="json"),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _respond(exc.to_api_error())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 的报错结构对用户没意义，压成一句话；细节留在 details 里给开发看
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _respond(
            ApiError.of(
                ErrorCode.INVALID_PARAMS,
                f"{field}: {first.get('msg', '参数校验失败')}",
                field=field,
            )
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：绝不把栈信息回给客户端
        log.exception("未处理异常", extra={"path": request.url.path})
        return _respond(ApiError.of(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}"))
