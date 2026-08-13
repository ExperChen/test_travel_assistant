"""行程请求的顶层契约（架构文档 §7.1）。

原来这里还有 `TripPlan` / `CostBreakdown`——它们是固定管线的**产物**：
状态机跑完把航班、酒店、逐日行程、配额、警告装进一个结构体。管线删掉之后
没有任何东西再生产它们（自主 agent 交付的是一段 Markdown），所以一并删了。
留下的 `TripRequest` 仍是全链路的入口：intake 收集它，agent 照它排行程。
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.dates import trip_day_count
from app.models.flight import TravelClass
from app.models.route import TransportMode

__all__ = ["TripRequest"]


class TripRequest(BaseModel):
    """前端表单提交的内容。目的地必须能解析到中国大陆城市（D1 决策）。"""

    departure_city: str = Field(min_length=1, description="城市名或 IATA 三字码")
    destination_city: str = Field(min_length=1)
    outbound_date: date
    return_date: date

    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    children_ages: list[int] = Field(default_factory=list)
    travel_class: TravelClass = "economy"

    budget_per_night: int | None = Field(
        default=None, ge=0, description="CNY，映射到 hotels 的 max_price"
    )
    hotel_class: list[Literal[2, 3, 4, 5]] = Field(default_factory=list)

    must_visit: list[str] = Field(default_factory=list, description="强制进入行程的景点名")
    avoid: list[str] = Field(default_factory=list)

    special_requests: list[str] = Field(
        default_factory=list,
        description="特殊出行需求：带老人、行李多、不早起、素食……"
        "认得出的换成一句可执行的指令进 prompt（见 app/models/special.py），"
        "认不出的原样传给模型——**不丢用户说过的话**",
    )

    transport: TransportMode = "transit"

    @field_validator("departure_city", "destination_city")
    @classmethod
    def _strip(cls, v: str) -> str:
        # min_length 校验的是未去空格的原串，"   " 能混过去，必须在这里补一刀
        if not (stripped := v.strip()):
            raise ValueError("城市名不能为空")
        return stripped

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.return_date <= self.outbound_date:
            raise ValueError("return_date 必须晚于 outbound_date")
        if len(self.children_ages) != self.children:
            raise ValueError("children_ages 的个数必须等于 children")
        if any(not 1 <= a <= 17 for a in self.children_ages):
            raise ValueError("儿童年龄必须在 1~17 之间，1 岁以下填 1")
        return self

    @property
    def nights(self) -> int:
        return (self.return_date - self.outbound_date).days

    @property
    def duration_days(self) -> int:
        """**用户口径**的行程天数：返程日 − 出发日。1 号来、5 号回 = 4 天。

        和 `travel_days` 的区别：那个是行程横跨的**日历天数**（上例为 5，
        1/2/3/4/5 号都要排时间窗），排期算法用它；对用户说话一律用这个。
        数值上恒等于 `nights`——住几晚就是几天，两个名字留着是为了让调用点
        自己说清在讲哪件事。
        """
        return self.nights

    @property
    def travel_days(self) -> int:
        """行程横跨的日历天数（含落地日与返程日），排期用。

        ⚠️ 不是用户说的"N 天"，那个看 `duration_days`。
        """
        return trip_day_count(self.outbound_date, self.return_date)
