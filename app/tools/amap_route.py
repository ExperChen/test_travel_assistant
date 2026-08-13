"""路径规划 Tool：高德批量距离测量 + 公交/驾车/步行路线。

对齐 `docs/navigation/amap-direction-api.md`。三条纪律：
- **绝不返回 polyline**：单条路线上万字符，进 LLM 上下文就是灾难（§9.1）
- **批量比较一律走 distance_batch**，不要对每个景点都调一次 driving（§10.11 配额）
- 入参收 GeoPoint 而不是裸经纬度，杜绝 WGS-84 被当成 GCJ-02 静默使用

这四个 Tool 由确定性的 route_planner 调用，不绑给 LLM（llm_facing=False）。
"""

from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.core.exceptions import InvalidParams
from app.core.logging import get_logger
from app.models.common import GeoPoint
from app.models.route import DistanceResult, RouteLeg
from app.providers.amap_client import AmapClient
from app.tools.registry import amap_client, tool

log = get_logger(__name__)

__all__ = [
    "distance_batch",
    "direction_transit",
    "direction_driving",
    "direction_walking",
    "MAX_ORIGINS_PER_CALL",
]

MAX_ORIGINS_PER_CALL = 100

DISTANCE_MODE_STRAIGHT = 0
DISTANCE_MODE_DRIVING = 1
DISTANCE_MODE_WALKING = 3


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _float_or_none(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@tool(
    name="distance_batch",
    provider="amap",
    llm_facing=False,
    description=(
        "批量测量多个起点到同一终点的距离与时长。一次最多 100 个起点，超出会自动分批。"
        "用于给景点/酒店按通勤时长排序——比对每对点调用路径规划省几十倍配额。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "origins": {"type": "array", "description": "起点列表（GeoPoint）"},
            "destination": {"type": "object", "description": "终点（GeoPoint）"},
            "mode": {"type": "integer", "enum": [0, 1, 3], "description": "0=直线 1=驾车 3=步行"},
        },
        "required": ["origins", "destination"],
    },
)
async def distance_batch(
    origins: list[GeoPoint],
    destination: GeoPoint,
    mode: int = DISTANCE_MODE_DRIVING,
    *,
    client: AmapClient | None = None,
) -> list[DistanceResult]:
    if not origins:
        raise InvalidParams("origins 不能为空")
    if mode not in (DISTANCE_MODE_STRAIGHT, DISTANCE_MODE_DRIVING, DISTANCE_MODE_WALKING):
        raise InvalidParams("mode 只能是 0（直线）/ 1（驾车）/ 3（步行）")

    amap = client or amap_client()
    results: list[DistanceResult] = []

    # 高德单次最多 100 个起点；自动分批，下标换算容易写错，收敛在这里做一次
    for offset in range(0, len(origins), MAX_ORIGINS_PER_CALL):
        chunk = origins[offset : offset + MAX_ORIGINS_PER_CALL]
        payload = await amap.get(
            "/v3/distance",
            {
                "origins": "|".join(p.to_amap() for p in chunk),
                "destination": destination.to_amap(),
                "type": mode,
            },
            ttl_s=settings.cache_ttl_amap_route_s,
        )
        for item in payload.get("results") or []:
            index = _int(item.get("origin_id"), 0) - 1 + offset
            if not 0 <= index < len(origins):
                log.warning("距离测量返回了越界的 origin_id", extra={"raw": str(item)[:120]})
                continue
            code = str(item.get("code") or "")
            results.append(
                DistanceResult(
                    origin_index=index,
                    distance_m=None if code else _int(item.get("distance")),
                    duration_s=None if code else _int(item.get("duration")),
                    error_code=code,
                    error_info=str(item.get("info") or ""),
                )
            )

    results.sort(key=lambda r: r.origin_index)
    return results


