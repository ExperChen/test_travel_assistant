"""景点模型。

字段对齐 `docs/poi/amap-poi-search-api.md` §8。注意：
`rating` / `cost` / `opentime_*` / `photos` 都属于扩展字段，请求时必须带
`show_fields=business,photos`，漏传会全空（文档 §11.4）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import GeoPoint

__all__ = ["Attraction", "AttractionBranch", "ATTRACTION_TYPES", "DEFAULT_STAY_MINUTES"]

ATTRACTION_TYPES = "110000|110101|110200|110300"
"""景点场景推荐的 POI 分类码（文档 §10.1）。不传 types 会混进一堆餐厅。"""

DEFAULT_STAY_MINUTES: dict[str, int] = {
    "relaxed": 150,
    "standard": 120,
    "packed": 90,
}


class Attraction(BaseModel):
    poi_id: str
    parent_id: str = Field(
        default="", description="父 POI ID。子景点（如'西湖-断桥残雪'）会指向所属景区"
    )
    name: str
    location: GeoPoint = Field(description="POI 中心点，GCJ-02")
    entrance: GeoPoint | None = Field(
        default=None,
        description="navi.entr_location（入口）。大型景区的中心点常落在湖里山里，"
        "算路线时优先用入口坐标。",
    )
    typecode: str = ""
    type_name: str = ""
    address: str = ""
    district: str = ""
    tel: str = ""
    distance_m: int | None = Field(default=None, description="仅周边搜索返回：距圆心的直线距离")

    rating: float | None = None
    ticket_cost: float | None = Field(default=None, description="business.cost，门票参考价")
    opentime_today: str = ""
    opentime_week: str = ""
    business_area: str = ""
    photos: list[str] = Field(default_factory=list)

    suggested_duration_min: int = 120
    score: float = 0.0
    must_visit: bool = Field(default=False, description="来自 TripRequest.must_visit，强制进入行程")
    recall_rank: int | None = Field(
        default=None,
        description="关键字搜索里的最佳名次（0-based）。高德按热度/权重排序，"
        "这是唯一能拿到的'知名度'信号；周边搜索按距离排序，不产生这个值。",
    )

    @property
    def routing_point(self) -> GeoPoint:
        """送进路径规划的坐标：有入口用入口。"""
        return self.entrance or self.location

    @property
    def is_large_scenic_area(self) -> bool:
        """大型景区（如整座山、整个湖区），游览时长要放大。"""
        return self.typecode.startswith("1100") and bool(self.business_area)


class AttractionBranch(BaseModel):
    pool: list[Attraction] = Field(default_factory=list, description="召回并打分后的候选池")
    selected: list[Attraction] = Field(default_factory=list, description="进入行程的 Top-K")
    centroid: GeoPoint | None = Field(default=None, description="景点重心，酒店重排的锚点")
