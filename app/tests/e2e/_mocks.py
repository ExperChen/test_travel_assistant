"""e2e 用的假响应构造器。

两个 e2e 文件共用。所有 payload 都尽量贴近 docs 里的真实结构，
字段缺失/为 null 的情况在契约测试里另行覆盖。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import respx

SERP_URL = "https://serpapi.com/search.json"
AMAP_BASE = "https://restapi.amap.com"
TEXT_URL = f"{AMAP_BASE}/v5/place/text"
AROUND_URL = f"{AMAP_BASE}/v5/place/around"
DETAIL_URL = f"{AMAP_BASE}/v5/place/detail"
DISTRICT_URL = f"{AMAP_BASE}/v3/config/district"
DISTANCE_URL = f"{AMAP_BASE}/v3/distance"
REGEO_URL = f"{AMAP_BASE}/v3/geocode/regeo"
WALKING_URL = f"{AMAP_BASE}/v3/direction/walking"
DRIVING_URL = f"{AMAP_BASE}/v3/direction/driving"
TRANSIT_URL = f"{AMAP_BASE}/v3/direction/transit/integrated"

OUTBOUND_TOKEN = "tok-outbound-1"


# ------------------------------------------------------------------ 高德
def district_payload(
    name: str = "杭州市",
    adcode: str = "330100",
    citycode: str = "0571",
    level: str = "city",
) -> dict:
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "districts": [
            {
                "citycode": citycode,
                "adcode": adcode,
                "name": name,
                "center": "120.209947,30.246026",
                "level": level,
                "districts": [],
            }
        ],
    }


def poi_payload(count: int, *, prefix: str = "景点") -> dict:
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "count": str(count),
        "pois": [
            {
                "name": f"{prefix}{i}",
                "id": f"{prefix}-{i}",
                "location": f"{120.15 + i * 0.01:.6f},{30.24 + i * 0.01:.6f}",
                "type": "风景名胜;风景名胜;风景名胜",
                "typecode": "110000",
                "cityname": "杭州市",
                "adname": "西湖区",
                "address": f"测试路{i}号",
                "business": {
                    "rating": f"{4.9 - i * 0.05:.2f}",
                    "cost": "免费" if i % 2 else "40",
                    "opentime_today": "08:00-18:00",
                },
                "photos": [{"title": prefix, "url": f"https://x/{i}.jpg"}],
            }
            for i in range(count)
        ],
    }


# ---------------------------------------------------------------- SerpAPI
def autocomplete_payload(city: str, airports: list[tuple[str, str]]) -> dict:
    return {
        "search_metadata": {"id": "fixture", "status": "Success"},
        "suggestions": [
            {
                "type": "City",
                "name": city,
                "id": f"/m/{city}",
                "airports": [
                    {"name": name, "id": code, "city": city, "distance": "25 km"}
                    for code, name in airports
                ],
            }
        ],
    }


def _leg(dep_id: str, arr_id: str, dep: datetime, arr: datetime, number: str) -> dict:
    return {
        "departure_airport": {"name": dep_id, "id": dep_id, "time": dep.strftime("%Y-%m-%d %H:%M")},
        "arrival_airport": {"name": arr_id, "id": arr_id, "time": arr.strftime("%Y-%m-%d %H:%M")},
        "duration": int((arr - dep).total_seconds() // 60),
        "airline": "测试航空",
        "travel_class": "Economy",
        "flight_number": number,
        "extensions": [],
    }


def outbound_payload(
    outbound: date, *, count: int = 2, dep_id: str = "PEK", arr_id: str = "HGH"
) -> dict:
    """去程搜索结果。注意 SerpAPI 往返搜索的 best_flights 里**只有去程**。

    `dep_id`/`arr_id` 必须和查询用的机场一致——真实接口只会返回所查航线的航班，
    而我们会校验航段起讫点，对不上的一律丢弃。
    """
    flights = []
    for i in range(count):
        dep = datetime.combine(outbound, datetime.min.time()) + timedelta(hours=8 + i * 4)
        arr = dep + timedelta(hours=2, minutes=30)
        flights.append(
            {
                "flights": [_leg(dep_id, arr_id, dep, arr, f"CA{100 + i}")],
                "layovers": [],
                "total_duration": 150,
                "price": 1200 + i * 300,
                "type": "Round trip",
                "departure_token": f"{OUTBOUND_TOKEN}-{i}" if i else OUTBOUND_TOKEN,
            }
        )
    return {"search_metadata": {"status": "Success"}, "best_flights": flights, "other_flights": []}


def return_payload(
    return_date: date, hour: int = 18, *, dep_id: str = "HGH", arr_id: str = "PEK"
) -> dict:
    """带 departure_token 再查一次才拿得到的返程列表。方向与去程相反。"""
    dep = datetime.combine(return_date, datetime.min.time()) + timedelta(hours=hour)
    arr = dep + timedelta(hours=2, minutes=30)
    return {
        "search_metadata": {"status": "Success"},
        "best_flights": [
            {
                "flights": [_leg(dep_id, arr_id, dep, arr, "CA200")],
                "layovers": [],
                "total_duration": 150,
                "price": 1200,
                "type": "Round trip",
                "departure_token": "tok-return",
            }
        ],
        "other_flights": [],
    }


def empty_flights_payload() -> dict:
    return {"search_metadata": {"status": "Success"}, "best_flights": [], "other_flights": []}


# ------------------------------------------------------------------ 酒店
def hotels_payload(count: int = 3, *, with_ads: bool = True) -> dict:
    """properties + ads 各一批。价格递增、评分递减，方便断言重排结果。"""
    properties = [
        {
            "type": "hotel",
            "name": f"酒店{i}",
            "property_token": f"tok-hotel-{i}",
            "gps_coordinates": {"latitude": 30.24 + i * 0.01, "longitude": 120.15 + i * 0.01},
            "rate_per_night": {"lowest": f"¥{400 + i * 100}", "extracted_lowest": 400 + i * 100},
            "total_rate": {
                "lowest": f"¥{1200 + i * 300}",
                "extracted_lowest": 1200 + i * 300,
            },
            "extracted_hotel_class": 4,
            "overall_rating": 4.8 - i * 0.2,
            "reviews": 500 - i * 50,
            "amenities": ["免费 Wi-Fi", "健身房"],
            "location_rating": 4.2,
            # Google Hotels 不返回门牌号地址，位置信息只有这个
            "nearby_places": [
                {"name": "西湖", "transportations": [{"type": "Walking", "duration": "9分钟"}]},
                {
                    "name": "杭州萧山国际机场",
                    "transportations": [{"type": "Taxi", "duration": "1小时 5分钟"}],
                },
            ],
        }
        for i in range(count)
    ]
    ads = (
        [
            {
                "name": "广告酒店",
                "source": "Booking.com",
                "property_token": "tok-ad-1",
                "gps_coordinates": {"latitude": 30.30, "longitude": 120.30},
                "overall_rating": 4.5,
                "reviews": 120,
                "price": "¥380",
                "extracted_price": 380,
                "amenities": ["泳池"],
            }
        ]
        if with_ads
        else []
    )
    return {"search_metadata": {"status": "Success"}, "ads": ads, "properties": properties}


def empty_hotels_payload() -> dict:
    return {"search_metadata": {"status": "Success"}, "ads": [], "properties": []}


def amap_hotel_payload(count: int = 3) -> dict:
    """高德住宿服务 POI，降级路径用：有坐标有评分，没有房价。"""
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "count": str(count),
        "pois": [
            {
                "name": f"本地酒店{i}",
                "id": f"hotel-poi-{i}",
                "location": f"{120.16 + i * 0.01:.6f},{30.25 + i * 0.01:.6f}",
                "type": "住宿服务;宾馆酒店;三星级宾馆",
                "typecode": "100000",
                "cityname": "杭州市",
                "address": f"酒店路{i}号",
                "business": {"rating": f"{4.5 - i * 0.1:.1f}", "tel": "0571-1234567"},
            }
            for i in range(count)
        ],
    }


def walking_payload(distance_m: int = 800, duration_s: int = 660) -> dict:
    return {
        "status": "1",
        "info": "OK",
        "route": {
            "paths": [
                {
                    "distance": str(distance_m),
                    "duration": str(duration_s),
                    "steps": [{"instruction": "直行到达", "distance": str(distance_m)}],
                }
            ]
        },
    }


def driving_payload(distance_m: int = 8000, duration_s: int = 1500) -> dict:
    return {
        "status": "1",
        "info": "OK",
        "route": {
            "taxi_cost": "35",
            "paths": [
                {
                    "distance": str(distance_m),
                    "duration": str(duration_s),
                    "tolls": "0",
                    "restriction": "0",
                    "steps": [{"instruction": "沿主干道行驶", "distance": str(distance_m)}],
                }
            ],
        },
    }


def transit_payload(distance_m: int = 9000, duration_s: int = 1800) -> dict:
    return {
        "status": "1",
        "info": "OK",
        "route": {
            "distance": str(distance_m),
            "taxi_cost": "35",
            "transits": [
                {
                    "cost": "4",
                    "duration": str(duration_s),
                    "walking_distance": "600",
                    "segments": [
                        {
                            "bus": {
                                "buslines": [
                                    {"name": "地铁1号线(测试)", "type": "地铁线路"}
                                ]
                            }
                        }
                    ],
                }
            ],
        },
    }


def distance_payload(durations_s: list[int]) -> dict:
    """批量距离测量结果，按 origins 顺序 1-based 编号。"""
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "results": [
            {
                "origin_id": str(i + 1),
                "dest_id": "1",
                "distance": str(d * 10),
                "duration": str(d),
            }
            for i, d in enumerate(durations_s)
        ],
    }


def regeo_payload(count: int = 20) -> dict:
    """批量逆地理编码。Google Hotels 不给地址，酒店的门牌号只能从这儿来。"""
    return {
        "status": "1",
        "info": "OK",
        "regeocodes": [
            {"formatted_address": f"浙江省杭州市西湖区测试路{i + 1}号"} for i in range(count)
        ],
    }


def _text_search(pois: dict):
    """`/v5/place/text` 一个端点扛三种用途，靠参数区分。

    - `types=100000` → 酒店降级检索
    - 带 `keywords`  → 必去景点的精确检索。**返回的名字必须和关键词沾边**：
      真实高德会返回「都江堰景区」这种同名 POI，而相关性校验正是靠这一点
      把「不存在的地方xyz → 新津区兴义镇」这类模糊命中挡掉的
    - 都没有       → types-only 的召回分页
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params.get("types") == "100000":
            return httpx.Response(200, json=amap_hotel_payload())
        if keywords := params.get("keywords"):
            return httpx.Response(200, json=poi_payload(2, prefix=keywords))
        return httpx.Response(200, json=pois)

    return handler


