"""坐标系转换与几何基元。

项目里同时存在两套坐标系（架构文档 §9.1）：

    高德 POI / Direction / Distance   -> GCJ-02（火星坐标）
    SerpAPI Google Hotels gps_coordinates -> WGS-84

**任何进入高德接口的坐标必须先转成 GCJ-02**。不转换不会报错，只会静默产生
300~600m 的系统性偏移——足以把酒店定位到马路对面的另一个街区，并给出错误路线。

本模块只处理裸浮点数 (lng, lat)，不依赖 pydantic；带坐标系标注的封装见
`app.models.common.GeoPoint`。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Coordinate = tuple[float, float]
"""(经度, 纬度)，与高德的书写顺序一致——注意不是 (lat, lng)。"""

# 克拉索夫斯基椭球参数（GCJ-02 偏移算法使用）
_A = 6378245.0
_EE = 0.00669342162296594323

# 地球平均半径（IUGG），用于 haversine
_EARTH_RADIUS_M = 6371008.8

# 中国大陆粗略外接矩形，用于判断是否需要做 GCJ-02 偏移
_CHINA_LNG_MIN, _CHINA_LNG_MAX = 72.004, 137.8347
_CHINA_LAT_MIN, _CHINA_LAT_MAX = 0.8293, 55.8271


# --------------------------------------------------------------------------
# 坐标系转换
# --------------------------------------------------------------------------
def out_of_china(lng: float, lat: float) -> bool:
    """粗略判断是否在中国境外。境外不做 GCJ-02 偏移（原样返回）。"""
    return not (
        _CHINA_LNG_MIN <= lng <= _CHINA_LNG_MAX and _CHINA_LAT_MIN <= lat <= _CHINA_LAT_MAX
    )


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _offset(lng: float, lat: float) -> Coordinate:
    """给定 WGS-84 点，返回该点处的 (Δ经度, Δ纬度)。"""
    x, y = lng - 105.0, lat - 35.0
    d_lat = _transform_lat(x, y)
    d_lng = _transform_lng(x, y)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return d_lng, d_lat


def wgs84_to_gcj02(lng: float, lat: float) -> Coordinate:
    """WGS-84（GPS / Google）-> GCJ-02（高德）。境外原样返回。"""
    if out_of_china(lng, lat):
        return lng, lat
    d_lng, d_lat = _offset(lng, lat)
    return lng + d_lng, lat + d_lat


def gcj02_to_wgs84(lng: float, lat: float, *, iterations: int = 6) -> Coordinate:
    """GCJ-02 -> WGS-84。

    正向偏移没有解析反函数，这里用不动点迭代求逆：每轮把正向变换的残差补回去。
    6 轮后残差远小于 1mm，比常见的 `2*gcj - forward(gcj)` 一次近似精确得多。
    """
    if out_of_china(lng, lat):
        return lng, lat
    w_lng, w_lat = lng, lat
    for _ in range(iterations):
        g_lng, g_lat = wgs84_to_gcj02(w_lng, w_lat)
        w_lng += lng - g_lng
        w_lat += lat - g_lat
    return w_lng, w_lat


def to_amap(lng: float, lat: float) -> str:
    """格式化成高德接口要求的 `"经度,纬度"`（最多 6 位小数）。"""
    return f"{lng:.6f},{lat:.6f}"


def parse_amap(value: str) -> Coordinate:
    """解析高德返回的 `"经度,纬度"` 字符串。"""
    lng_s, _, lat_s = value.partition(",")
    return float(lng_s), float(lat_s)


# --------------------------------------------------------------------------
# 几何
# --------------------------------------------------------------------------
def haversine_m(a: Coordinate, b: Coordinate) -> float:
    """两点大圆距离（米）。用于粗筛，真实通勤时长一律以高德返回为准。"""
    (lng1, lat1), (lng2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lng2 - lng1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def bearing_deg(origin: Coordinate, point: Coordinate) -> float:
    """origin -> point 的方位角，正北为 0°，顺时针递增，范围 [0, 360)。"""
    (lng1, lat1), (lng2, lat2) = origin, point
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lng2 - lng1)
    y = math.sin(d_lambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(d_lambda)
    return math.degrees(math.atan2(y, x)) % 360.0


def centroid(points: Sequence[Coordinate]) -> Coordinate:
    """算术重心。

    城市尺度（<100km）下算术平均与球面重心的差异可忽略，且更稳定可测；
    本项目只用它做"景点重心"这类锚点，精度足够。
    """
    if not points:
        raise ValueError("centroid() 需要至少一个点")
    n = len(points)
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n


def cluster_by_bearing(
    points: Sequence[Coordinate],
    origin: Coordinate,
    k: int,
    *,
    balanced: bool = True,
) -> list[list[int]]:
    """以 origin 为原点按方位角做扇形聚类，返回 k 组点的**下标**。

    行程规划里用它把同一方向的景点排进同一天，避免一天之内来回横穿城市。

    balanced=True（默认）：先把环上最大的角度空隙旋到接缝处，再切成大小尽量
    均等的 k 段——每天的景点数量相近，符合行程排布的实际需要。
    balanced=False：直接在最大的 k 个角度空隙处下刀，几何上更"自然"，但可能
    出现某天 8 个点、某天 1 个点。
    """
    if k <= 0:
        raise ValueError("k 必须为正整数")
    n = len(points)
    if n == 0:
        return [[] for _ in range(k)]
    if k >= n:
        return [[i] for i in range(n)] + [[] for _ in range(k - n)]

    order = sorted(range(n), key=lambda i: bearing_deg(origin, points[i]))
    bearings = [bearing_deg(origin, points[i]) for i in order]

    # 环上相邻点的角度空隙，gaps[i] = order[i] 与 order[i+1] 之间的空隙
    gaps = [(bearings[(i + 1) % n] - bearings[i]) % 360.0 for i in range(n)]

    if not balanced:
        cut_after = sorted(sorted(range(n), key=lambda i: gaps[i], reverse=True)[:k])
        clusters: list[list[int]] = []
        start = (cut_after[-1] + 1) % n
        for cut in cut_after:
            idx, group = start, []
            while True:
                group.append(order[idx])
                if idx == cut:
                    break
                idx = (idx + 1) % n
            clusters.append(group)
            start = (cut + 1) % n
        return clusters

    seam = max(range(n), key=lambda i: gaps[i])
    rotated = [order[(seam + 1 + i) % n] for i in range(n)]
    base, extra = divmod(n, k)
    clusters, pos = [], 0
    for c in range(k):
        size = base + (1 if c < extra else 0)
        clusters.append(rotated[pos : pos + size])
        pos += size
    return clusters
