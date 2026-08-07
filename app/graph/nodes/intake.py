"""intake：参数归一与前置校验。

Pydantic 已经挡掉了格式错误，这里管的是它管不了的业务规则——尤其是
"出发日期在过去"，这种请求发给 SerpAPI 只会返回零结果，白烧额度。
"""

from __future__ import annotations

from datetime import date

from app.config import settings
from app.core.dates import format_cn
from app.core.logging import get_logger
from app.graph.nodes._common import fail, warn
from app.graph.state import TripState
from app.models.common import LocaleCtx
from app.models.errors import ErrorCode

log = get_logger(__name__)

__all__ = ["intake", "MAX_TRAVEL_DAYS"]

MAX_TRAVEL_DAYS = 14
"""超过两周的行程，逐日路径规划的价值急剧下降，也会把配额吃光。"""


async def intake(state: TripState) -> dict:
    request = state["request"]
    today = date.today()

    if request.outbound_date < today:
        return fail(
            ErrorCode.INVALID_PARAMS,
            f"出发日期 {request.outbound_date} 已过（今天 {today}）",
            field="outbound_date",
        )

    patch: dict = {
        # 一次会话内 gl/hl/currency 必须恒定，否则各处价格币种对不上
        "locale": LocaleCtx(
            gl=settings.default_gl,
            hl=settings.default_hl,
            currency=settings.default_currency,
        ),
        "phase": "resolve_city",
    }

    if request.travel_days > MAX_TRAVEL_DAYS:
        patch["warnings"] = warn(
            "TRIP_TOO_LONG",
            f"行程共 {request.travel_days} 天，超过 {MAX_TRAVEL_DAYS} 天后逐日规划会比较粗略",
            stage="intake",
        )

    log.info(
        "intake ok",
        extra={
            "from": request.departure_city,
            "to": request.destination_city,
            "dates": f"{format_cn(request.outbound_date)} → {format_cn(request.return_date)}",
            "days": request.travel_days,
        },
    )
    return patch