# ------------------------------------------------------------------ 组装
def mock_amap(
    *,
    district: dict | None = None,
    pois: dict | None = None,
    detail: dict | None = None,
    distances: list[int] | None = None,
) -> None:
    """注册高德的四个端点。必须在 respx 上下文里调用。

    景点检索与酒店降级检索打的是同一个 `/v5/place/text`，靠 types 区分——
    住宿服务（100000）的路由要先注册，否则会被景点路由抢走。
    """
    respx.get(DISTRICT_URL).mock(
        return_value=httpx.Response(200, json=district or district_payload())
    )
    respx.get(TEXT_URL).mock(side_effect=_text_search(pois or poi_payload(8)))
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, json=detail or poi_payload(0))
    )
    respx.get(DISTANCE_URL).mock(
        return_value=httpx.Response(
            200, json=distance_payload(distances or [600, 1200, 1800, 3600])
        )
    )
    respx.get(REGEO_URL).mock(return_value=httpx.Response(200, json=regeo_payload()))
    respx.get(WALKING_URL).mock(return_value=httpx.Response(200, json=walking_payload()))
    respx.get(DRIVING_URL).mock(return_value=httpx.Response(200, json=driving_payload()))
    respx.get(TRANSIT_URL).mock(return_value=httpx.Response(200, json=transit_payload()))


