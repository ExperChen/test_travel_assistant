"""高德模拟数据生成器。

覆盖项目实际用到的 9 个端点（`docs/poi/` 与 `docs/navigation/`）：

    /v3/config/district          行政区 → adcode / citycode / 中心坐标
    /v5/place/text               关键字 / 分类码检索
    /v5/place/around             周边检索
    /v5/place/detail             POI 详情（补入口坐标）
    /v3/geocode/regeo            批量逆地理编码
    /v3/distance                 批量距离测量
    /v3/direction/walking        步行路线
    /v3/direction/driving        驾车路线
    /v3/direction/transit/integrated  公交换乘

## 最重要的一条：几何必须自洽

路径类端点的距离与时长**由请求里的真实坐标算出来**，不是随机数。
否则规划出来会是"相隔 50 公里、步行 5 分钟"这种行程，
而它的时间窗求解完全建立在这些数字上——假数据不自洽，排出来的行程就是垃圾。

    路网距离 ≈ 直线距离 × 1.3（绕行系数）
    步行 5 km/h · 驾车 28 km/h（市区均速）· 公交 22 km/h + 候车换乘 5 分钟

## 坐标系

高德全部是 **GCJ-02**，与 SerpAPI 的 WGS-84 相反。`GeoPoint` 会在边界处转换，
这里一律按 GCJ-02 产出。
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from app.core.geo import haversine_m
from app.providers.mock.airports import (
    CITY_CODES,
    city_center,
    find_city,
)

__all__ = ["AmapMockGenerator", "ATTRACTIONS", "DETOUR", "SPEED_KMH"]

DETOUR = 1.30
"""路网距离 / 直线距离。市区路网绕行的经验系数。"""

SPEED_KMH: dict[str, float] = {"walking": 5.0, "driving": 28.0, "transit": 22.0}
"""市区平均速度。驾车 28 是含红灯与拥堵的实际均速，不是限速。"""

TRANSIT_OVERHEAD_S = 300
"""公交的候车 + 换乘固定开销。"""

DRIVING_OVERHEAD_S = 120
"""驾车的起步、找车位等固定开销。"""

# 主要城市的知名景点。有真名的城市生成出来的数据可读性好得多——
# 「成都景点3」和「都江堰景区」在排查问题时完全不是一个体验。
ATTRACTIONS: dict[str, tuple[str, ...]] = {
    "北京": ("故宫博物院", "天安门广场", "颐和园", "天坛公园", "八达岭长城",
             "南锣鼓巷", "什刹海", "圆明园", "北海公园", "雍和宫"),
    "上海": ("外滩", "东方明珠", "豫园", "南京路步行街", "田子坊",
             "上海博物馆", "武康路", "朱家角古镇", "上海科技馆", "新天地"),
    "成都": ("宽窄巷子", "锦里古街", "成都大熊猫繁育研究基地", "武侯祠", "杜甫草堂",
             "都江堰景区", "青城山", "春熙路", "文殊院", "人民公园"),
    "杭州": ("西湖风景名胜区", "灵隐寺", "西溪国家湿地公园", "宋城", "雷峰塔",
             "河坊街", "千岛湖", "钱塘江大桥", "六和塔", "浙江省博物馆"),
    "西安": ("秦始皇兵马俑博物馆", "大雁塔", "西安城墙", "华清池", "回民街",
             "陕西历史博物馆", "大唐不夜城", "钟楼", "华山", "碑林博物馆"),
    "广州": ("广州塔", "陈家祠", "沙面岛", "白云山", "上下九步行街",
             "越秀公园", "长隆野生动物世界", "北京路步行街", "南越王博物馆", "石室圣心大教堂"),
    "深圳": ("世界之窗", "东部华侨城", "深圳湾公园", "大梅沙海滨公园", "锦绣中华民俗村",
             "莲花山公园", "欢乐谷", "梧桐山", "海上世界", "深圳博物馆"),
    "南京": ("中山陵", "夫子庙", "总统府", "明孝陵", "南京博物院",
             "玄武湖", "老门东", "栖霞山", "侵华日军南京大屠杀遇难同胞纪念馆", "鸡鸣寺"),
    "武汉": ("黄鹤楼", "东湖风景区", "武汉大学", "户部巷", "湖北省博物馆",
             "汉口江滩", "归元寺", "楚河汉街", "木兰天池", "晴川阁"),
    "厦门": ("鼓浪屿", "厦门大学", "南普陀寺", "环岛路", "曾厝垵",
             "胡里山炮台", "中山路步行街", "园林植物园", "五缘湾", "集美学村"),
    "三亚": ("亚龙湾", "天涯海角", "南山文化旅游区", "蜈支洲岛", "大东海",
             "鹿回头公园", "三亚湾", "西岛", "亚特兰蒂斯", "崖州古城"),
    "重庆": ("洪崖洞", "解放碑", "磁器口古镇", "长江索道", "武隆天生三桥",
             "白公馆", "南山一棵树", "李子坝轻轨站", "大足石刻", "三峡博物馆"),
}

DAY_TRIP_SPOTS: frozenset[str] = frozenset({
    "八达岭长城", "圆明园",
    "朱家角古镇", "千岛湖",
    "都江堰景区", "青城山",
    "华山", "华清池",
    "长隆野生动物世界",
    "东部华侨城", "大梅沙海滨公园", "梧桐山",
    "栖霞山", "木兰天池", "武隆天生三桥", "大足石刻",
    "蜈支洲岛", "西岛", "崖州古城",
    "西溪国家湿地公园", "宋城",
})
"""真实位置在远郊的景点，撒点时用大得多的半径。

