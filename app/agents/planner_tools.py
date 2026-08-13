"""给 LLM 用的工具封装层。

`app/tools/` 里的 13 个工具有 4 个标了 `llm_facing=False`——它们收
`GeoPoint`（带坐标系标注的对象），模型没法凭空构造。但那个标注恰恰是防止
WGS-84 被当成 GCJ-02 静默使用的唯一保险，不能为了迁就模型就去掉。

所以这里做一层薄封装：**对模型收裸经纬度，对内立刻包成 GCJ-02 的 GeoPoint**。
坐标系的约束还在，只是把"必须显式标注"这个负担从模型移到了这一层。

> ⚠️ 模型给的经纬度一律按 **GCJ-02** 处理——它拿到的坐标全部来自高德
> （`poi_keyword` / `poi_around` / `district_lookup`），本来就是 GCJ-02。
> 若将来把 Google 的 WGS-84 坐标也喂给模型，这里必须加坐标系参数。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.common import GeoPoint
from app.tools.amap_poi import regeo_batch
from app.tools.amap_route import (
    direction_driving,
    direction_transit,
    direction_walking,
    distance_batch,
)
from app.tools.registry import tool

log = get_logger(__name__)

__all__ = ["route_between", "distance_many", "address_of"]

_POINT = {"type": "number"}


def _leg_dict(leg) -> dict[str, Any]:
    """RouteLeg → 给模型看的扁平 dict。**不含 polyline**（上万字符，纯噪音）。"""
    return {
        "mode": leg.mode,
        "distance_m": leg.distance_m,
        "duration_min": leg.duration_min,
        "fare_cny": leg.cost_cny,
        "taxi_cost_cny": leg.taxi_cost_cny,
        "detail": leg.detail,
    }


@tool(
    name="route_between",
    provider="amap",
    description=(
        "查两点之间的真实路线，返回距离、时长（分钟）、票价/过路费与换乘说明。"
        "坐标必须是高德口径（GCJ-02），也就是本工具集里其它接口返回的坐标。"
        "mode 取 transit（公交地铁，需要 citycode）/ driving（驾车）/ walking（步行）。"
        "**排行程时每一段位移都要用它算，不要自己估**——估错时间会让整份行程失真。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "from_lng": _POINT, "from_lat": _POINT,
            "to_lng": _POINT, "to_lat": _POINT,
            "mode": {
                "type": "string",
                "enum": ["transit", "driving", "walking"],
                "description": "出行方式，默认 transit",
            },
            "citycode": {
                "type": "string",
                "description": "公交查询必填，如深圳 0755；由 district_lookup 返回",
            },
        },
        "required": ["from_lng", "from_lat", "to_lng", "to_lat"],
    },
)
async def route_between(
    from_lng: float,
    from_lat: float,
    to_lng: float,
    to_lat: float,
    mode: str = "transit",
    citycode: str = "",
    *,
    client=None,
) -> dict[str, Any]:
    origin = GeoPoint.gcj02(float(from_lng), float(from_lat))
    destination = GeoPoint.gcj02(float(to_lng), float(to_lat))

    if mode == "walking":
        leg = await direction_walking(origin, destination, client=client)
    elif mode == "driving":
        leg = await direction_driving(origin, destination, client=client)
    else:
        if not citycode:
            # 与其发一个必然报错的请求白烧额度，不如把话说清楚让模型补上
            return {"error": "公交查询必须提供 citycode，先用 district_lookup 拿到它"}
        leg = await direction_transit(
            origin, destination, city=citycode, client=client
        )
        if leg is None:
            # 郊区常见。如实告诉模型没有公交方案，让它自己决定改驾车还是换点
            return {"ok": False, "reason": "该路线没有公交方案，可以改用 driving"}
    return {"ok": True, **_leg_dict(leg)}


@tool(
    name="distance_many",
    provider="amap",
    description=(
        "一次算多个起点到同一终点的距离与时长（最多 100 个）。"
        "给酒店/景点按通勤远近排序时用它——比对每一对点各查一次路线省几十倍额度。"
        "points 传 [[lng, lat], ...]，坐标为 GCJ-02。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "description": "起点列表，每项是 [经度, 纬度]",
                "items": {"type": "array", "items": {"type": "number"}},
            },
            "to_lng": _POINT,
            "to_lat": _POINT,
            "mode": {
                "type": "integer",
                "enum": [0, 1, 3],
                "description": "0=直线 1=驾车（默认）3=步行",
            },
        },
        "required": ["points", "to_lng", "to_lat"],
    },
)
async def distance_many(
    points: list[list[float]],
    to_lng: float,
    to_lat: float,
    mode: int = 1,
    *,
    client=None,
) -> dict[str, Any]:
    origins = [GeoPoint.gcj02(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    if not origins:
        return {"error": "points 为空或格式不对，应为 [[经度, 纬度], ...]"}

    results = await distance_batch(
        origins, GeoPoint.gcj02(float(to_lng), float(to_lat)), mode=int(mode),
        client=client,
    )
    return {
        "ok": True,
        "results": [
            {
                "index": r.origin_index,
                "distance_m": r.distance_m,
                "duration_min": r.duration_min,
                "error": r.error_info or None,
            }
            for r in results
        ],
    }


@tool(
    name="address_of",
    provider="amap",
    description=(
        "把坐标反查成文字地址，一次最多 20 个点。"
        "Google 的酒店数据不含门牌号地址，要展示地址时用它补。"
        "points 传 [[lng, lat], ...]，坐标为 GCJ-02。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "description": "坐标列表，每项是 [经度, 纬度]",
                "items": {"type": "array", "items": {"type": "number"}},
            }
        },
        "required": ["points"],
    },
)
async def address_of(points: list[list[float]], *, client=None) -> dict[str, Any]:
    coords = [GeoPoint.gcj02(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    if not coords:
        return {"error": "points 为空或格式不对"}
    return {"ok": True, "addresses": await regeo_batch(coords, client=client)}
