"""酒店相关模型。

字段对齐 `docs/hotel/serpapi-google-hotels-api.md` §4.4 与
`serpapi-google-hotels-autocomplete-api.md` §4.1。

两个必须记住的点：
1. `gps_coordinates` 是 **WGS-84**，进高德前必须转 GCJ-02（GeoPoint 已封装）。
2. 总价看 `total_rate`，不要用"单晚 × 晚数"——后者通常是税前（文档 §7.8）。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.common import GeoPoint

__all__ = [
    "Rate",
    "HotelSuggestion",
    "HotelCandidate",
    "HotelSearchParams",
    "HotelBranch",
    "SORT_BY_LOWEST_PRICE",
    "SORT_BY_HIGHEST_RATING",
    "SORT_BY_MOST_REVIEWED",
]

# sort_by 的取值（文档 §3.4）；不传 = 相关度排序
SORT_BY_LOWEST_PRICE = 3
SORT_BY_HIGHEST_RATING = 8
SORT_BY_MOST_REVIEWED = 13

PropertyKind = Literal["hotel", "vacation rental"]


class Rate(BaseModel):
    """价格对象。`lowest` 带货币符号，`extracted_lowest` 是数值。"""

    lowest: str = ""
    extracted_lowest: float | None = None
    before_taxes_fees: str = ""
    extracted_before_taxes_fees: float | None = None


class HotelSuggestion(BaseModel):
    """Autocomplete 的一条建议。

    有 `property_token` = 具体门店（可走单店模式）；没有 = 品牌或搜索词建议。
    """

    position: int = 0
    value: str = ""
    type: str = ""
    location: str = ""
    thumbnail: str = ""
    autocomplete_suggestion: str = Field(
        default="", description="选中这条后交给 google_hotels 的 q，优先于用户原输入"
    )
    property_token: str = ""
    kgmid: str = ""
    data_cid: str = ""

    @property
    def is_single_property(self) -> bool:
        return bool(self.property_token)


def price_text(total: float | None, nightly: float | None, nights: int) -> str:
    """把房价说成「每晚价 · 共总价」，两个都给。

    **只给其中一个，候选之间就没法比**：ads 只有单晚价、organic 才有 total_rate，
    混排会让「总价 ¥301」看着比「¥190/晚」贵，而前者每晚其实才 ¥100
    （实测成都 2026-08-06）。

    两个字段都在时**以总价为准、每晚价由它折算**：`rate_per_night` 是"起价"，
    `total_rate` 才是这一单实际要付的钱，直接并排会自相矛盾（4 晚却写着
    ¥400/晚 · 共 ¥1200）。折算出来的是均价，也正是可比的口径。
    """
    stay = max(nights, 1)
    if total is not None:
        return f"¥{total / stay:.0f}/晚 · 共 ¥{total:.0f}"
    if nightly is not None:
        return f"¥{nightly:.0f}/晚 · 共约 ¥{nightly * stay:.0f}"
    return "价格暂无"  # 绝不显示 ¥0——"没标价"不等于"免费"


class NearbyPlace(BaseModel):
    """酒店周边地标 + 到那里的交通耗时。

    **Google Hotels 不返回门牌号地址**（`properties[]` 里根本没有 address 字段），
    这是它给出的唯一"这地方在哪儿"的信息。实测中文城市返回的就是中文，
    如「天府广场 / 步行 9分钟」「成都天府国际机场 / 打车 1小时25分钟」——
    对"方不方便"这个真问题，它比一串街道号还管用。
    """

    name: str = ""
    mode: str = Field(default="", description="Walking / Taxi / Public transport")
    duration: str = Field(default="", description="原样保留，如 '9分钟'")

    @property
    def label(self) -> str:
        mode = {"Walking": "步行", "Taxi": "打车", "Public transport": "公交"}.get(
            self.mode, self.mode
        )
        return f"{self.name} {mode}{self.duration}".strip()


class HotelCandidate(BaseModel):
    """裁剪后的统一酒店候选（ads 与 properties 合并后的形态）。"""

    name: str
    kind: PropertyKind = "hotel"
    property_token: str = ""
    is_ad: bool = Field(default=False, description="来自 ads[]；不过滤，但要打标签")
    source: str = Field(default="", description="ads 的预订渠道，如 Booking.com")

    hotel_class: int | None = None
    overall_rating: float | None = None
    reviews: int | None = None

    total_rate: Rate | None = Field(default=None, description="整段入住总价，优先展示")
    rate_per_night: Rate | None = None

    location: GeoPoint | None = Field(
        default=None,
        description="来源坐标系原样保留（SerpAPI 为 WGS-84，高德降级来源为 GCJ-02），"
        "GeoPoint 自带 crs 标注，进高德接口前由 as_gcj02() 转换",
    )
    address: str = Field(
        default="", description="仅高德降级来源有；Google Hotels 不返回地址"
    )
    nearby_places: list[NearbyPlace] = Field(
        default_factory=list, description="周边地标；Google 来源下唯一的位置信息"
    )
    location_rating: float | None = Field(
        default=None, description="Google 给的地段评分 0~5"
    )
    amenities: list[str] = Field(default_factory=list)
    thumbnail: str = ""
    deal_description: str = ""
    link: str = ""

    # ---- 重排结果（architecture §5.2 step 3）----
    commute_to_centroid_min: int | None = None
    score: float = 0.0
    price_unavailable: bool = Field(
        default=False, description="高德降级来源：有坐标无房价"
    )

    @property
    def total_price(self) -> float | None:
        return self.total_rate.extracted_lowest if self.total_rate else None

    @property
    def nightly_price(self) -> float | None:
        return self.rate_per_night.extracted_lowest if self.rate_per_night else None


class HotelSearchParams(BaseModel):
    """`engine=google_hotels` 的入参。日期永远必填，哪怕单店模式（文档 §7.1）。"""

    q: str = ""
    property_token: str = ""
    check_in_date: date
    check_out_date: date
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    children_ages: list[int] = Field(default_factory=list)
    sort_by: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    rating: int | None = Field(default=None, description="7=3.5+ / 8=4.0+ / 9=4.5+")
    hotel_class: list[int] = Field(default_factory=list)
    free_cancellation: bool = False
    vacation_rentals: bool = False
    next_page_token: str = ""

    @model_validator(mode="after")
    def _check(self) -> HotelSearchParams:
        if self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date 必须晚于 check_in_date")
        if len(self.children_ages) != self.children:
            raise ValueError("children_ages 的个数必须等于 children（文档 §7.2）")
        if any(not 1 <= a <= 17 for a in self.children_ages):
            raise ValueError("儿童年龄必须在 1~17 之间，1 岁以下填 1")
        if not self.q and not self.property_token:
            raise ValueError("q 与 property_token 至少要有一个")
        return self

    def to_serpapi(self, *, gl: str, hl: str, currency: str) -> dict:
        params: dict = {
            "engine": "google_hotels",
            "check_in_date": self.check_in_date.isoformat(),
            "check_out_date": self.check_out_date.isoformat(),
            "adults": self.adults,
            "gl": gl,
            "hl": hl,
            "currency": currency,
        }
        if self.q:
            params["q"] = self.q
        if self.property_token:
            params["property_token"] = self.property_token
        if self.children:
            params["children"] = self.children
            params["children_ages"] = ",".join(str(a) for a in self.children_ages)
        if self.sort_by:
            params["sort_by"] = self.sort_by
        if self.min_price:
            params["min_price"] = self.min_price
        if self.max_price:
            params["max_price"] = self.max_price
        if self.rating:
            params["rating"] = self.rating
        if self.next_page_token:
            params["next_page_token"] = self.next_page_token

        if self.vacation_rentals:
            # hotels 专属筛选在 vacation_rentals 模式下全部失效（文档 §7.6），
            # 与其被服务端静默忽略，不如这里就不发出去。
            params["vacation_rentals"] = "true"
        else:
            if self.hotel_class:
                params["hotel_class"] = ",".join(str(c) for c in self.hotel_class)
            if self.free_cancellation:
                params["free_cancellation"] = "true"
        return params


class HotelBranch(BaseModel):
    candidates: list[HotelCandidate] = Field(default_factory=list)
    selected_index: int | None = None

    @property
    def selected(self) -> HotelCandidate | None:
        if self.selected_index is None:
            return None
        if not 0 <= self.selected_index < len(self.candidates):
            return None
        return self.candidates[self.selected_index]
