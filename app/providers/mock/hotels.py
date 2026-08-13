"""酒店模拟数据生成器。

响应结构逐字段对齐 `docs/architecture/serpapi-usage-and-mocking.md` §3.3 / §3.4
（SerpAPI `google_hotels` 与 `google_hotels_autocomplete`）。

## 房价模型

    基准价/晚 = 星级基准 × 城市系数 × 随机 ±20%
    total_rate = 单晚价 × 晚数 × 含税系数（1.10~1.16）

`total_rate` **不是**单晚价的整数倍——真实数据里它含税含费，
而 `rate_per_night` 是"起价"。下游 `price_text()` 专门处理过这个差异，
造成整数倍会让那段逻辑测不出问题。

## 四个必须还原的真实行为（文档 §3.3）

1. **`gps_coordinates` 是 WGS-84**，不是 GCJ-02。给错坐标系会绕过
   `GeoPoint.from_google()` 的转换，线上换真接口时出现 300~600m 系统性偏移。
2. **`properties[]` 里没有 address 字段。** Google Hotels 不返回门牌号，
   位置信息只有 `nearby_places`——项目里的酒店地址是事后用高德逆地理编码补的。
3. **`ads` 只有单晚价，`properties` 才有 `total_rate`。**
4. **`max_price` 只约束 organic，`ads[]` 不听话。** 实测成都 `--budget 500`
   依然返回 ¥725/晚 的广告位——本地的 `drop_over_budget()` 就是为它准备的，
   模拟数据必须保留这个"不听话"，否则那段逻辑等于没被验证。
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models.hotel import Rate
from app.providers.mock.airports import (
    CITY_TIER,
    DEFAULT_CITY_TIER,
    airports_of_city,
    city_center,
    find_city,
)

__all__ = [
    "HotelMockGenerator",
    "BRANDS",
    "STAR_BASE_CNY",
    "PRICE_JITTER",
    "attach_mock_pricing",
    "star_from_amap_type",
]

PRICE_JITTER = 0.20
"""房价随机波动幅度，与机票保持一致。"""

STAR_BASE_CNY: dict[int, float] = {2: 170.0, 3: 320.0, 4: 620.0, 5: 1150.0}
"""各星级的基准单晚价（未乘城市系数）。"""


@dataclass(frozen=True)
class Brand:
    name: str
    stars: int
    rating_range: tuple[float, float]


BRANDS: tuple[Brand, ...] = (
    # 经济型
    Brand("如家酒店", 2, (3.9, 4.3)),
    Brand("汉庭酒店", 2, (4.0, 4.4)),
    Brand("7天优品", 2, (3.8, 4.2)),
    Brand("锦江之星", 3, (4.0, 4.4)),
    Brand("格林豪泰", 3, (3.9, 4.3)),
    # 中端
    Brand("全季酒店", 4, (4.4, 4.8)),
    Brand("亚朵酒店", 4, (4.5, 4.9)),
    Brand("桔子水晶酒店", 4, (4.3, 4.7)),
    Brand("麗枫酒店", 3, (4.2, 4.6)),
    Brand("维也纳国际酒店", 3, (4.1, 4.5)),
    # 高端
    Brand("希尔顿酒店", 5, (4.5, 4.9)),
    Brand("万豪酒店", 5, (4.5, 4.9)),
    Brand("凯悦酒店", 5, (4.4, 4.8)),
    Brand("香格里拉大酒店", 5, (4.6, 4.9)),
    Brand("洲际酒店", 5, (4.5, 4.9)),
)

_AMENITIES_BY_STAR: dict[int, tuple[str, ...]] = {
    2: ("免费 Wi-Fi", "空调", "24 小时前台", "行李寄存"),
    3: ("免费 Wi-Fi", "空调", "24 小时前台", "行李寄存", "免费停车", "早餐"),
    4: ("免费 Wi-Fi", "空调", "健身房", "餐厅", "免费停车", "早餐", "商务中心"),
    5: ("免费 Wi-Fi", "空调", "健身房", "室内泳池", "餐厅", "行政酒廊",
        "免费停车", "早餐", "接机服务", "SPA"),
}

_AD_SOURCES = ("Booking.com", "Agoda", "Trip.com", "Expedia", "Hotels.com")

_GENERIC_LANDMARKS = ("市中心", "火车站", "地铁站", "商业步行街")

_TRANSPORT_MODES = ("Walking", "Taxi", "Public transport")


class HotelMockGenerator:
    """生成 SerpAPI 格式的酒店响应。`seed` 给定则完全可复现。"""

    def __init__(self, *, seed: int | None = None, jitter: float = PRICE_JITTER):
        self._rng = random.Random(seed)
        self._jitter = jitter

    # ------------------------------------------------------------------
    def search(
        self,
        *,
        q: str = "",
        property_token: str = "",
        check_in_date: str | date | None = None,
        check_out_date: str | date | None = None,
        adults: int = 2,
        children: int = 0,
        max_price: int | None = None,
        min_price: int | None = None,
        hotel_class: list[int] | None = None,
        vacation_rentals: bool = False,
        currency: str = "CNY",
        count: int = 12,
        ads_count: int = 2,
    ) -> dict[str, Any]:
        """`engine=google_hotels` 的响应。"""
        check_in = _coerce(check_in_date)
        check_out = _coerce(check_out_date)
        if check_in is None or check_out is None or check_out <= check_in:
            return self._empty(q)

        city = find_city(q or "")
        if city is None:
            return self._empty(q)

        nights = (check_out - check_in).days
        center = city_center(city)
        if center is None:
            return self._empty(q)

        area = _extract_area(q, city)
        tier = CITY_TIER.get(city, DEFAULT_CITY_TIER)
        stars_filter = set(hotel_class or ())

        weights = _brand_weights(tier)
        properties: list[dict[str, Any]] = []
        for i in range(count):
            brand = self._rng.choices(BRANDS, weights=weights, k=1)[0]
            # vacation_rentals 模式下星级筛选失效（真实接口就是这样）
            if stars_filter and not vacation_rentals and brand.stars not in stars_filter:
                continue
            item = self._property(
                brand, city, area, center, nights, tier, i,
                vacation_rental=vacation_rentals,
            )
            nightly = item["rate_per_night"]["extracted_lowest"]
            # organic 结果**遵守**价格筛选
            if max_price is not None and nightly > max_price:
                continue
            if min_price is not None and nightly < min_price:
                continue
            properties.append(item)

        # ⚠️ ads 刻意**不遵守** max_price —— 真实接口就是这样，
        #    本地的 drop_over_budget() 正是为它准备的
        ads = [
            self._ad(
                self._rng.choices(BRANDS, weights=weights, k=1)[0],
                city, area, center, tier, i,
            )
            for i in range(ads_count)
        ]

        return {
            "search_metadata": {
                "id": self._sid("ht", f"{city}{check_in}"),
                "status": "Success",
                "total_time_taken": round(self._rng.uniform(1.0, 3.2), 2),
            },
            "search_parameters": {
                "engine": "google_hotels",
                "q": q,
                "check_in_date": check_in.isoformat(),
                "check_out_date": check_out.isoformat(),
                "adults": adults,
                **({"children": children} if children else {}),
                "currency": currency,
                "gl": "cn",
                "hl": "zh-CN",
            },
            "search_information": {"total_results": len(properties) + len(ads)},
            "brands": [],
            "ads": ads,
            "properties": properties,
        }

    def _empty(self, q: str) -> dict[str, Any]:
        """空结果。**必须造得出来**——酒店降级到高德那条路径全靠它触发。"""
        return {
            "search_metadata": {"status": "Success"},
            "search_parameters": {"engine": "google_hotels", "q": q},
            "ads": [],
            "properties": [],
        }

    # ------------------------------------------------------------------
    def autocomplete(self, q: str, *, currency: str = "CNY") -> dict[str, Any]:
        """`engine=google_hotels_autocomplete` 的响应。

        三类建议：带 `property_token` 的是具体门店，带 `kgmid` 的是品牌，
        其余是搜索词建议。
        """
        text = (q or "").strip()
        if not text:
            return {"search_metadata": {"status": "Success"}, "suggestions": []}

        city = find_city(text) or text
        suggestions: list[dict[str, Any]] = []

        for i, brand in enumerate(self._rng.sample(list(BRANDS), 3), start=1):
            suggestions.append({
                "position": i,
                "value": f"{city}{brand.name}",
                "type": "hotel",
                "location": f"{city}，中国",
                "autocomplete_suggestion": f"{city}{brand.name}",
                "property_token": self._token("prop", f"{city}{brand.name}"),
                "thumbnail": f"https://lh3.googleusercontent.com/mock/{i}",
            })
        suggestions.append({
            "position": len(suggestions) + 1,
            "value": f"{city}酒店",
            "type": "search",
            "location": f"{city}，中国",
            "autocomplete_suggestion": f"{city}酒店",
        })

        return {
            "search_metadata": {"id": self._sid("ha", text), "status": "Success"},
            "search_parameters": {
                "engine": "google_hotels_autocomplete",
                "q": text,
                "currency": currency,
                "gl": "cn",
                "hl": "zh-CN",
            },
            "suggestions": suggestions,
        }

    # ------------------------------------------------------------ 单条酒店
    def _property(
        self, brand: Brand, city: str, area: str, center, nights: int,
        tier: float, index: int, *, vacation_rental: bool,
    ) -> dict[str, Any]:
        nightly = self._nightly(brand.stars, tier)
        # total_rate 含税含费，不是单晚价的整数倍
        total = round(nightly * nights * self._rng.uniform(1.10, 1.16))
        before_tax = round(total / self._rng.uniform(1.08, 1.14))
        lng, lat = self._scatter(center, index)
        name = self._name(brand, city, area, index)

        item: dict[str, Any] = {
            "type": "vacation rental" if vacation_rental else "hotel",
            "name": name,
            "property_token": self._token("prop", name),
            # ⚠️ WGS-84，不是 GCJ-02
            "gps_coordinates": {"latitude": round(lat, 6), "longitude": round(lng, 6)},
            "check_in_time": "14:00",
            "check_out_time": "12:00",
            "rate_per_night": {
                "lowest": f"¥{nightly:,.0f}",
                "extracted_lowest": nightly,
            },
            "total_rate": {
                "lowest": f"¥{total:,.0f}",
                "extracted_lowest": float(total),
                "before_taxes_fees": f"¥{before_tax:,.0f}",
                "extracted_before_taxes_fees": float(before_tax),
            },
            "overall_rating": round(self._rng.uniform(*brand.rating_range), 1),
            "reviews": self._rng.randint(80, 4200),
            "location_rating": round(self._rng.uniform(3.6, 4.9), 1),
            "amenities": list(_AMENITIES_BY_STAR[brand.stars]),
            "thumbnail": f"https://lh3.googleusercontent.com/mock/hotel/{index}",
            "link": f"https://www.google.com/travel/hotels/entity/mock{index}",
            "nearby_places": self._nearby(city, area),
        }
        # 民宿常常没有星级——真实数据里这个字段会直接缺失
        if not vacation_rental:
            item["extracted_hotel_class"] = brand.stars
            item["hotel_class"] = f"{brand.stars}-star hotel"
        if self._rng.random() < 0.25:
            item["deal_description"] = self._rng.choice(("特价", "限时折扣", "含早优惠"))
        return item

    def _ad(self, brand: Brand, city: str, area: str, center, tier: float, index: int):
        """广告位：只有单晚价，没有 total_rate，且不受 max_price 约束。"""
        nightly = self._nightly(brand.stars, tier) * self._rng.uniform(1.0, 1.45)
        nightly = round(nightly)
        lng, lat = self._scatter(center, index + 90)
        name = self._name(brand, city, area, index + 90)
        return {
            "name": name,
            "source": self._rng.choice(_AD_SOURCES),
            "property_token": self._token("ad", name),
            "gps_coordinates": {"latitude": round(lat, 6), "longitude": round(lng, 6)},
            "hotel_class": f"{brand.stars}-star hotel",
            "overall_rating": round(self._rng.uniform(*brand.rating_range), 1),
            "reviews": self._rng.randint(50, 2600),
            "price": f"¥{nightly:,.0f}",
            "extracted_price": float(nightly),
            "amenities": list(_AMENITIES_BY_STAR[brand.stars][:3]),
            "thumbnail": f"https://lh3.googleusercontent.com/mock/ad/{index}",
            "link": f"https://www.google.com/travel/hotels/entity/mockad{index}",
            "nearby_places": self._nearby(city, area),
        }

    # ---------------------------------------------------------------- 细节
    def _nightly(self, stars: int, tier: float) -> float:
        base = STAR_BASE_CNY[stars] * tier
        low, high = 1 - self._jitter, 1 + self._jitter
        return round(base * self._rng.uniform(low, high))

    def _scatter(self, center, index: int) -> tuple[float, float]:
        """把房源撒在市中心 0.5~6 km 半径内。

        用极坐标而不是各自独立抖 lng/lat：后者会撒成一个方块，
        且高纬度城市（哈尔滨）的经度间距会被拉伸得不成比例。
        """
        import math

        lng, lat = center
        radius_km = self._rng.uniform(0.5, 6.0)
        bearing = self._rng.uniform(0, 2 * math.pi)
        d_lat = radius_km / 111.0 * math.cos(bearing)
        d_lng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.2)) * math.sin(bearing)
        return lng + d_lng, lat + d_lat

    def _name(self, brand: Brand, city: str, area: str, index: int) -> str:
        """「成都天府广场亚朵酒店」——商圈来自查询里的 `q`，让结果看起来响应了输入。"""
        spot = area or self._rng.choice(_GENERIC_LANDMARKS)
        return f"{city}{spot}{brand.name}"

    def _nearby(self, city: str, area: str) -> list[dict[str, Any]]:
        """周边地标。**这是 Google Hotels 唯一的位置信息**——它不给门牌号地址。"""
        spots = [area] if area else []
        spots += [s for s in self._rng.sample(_GENERIC_LANDMARKS, 2) if s != area]
        airports = airports_of_city(city)
        if airports:
            spots.append(airports[0].name)

        out = []
        for spot in spots[:3]:
            mode = (
                "Taxi" if "机场" in spot else self._rng.choice(_TRANSPORT_MODES)
            )
            minutes = self._rng.randint(45, 95) if mode == "Taxi" else self._rng.randint(4, 25)
            duration = (
                f"{minutes // 60}小时 {minutes % 60}分钟" if minutes >= 60 else f"{minutes}分钟"
            )
            out.append({
                "name": spot,
                "transportations": [{"type": mode, "duration": duration}],
            })
        return out

    def _token(self, prefix: str, payload: str) -> str:
        return f"{prefix}_{hashlib.sha1(payload.encode()).hexdigest()[:20]}"

    def _sid(self, prefix: str, payload: str) -> str:
        return f"{prefix}_{hashlib.md5(payload.encode()).hexdigest()[:16]}"


_STAR_WORDS: tuple[tuple[str, int], ...] = (
    ("五星级", 5), ("四星级", 4), ("三星级", 3), ("二星级", 2),
)


def star_from_amap_type(type_name: str) -> int | None:
    """从高德 POI 的 type 串里读星级，如 `住宿服务;宾馆酒店;三星级宾馆` → 3。

    这是**真实数据**，比按名字猜靠谱得多。读不出来返回 None，由调用方按价格档推断。
    """
    for word, star in _STAR_WORDS:
        if word in (type_name or ""):
            return star
    return None


def attach_mock_pricing(
    candidates: list,
    *,
    city: str,
    nights: int,
    seed: int | None = None,
) -> list:
    """给「有位置、没房价」的候选补上模拟房价（`HOTEL_SOURCE=hybrid` 用）。

    输入通常来自高德「住宿服务」POI：名称、坐标、地址、评分**都是真的**，
    唯独没有房价——高德本来就不提供。这里只补商业字段：

        rate_per_night / total_rate / hotel_class / amenities

    **定价按酒店名做稳定散列**，不按调用顺序。同一家店在同一次规划的多轮
    重放里价格必须一致，否则 LangGraph 中断恢复后用户会看到房价莫名变了。

    星级优先用高德 type 串里的真实星级（`star_from_amap_type`），
    读不出来才按名字里的品牌关键词猜。
    """
    tier = CITY_TIER.get(city, DEFAULT_CITY_TIER)
    out = []
    for candidate in candidates:
        salt = f"{seed or 0}:{candidate.name}"
        rng = random.Random(int(hashlib.sha1(salt.encode()).hexdigest()[:12], 16))

        stars = getattr(candidate, "hotel_class", None) or _guess_star(candidate.name)
        base = STAR_BASE_CNY[stars] * tier
        nightly = round(base * rng.uniform(1 - PRICE_JITTER, 1 + PRICE_JITTER))
        total = round(nightly * max(nights, 1) * rng.uniform(1.10, 1.16))
        before_tax = round(total / rng.uniform(1.08, 1.14))

        out.append(candidate.model_copy(update={
            "hotel_class": stars,
            "rate_per_night": Rate(lowest=f"¥{nightly:,}", extracted_lowest=float(nightly)),
            "total_rate": Rate(
                lowest=f"¥{total:,}", extracted_lowest=float(total),
                before_taxes_fees=f"¥{before_tax:,}",
                extracted_before_taxes_fees=float(before_tax),
            ),
            "amenities": list(_AMENITIES_BY_STAR[stars]),
            # 位置是真的，房价是合成的——不再是"查不到价格"
            "price_unavailable": False,
        }))
    return out


def _guess_star(name: str) -> int:
    """按名字里的品牌关键词猜星级。高德没给星级时的兜底。"""
    for brand in BRANDS:
        if brand.name in name:
            return brand.stars
    # 猜不出就给中端——给 2 星会让所有无名小店都排到前面
    return 3


def _brand_weights(tier: float) -> list[float]:
    """按城市档位给品牌加权，让**结果集的档次构成**也随城市变化。

    只缩放单价是不够的：品牌若均匀抽样，三亚（系数 1.5）可能恰好抽到一堆经济型
    连锁，均价反而低于成都——既不真实（三亚的真实搜索结果偏度假村），
    也会让人误以为城市系数没生效。

    做法是把城市系数映射成"这座城市的典型星级"，再按星级距离做指数衰减：

        兰州 0.85 → 偏好 ≈2.9 星      北京 1.40 → 偏好 ≈4.2 星
        成都 1.10 → 偏好 ≈3.5 星      三亚 1.50 → 偏好 ≈4.4 星

    衰减而不是硬筛：一线城市照样有如家，只是占比低。
    """
    import math

    # 过 (0.85, 2.9) 与 (1.50, 4.4) 两点的线性映射
    preferred = 2.9 + (tier - DEFAULT_CITY_TIER) * 2.3
    return [math.exp(-abs(brand.stars - preferred) * 0.9) for brand in BRANDS]


def _extract_area(q: str, city: str) -> str:
    """从「成都市天府广场附近酒店」里抠出「天府广场」。

    `build_query()` 拼的就是这个格式（`f"{city.name}{area}附近酒店"`），
    把商圈回显到酒店名和 nearby_places 里，结果才像是响应了查询。
    """
    text = (q or "").strip()
    # 长前缀优先：高德给的城市名是「成都市」，先剥「成都」会留下一个「市」，
    # 于是商圈变成「市天府广场」、酒店名变成「成都市天府广场亚朵酒店」
    for token in (f"{city}市", city):
        if token and text.startswith(token):
            text = text[len(token):]
            break
    for suffix in ("附近酒店", "酒店", "附近"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def _coerce(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
