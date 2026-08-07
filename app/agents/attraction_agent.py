"""景点召回、打分与筛选（架构文档 §5.3）。

召回与详情补全是 IO，打分与筛选是纯函数——后者单独可测，不需要 mock 任何东西。
"""

from __future__ import annotations

from app.agents.route_planner import DAY_TRIP_RADIUS_M
from app.core.geo import centroid, haversine_m
from app.core.logging import get_logger
from app.models.attraction import DEFAULT_STAY_MINUTES, Attraction
from app.models.common import CityRef, GeoPoint
from app.models.errors import PlanWarning
from app.models.trip import Pace
from app.tools.amap_poi import poi_detail, poi_keyword

log = get_logger(__name__)

__all__ = [
    "recall_attractions",
    "recall_with_report",
    "looks_like_match",
    "score_attractions",
    "select_attractions",
    "enrich_entrances",
    "attractions_centroid",
    "is_sub_area",
    "stay_minutes",
    "type_weight",
    "TYPE_WEIGHTS",
]

RECALL_PAGES = 2
RECALL_PAGE_SIZE = 25
"""types-only 搜索取前 2 页 = 50 条按知名度排序的候选，足够 20 个名额挑。"""

MAX_POOL = 60
MAX_SELECTED = 20
PER_DAY_CANDIDATES = 4

# 打分权重（架构文档 §5.3）
#
# 权重是实测调过的。最初是 rating .40 / type .30 / distance .20 / completeness .10，
# 在杭州跑出来把西湖排到第 6，前面全是崇一堂、江堤步道这类冷门 POI——因为高德的
# 评分普遍挤在 4.2~4.9 区分不开，而"距市中心"在很多中国城市指向的是 CBD 而不是
# 旅游核心区。真正的解法不在权重而在召回（见 recall_attractions）：换成 types-only
# 检索后，recall_rank 才成为可信的知名度信号，于是让它主导排序。
W_POPULARITY = 0.35
W_RATING = 0.25
W_TYPE = 0.15
W_DISTANCE = 0.15
W_COMPLETENESS = 0.10

RANK_HORIZON = 30
"""知名度排序前 30 名之外区分度就很低了。"""

UNRANKED_POPULARITY = 0.30
"""没有名次的候选（例如只由必去检索带进来的）：压一档，但不至于直接出局。"""

TYPE_WEIGHTS: dict[str, float] = {
    "110201": 1.00,  # 世界遗产
    "110000": 0.95,  # 风景名胜
    "110202": 0.92,  # 全国重点文物保护单位
    "110200": 0.90,  # 文物古迹
    "110105": 0.85,  # 园林
    "110301": 0.85,  # 博物馆
    "110300": 0.85,  # 博物馆大类
    "110303": 0.80,  # 美术馆
    "110102": 0.80,  # 主题公园
    "110302": 0.75,  # 展览馆
    "110500": 0.75,  # 宗教场所
    "110100": 0.75,  # 公园
    "110101": 0.75,  # 城市公园
    "110103": 0.70,  # 植物园
    "110104": 0.70,  # 动物园
    "110400": 0.70,  # 纪念馆
    "110600": 0.60,  # 剧院
    "190101": 0.40,  # 度假村
}
DEFAULT_TYPE_WEIGHT = 0.50

NEUTRAL_RATING_SCORE = 0.5
"""没有评分的 POI 给中位分——直接给 0 会把没被评过分的冷门好去处一律埋掉。"""

FAR_AWAY_M = 30000.0
"""距离得分的归一化上限：离市中心 30km 以上都算"远"。"""


# --------------------------------------------------------------------------
# 召回（IO）
# --------------------------------------------------------------------------
MATCH_THRESHOLD = 0.6
"""必去景点名与 POI 名的字符重合度下限。"""


