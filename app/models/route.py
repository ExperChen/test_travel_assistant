"""路线模型（架构文档 §7.2）。

`DayItem` / `DayPlan` / `Itinerary` 已随固定管线一起删除——它们是排期算法的
输出结构，而排期现在由模型自己在文字里完成。剩下的两个是**路径工具的返回值**，
和编排方式无关：谁来决定查哪一段，查出来都是这个形状。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["TransportMode", "RouteLeg", "DistanceResult"]

TransportMode = Literal["transit", "driving", "walking"]


class RouteLeg(BaseModel):
    """两个行程点之间的一段交通。

    `from_ref` / `to_ref` 供调用方标注这一段连的是哪两个点——路径工具只认坐标。
    """

    from_ref: str = ""
    to_ref: str = ""
    mode: TransportMode
    distance_m: int = 0
    duration_min: int = 0
    cost_cny: float | None = Field(default=None, description="公交票价 / 驾车过路费")
    taxi_cost_cny: float | None = None
    detail: str = Field(default="", description="如「地铁2号线 → 换乘10号线，步行 850m」")
    restriction: str = Field(default="", description="驾车限行提示")


class DistanceResult(BaseModel):
    """批量距离测量的单条结果。

    高德对单个起点也可能失败（在海上/矿区/境外），这时 distance/duration 无意义，
    必须用 `ok` 判断后再参与排序，否则会把不可达的点排到最前面。
    """

    origin_index: int = Field(description="对应入参 origins 的下标，0-based")
    distance_m: int | None = None
    duration_s: int | None = None
    error_code: str = ""
    error_info: str = ""

    @property
    def ok(self) -> bool:
        return self.distance_m is not None and not self.error_code

    @property
    def duration_min(self) -> int | None:
        return None if self.duration_s is None else round(self.duration_s / 60)