@tool(
    name="direction_transit",
    provider="amap",
    llm_facing=False,
    description=(
        "公交/地铁综合换乘路线。返回总时长、票价、步行距离与换乘线路名，不含 polyline。"
        "跨城时必须同时给 city 与 cityd。没有可行方案时返回 None。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "origin": {"type": "object", "description": "起点 GeoPoint"},
            "destination": {"type": "object", "description": "终点 GeoPoint"},
            "city": {"type": "string", "description": "起点城市名或 citycode，如 '0571'"},
            "cityd": {"type": "string", "description": "跨城时的终点城市"},
            "strategy": {"type": "integer", "description": "0=最快 1=最经济 2=最少换乘 3=最少步行"},
        },
        "required": ["origin", "destination", "city"],
    },
)
async def direction_transit(
    origin: GeoPoint,
    destination: GeoPoint,
    city: str,
    cityd: str = "",
    strategy: int = 0,
    depart_at: datetime | None = None,
    *,
    client: AmapClient | None = None,
) -> RouteLeg | None:
    if not city:
        raise InvalidParams("公交换乘必须提供起点城市（city）")

    use_v5 = settings.amap_route_version == "v5"
    if use_v5:
        # v5：城市参数改名 city1/city2；时长票价挪进 cost 对象，
        # **不传 show_fields=cost 就一个数都拿不到**
        path = "/v5/direction/transit/integrated"
        params: dict = {
            "origin": origin.to_amap(),
            "destination": destination.to_amap(),
            "city1": city,
            "city2": cityd or city,
            "strategy": strategy,
            "show_fields": "cost",
        }
    else:
        path = "/v3/direction/transit/integrated"
        params = {
            "origin": origin.to_amap(),
            "destination": destination.to_amap(),
            "city": city,
            "cityd": cityd,
            "strategy": strategy,
            "extensions": "all",  # 不传 all 就拿不到票价与途经站点
        }
    if depart_at:
        params["date"] = f"{depart_at.year}-{depart_at.month}-{depart_at.day}"
        # v5 的时刻格式是 9-30，v3 是 09:30
        params["time"] = (
            f"{depart_at.hour}-{depart_at.minute}" if use_v5
            else depart_at.strftime("%H:%M")
        )

    payload = await (client or amap_client()).get(
        path, params, ttl_s=settings.cache_ttl_amap_route_s
    )

    route = payload.get("route") or {}
    transits = route.get("transits") or []
    if not transits:
        # 没有公交方案是正常结果（郊区景点常见），让 planner 去降级到驾车。
        # ⚠️ v5 的 strategy=6（地铁图模式）在很多 OD 上就是这样：
        #    status=1 但 transits 为空——是"成功且无方案"，不是报错。
        log.info("该 OD 无公交方案", extra={"city": city, "api": path})
        return None

    best = transits[0]
    lines: list[str] = []
    for segment in best.get("segments") or []:
        for busline in (segment.get("bus") or {}).get("buslines") or []:
            if name := busline.get("name"):
                lines.append(str(name).split("(")[0])
        railway = segment.get("railway") or {}
        if railway.get("name"):
            lines.append(f"{railway['name']} {railway.get('trip', '')}".strip())

    walking_m = _int(best.get("walking_distance"))
    detail = " → ".join(lines) if lines else "全程步行"
    if walking_m:
        detail += f"（步行 {walking_m} 米）"

    cost = best.get("cost") if isinstance(best.get("cost"), dict) else {}
    return RouteLeg(
        mode="transit",
        # route.distance 在官方文档里被标注为"全程步行距离"，与字段名语义存疑，
        # 联调时需实测确认；步行距离另有 walking_distance，已单独体现在 detail 里。
        distance_m=_int(route.get("distance")),
        # v5: transits[].cost.duration / .transit_fee；v3: transits[].duration / .cost
        duration_min=round(_int(cost.get("duration") if use_v5 else best.get("duration")) / 60),
        cost_cny=_float_or_none(
            cost.get("transit_fee") if use_v5 else best.get("cost")
        ),
        taxi_cost_cny=_float_or_none(
            (route.get("cost") or {}).get("taxi_fee") if use_v5
            else route.get("taxi_cost")
        ),
        detail=detail,
    )


@tool(
    name="direction_driving",
    provider="amap",
    llm_facing=False,
    description=(
        "驾车路线。默认 strategy=10（返回多条备选，取第一条），extensions=all 以拿到"
        "过路费、打车估价与限行信息。返回距离/时长/过路费/限行，"
        "**不含 polyline 也不含转向指引**——开车会用导航 App，路线细节只是噪音。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "origin": {"type": "object", "description": "起点 GeoPoint"},
            "destination": {"type": "object", "description": "终点 GeoPoint"},
            "strategy": {"type": "integer", "description": "选路策略，默认 10（多策略择优）"},
        },
        "required": ["origin", "destination"],
    },
)
async def direction_driving(
    origin: GeoPoint,
    destination: GeoPoint,
    strategy: int = 10,
    *,
    client: AmapClient | None = None,
) -> RouteLeg | None:
    payload = await (client or amap_client()).get(
        "/v3/direction/driving",
        {
            "origin": origin.to_amap(),
            "destination": destination.to_amap(),
            "strategy": strategy,
            "extensions": "all",
        },
        ttl_s=settings.cache_ttl_amap_route_s,
    )

    route = payload.get("route") or {}
    paths = route.get("paths") or []
    if not paths:
        return None

    best = paths[0]

    # **驾车不产出 detail**：它原来是前 3 条转向指引拼起来的
    # （「沿人民南路向南行驶；右转进入天府大道…」），对行程规划毫无用处——
    # 真上路会开导航 App，而三步指引既到不了目的地，又把输出塞满噪音。
    # 时长、过路费、限行才是排期和决策要用的东西，这些照常返回。
    return RouteLeg(
        mode="driving",
        distance_m=_int(best.get("distance")),
        duration_min=round(_int(best.get("duration")) / 60),
        cost_cny=_float_or_none(best.get("tolls")),
        taxi_cost_cny=_float_or_none(route.get("taxi_cost")),
        restriction="途经限行路段" if str(best.get("restriction")) == "1" else "",
    )


@tool(
    name="direction_walking",
    provider="amap",
    llm_facing=False,
    description="步行路线。适合 5km 以内的短途，返回距离与时长，不含 polyline。",
    parameters={
        "type": "object",
        "properties": {
            "origin": {"type": "object", "description": "起点 GeoPoint"},
            "destination": {"type": "object", "description": "终点 GeoPoint"},
        },
        "required": ["origin", "destination"],
    },
)
async def direction_walking(
    origin: GeoPoint,
    destination: GeoPoint,
    *,
    client: AmapClient | None = None,
) -> RouteLeg | None:
    payload = await (client or amap_client()).get(
        "/v3/direction/walking",
        {"origin": origin.to_amap(), "destination": destination.to_amap()},
        ttl_s=settings.cache_ttl_amap_route_s,
    )

    paths = (payload.get("route") or {}).get("paths") or []
    if not paths:
        return None

    best = paths[0]
    distance_m = _int(best.get("distance"))
    return RouteLeg(
        mode="walking",
        distance_m=distance_m,
        duration_min=round(_int(best.get("duration")) / 60),
        detail=f"步行 {distance_m} 米",
    )