def looks_like_match(query: str, poi_name: str) -> bool:
    """用户报的名字和搜到的 POI 是不是一回事。

    **高德对任何关键词都会模糊返回一条**，实测：
        「不存在的地方xyz」→「四川省成都市新津区兴义镇」
        「zzzqqq不可能存在」→「不可方物」
    所以"搜到了就算命中"等于没有校验，`MUST_VISIT_NOT_FOUND` 永远不会触发。

    用字符集重合度而不是子串：中文景点的官方名常带后缀或插字，
    「大熊猫基地」的正式名是「成都大熊猫繁育研究基地」，子串匹配会漏掉。
    """
    q = {c for c in query.strip().lower() if not c.isspace()}
    if not q:
        return False
    overlap = len(q & set(poi_name.lower())) / len(q)
    return overlap >= MATCH_THRESHOLD


async def recall_attractions(
    city: CityRef,
    *,
    must_visit: list[str] | None = None,
    pages: int = RECALL_PAGES,
    client=None,
    _missing: list[str] | None = None,
) -> list[Attraction]:
    """召回候选景点，按 poi_id 去重。

    `_missing` 是给 `recall_with_report` 收集"没搜到的必去景点"用的出参，
    调用方通常不用管。

    **只按 types 搜、不传 keywords** —— 这是实测得出的关键。杭州对照实验：

        types-only：  千岛湖 → 西湖 → 西溪湿地 → 灵隐寺 → 飞来峰 → 雷峰塔
        keywords=景点：钱江世纪公园 → 清河坊 → 钱江新城灯光秀 → 五柳巷 …

    传 keywords 时高德做的是文本匹配，会把名字里带"景点"的和搜索区域中心附近的
    POI 捞上来；不传 keywords 时才按 POI 权重（知名度）排序。周边搜索同理，
    `sortrule` 无论取 distance 还是 weight，返回的都是行政中心（往往是 CBD）
    附近的东西，对"这个城市值得去哪"毫无帮助，因此不再参与召回。

    必去景点走关键字精确检索——那种场景下文本匹配正是我们要的。
    """
    missing = _missing if _missing is not None else []
    pool: dict[str, Attraction] = {}

    for page in range(1, pages + 1):
        results = await poi_keyword(
            region=city.name,
            city_limit=True,
            page_size=RECALL_PAGE_SIZE,
            page_num=page,
            client=client,
        )
        if not results:
            break
        for offset, poi in enumerate(results):
            rank = (page - 1) * RECALL_PAGE_SIZE + offset
            pool.setdefault(poi.poi_id, poi.model_copy(update={"recall_rank": rank}))

    for name in must_visit or []:
        matches = await poi_keyword(name, region=city.name, city_limit=True, client=client)
        matches = [m for m in matches if looks_like_match(name, m.name)]
        if not matches:
            # 不能用 "name" 做 extra 的键——它是 LogRecord 的保留字段，会抛 KeyError
            log.warning("必去景点没搜到", extra={"spot": name, "city": city.name})
            missing.append(name)
            continue
        existing = pool.get(matches[0].poi_id)
        hit = (existing or matches[0]).model_copy(update={"must_visit": True})
        pool[hit.poi_id] = hit

    return list(pool.values())


async def recall_with_report(
    city: CityRef,
    *,
    must_visit: list[str] | None = None,
    pages: int = RECALL_PAGES,
    client=None,
) -> tuple[list[Attraction], list[PlanWarning]]:
    """`recall_attractions` + 必去景点没搜到时的告警。

    用户点名要去的地方**悄悄消失**是最糟的失败方式——他会拿到一份看起来正常
    的行程，直到出发前才发现没安排。宁可多说一句。
    """
    missing: list[str] = []
    pool = await recall_attractions(
        city, must_visit=must_visit, pages=pages, client=client, _missing=missing
    )
    if not missing:
        return pool, []

    names = "、".join(missing)
    # 给几个同城的高分景点当台阶，比单说"没找到"有用
    nearby = "、".join(a.name for a in sorted(pool, key=lambda a: a.recall_rank)[:3])
    message = f"没找到「{names}」，已跳过"
    if nearby:
        message += f"；该城市的热门景点有：{nearby}"
    return pool, [PlanWarning.of("MUST_VISIT_NOT_FOUND", message, stage="attraction")]


