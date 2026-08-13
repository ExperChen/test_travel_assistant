"""景点 POI Tool：高德 POI 搜索 2.0。

对齐 `docs/poi/amap-poi-search-api.md`。三个反复踩的坑都在这里挡掉：
- 不传 `show_fields=business` 则 rating/cost/opentime 全空（§11.4）
- 不传 types 会混进一堆餐厅（§11.5）
- `page_size × page_num ≤ 200`，超了返回空（§11.3）
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.config import settings
from app.core.exceptions import InvalidParams
from app.core.logging import get_logger
from app.models.attraction import ATTRACTION_TYPES, Attraction
from app.models.common import GeoPoint
from app.providers.amap_client import AmapClient
from app.tools.registry import amap_client, tool

log = get_logger(__name__)

__all__ = [
    "poi_keyword",
    "poi_around",
    "poi_detail",
    "district_lookup",
    "regeo_batch",
    "District",
    "pick_city",
    "is_too_broad",
    "DEFAULT_SHOW_FIELDS",
]

DEFAULT_SHOW_FIELDS = "business,photos,navi"
MAX_PAGE_SIZE = 25
MAX_TOTAL_RESULTS = 200
MAX_DETAIL_IDS = 20

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_cost(value: object) -> float | None:
    """门票价：高德这个字段可能是 '免费' / '40' / '40元起'，不能直接 float()。"""
    text = str(value or "").strip()
    if not text:
        return None
    if "免费" in text or "免票" in text:
        return 0.0
    if m := _NUMBER_RE.search(text):
        return float(m.group())
    return None


def _parse_point(value: object) -> GeoPoint | None:
    text = str(value or "").strip()
    if "," not in text:
        return None
    try:
        return GeoPoint.from_amap(text)
    except (ValueError, TypeError):
        return None


def _parse_poi(raw: dict) -> Attraction | None:
    location = _parse_point(raw.get("location"))
    if location is None:
        # 没有坐标的 POI 无法参与路径规划，直接丢弃比留个空壳安全
        log.warning("跳过没有坐标的 POI", extra={"poi": raw.get("name")})
        return None

    business = raw.get("business") or {}
    navi = raw.get("navi") or {}

    return Attraction(
        poi_id=raw.get("id") or "",
        parent_id=raw.get("parent") or "",
        name=raw.get("name") or "",
        location=location,
        entrance=_parse_point(navi.get("entr_location")),
        typecode=raw.get("typecode") or "",
        type_name=raw.get("type") or "",
        address=raw.get("address") or "",
        district=raw.get("adname") or "",
        tel=business.get("tel") or "",
        distance_m=int(d) if (d := _to_float(raw.get("distance"))) is not None else None,
        rating=_to_float(business.get("rating")),
        ticket_cost=_parse_cost(business.get("cost")),
        opentime_today=business.get("opentime_today") or "",
        opentime_week=business.get("opentime_week") or "",
        business_area=business.get("business_area") or "",
        photos=[p["url"] for p in (raw.get("photos") or []) if p.get("url")],
    )


def _parse_pois(payload: dict) -> list[Attraction]:
    out: list[Attraction] = []
    for raw in payload.get("pois") or []:
        try:
            if (poi := _parse_poi(raw)) is not None:
                out.append(poi)
        except Exception:  # noqa: BLE001 —— 单条脏数据不该让整次检索失败
            log.warning("跳过无法解析的 POI", extra={"raw": str(raw)[:200]})
    return out


def _check_paging(page_size: int, page_num: int) -> None:
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise InvalidParams(f"page_size 必须在 1~{MAX_PAGE_SIZE} 之间")
    if page_size * page_num > MAX_TOTAL_RESULTS:
        raise InvalidParams(
            f"page_size × page_num 不能超过 {MAX_TOTAL_RESULTS}，超过会返回空结果"
        )


@tool(
    name="poi_keyword",
    provider="amap",
    description=(
        "搜索某城市的景点。keywords 留空、只按 types 搜时，返回的是该城市按 POI 权重"
        "（知名度）排序的结果——想要'这个城市最值得去的地方'就该这样调。"
        "传 keywords 则变成文本匹配，只适合找某个指定的景点，且一次只能传一个词。"
        "返回名称、POI ID、坐标（GCJ-02）、分类、地址、评分、门票参考价、营业时间、图片。"
        "只支持中国大陆。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "单个关键词，如 '西湖'。找某城市的热门景点时应留空",
            },
            "region": {"type": "string", "description": "城市名/citycode/adcode，如 '杭州市'"},
            "city_limit": {
                "type": "boolean",
                "description": "true=严格限定在 region 内，默认 true",
            },
            "types": {"type": "string", "description": f"POI 分类码，默认 {ATTRACTION_TYPES}"},
            "page_size": {"type": "integer", "description": "每页条数 1~25，默认 20"},
            "page_num": {"type": "integer", "description": "页码，默认 1"},
        },
        "required": ["keywords"],
    },
)
async def poi_keyword(
    keywords: str = "",
    region: str = "",
    city_limit: bool = True,
    types: str = ATTRACTION_TYPES,
    page_size: int = 20,
    page_num: int = 1,
    *,
    show_fields: str = DEFAULT_SHOW_FIELDS,
    client: AmapClient | None = None,
) -> list[Attraction]:
    # keywords 与 types 二选一必填（文档 §4.2）。留空 keywords 只按 types 搜时，
    # 高德返回的是该城市按 POI 权重排序的结果——这正是"知名度"排序。
    if not keywords.strip() and not types.strip():
        raise InvalidParams("keywords 与 types 至少要填一个")
    if "," in keywords or "|" in keywords:
        raise InvalidParams("高德关键字搜索一次只接受一个关键词，多个词请分多次调用")
    _check_paging(page_size, page_num)

    payload = await (client or amap_client()).get(
        "/v5/place/text",
        {
            "keywords": keywords.strip(),
            "types": types,
            "region": region,
            "city_limit": "true" if (city_limit and region) else None,
            "show_fields": show_fields,
            "page_size": page_size,
            "page_num": page_num,
        },
        ttl_s=settings.cache_ttl_amap_poi_s,
    )
    return _parse_pois(payload)


@tool(
    name="poi_around",
    provider="amap",
    description=(
        "按经纬度 + 半径搜索周边景点，可按距离排序。坐标必须是 GCJ-02。"
        "返回字段同 poi_keyword，另带 distance_m（距圆心米数）。只支持中国大陆。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "lng": {"type": "number", "description": "圆心经度（GCJ-02）"},
            "lat": {"type": "number", "description": "圆心纬度（GCJ-02）"},
            "radius": {"type": "integer", "description": "半径米，0~50000，默认 5000"},
            "keywords": {"type": "string", "description": "可选的周边过滤词"},
            "types": {"type": "string", "description": f"POI 分类码，默认 {ATTRACTION_TYPES}"},
            "sortrule": {
                "type": "string",
                "enum": ["distance", "weight"],
                "description": "distance=按距离，weight=综合排序",
            },
            "page_size": {"type": "integer", "description": "每页条数 1~25，默认 25"},
        },
        "required": ["lng", "lat"],
    },
)
async def poi_around(
    lng: float,
    lat: float,
    radius: int = 5000,
    keywords: str = "",
    types: str = ATTRACTION_TYPES,
    sortrule: str = "distance",
    page_size: int = 25,
    page_num: int = 1,
    *,
    show_fields: str = DEFAULT_SHOW_FIELDS,
    client: AmapClient | None = None,
) -> list[Attraction]:
    if not 0 < radius <= 50000:
        raise InvalidParams("radius 必须在 1~50000 米之间")
    _check_paging(page_size, page_num)

    payload = await (client or amap_client()).get(
        "/v5/place/around",
        {
            "location": GeoPoint.gcj02(lng, lat).to_amap(),
            "radius": radius,
            "keywords": keywords,
            "types": types,
            "sortrule": sortrule,
            "show_fields": show_fields,
            "page_size": page_size,
            "page_num": page_num,
        },
        ttl_s=settings.cache_ttl_amap_poi_s,
    )
    return _parse_pois(payload)


class District(BaseModel):
    """行政区查询结果。`level` 用于区分国家/省/市/区。"""

    name: str
    adcode: str = ""
    citycode: str = ""
    level: str = ""
    center: GeoPoint | None = None


def pick_city(districts: list[District]) -> District | None:
    """从行政区查询结果里挑出最合适的一条（按 city → 直辖市 → 区县 → 省 的优先级）。

    只负责"选哪条"，不负责"这条能不能用"——境外和省级的判断由调用方做，
    这样才能给出各自准确的错误信息，而不是一律回一句"没找到这个城市"。
    """
    usable = [d for d in districts if d.center]
    if not usable:
        return None

    for level_filter in (
        lambda d: d.level == "city",
        # 直辖市的 level 是 province，但 citycode 非空（北京 "010"）；真省份为空
        lambda d: d.level == "province" and bool(d.citycode),
        lambda d: d.level == "district",
        lambda d: True,
    ):
        if matched := [d for d in usable if level_filter(d)]:
            return matched[0]
    return None


def is_too_broad(district: District) -> bool:
    """省 / 国家级结果：能定位但不能当作行程目的地。"""
    return district.level in ("province", "country") and not district.citycode


@tool(
    name="district_lookup",
    provider="amap",
    description=(
        "把城市名解析成行政区编码与中心坐标：返回 adcode、citycode、level（country/"
        "province/city/district）与中心点（GCJ-02）。citycode 是公交换乘接口的必填参数，"
        "adcode 用于判断目的地是否在高德覆盖范围内。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "城市名/adcode，如 '杭州' 或 '330100'"}
        },
        "required": ["keywords"],
    },
)
async def district_lookup(
    keywords: str, *, client: AmapClient | None = None
) -> list[District]:
    """行政区查询 `/v3/config/district`。

    注意：这个端点不在 `docs/` 收录的四份接口文档里，但它是把"杭州"这种城市名
    解析成 adcode/citycode/中心坐标的唯一正规途径——用 POI 搜索反推城市编码
    既不稳定也拿不到 level 字段。
    """
    if not keywords or not keywords.strip():
        raise InvalidParams("城市名不能为空")

    payload = await (client or amap_client()).get(
        "/v3/config/district",
        {"keywords": keywords.strip(), "subdistrict": 0, "extensions": "base"},
        ttl_s=settings.cache_ttl_amap_poi_s,
    )

    out: list[District] = []
    for raw in payload.get("districts") or []:
        citycode = raw.get("citycode")
        out.append(
            District(
                name=raw.get("name") or "",
                adcode=str(raw.get("adcode") or ""),
                # 省级行政区的 citycode 是空数组 []，不是字符串
                citycode=citycode if isinstance(citycode, str) else "",
                level=raw.get("level") or "",
                center=_parse_point(raw.get("center")),
            )
        )
    return out


@tool(
    name="poi_detail",
    provider="amap",
    description=(
        "按 POI ID 批量查详情（一次最多 20 个，不支持分页）。"
        "比列表搜索多返回导航入口坐标 entr_location、整周营业时间、子 POI。"
        "大型景区的中心点常落在湖里山里，规划路线前应当用这个接口拿入口坐标。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "poi_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "POI ID 列表，最多 20 个",
            }
        },
        "required": ["poi_ids"],
    },
)
async def poi_detail(
    poi_ids: list[str],
    *,
    show_fields: str = "business,photos,navi,children",
    client: AmapClient | None = None,
) -> list[Attraction]:
    ids = [i.strip() for i in poi_ids if i and i.strip()]
    if not ids:
        raise InvalidParams("poi_ids 不能为空")
    if len(ids) > MAX_DETAIL_IDS:
        raise InvalidParams(f"一次最多查询 {MAX_DETAIL_IDS} 个 POI ID")

    payload = await (client or amap_client()).get(
        "/v5/place/detail",
        {"id": ",".join(ids), "show_fields": show_fields},
        ttl_s=settings.cache_ttl_amap_poi_s,
    )
    return _parse_pois(payload)


MAX_REGEO_POINTS = 20
"""高德批量逆地理编码单次上限（`batch=true`）。"""


@tool(
    name="regeo_batch",
    provider="amap",
    # 和路径工具同理：收的是带坐标系标注的 GeoPoint，裸经纬度进来就可能把
    # WGS-84 当 GCJ-02 用，偏出 300~600 米
    llm_facing=False,
    description=(
        "批量逆地理编码：把一组坐标转成文字地址。用于给只有经纬度的地点补地址——"
        "Google Hotels 不返回门牌号，只能靠这个。一次最多 20 个点。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "GCJ-02 的 'lng,lat' 字符串列表，最多 20 个",
            }
        },
        "required": ["points"],
    },
)
async def regeo_batch(
    points: list[GeoPoint], *, client: AmapClient | None = None
) -> list[str]:
    """坐标 → 文字地址，顺序与入参一一对应；查不到的位置返回空串。

    **一次调用覆盖 20 个点**，比逐个查省 20 倍额度。传进来的点会自动转 GCJ-02
    ——高德只认这套坐标系，喂 WGS-84 会偏移 300~600 米，落到隔壁街区。
    """
    if not points:
        raise InvalidParams("points 不能为空")
    if len(points) > MAX_REGEO_POINTS:
        raise InvalidParams(f"一次最多解析 {MAX_REGEO_POINTS} 个坐标")

    payload = await (client or amap_client()).get(
        "/v3/geocode/regeo",
        {
            "location": "|".join(p.as_gcj02().to_amap() for p in points),
            "batch": "true",
            "extensions": "base",
        },
        ttl_s=settings.cache_ttl_amap_poi_s,
    )

    out: list[str] = []
    for raw in payload.get("regeocodes") or []:
        address = raw.get("formatted_address")
        # 查不到时高德返回的是空列表 [] 而不是空字符串，str() 会得到 "[]"
        out.append(address if isinstance(address, str) else "")
    # 返回条数必须和入参对齐，否则调用方按下标取值会串位
    out.extend([""] * (len(points) - len(out)))
    return out[: len(points)]
