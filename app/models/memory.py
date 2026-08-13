"""长期记忆模型（记忆与追问文档 §2 / §3）。

三层记忆里 L1（会话）已经由 checkpointer 承担，这里只定义 **L2 偏好** 与
**L3 履历**。两者刻意分开：

    L2 是**声明**（"我一般坐经济舱"）→ 可以直接拿来填参数
    L3 是**事实**（"我去年去过西湖"）→ 只能降权，不能做决定

混在一起会得出"你去过成都，所以不给你推荐成都的景点"这种蠢结论。

本模块是纯数据 + 纯函数，不碰数据库也不碰网络——存储在 `app.store.memory`。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "Preference",
    "Profile",
    "VisitedAttraction",
    "TripHistory",
    "MemorySnapshot",
    "REMEMBERED_FIELDS",
    "preference_payload",
    "BUDGET_BUCKETS",
    "budget_bucket",
    "bucket_to_budget",
    "LEARN_RATE",
    "DECAY_RATE",
    "HIGH_CONFIDENCE",
]

# ---------------------------------------------------------------- L2 偏好

REMEMBERED_FIELDS: tuple[str, ...] = (
    "departure_city",
    "travel_class",
    "transport",
    "adults",
    "children",
    "children_ages",
    "budget_per_night",
    "hotel_class",
)
"""只记 `TripRequest` 里**用户会重复表达、且跨行程稳定**的字段（文档 §2）。

刻意不记的：
- `destination_city` / `outbound_date` / `return_date` —— 每次都不同，记了是噪音
- `must_visit` / `avoid` —— 强绑定目的地，那是 L3 的活
"""

LEARN_RATE = 0.3
"""新观测的权重。指数滑动，不用贝叶斯——一个人一年规划十几次，
数据量太小，复杂模型没有收益（文档 §2）。"""

DECAY_RATE = 0.7
"""未命中时的衰减系数。"""

HIGH_CONFIDENCE = 0.6
"""≥ 此值才允许**直接填**参数；低于它只能作为建议展示，并注明可以改。

