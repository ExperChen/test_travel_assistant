"""酒店搜索、重排与降级（架构文档 §5.2）。

本模块的增量价值全在**重排**上：Google Hotels 的原生排序不知道用户要去哪些景点，
而"离要去的地方近不近"往往比便宜几十块更影响体验。做法是拿景点重心当锚点，
用一次 `distance_batch`（1 次额度换 100 个起点）算出真实驾车时长再排序。
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.attraction import Attraction
from app.models.common import CityRef, GeoPoint
from app.models.errors import PlanWarning
from app.models.hotel import HotelCandidate, HotelSearchParams
from app.models.route import DistanceResult
from app.models.trip import TripRequest
from app.tools.amap_poi import MAX_REGEO_POINTS, poi_keyword, regeo_batch
from app.tools.amap_route import distance_batch
from app.tools.serpapi_hotels import hotels_search

log = get_logger(__name__)

__all__ = [
    "build_query",
    "search_hotels",
    "rerank_hotels",
    "fallback_to_amap",
    "drop_over_budget",
    "attach_commute",
    "attach_addresses",
    "pick_options",
    "TOP_N",
    "ASK_N",
    "MIN_EXPECTED",
    "MIN_ORGANIC_ASKED",
    "W_PRICE",
    "W_RATING",
    "W_COMMUTE",
]

TOP_N = 8
"""保留的候选数。

不额外花额度：`attach_commute` 用 `distance_batch` 一次覆盖最多 100 个起点，
候选从 3 变 8 依然是同一次调用。上游搜索本来也返回更多，只是被截断了。"""

ASK_N = 4
"""挂起问用户时最多列几家。候选可以多，选项不能多——超过 4 个就不是"选择"
而是"翻页"了。"""

MIN_ORGANIC_ASKED = 2
"""选项里至少留几个非广告位（有的话）。

广告位常常评分高又便宜，容易把前几名全占了——实测成都 8 家候选有 6 家是广告。
只列广告等于没得选：它们是 Google 的付费展位，不是"最合适的酒店"。"""


def pick_options(top: list[HotelCandidate], ask_n: int = ASK_N) -> list[HotelCandidate]:
    """从候选里挑出要问用户的那几家，**保证非广告位有位置**。

    先按分数取前 `ask_n`，若其中非广告不足 `MIN_ORGANIC_ASKED` 而候选池里还有，
    就用分数最高的非广告位替换掉分数最低的广告位。顺序仍按分数排。
    """
    asked = top[:ask_n]
    organic_pool = [c for c in top if not c.is_ad]
    need = min(MIN_ORGANIC_ASKED, len(organic_pool)) - len([c for c in asked if not c.is_ad])
    if need <= 0:
        return asked

    picked = list(asked)
    spare = [c for c in organic_pool if c not in picked][:need]
    for replacement in spare:
        ads = [c for c in picked if c.is_ad]
        if not ads:
            break
        picked[picked.index(ads[-1])] = replacement  # 踢掉分数最低的广告位
    return sorted(picked, key=lambda c: c.score, reverse=True)

MIN_EXPECTED = 5
"""少于这个数就提示候选偏少，让用户知道不是我们挑剩的。"""

MAX_COMMUTE_MIN = 60.0
"""通勤时长的归一化上限：到景点重心超过 1 小时，再远也没区别了。"""

AMAP_HOTEL_TYPES = "100000"
"""高德「住宿服务」大类，降级检索用。"""

NEUTRAL = 0.5
"""缺数据时的中位分。给 0 会把"没标价"的酒店一律判死刑。"""

# 重排权重（合计 1.0）
W_PRICE = 0.27
W_RATING = 0.18
W_COMMUTE = 0.55
"""通勤压倒价格与评分。

实测成都：原来 0.45/0.30/0.25 的配比，让一家 ¥100/晚、到景点重心 **75 分钟**的
酒店排到第一，而 34 分钟的那家排第五。省下的钱远不够补每天多花两小时通勤——
住宿的好坏在行程里主要就体现为"离要去的地方近不近"。

