"""逐日行程编排（架构文档 §5.4）。

**这里没有 LLM。** 分天聚类、时间窗约束、天内排序是确定性优化问题，LLM 做这个
既慢又不稳定（会编造距离和时间）。行程骨架用算法算，只把自然语言解释交给 LLM。

与架构文档 §5.4 Step 4 的一处修正：文档写「粗排用 distance_batch，每天 1 次调用
拿到距离矩阵」——但 `/v3/distance` 是**多起点 → 单终点**的向量接口，拿不到
N×N 矩阵；真要凑齐矩阵得每天 N+1 次调用。改成：

    天内排序用 haversine 直线距离求解（纯计算、零额度、同一天的点本来就聚在一起，
    直线距离与实际路网的相对顺序高度一致）→ 只对**最终选定的相邻点对**拉一次
    真实路线，那也正是要展示给用户的内容。

每天的路径调用数 = 景点数 + 1（含往返酒店），4 天行程约 20 次，高德日配额 5000 够用。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from app.core.dates import DayWindow
from app.core.exceptions import AppError
from app.core.geo import cluster_by_bearing, haversine_m
from app.core.logging import get_logger
from app.models.attraction import Attraction
from app.models.common import GeoPoint
from app.models.route import DayItem, DayPlan, Itinerary, RouteLeg, TransportMode
from app.tools.amap_route import direction_driving, direction_transit, direction_walking

log = get_logger(__name__)

__all__ = [
    "split_day_trips",
    "assign_days",
    "order_within_day",
    "parse_open_window",
    "estimate_minutes",
    "fit_visit",
    "schedule_day",
    "fetch_leg",
    "build_itinerary",
    "WALK_THRESHOLD_M",
    "ESTIMATED_SPEED_KMH",
    "MIN_VISIT_MINUTES",
    "DAY_TRIP_RADIUS_M",
    "MAX_COMMUTE_RATIO",
]

WALK_THRESHOLD_M = 1200
"""直线距离小于这个数就直接走路，不必浪费一次公交查询。"""

ESTIMATED_SPEED_KMH = 22.0
"""市内平均通行速度（含等车、换乘、堵车）。只用于排期时的预估，展示的是实测值。"""

MIN_LEG_MINUTES = 10
"""再近的两点，算上找路和进出场也要 10 分钟。"""

MIN_VISIT_MINUTES = 30
"""某个景点只剩不到半小时可逛，不如不排。"""

DAY_TRIP_RADIUS_M = 40_000
"""超过这个直线距离就不属于「城市行程」，而是独立的一日游。

成都实测：安仁古镇 ~50km、街子古镇 ~50km、都江堰景区 ~50km、青城后山 ~65km。
知名度排序会把它们排得很靠前，但把它们塞进市内行程会得到「通勤 8.8 小时、
游玩 2.9 小时」这种没法用的安排。前置按直线距离筛掉，零额度。
"""

MAX_COMMUTE_RATIO = 1.5
"""当天「累计通勤 ÷ 累计游玩」的上限。