async def enrich_entrances(
    attractions: list[Attraction], *, client=None
) -> list[Attraction]:
    """给缺 entrance 的景点补导航入口坐标。

    大型景区的 POI 中心点常落在湖里或山里，直接拿去算驾车路线会得到荒谬结果。
    只对最终入选的景点做，一次最多 20 个。
    """
    missing = [a for a in attractions if a.entrance is None]
    if not missing:
        return attractions

    detail_by_id: dict[str, Attraction] = {}
    for offset in range(0, len(missing), 20):
        batch = missing[offset : offset + 20]
        for detail in await poi_detail([a.poi_id for a in batch], client=client):
            detail_by_id[detail.poi_id] = detail

    out: list[Attraction] = []
    for a in attractions:
        detail = detail_by_id.get(a.poi_id)
        if a.entrance is None and detail is not None and detail.entrance is not None:
            a = a.model_copy(update={"entrance": detail.entrance})
        out.append(a)
    return out


# --------------------------------------------------------------------------
# 打分与筛选（纯函数）
# --------------------------------------------------------------------------
def type_weight(typecode: str) -> float:
    """先精确匹配分类码，再退到大类（前 4 位 + '00'）。"""
    if typecode in TYPE_WEIGHTS:
        return TYPE_WEIGHTS[typecode]
    if len(typecode) >= 4 and (major := typecode[:4] + "00") in TYPE_WEIGHTS:
        return TYPE_WEIGHTS[major]
    return DEFAULT_TYPE_WEIGHT


def _completeness(a: Attraction) -> float:
    """有图有营业时间的条目，展示效果和可规划性都更好。"""
    return 0.5 * bool(a.photos) + 0.5 * bool(a.opentime_today)


def _popularity(a: Attraction) -> float:
    """把关键字搜索的名次折算成 0~1 的热度分。"""
    if a.recall_rank is None:
        return UNRANKED_POPULARITY
    return max(0.0, 1.0 - a.recall_rank / RANK_HORIZON)


def _distance_score(a: Attraction, anchor: GeoPoint) -> float:
    """离锚点越近越高。锚点用城市中心而不是景点重心——重心要等选完才有，
    用它打分会造成循环依赖。"""
    meters = a.routing_point.distance_to(anchor)
    return max(0.0, 1.0 - min(meters, FAR_AWAY_M) / FAR_AWAY_M)


def stay_minutes(a: Attraction, pace: Pace) -> int:
    base = DEFAULT_STAY_MINUTES[pace]
    return round(base * 1.5) if a.is_large_scenic_area else base


def score_attractions(
    pool: list[Attraction],
    anchor: GeoPoint,
    pace: Pace = "standard",
    *,
    avoid: list[str] | None = None,
) -> list[Attraction]:
    """打分并按分数降序返回。必去景点恒定排在最前。"""
    avoid_terms = [t.strip() for t in (avoid or []) if t.strip()]

    scored: list[Attraction] = []
    for a in pool:
        if not a.must_visit and any(term in a.name for term in avoid_terms):
            continue
        rating_score = NEUTRAL_RATING_SCORE if a.rating is None else min(a.rating, 5.0) / 5.0
        score = (
            W_POPULARITY * _popularity(a)
            + W_RATING * rating_score
            + W_TYPE * type_weight(a.typecode)
            + W_DISTANCE * _distance_score(a, anchor)
            + W_COMPLETENESS * _completeness(a)
        )
        scored.append(
            a.model_copy(
                update={"score": round(score, 4), "suggested_duration_min": stay_minutes(a, pace)}
            )
        )

    scored.sort(key=lambda a: (a.must_visit, a.score), reverse=True)
    return scored[:MAX_POOL]