def mock_hotels(payload: dict | None = None):
    return respx.get(SERP_URL, params__contains={"engine": "google_hotels"}).mock(
        return_value=httpx.Response(200, json=payload or hotels_payload())
    )


def pending_of(state, prefix: str = ""):
    """从 state["pending"] 里取第一个匹配前缀的问题。

    并行分支下可能同时挂着航班和酒店两个问题，用例得能指名要哪一个。
    """
    for question in state.get("pending") or []:
        if not prefix or question.id.startswith(prefix):
            return question
    return None


def mock_downstream(*, hotels: dict | None = None, **amap_kw) -> None:
    """航班之后的全部下游（高德 + 酒店）。只关心航班行为的用例用这个一次配齐。"""
    mock_amap(**amap_kw)
    mock_hotels(hotels)


def mock_flights(
    outbound: date,
    return_date: date,
    *,
    departure_airports: list[tuple[str, str]] | None = None,
    arrival_airports: list[tuple[str, str]] | None = None,
    departure_query: str = "北京",
    outbound_result: dict | None = None,
    return_result: dict | None = None,
) -> None:
    """注册 SerpAPI 的三类调用。

    三条路由都打到同一个 URL，靠 query 参数区分；**带 departure_token 的必须先注册**，
    否则会被更宽泛的 engine=google_flights 抢先匹配，返程就变成了去程。
    """
    departures = departure_airports or [("PEK", "首都国际机场")]
    arrivals = arrival_airports or [("HGH", "萧山国际机场")]

    def autocomplete(request: httpx.Request) -> httpx.Response:
        """按 `q` 分发，**不能按调用次序**。

        出发地填 IATA 三字码（测试里 `departure_city="PEK"` 是常态）时根本不会
        走补全，按次序发响应会让到达地拿到出发地那一份——目的地于是解析成 PEK。
        这个错位以前被"航段写死 PEK→HGH"掩盖着，直到加了航线校验才暴露。
        """
        q = request.url.params.get("q", "")
        is_departure = q == departure_query
        return httpx.Response(
            200,
            json=autocomplete_payload(q or "城市", departures if is_departure else arrivals),
        )

    respx.get(SERP_URL, params__contains={"engine": "google_flights_autocomplete"}).mock(
        side_effect=autocomplete
    )
    respx.get(SERP_URL, params__contains={"departure_token": OUTBOUND_TOKEN}).mock(
        return_value=httpx.Response(200, json=return_result or return_payload(return_date))
    )
    respx.get(SERP_URL, params__contains={"engine": "google_flights"}).mock(
        return_value=httpx.Response(200, json=outbound_result or outbound_payload(outbound))
    )