取 0.6 的理由：从零开始连续命中 3 次是 0.657，第 2 次是 0.51。
也就是"说过三次"才算数——第 1 次是偶然，第 3 次是习惯。
"""


class Preference(BaseModel):
    """一条偏好。

    **不能只存一个裸值**（文档 §2）：说过一次和说过七次可信度天差地别，
    而且有了 `samples` 才能算出"改变"——一个人换了出发城市，
    第 1 次是异常，第 3 次是搬家。
    """

    value: Any
    confidence: float = Field(default=LEARN_RATE, ge=0.0, le=1.0)
    samples: int = Field(default=1, ge=0)
    last_seen: date

    @property
    def is_confident(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE

    def observe(self, value: Any, *, on: date) -> Preference:
        """记一次新观测，返回更新后的副本（不原地改，便于对比前后）。

        命中：  confidence ← confidence + 0.3 × (1 − confidence)
        未命中：confidence ← confidence × 0.7，samples 重置为 1，value 换新
        """
        if _same(self.value, value):
            return self.model_copy(
                update={
                    "confidence": round(
                        self.confidence + LEARN_RATE * (1 - self.confidence), 4
                    ),
                    "samples": self.samples + 1,
                    "last_seen": on,
                }
            )
        return self.model_copy(
            update={
                "value": value,
                "confidence": round(self.confidence * DECAY_RATE, 4),
                "samples": 1,
                "last_seen": on,
            }
        )


def _same(a: Any, b: Any) -> bool:
    """列表按内容比（children_ages / hotel_class），其余按值比。"""
    if isinstance(a, list) and isinstance(b, list):
        return list(a) == list(b)
    return a == b


class Profile(BaseModel):
    """一个用户的 L2 偏好集合。

    ⚠️ `profile_id` 在方案 A（`X-Profile-Id` 头）下是**可猜的**，
    记忆内容等于半公开——绝不能存姓名、证件、支付信息（文档 §5）。
    """

    profile_id: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    preferences: dict[str, Preference] = Field(default_factory=dict)

    def get(self, key: str) -> Preference | None:
        return self.preferences.get(key)

    def confident_value(self, key: str) -> Any | None:
        """够可信才返回值，否则 None。调用方据此决定"直接填"还是"只建议"。"""
        pref = self.preferences.get(key)
        return pref.value if pref is not None and pref.is_confident else None

    def observe_all(self, values: dict[str, Any], *, on: date) -> Profile:
        """用一次成功规划的最终参数更新全部偏好。

        只在 `status == "done"` 时调用（文档 §2）——解析阶段的值可能被用户
        在中断点改掉，以最终生效的 `TripRequest` 为准。
        """
        updated = dict(self.preferences)
        for key, value in values.items():
            if key not in REMEMBERED_FIELDS or value is None:
                continue
            existing = updated.get(key)
            updated[key] = (
                existing.observe(value, on=on)
                if existing is not None
                else Preference(value=value, last_seen=on)
            )
        return self.model_copy(
            update={"preferences": updated, "updated_at": datetime.now(UTC)}
        )

    def advance_children_ages(self, today: date) -> Profile:
        """儿童年龄按经过的年数推进（文档 §2：不能存死数字）。

        存的是"上次见到时几岁"，取用时按 `last_seen` 到今天的整年数加上去。
        不改 `last_seen`——那是观测时间，不是读取时间。
        """
        pref = self.preferences.get("children_ages")
        if pref is None or not isinstance(pref.value, list) or not pref.value:
            return self

        years = _elapsed_years(pref.last_seen, today)
        if years <= 0:
            return self

        grown = [int(age) + years for age in pref.value]
        updated = dict(self.preferences)
        updated["children_ages"] = pref.model_copy(update={"value": grown})
        return self.model_copy(update={"preferences": updated})


def _elapsed_years(since: date, today: date) -> int:
    """整年数。8 月 7 日记的 5 岁，到次年 8 月 6 日还是 5 岁。"""
    if today <= since:
        return 0
    years = today.year - since.year
    if (today.month, today.day) < (since.month, since.day):
        years -= 1
    return max(years, 0)


# ---------------------------------------------------------------- 预算分桶

BUDGET_BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("under_300", None, 300),
    ("300_600", 300, 600),
    ("600_1000", 600, 1000),
    ("over_1000", 1000, None),
)
"""预算**记区间不记数字**（文档 §2）：去三亚和去县城的预算不是一回事，
只有"档位"才跨行程稳定。"""

BUDGET_LABELS: dict[str, str] = {
    "under_300": "300 以内",
    "300_600": "300-600",
    "600_1000": "600-1000",
    "over_1000": "1000 以上",
    "any": "不限",
}


def budget_bucket(value: int | None) -> str:
    """具体金额 → 档位。None（不限）单独成档。"""
    if value is None:
        return "any"
    for name, low, high in BUDGET_BUCKETS:
        if (low is None or value > low) and (high is None or value <= high):
            return name
    return "any"


def preference_payload(request) -> dict:
    """从最终生效的 `TripRequest` 里摘出该记住的字段（记忆与追问文档 §2）。

    预算**记档位不记数字**：去三亚和去县城的预算不是一回事，只有档位跨行程稳定。

    原来这个函数在 `services/trip_service.py`——图跑完 `status == "done"` 时调用。
    图删掉之后写入点挪到了 CLI（`main.py` 的自主规划收尾），但"记什么"这件事
    和谁来触发无关，所以放回记忆模型自己身边。
    """
    payload: dict = {}
    for key in REMEMBERED_FIELDS:
        value = getattr(request, key, None)
        if value is None or value == []:
            continue
        payload[key] = budget_bucket(value) if key == "budget_per_night" else value
    return payload


def bucket_to_budget(bucket: str) -> int | None:
    """档位 → 用于查询的上限值。

    取区间**上界**：预算是"不超过多少"，取上界才不会把用户想住的酒店过滤掉。
    `over_1000` 与 `any` 都返回 None（不设上限）。
    """
    for name, _low, high in BUDGET_BUCKETS:
        if name == bucket:
            return high
    return None


# ---------------------------------------------------------------- L3 履历


class VisitedAttraction(BaseModel):
    poi_id: str
    name: str


class TripHistory(BaseModel):
    """一次**已完成**的行程。

    只记最终排进行程的：目的地城市 + 景点。不记候选、不记备选——
    没去成的不算去过（文档 §3）。
    """

    trip_id: str
    city: str
    adcode: str = ""
    start_date: date
    end_date: date
    attractions: list[VisitedAttraction] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


RecencyBand = Literal["recent", "mid", "old"]

RECENT_DAYS = 183
"""≈ 6 个月。"""
MID_DAYS = 730
"""≈ 2 年。"""

VISIT_DECAY: dict[RecencyBand, float] = {
    "recent": 0.5,
    "mid": 0.8,
    "old": 1.0,
}
"""去过景点的打分衰减（文档 §3）。

⚠️ **只降权，绝不硬过滤。** 去过西湖不代表不想再去；而系统单方面把它从候选里
删掉，用户连"为什么没有西湖"都无从知道。
"""


def recency_band(visited_on: date, today: date) -> RecencyBand:
    days = (today - visited_on).days
    if days <= RECENT_DAYS:
        return "recent"
    if days <= MID_DAYS:
        return "mid"
    return "old"


class MemorySnapshot(BaseModel):
    """一次规划开始时取到的记忆视图。

    把 L2 和 L3 打包在一起传递，避免每个调用点各查一次库。
    `visited` 的值是**最近一次**去的日期——同一个景点去过多次，按最近那次算。
    """

    profile: Profile | None = None
    visited: dict[str, date] = Field(
        default_factory=dict, description="{poi_id: 最近一次到访日期}"
    )
    visited_cities: dict[str, date] = Field(
        default_factory=dict, description="{城市名: 最近一次到访日期}"
    )

    @property
    def is_empty(self) -> bool:
        return (self.profile is None or not self.profile.preferences) and not self.visited

    def decay_for(self, poi_id: str, today: date) -> float:
        """该景点的打分衰减系数。没去过就是 1.0。"""
        visited_on = self.visited.get(poi_id)
        if visited_on is None:
            return 1.0
        return VISIT_DECAY[recency_band(visited_on, today)]

    def visited_note(self, poi_id: str, today: date) -> str:
        """给用户看的说明。降权必须**说出来**，否则用户不知道发生了什么。"""
        visited_on = self.visited.get(poi_id)
        if visited_on is None or recency_band(visited_on, today) == "old":
            return ""
        return f"你 {visited_on:%Y-%m} 去过，已降低推荐权重"