def is_sub_area(child: Attraction, parent: Attraction) -> bool:
    """子景点是不是父景区的**一个分区**（而不是园内一个独立景点）。

    只看 `parent` 字段会误伤。深圳实测：

        深圳华侨城旅游度假区  ←parent─  深圳世界之窗 / 锦绣中华民俗村
        西涌国际滨海旅游区    ←parent─  杨梅坑

    这些"父"是行政/商业容器，子才是人们真正要去的地方、各自独立收费、各逛半天。
    单靠 parent 去重会把第 2 高分的世界之窗挤掉，而容器本身又没排进行程——两个都丢。

    判别信号在高德的命名惯例里：**同一景区内部的分区叫「父名-子名」**。

        杭州西湖风景名胜区-断桥残雪   → 是分区，该去重
        深圳仙湖植物园-弘法寺         → 是分区，该去重
        深圳世界之窗                  → 独立景点，保留
    """
    return child.name.startswith(parent.name) and len(child.name) > len(parent.name)


def select_attractions(scored: list[Attraction], travel_days: int) -> list[Attraction]:
    """取 Top-K，并把已入选景区的**分区**挡掉。

    K = 游玩天数 × 4，上限 20；必去景点即使超额也不会被挤掉。

    去重的由来：高德会把"西湖风景名胜区"和它的"断桥残雪""柳浪闻莺"作为独立 POI
    返回。不过滤的话一个西湖能占掉 16 个名额里的 3 个，而用户实际上只是
    "去西湖玩一天"。判别方式见 `is_sub_area`。
    """
    limit = min(max(travel_days, 1) * PER_DAY_CANDIDATES, MAX_SELECTED)

    must = [a for a in scored if a.must_visit]
    selected = list(must)
    taken = {a.poi_id: a for a in must}

    for a in scored:
        if len(selected) >= limit:
            break
        if a.poi_id in taken:
            continue
        parent = taken.get(a.parent_id) if a.parent_id else None
        if parent is not None and is_sub_area(a, parent):
            continue  # 所属景区已入选，这只是它的一个分区
        selected.append(a)
        taken[a.poi_id] = a

    return selected


def attractions_centroid(
    attractions: list[Attraction], city_center: GeoPoint | None = None
) -> GeoPoint | None:
    """景点重心——酒店重排的锚点。

    必须在 enrich_entrances 之后再算：入口坐标和 POI 中心点可能差好几公里。

    **给了 `city_center` 就先剔除远郊景点再取平均。** 算术平均对离群点毫无抵抗力：
    实测成都 20 个景点里有 7 个在 40 km 外（都江堰 64 km、青城后山 67 km、
    天台山 91 km），把重心从市中心拽出 **19.3 km**，于是每一家市区酒店测出来
    都是"距景点集中区 40~60 分钟"，通勤这一维在重排里彻底失效。

        算术平均 19.3 km ／ 中位数 10.6 km ／ 剔除远郊后 **5.1 km**

    远郊景点本来就要单独安排一日游（见 `route_planner.split_day_trips`），
    不该把酒店往它们那边拽——判据用同一个半径，保持口径一致。
    必去景点也一视同仁：用户说要去都江堰，不等于愿意为它把酒店挪到 60 km 外。
    """
    if not attractions:
        return None

    points = [a.routing_point.as_gcj02().coordinate for a in attractions]
    if city_center is not None:
        anchor = city_center.as_gcj02().coordinate
        inner = [p for p in points if haversine_m(p, anchor) <= DAY_TRIP_RADIUS_M]
        # 全都在远郊（景点都在郊县的小城市）就退回全量，总比没有锚点强
        if inner:
            points = inner

    lng, lat = centroid(points)
    return GeoPoint.gcj02(lng, lat)
