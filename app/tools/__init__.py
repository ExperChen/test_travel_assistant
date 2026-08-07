"""Tool 层。

导入本包即完成全部 11 个 Tool 的注册（装饰器在模块导入时执行），
之后可以用 `registry.get_tool(name)` / `registry.as_langchain_tools()` 取用。
"""

from app.tools.amap_poi import poi_around, poi_detail, poi_keyword
from app.tools.amap_route import (
    direction_driving,
    direction_transit,
    direction_walking,
    distance_batch,
)
from app.tools.registry import ToolSpec, all_specs, as_langchain_tools, close_clients, get_tool
from app.tools.serpapi_flights import flights_autocomplete, flights_search
from app.tools.serpapi_hotels import hotels_autocomplete, hotels_search

__all__ = [
    # 注册表
    "ToolSpec",
    "all_specs",
    "as_langchain_tools",
    "close_clients",
    "get_tool",
    # SerpAPI
    "flights_autocomplete",
    "flights_search",
    "hotels_autocomplete",
    "hotels_search",
    # 高德 POI
    "poi_keyword",
    "poi_around",
    "poi_detail",
    # 高德路径
    "distance_batch",
    "direction_transit",
    "direction_driving",
    "direction_walking",
]
