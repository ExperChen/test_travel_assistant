"""attraction_search：景点召回 → 打分 → Top-K → 补入口坐标 → 算重心。"""

from __future__ import annotations

from app.agents.attraction_agent import (
    attractions_centroid,
    enrich_entrances,
    recall_with_report,
    score_attractions,
    select_attractions,
)
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.graph.nodes._common import fail, warn
from app.graph.state import TripState
from app.models.attraction import AttractionBranch
from app.models.errors import ErrorCode

log = get_logger(__name__)

__all__ = ["attraction_search", "MIN_PER_DAY"]

MIN_PER_DAY = 2
"""每天少于 2 个景点就不成行程了，要提示用户放宽条件。"""


async def attraction_search(state: TripState) -> dict:
    city = state["dest_city"]
    request = state["request"]

    try:
        pool, warnings = await recall_with_report(city, must_visit=request.must_visit)
    except AppError as exc:
        return fail(exc.code, exc.message, city=city.name)

    if not pool:
        return fail(ErrorCode.NO_ATTRACTIONS, f"{city.name} 没有召回到任何景点", city=city.name)

    scored = score_attractions(pool, city.center, request.pace, avoid=request.avoid)
    selected = select_attractions(scored, request.travel_days)

    try:
        # 入口坐标只对最终入选的景点补，一次 amap 调用换一批
        selected = await enrich_entrances(selected)
    except AppError as exc:
        # 拿不到入口坐标不致命，退回用 POI 中心点，但要让用户知道
        log.warning("补入口坐标失败", extra={"err": exc.message})

    patch: dict = {
        "attractions": AttractionBranch(
            pool=scored,
            selected=selected,
            # 重心必须在补完入口坐标之后算——入口和中心点可能差好几公里
            # 传市中心：重心要剔掉远郊景点，否则酒店锚点会被拽出市区
            centroid=attractions_centroid(selected, city.center),
        ),
        "phase": "hotel",
    }

    expected = request.travel_days * MIN_PER_DAY
    if len(selected) < expected:
        warnings = warnings + warn(
            "FEW_ATTRACTIONS",
            f"{city.name} 只筛出 {len(selected)} 个景点，少于 {request.travel_days} 天所需的 "
            f"{expected} 个，行程会比较松散",
            stage="attraction",
        )
    if warnings:
        patch["warnings"] = warnings

    log.info(
        "景点已筛选",
        extra={"city": city.name, "pool": len(scored), "selected": len(selected)},
    )
    return patch