不这么做，所有景点都落在市中心 6 km 内，"这个点太远、更适合单独安排一日游"
这件事就永远遇不上——而它恰恰是"通勤 8.8 小时、游玩 2.9 小时"那种行程的成因，
假数据里遇不到，就没人会想起来要处理它。
"""

DAY_TRIP_RADIUS_M = 70_000
"""远郊景点的散布半径。真实值：青城山距成都 65 km、八达岭距北京 60 km。"""

_GENERIC_ATTRACTIONS = (
    "博物馆", "公园", "古城墙", "文化广场", "植物园", "步行街",
    "森林公园", "湿地公园", "老街", "寺庙",
)

_BUSINESS_AREAS = ("市中心", "老城区", "新区", "火车站", "高新区")

_ROAD_NAMES = ("人民路", "解放路", "中山路", "建设路", "文化路", "新华路")

_TYPE_ATTRACTION = ("风景名胜;风景名胜;风景名胜", "110000")
_TYPE_HOTEL = ("住宿服务;宾馆酒店;三星级宾馆", "100000")


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    """高德的成功响应外壳。status 是**字符串 "1"**，不是数字。"""
    return {"status": "1", "info": "OK", "infocode": "10000", **payload}


class AmapMockGenerator:
    """按端点分发。`seed` 给定则完全可复现。"""

    def __init__(self, *, seed: int | None = None):
        self._rng = random.Random(seed)

    # ------------------------------------------------------------ 行政区
    def district(self, keywords: str) -> dict[str, Any]:
        city = find_city(keywords or "")
        if city is None or city not in CITY_CODES:
            return _ok({"count": "0", "districts": []})

        adcode, citycode = CITY_CODES[city]
        lng, lat = city_center(city) or (0.0, 0.0)
        return _ok({
            "count": "1",
            "districts": [{
                "citycode": citycode,
                "adcode": adcode,
                "name": f"{city}市",
                "center": f"{lng:.6f},{lat:.6f}",
                "level": "city",
                "districts": [],
            }],
        })

    # ---------------------------------------------------------------- POI
    def place_text(self, params: dict[str, Any]) -> dict[str, Any]:
        """`/v5/place/text`。一个端点扛三种用途，靠参数区分——和真实接口一样。

        - `types=100000` → 住宿服务（酒店降级检索）
        - 带 `keywords`  → 精确检索；**返回名必须和关键词沾边**，
          否则上游的相关性校验（`looks_like_match`）会把它全判为不匹配
        - 都没有        → 按分类码的分页召回，高德按 POI 权重排序
        """
        region = str(params.get("region") or "")
        keywords = str(params.get("keywords") or "").strip()
        types = str(params.get("types") or "")
        page_size = int(params.get("page_size") or 20)
        page_num = int(params.get("page_num") or 1)

        city = find_city(region) or find_city(keywords) or "北京"
        center = city_center(city) or (116.4074, 39.9042)

        if types.startswith("100000"):
            return self._pois(city, center, page_size, kind="hotel")
        if keywords:
            return self._pois(city, center, min(page_size, 3), kind="keyword",
                              keyword=keywords)
        return self._pois(city, center, page_size, kind="attraction", page_num=page_num)

    def place_around(self, params: dict[str, Any]) -> dict[str, Any]:
        location = _parse_point(str(params.get("location") or ""))
        radius = int(params.get("radius") or 5000)
        page_size = int(params.get("page_size") or 25)
        if location is None:
            return _ok({"count": "0", "pois": []})

        city = self._nearest_city(location) or "北京"
        return self._pois(city, location, page_size, kind="attraction",
                          radius=radius, with_distance=True)

    def place_detail(self, params: dict[str, Any]) -> dict[str, Any]:
        """POI 详情。上游只拿它补 `navi.entr_location`（入口坐标）。"""
        ids = [i for i in str(params.get("id") or "").split("|") if i]
        pois = []
        for poi_id in ids:
            base = _stable_point(poi_id)
            pois.append({
                "id": poi_id,
                "name": poi_id,
                "location": f"{base[0]:.6f},{base[1]:.6f}",
                "type": _TYPE_ATTRACTION[0],
                "typecode": _TYPE_ATTRACTION[1],
                # 入口与中心点差几百米——大型景区正是因此才需要这个字段
                "navi": {
                    "entr_location": f"{base[0] + 0.003:.6f},{base[1] + 0.002:.6f}"
                },
            })
        return _ok({"count": str(len(pois)), "pois": pois})

    def regeo(self, params: dict[str, Any]) -> dict[str, Any]:
        """批量逆地理编码。Google Hotels 不给地址，酒店门牌号只能从这儿来。"""
        points = [p for p in str(params.get("location") or "").split("|") if p]
        out = []
        for raw in points:
            point = _parse_point(raw)
            city = self._nearest_city(point) if point else None
            road = self._pick(_ROAD_NAMES, raw)
            number = (int(hashlib.md5(raw.encode()).hexdigest()[:4], 16) % 300) + 1
            area = self._pick(_BUSINESS_AREAS, raw)
            out.append({
                "formatted_address": f"{city or '某'}市{area}{road}{number}号",
                "addressComponent": {"city": f"{city or ''}市"},
            })
        return _ok({"regeocodes": out})

    # -------------------------------------------------------------- 路径
    def distance(self, params: dict[str, Any]) -> dict[str, Any]:
        """批量距离测量。距离由**真实坐标**算出，保证与后续路线自洽。"""
        origins = [_parse_point(p) for p in str(params.get("origins") or "").split("|")]
        dest = _parse_point(str(params.get("destination") or ""))
        # ⚠️ 不能写 `params.get("type") or 1`：**0 是合法取值（直线距离）却是 falsy**，
        # 会被静默当成驾车模式，于是直线距离也乘上了 1.3 的绕行系数
        raw_mode = params.get("type")
        mode = 1 if raw_mode is None or raw_mode == "" else int(raw_mode)
        if dest is None:
            return _ok({"results": []})

        speed = SPEED_KMH["walking"] if mode == 3 else SPEED_KMH["driving"]
        results = []
        for i, origin in enumerate(origins, start=1):
            if origin is None:
                continue
            straight = haversine_m(origin, dest)
            road = straight if mode == 0 else straight * DETOUR
            seconds = road / (speed * 1000 / 3600)
            if mode == 1:
                seconds += DRIVING_OVERHEAD_S
            results.append({
                "origin_id": str(i),
                "dest_id": "1",
                "distance": str(int(road)),
                "duration": str(int(seconds)),
            })
        return _ok({"results": results})

    def direction_walking(self, params: dict[str, Any]) -> dict[str, Any]:
        road, seconds = self._leg(params, "walking")
        if road is None:
            return _ok({"route": {"paths": []}})
        return _ok({"route": {"paths": [{
            "distance": str(int(road)),
            "duration": str(int(seconds)),
            "steps": [{"instruction": "沿人行道直行到达", "distance": str(int(road))}],
        }]}})

    def direction_driving(self, params: dict[str, Any]) -> dict[str, Any]:
        road, seconds = self._leg(params, "driving")
        if road is None:
            return _ok({"route": {"paths": []}})
        return _ok({"route": {
            "taxi_cost": str(int(10 + road / 1000 * 2.4)),
            "paths": [{
                "distance": str(int(road)),
                "duration": str(int(seconds)),
                "tolls": "0",
                "restriction": "0",
                "steps": [{"instruction": "沿主干道行驶", "distance": str(int(road))}],
            }],
        }})

    def direction_transit(self, params: dict[str, Any]) -> dict[str, Any]:
        road, seconds = self._leg(params, "transit")
        if road is None:
            return _ok({"route": {"transits": []}})
        km = road / 1000
        line = self._pick(("地铁1号线", "地铁2号线", "地铁3号线", "公交 5 路", "公交 21 路"),
                          str(params.get("origin", "")))
        return _ok({"route": {
            "distance": str(int(road)),
            "taxi_cost": str(int(10 + km * 2.4)),
            "transits": [{
                "cost": str(max(2, int(2 + km / 5))),
                "duration": str(int(seconds)),
                "walking_distance": str(int(min(road * 0.12, 1200))),
                "segments": [{"bus": {"buslines": [
                    {"name": line, "type": "地铁线路" if "地铁" in line else "普通公交"}
                ]}}],
            }],
        }})

    def _leg(self, params: dict[str, Any], mode: str) -> tuple[float | None, float]:
        origin = _parse_point(str(params.get("origin") or ""))
        dest = _parse_point(str(params.get("destination") or ""))
        if origin is None or dest is None:
            return None, 0.0
        road = haversine_m(origin, dest) * DETOUR
        seconds = road / (SPEED_KMH[mode] * 1000 / 3600)
        if mode == "driving":
            seconds += DRIVING_OVERHEAD_S
        elif mode == "transit":
            seconds += TRANSIT_OVERHEAD_S
        return road, seconds

    # -------------------------------------------------------------- 内部
    def _pois(
        self,
        city: str,
        center: tuple[float, float],
        count: int,
        *,
        kind: str,
        keyword: str = "",
        page_num: int = 1,
        radius: int = 6000,
        with_distance: bool = False,
    ) -> dict[str, Any]:
        names = self._names(city, count, kind=kind, keyword=keyword, page_num=page_num)
        type_str, type_code = _TYPE_HOTEL if kind == "hotel" else _TYPE_ATTRACTION

        pois = []
        for i, name in enumerate(names):
            # 远郊景点撒得远得多，好让 split_day_trips() 那条分支能被触发
            spread = DAY_TRIP_RADIUS_M if name in DAY_TRIP_SPOTS else radius
            point = _stable_point(f"{city}{name}", center, spread)
            poi: dict[str, Any] = {
                "id": f"B{hashlib.md5(f'{city}{name}'.encode()).hexdigest()[:15].upper()}",
                "parent": "",
                "name": name,
                "location": f"{point[0]:.6f},{point[1]:.6f}",
                "type": type_str,
                "typecode": type_code,
                "cityname": f"{city}市",
                "adname": f"{self._pick(_BUSINESS_AREAS, name)}区",
                "address": f"{self._pick(_ROAD_NAMES, name)}{(i + 1) * 17 % 200 + 1}号",
                "business": {
                    "rating": f"{4.8 - i * 0.05:.1f}",
                    "cost": "免费" if i % 3 == 0 else str(30 + i * 10),
                    "opentime_today": "08:30-17:30",
                    "opentime_week": "周一至周日 08:30-17:30",
                    "business_area": self._pick(_BUSINESS_AREAS, name),
                    "tel": f"0{self._rng.randint(10, 999)}-{self._rng.randint(1000000, 9999999)}",
                },
                "photos": [{"title": name, "url": f"https://store.is.autonavi.com/mock/{i}.jpg"}],
            }
            if with_distance:
                poi["distance"] = str(int(haversine_m(center, point)))
            pois.append(poi)

        return _ok({"count": str(len(pois)), "pois": pois})

    def _names(
        self, city: str, count: int, *, kind: str, keyword: str, page_num: int
    ) -> list[str]:
        if kind == "hotel":
            from app.providers.mock.hotels import BRANDS

            return [
                f"{city}{self._pick(_BUSINESS_AREAS, f'{city}{i}')}{BRANDS[i % len(BRANDS)].name}"
                for i in range(count)
            ]
        if kind == "keyword":
            # **返回名必须和关键词沾边**——上游 looks_like_match() 靠这个判定相关性，
            # 返回不相关的名字会让"必去景点"被整体判为未命中
            return [keyword, f"{keyword}景区", f"{keyword}公园"][:count]

        pool = ATTRACTIONS.get(city)
        if pool:
            start = (page_num - 1) * count
            picked = list(pool[start : start + count])
            # 名录用完了就用通用名补齐，保证分页能取到 count 条
            while len(picked) < count:
                kind_name = _GENERIC_ATTRACTIONS[len(picked) % len(_GENERIC_ATTRACTIONS)]
                picked.append(f"{city}{kind_name}{len(picked) + start + 1}")
            return picked

        offset = (page_num - 1) * count
        return [
            f"{city}{_GENERIC_ATTRACTIONS[i % len(_GENERIC_ATTRACTIONS)]}{offset + i + 1}"
            for i in range(count)
        ]

    def _nearest_city(self, point: tuple[float, float] | None) -> str | None:
        if point is None:
            return None
        best, best_d = None, float("inf")
        for city in CITY_CODES:
            center = city_center(city)
            if center is None:
                continue
            d = haversine_m(point, center)
            if d < best_d:
                best, best_d = city, d
        return best

    def _pick(self, pool: tuple[str, ...], salt: str) -> str:
        """按内容稳定地挑一个——同一个 POI 每次落在同一个商圈。"""
        index = int(hashlib.md5(salt.encode()).hexdigest()[:8], 16) % len(pool)
        return pool[index]


def _parse_point(raw: str) -> tuple[float, float] | None:
    """`"116.397428,39.90923"` → `(lng, lat)`。"""
    parts = raw.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _stable_point(
    salt: str, center: tuple[float, float] | None = None, radius_m: int = 6000
) -> tuple[float, float]:
    """由字符串稳定地散出一个坐标。

    同名 POI 每次都落在同一位置——否则同一个景点在召回和详情两次调用里
    坐标会不一致，`enrich_entrances()` 补出来的入口就飘了。
    """
    center = center or (116.4074, 39.9042)
    digest = hashlib.md5(salt.encode()).hexdigest()
    bearing = int(digest[:8], 16) / 0xFFFFFFFF * 2 * math.pi
    radius = (int(digest[8:16], 16) / 0xFFFFFFFF) ** 0.5 * radius_m / 1000

    lng, lat = center
    d_lat = radius / 111.0 * math.cos(bearing)
    d_lng = radius / (111.0 * max(math.cos(math.radians(lat)), 0.2)) * math.sin(bearing)
    return lng + d_lng, lat + d_lat