价格与评分按原比例（3:2）缩放，保持它们之间的相对关系不变。"""


def build_query(city: CityRef, attractions: list[Attraction]) -> str:
    """构造 Google Hotels 的 `q`。

    景点集中在某个商圈时，用「城市+商圈+酒店」比「城市+酒店」命中率高得多——
    前者 Google 会理解成"这一带的酒店"，后者只会给市中心的大路货。
    """
    areas = Counter(a.business_area for a in attractions if a.business_area)
    if areas:
        area, _ = areas.most_common(1)[0]
        return f"{city.name}{area}附近酒店"
    return f"{city.name}酒店"


def _dedupe(candidates: list[HotelCandidate]) -> list[HotelCandidate]:
    """同一家酒店可能同时出现在 ads 和 properties 里。

    保留先出现的那条（ads 在前，常常更便宜），但如果它没有 total_rate 而重复项有，
    就把总价补过来——展示总价用的是 total_rate（文档 §7.8）。
    """
    kept: dict[str, HotelCandidate] = {}
    order: list[str] = []

    for c in candidates:
        key = c.property_token or c.name
        if key not in kept:
            kept[key] = c
            order.append(key)
        elif kept[key].total_rate is None and c.total_rate is not None:
            kept[key] = kept[key].model_copy(update={"total_rate": c.total_rate})

    return [kept[k] for k in order]


async def search_hotels(
    request: TripRequest,
    query: str,
    *,
    gl: str,
    hl: str,
    currency: str,
    client=None,
) -> list[HotelCandidate]:
    """列表模式搜索。预算未填就不传 max_price——瞎设上限是空结果最常见的原因。"""
    params = HotelSearchParams(
        q=query,
        check_in_date=request.outbound_date,
        check_out_date=request.return_date,
        adults=request.adults,
        children=request.children,
        children_ages=request.children_ages,
        max_price=request.budget_per_night,
        hotel_class=list(request.hotel_class),
    )
    candidates = await hotels_search(
        check_in_date=params.check_in_date,
        check_out_date=params.check_out_date,
        q=params.q,
        adults=params.adults,
        children=params.children,
        children_ages=params.children_ages,
        max_price=params.max_price,
        hotel_class=params.hotel_class,
        gl=gl,
        hl=hl,
        currency=currency,
        client=client,
    )
    return _dedupe(candidates)


def drop_over_budget(
    candidates: list[HotelCandidate], budget_per_night: int | None, nights: int
) -> tuple[list[HotelCandidate], PlanWarning | None]:
    """按每晚预算再筛一遍。

    **SerpAPI 的 `max_price` 只作用于 organic 结果，`ads[]` 不受约束**——实测成都
    `--budget 500` 依然返回 ¥725/晚 的广告位，还因为离景点近被排到了第一。
    用户说了上限就得当真。

    价格查不到的候选保留：无从判断，不能当超预算处理。
    全部被筛光时退回原样并告警——给一份超预算的候选，好过给空。
    """
    if not budget_per_night or not candidates:
        return candidates, None

    def nightly(c: HotelCandidate) -> float | None:
        if c.nightly_price is not None:
            return c.nightly_price
        if c.total_price is not None:
            return c.total_price / max(nights, 1)
        return None

    kept = [c for c in candidates if (n := nightly(c)) is None or n <= budget_per_night]
    dropped = len(candidates) - len(kept)
    if not kept:
        return candidates, PlanWarning.of(
            "HOTEL_OVER_BUDGET",
            f"没有每晚 ¥{budget_per_night} 以内的酒店，以下候选均超出预算",
            stage="hotel",
        )
    if dropped:
        log.info("超预算候选已剔除", extra={"dropped": dropped, "cap": budget_per_night})
    return kept, None


def _as_hotel(poi: Attraction) -> HotelCandidate:
    """把高德 POI 转成酒店候选。

    复用了通用 POI 解析器（返回类型叫 Attraction，这里属于借用），因为需要的字段
    ——名称、坐标、评分、地址——完全一致。降级来源没有房价，标记 price_unavailable。
    """
    return HotelCandidate(
        name=poi.name,
        kind="hotel",
        location=poi.location,
        address=poi.address,
        overall_rating=poi.rating,
        price_unavailable=True,
    )


async def fallback_to_amap(
    city: CityRef, *, client=None
) -> tuple[list[HotelCandidate], PlanWarning]:
    """Google Hotels 在大陆中小城市房源稀疏时的兜底。

    产出有坐标、有评分、**没有房价**的候选。路径规划只需要坐标，所以这个降级
    不阻断主流程——但必须让用户知道价格是查不到而不是免费。
    """
    pois = await poi_keyword(
        region=city.name,
        city_limit=True,
        types=AMAP_HOTEL_TYPES,
        page_size=25,
        client=client,
    )
    warning = PlanWarning.of(
        "HOTEL_PRICE_UNAVAILABLE",
        f"{city.name} 没有查到可预订的房价，已改用地图数据提供酒店位置与评分（不含价格）",
        stage="hotel",
    )
    return [_as_hotel(p) for p in pois], warning


async def attach_commute(
    candidates: list[HotelCandidate], centroid: GeoPoint, *, client=None
) -> list[HotelCandidate]:
    """算出每家酒店到景点重心的真实驾车时长。

    一次 `distance_batch` 覆盖最多 100 个起点——比对每家酒店调一次路径规划省几十倍
    额度（高德文档 §10.11）。Google 给的坐标是 WGS-84，GeoPoint 会在进高德前自动转换。
    """
    located = [c for c in candidates if c.location is not None]
    if not located or centroid is None:
        return candidates

    results: list[DistanceResult] = await distance_batch(
        [c.location for c in located],  # type: ignore[misc]
        centroid,
        client=client,
    )
    by_index = {r.origin_index: r for r in results if r.ok}

    updated = {
        id(c): c.model_copy(update={"commute_to_centroid_min": by_index[i].duration_min})
        for i, c in enumerate(located)
        if i in by_index
    }
    return [updated.get(id(c), c) for c in candidates]


def _effective_price(candidate: HotelCandidate, nights: int) -> float | None:
    """统一成整段总价。单晚价 × 晚数通常是税前，只在没有 total_rate 时兜底。"""
    if candidate.total_price is not None:
        return candidate.total_price
    if candidate.nightly_price is not None:
        return candidate.nightly_price * max(nights, 1)
    return None


def _minmax(values: list[float]) -> tuple[float, float]:
    return min(values), max(values)


def rerank_hotels(
    candidates: list[HotelCandidate], nights: int, *, top_n: int = TOP_N
) -> list[HotelCandidate]:
    """按 价格 / 评分 / 通勤 综合打分，返回 Top-N。

    三个维度各自 min-max 归一。全部相同（或全部缺失）时给中位分，避免除零，
    也避免把"大家都一样"误判成"都很好"。
    """
    if not candidates:
        return []

    prices = [p for c in candidates if (p := _effective_price(c, nights)) is not None]
    commutes = [
        float(c.commute_to_centroid_min)
        for c in candidates
        if c.commute_to_centroid_min is not None
    ]
    price_lo, price_hi = _minmax(prices) if prices else (0.0, 0.0)

    scored: list[HotelCandidate] = []
    for c in candidates:
        price = _effective_price(c, nights)
        if price is None or price_hi == price_lo:
            price_score = NEUTRAL
        else:
            price_score = 1.0 - (price - price_lo) / (price_hi - price_lo)  # 越便宜越高

        rating_score = NEUTRAL if c.overall_rating is None else min(c.overall_rating, 5.0) / 5.0

        if c.commute_to_centroid_min is None or not commutes:
            commute_score = NEUTRAL
        else:
            capped = min(float(c.commute_to_centroid_min), MAX_COMMUTE_MIN)
            commute_score = 1.0 - capped / MAX_COMMUTE_MIN

        total = W_PRICE * price_score + W_RATING * rating_score + W_COMMUTE * commute_score
        scored.append(c.model_copy(update={"score": round(total, 4)}))

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_n]


def nights_between(check_in: date, check_out: date) -> int:
    return max((check_out - check_in).days, 1)


async def attach_addresses(
    candidates: list[HotelCandidate], *, client=None
) -> list[HotelCandidate]:
    """给没有地址的候选补上文字地址。

    **Google Hotels 不返回门牌号**——`properties[]` 里根本没有 address 字段，
    只有少数条目带 `nearby_places`（广告位连这个都没有）。实测成都 8 家候选里
    7 家是广告位，位置信息一片空白。

    一次批量逆地理编码覆盖全部候选，只花 1 次高德额度。已有地址的（高德降级
    来源）跳过。
    """
    todo = [
        (i, c)
        for i, c in enumerate(candidates)
        if c.location is not None and not c.address
    ][:MAX_REGEO_POINTS]
    if not todo:
        return candidates

    try:
        addresses = await regeo_batch([c.location for _, c in todo], client=client)
    except AppError as exc:
        # 补不到地址不致命，用户还有周边地标和通勤时长可看
        log.warning("酒店地址反查失败", extra={"err": exc.message})
        return candidates

    out = list(candidates)
    for (index, candidate), address in zip(todo, addresses, strict=False):
        if address:
            out[index] = candidate.model_copy(update={"address": address})
    return out