半径筛选用的是直线距离，挡不住「30km 但公交极烂」的情况；这一道用实测时长兜底。
超了就跳过这一站去试下一个——而不是砍掉当天剩下的所有安排。
"""

DETOUR_FACTOR = 1.35
"""直线距离 → 实际路网距离的经验系数。"""

# 分隔符要把全角的一并收进来：中文 POI 数据里 `08：00－20：00` 比半角写法还常见
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})[:：](\d{2})\s*[-－–—~～至到]\s*(\d{1,2})[:：](\d{2})"
)
_ALL_DAY_WORDS = ("全天", "24小时", "24 小时", "全年无休")


# --------------------------------------------------------------------------
# 分天与排序（纯函数）
# --------------------------------------------------------------------------
def split_day_trips(
    attractions: list[Attraction], hotel: GeoPoint, *, radius_m: float = DAY_TRIP_RADIUS_M
) -> tuple[list[Attraction], list[Attraction]]:
    """按「是否适合当日往返」把景点分成两组，返回 (市内可排的, 太远的)。

    用直线距离而不是实测通勤：这一步要在花任何路径规划额度**之前**做完。
    两条例外：
      · 必去景点不筛——用户明确点名了，远也得去；
      · 如果筛完一个不剩（全是远郊的小城市），退回全留，总比给空行程强。
    """
    near: list[Attraction] = []
    far: list[Attraction] = []
    for spot in attractions:
        if spot.must_visit or spot.routing_point.distance_to(hotel) <= radius_m:
            near.append(spot)
        else:
            far.append(spot)

    if not near:
        log.warning("全部景点都超出当日往返半径，保留原样", extra={"count": len(far)})
        return attractions, []
    return near, far


def assign_days(
    attractions: list[Attraction], anchor: GeoPoint, day_count: int
) -> list[list[Attraction]]:
    """按方位角把景点分到各天，同方向的排在同一天。

    以酒店为原点做扇形聚类——避免一天之内来回横穿城市。必去景点先落位，
    保证它们一定出现在某一天。
    """
    if day_count <= 0 or not attractions:
        return [[] for _ in range(max(day_count, 0))]

    points = [a.routing_point.as_gcj02().coordinate for a in attractions]
    clusters = cluster_by_bearing(points, anchor.as_gcj02().coordinate, day_count)

    days = [[attractions[i] for i in cluster] for cluster in clusters]
    # 每天内部先按分数排，后面 TSP 只调整顺序不调整成员
    return [sorted(day, key=lambda a: (a.must_visit, a.score), reverse=True) for day in days]


def _tsp_order(points: list[GeoPoint], start: GeoPoint) -> list[int]:
    """从 start 出发的最近邻 + 2-opt 改进。

    每天最多 6 个点，暴力可解；2-opt 足以消掉最近邻常见的交叉路径。
    """
    n = len(points)
    if n <= 1:
        return list(range(n))
    # n == 2 也要走最近邻：往返总长虽然相同，但先去近的那个更符合直觉

    coords = [p.as_gcj02().coordinate for p in points]
    origin = start.as_gcj02().coordinate

    def dist(a: int, b: int) -> float:
        return haversine_m(coords[a], coords[b])

    # 最近邻
    unvisited = set(range(n))
    current = min(unvisited, key=lambda i: haversine_m(origin, coords[i]))
    order = [current]
    unvisited.remove(current)
    while unvisited:
        current = min(unvisited, key=lambda i: dist(order[-1], i))
        order.append(current)
        unvisited.remove(current)

    # 2-opt：反转任意区间若能缩短总长就采纳
    def total(seq: list[int]) -> float:
        length = haversine_m(origin, coords[seq[0]])
        length += sum(dist(seq[i], seq[i + 1]) for i in range(len(seq) - 1))
        return length + haversine_m(coords[seq[-1]], origin)

    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                if total(candidate) < total(order) - 1.0:  # 1m 容差，避免浮点抖动
                    order = candidate
                    improved = True
    return order


def _tsp_ordered(attractions: list[Attraction], start: GeoPoint) -> list[Attraction]:
    if len(attractions) <= 1:
        return list(attractions)
    order = _tsp_order([a.routing_point for a in attractions], start)
    return [attractions[i] for i in order]


def order_within_day(attractions: list[Attraction], hotel: GeoPoint) -> list[Attraction]:
    """以酒店为起终点排出当天顺序，**必去景点排在最前**。

    `assign_days` 已经按 `(must_visit, score)` 把必去排到桶首，但 TSP 纯按地理
    位置重排，会把这个优先级整个抹掉——实测都江堰被甩到当天第 4 位，前 3 个
    先把时间窗吃光，它就整批进了 `leftover`。用户点名要去的地方就这么没了。

    代价是总通勤可能变长：必去景点不一定在最优环线的开头。这是刻意的取舍——
    "去不成"比"多走一段"严重得多。没有必去景点时行为完全不变。
    """
    if len(attractions) <= 1:
        return list(attractions)

    musts = [a for a in attractions if a.must_visit]
    if not musts:
        return _tsp_ordered(attractions, hotel)

    ordered_musts = _tsp_ordered(musts, hotel)
    rest = [a for a in attractions if not a.must_visit]
    # 其余景点从最后一个必去景点接着排，而不是从酒店——否则会绕回去
    return [*ordered_musts, *_tsp_ordered(rest, ordered_musts[-1].routing_point)]


# --------------------------------------------------------------------------
# 营业时间与用时估算
# --------------------------------------------------------------------------
def parse_open_window(text: str) -> tuple[time, time] | None:
    """从高德的 `opentime_today` 里抠出开放时段。

    格式很不统一：'08:00-18:00'、'全天开放'、'07:00-18:30(4月1日-10月31日)'。
    解析不出来就返回 None——按"不限制"处理，宁可排上也不要把景点误杀。
    """
    if not text:
        return None
    if any(word in text for word in _ALL_DAY_WORDS):
        return None
    if m := _TIME_RANGE_RE.search(text):
        oh, om, ch, cm = (int(g) for g in m.groups())
        if oh > 23 or ch > 24 or om > 59 or cm > 59:
            return None
        # 有的景点写 24:00，转成当天最后一分钟
        close = time(23, 59) if ch == 24 else time(ch, cm)
        return time(oh, om), close
    return None


def estimate_minutes(a: GeoPoint, b: GeoPoint) -> int:
    """排期阶段的通勤预估。真实值等选定顺序后再拉路线覆盖。"""
    meters = haversine_m(a.as_gcj02().coordinate, b.as_gcj02().coordinate) * DETOUR_FACTOR
    minutes = meters / 1000.0 / ESTIMATED_SPEED_KMH * 60.0
    return max(MIN_LEG_MINUTES, round(minutes))


# --------------------------------------------------------------------------
# 单日排期（纯函数）
# --------------------------------------------------------------------------
def fit_visit(
    attraction: Attraction,
    arrive: datetime,
    day: date,
    window_end: datetime,
    *,
    duration_min: int | None = None,
) -> tuple[datetime, datetime] | None:
    """把一次游览钉进营业时间与当日时间窗。

    返回 (实际开始, 实际结束)；返回 None 表示这天去不了（已闭园，或剩余时间太短）。

    两轮排期都必须走这里：第一轮用估算时长，第二轮用实测路线重算时刻——
    第二轮若不再施加一次约束，会把 14:00 才开门的景点排到 13:00。
    """
    open_window = parse_open_window(attraction.opentime_today)
    closes_at: datetime | None = None
    if open_window:
        opens, closes = open_window
        opens_at = datetime.combine(day, opens)
        closes_at = datetime.combine(day, closes)
        if arrive < opens_at:
            arrive = opens_at  # 到早了就等开门
        if arrive >= closes_at:
            return None

    latest_end = window_end if closes_at is None else min(window_end, closes_at)
    stay = duration_min if duration_min is not None else attraction.suggested_duration_min
    end = min(arrive + timedelta(minutes=stay), latest_end)

    if (end - arrive) < timedelta(minutes=MIN_VISIT_MINUTES):
        return None
    return arrive, end


def schedule_day(
    ordered: list[Attraction],
    window: DayWindow,
    hotel: GeoPoint,
    *,
    travel_minutes=estimate_minutes,
) -> tuple[list[DayItem], list[Attraction]]:
    """把当天的景点塞进时间窗，返回 (排上的, 塞不下的)。

    `travel_minutes` 可注入真实通勤时长做第二轮精修。
    """
    items: list[DayItem] = []
    leftover: list[Attraction] = []
    if not window.is_usable:
        return [], list(ordered)

    cursor = window.start
    previous = hotel

    for index, attraction in enumerate(ordered):
        point = attraction.routing_point
        arrive = cursor + timedelta(minutes=travel_minutes(previous, point))

        fitted = fit_visit(attraction, arrive, window.day, window.end)
        if fitted is None:
            leftover.extend(ordered[index:])  # 这站去不了，后面的按原顺序顺延
            break
        arrive, end = fitted

        items.append(
            DayItem(
                kind="attraction",
                ref_id=attraction.poi_id,
                name=attraction.name,
                location=point,
                start_time=arrive,
                end_time=end,
                ticket_cost_cny=attraction.ticket_cost,
            )
        )
        cursor = end
        previous = point

    return items, leftover


# --------------------------------------------------------------------------
# 真实路线（IO）
# --------------------------------------------------------------------------
async def fetch_leg(
    origin: GeoPoint,
    destination: GeoPoint,
    *,
    mode: TransportMode,
    citycode: str,
    depart_at: datetime | None = None,
    client=None,
) -> RouteLeg:
    """拉一段真实路线。

    近距离直接走路——两点相隔 800m 还去查公交换乘，既费额度又给出可笑的方案。
    公交查不到方案时（郊区常见）降级到驾车；再拿不到就用直线估算兜底，
    并把 mode 标成实际使用的方式，不谎报。
    """
    straight = haversine_m(
        origin.as_gcj02().coordinate, destination.as_gcj02().coordinate
    )

    note = "未查到路线"
    leg: RouteLeg | None = None
    try:
        if straight <= WALK_THRESHOLD_M:
            leg = await direction_walking(origin, destination, client=client)
        elif mode == "transit" and citycode:
            leg = await direction_transit(
                origin, destination, city=citycode, depart_at=depart_at, client=client
            )
            if leg is None:
                leg = await direction_driving(origin, destination, client=client)
        elif mode == "walking":
            leg = await direction_walking(origin, destination, client=client)
        else:
            leg = await direction_driving(origin, destination, client=client)
    except AppError as exc:
        # 一段路线查不动**绝不能**毁掉整份行程。深圳实测：某几段公交换乘查询
        # 反复超时，重试耗尽后异常一路上抛，把已经排好的 4 天行程整个作废了。
        # 退回直线估算，并在 detail 里如实标注，不冒充实测值。
        note = f"路线查询失败（{exc.code}）"
        log.warning("路线查询失败，改用直线估算", extra={"err": exc.message, "mode": mode})

    if leg is not None:
        return leg

    return RouteLeg(
        mode=mode,
        distance_m=round(straight * DETOUR_FACTOR),
        duration_min=estimate_minutes(origin, destination),
        detail=f"（{note}，按直线距离估算）",
    )


def _hotel_item(name: str, point: GeoPoint, start: datetime, end: datetime) -> DayItem:
    return DayItem(
        kind="hotel", ref_id="hotel", name=name, location=point, start_time=start, end_time=end
    )


async def build_itinerary(
    windows: list[DayWindow],
    day_buckets: list[list[Attraction]],
    hotel_point: GeoPoint,
    hotel_name: str,
    *,
    mode: TransportMode,
    citycode: str,
    client=None,
) -> Itinerary:
    """两阶段排期：先用估算把景点塞进时间窗，再用实测路线精修。

    第二轮精修会让某些景点超出时间窗（实际比估算慢），这时把尾部挪到
    `unscheduled` —— 宁可少排，也不要给出一份跑不完的行程。
    """
    days: list[DayPlan] = []
    unscheduled: list[Attraction] = []

    for window, bucket in zip(windows, day_buckets, strict=False):
        ordered = order_within_day(bucket, hotel_point)
        planned, leftover = schedule_day(ordered, window, hotel_point)
        unscheduled.extend(leftover)

        if not planned:
            days.append(
                DayPlan(
                    day_index=window.day_index,
                    day=window.day,
                    window_start=window.start,
                    window_end=window.end,
                )
            )
            continue

        # 第二轮：拉真实路线，并用实测时长重排时刻
        by_id = {a.poi_id: a for a in bucket}
        legs: list[RouteLeg] = []
        cursor = window.start
        previous_point, previous_ref = hotel_point, "hotel"
        confirmed: list[DayItem] = []
        last_back: RouteLeg | None = None

        inbound_min = 0  # 已确认的去程腿累计
        visit_min = 0

        for position, item in enumerate(planned):
            # 窗口真的到头了才整体收工；其余情况一律「跳过这一站，试下一个」
            if cursor + timedelta(minutes=MIN_VISIT_MINUTES) > window.end:
                unscheduled.extend(
                    by_id[i.ref_id] for i in planned[position:] if i.ref_id in by_id
                )
                break

            leg = await fetch_leg(
                previous_point,
                item.location,
                mode=mode,
                citycode=citycode,
                depart_at=cursor,
                client=client,
            )
            arrive = cursor + timedelta(minutes=leg.duration_min)
            planned_stay = round((item.end_time - item.start_time).total_seconds() / 60)

            # 用实测时长重算后必须重新过一遍营业时间——否则会把 14:00 才开门的
            # 景点排到 13:00（第一轮的约束在这里已经失效）
            attraction = by_id.get(item.ref_id)
            fitted = (
                fit_visit(attraction, arrive, window.day, window.end, duration_min=planned_stay)
                if attraction
                else (arrive, arrive + timedelta(minutes=planned_stay))
            )
            if fitted is None or fitted[1] > window.end:
                # 已闭园 / 实测比估算慢。可能只是这一站的问题（它 17:00 关门，
                # 下一站开到 21:00），所以跳过而不是砍掉全天。
                if (spot := by_id.get(item.ref_id)) is not None:
                    unscheduled.append(spot)  # 只丢这一站，当天剩下的继续试
                continue

            # 还得算上**回酒店那一段**。只看景点结束时间会排出这种行程：
            # 20:54 逛完青城后山（确实在 21:00 窗口内），然后坐 232 分钟公交，
            # 凌晨 00:46 到店。
            back_home = await fetch_leg(
                item.location,
                hotel_point,
                mode=mode,
                citycode=citycode,
                depart_at=fitted[1],
                client=client,
            )
            if fitted[1] + timedelta(minutes=back_home.duration_min) > window.end:
                log.info(
                    "回程会超出当日时间窗，跳过这一站",
                    extra={"spot": item.name, "return_min": back_home.duration_min},
                )
                if (spot := by_id.get(item.ref_id)) is not None:
                    unscheduled.append(spot)  # 只丢这一站，当天剩下的继续试
                continue

            stay = round((fitted[1] - fitted[0]).total_seconds() / 60)
            ratio = (inbound_min + leg.duration_min + back_home.duration_min) / max(
                visit_min + stay, 1
            )
            spot = by_id.get(item.ref_id)
            must_visit = spot is not None and spot.must_visit
            if ratio > MAX_COMMUTE_RATIO and not must_visit:
                # 半径筛选挡不住「30km 但公交极烂」，这一道用实测时长兜底。
                # **必去景点豁免**：用户点名要去，通勤划不划算轮不到我们替他判。
                log.info(
                    "通勤远超游玩时间，跳过这一站",
                    extra={"spot": item.name, "ratio": round(ratio, 2)},
                )
                if spot is not None:
                    unscheduled.append(spot)  # 只丢这一站，当天剩下的继续试
                continue
            if ratio > MAX_COMMUTE_RATIO:
                log.info(
                    "必去景点通勤划不来，但照排",
                    extra={"spot": item.name, "ratio": round(ratio, 2)},
                )

            arrive, end = fitted
            legs.append(leg.model_copy(update={"from_ref": previous_ref, "to_ref": item.ref_id}))
            confirmed.append(item.model_copy(update={"start_time": arrive, "end_time": end}))
            cursor = end
            previous_point, previous_ref = item.location, item.ref_id
            inbound_min += leg.duration_min
            visit_min += stay
            last_back = back_home  # 收下这一站，它的回程就是当前的收尾腿

        if confirmed and last_back is not None:
            legs.append(last_back.model_copy(update={"from_ref": previous_ref, "to_ref": "hotel"}))
            back_at = cursor + timedelta(minutes=last_back.duration_min)
            confirmed.append(_hotel_item(hotel_name, hotel_point, back_at, back_at))

        days.append(
            DayPlan(
                day_index=window.day_index,
                day=window.day,
                window_start=window.start,
                window_end=window.end,
                items=confirmed,
                legs=legs,
            )
        )

    return Itinerary(days=days, unscheduled=unscheduled)
