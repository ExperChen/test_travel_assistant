"""resolve_city：目的地城市名 → adcode / citycode / 中心坐标。

这一步同时承担 D1 决策的守门：目的地不在中国大陆就立刻失败，
避免后面白花 SerpAPI 额度去查一个注定没有景点和路线数据的城市。
"""

from __future__ import annotations

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.graph.nodes._common import fail
from app.graph.state import TripState
from app.models.common import CityRef
from app.models.errors import ErrorCode
from app.tools.amap_poi import District, district_lookup

log = get_logger(__name__)

__all__ = ["resolve_city", "pick_city"]


def pick_city(districts: list[District]) -> District | None:
    """从行政区查询结果里挑出最合适的一条（按 city → 直辖市 → 区县 → 省 的优先级）。

    只负责"选哪条"，不负责"这条能不能用"——省级和境外的判断放在 resolve_city 里，
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


async def resolve_city(state: TripState) -> dict:
    name = state["request"].destination_city

    try:
        candidates = await district_lookup(name)
    except AppError as exc:
        return fail(exc.code, exc.message, city=name)

    best = pick_city(candidates)
    if best is None:
        return fail(ErrorCode.CITY_NOT_FOUND, f"高德没有解析出城市：{name}", city=name)

    city = CityRef(
        name=best.name,
        adcode=best.adcode,
        citycode=best.citycode,
        center=best.center,  # type: ignore[arg-type] —— pick_city 已保证非空
    )

    # 顺序有意义：港澳台本身也是 province 级且 citycode 为空，必须先判覆盖范围，
    # 否则会被误报成"请具体到城市"，用户改几次也改不对
    if not city.is_mainland_china:
        return fail(
            ErrorCode.DESTINATION_UNSUPPORTED,
            f"{city.name}（adcode={city.adcode}）不在高德 Web 服务覆盖范围内",
            city=city.name,
            adcode=city.adcode,
        )

    if is_too_broad(best):
        return fail(
            ErrorCode.DESTINATION_TOO_BROAD,
            f"{city.name} 是 {best.level} 级行政区，不能作为行程目的地",
            city=city.name,
            level=best.level,
        )

    log.info(
        "城市已解析",
        extra={"city": city.name, "adcode": city.adcode, "citycode": city.citycode},
    )
    return {"dest_city": city, "phase": "attraction"}
