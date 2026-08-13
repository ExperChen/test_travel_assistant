"""航司与机型表（模拟数据用）。

航司**带枢纽**，不是随机分配的：从成都飞出去大概率是川航/国航，从厦门飞出去
大概率是厦航。随机撒航司会让数据一眼假，也会让"同一条航线反复查到的航司完全
不同"这种真实接口不会有的现象混进来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["MockAirline", "AIRLINES", "airlines_for_route", "aircraft_for", "LOGO_BASE"]

LOGO_BASE = "https://www.gstatic.com/flights/airline_logos/70px"
"""真实响应里的 airline_logo 就是这个域下的 PNG，路径形如 `/CA.png`。"""


@dataclass(frozen=True)
class MockAirline:
    code: str
    """IATA 二字码，航班号前缀。"""
    name: str
    hubs: tuple[str, ...] = ()
    """枢纽城市名。为空表示全国性/无明显枢纽。"""
    low_cost: bool = False
    """低成本航司：票价打折、腿部空间小、无餐食。"""
    number_range: tuple[int, int] = (1000, 9999)
    fleet: tuple[str, ...] = field(default=("Boeing 737-800", "Airbus A320neo"))

    @property
    def logo(self) -> str:
        return f"{LOGO_BASE}/{self.code}.png"


AIRLINES: tuple[MockAirline, ...] = (
    MockAirline("CA", "中国国际航空", ("北京",), number_range=(1100, 1999),
                fleet=("Boeing 737-800", "Airbus A330-300", "Boeing 777-300ER", "Airbus A350-900")),
    MockAirline("MU", "中国东方航空", ("上海",), number_range=(5000, 5999),
                fleet=("Boeing 737-800", "Airbus A320neo", "Airbus A330-200", "COMAC C919")),
    MockAirline("CZ", "中国南方航空", ("广州", "乌鲁木齐"), number_range=(3000, 3999),
                fleet=("Boeing 737-800", "Airbus A320neo", "Airbus A330-300", "Boeing 787-9")),
    MockAirline("HU", "海南航空", ("海口", "北京"), number_range=(7100, 7999),
                fleet=("Boeing 737-800", "Boeing 787-9")),
    MockAirline("3U", "四川航空", ("成都",), number_range=(8000, 8999),
                fleet=("Airbus A320neo", "Airbus A321neo", "Airbus A330-300")),
    MockAirline("ZH", "深圳航空", ("深圳",), number_range=(9600, 9999)),
    MockAirline("MF", "厦门航空", ("厦门", "福州"), number_range=(8100, 8999)),
    MockAirline("SC", "山东航空", ("济南", "青岛"), number_range=(4600, 4999)),
    MockAirline("FM", "上海航空", ("上海",), number_range=(9100, 9399)),
    MockAirline("GJ", "浙江长龙航空", ("杭州",), number_range=(5100, 5399)),
    MockAirline("HO", "吉祥航空", ("上海",), number_range=(1200, 1699),
                fleet=("Airbus A320neo", "Boeing 787-9")),
    MockAirline("EU", "成都航空", ("成都",), number_range=(2700, 2999),
                fleet=("COMAC ARJ21", "Airbus A320neo")),
    MockAirline("GS", "天津航空", ("天津",), number_range=(7600, 7999)),
    MockAirline("JD", "首都航空", ("北京",), number_range=(5100, 5599)),
    MockAirline("SC", "山东航空", ("济南",), number_range=(4600, 4999)),
    MockAirline("PN", "西部航空", ("重庆",), low_cost=True, number_range=(6300, 6999)),
    MockAirline("9C", "春秋航空", ("上海",), low_cost=True, number_range=(8500, 8999),
                fleet=("Airbus A320neo",)),
    MockAirline("KN", "中国联合航空", ("北京",), low_cost=True, number_range=(5900, 5999),
                fleet=("Boeing 737-800",)),
    MockAirline("G5", "华夏航空", ("贵阳", "重庆"), number_range=(4800, 4999),
                fleet=("COMAC ARJ21", "Bombardier CRJ900")),
    MockAirline("DR", "瑞丽航空", ("昆明",), number_range=(6500, 6999)),
)

_GENERIC = tuple(a for a in AIRLINES if not a.hubs)


def airlines_for_route(departure_city: str, arrival_city: str) -> list[MockAirline]:
    """这条航线上"合理"的航司：两端任一是其枢纽的优先，其次全国性航司。

    去重按二字码——表里 SC 出现了两次（济南/青岛两个枢纽），不去重会让同一家
    航司在候选里占两个位置。
    """
    hub_based = [
        a for a in AIRLINES if departure_city in a.hubs or arrival_city in a.hubs
    ]
    pool = hub_based or list(_GENERIC) or list(AIRLINES)

    seen: dict[str, MockAirline] = {}
    for airline in [*pool, *AIRLINES]:  # 兜底补齐，保证候选数量够挑
        seen.setdefault(airline.code, airline)
    return list(seen.values())


def aircraft_for(airline: MockAirline, distance_km: float) -> str:
    """按航程挑机型：远程用宽体，短途用窄体。

    机队里没有宽体的航司（低成本、支线）无论多远都用它自己的机型——
    春秋飞乌鲁木齐也还是 A320。
    """
    wide = [m for m in airline.fleet if any(k in m for k in ("330", "350", "777", "787"))]
    narrow = [m for m in airline.fleet if m not in wide]

    if distance_km >= 2200 and wide:
        return wide[0]
    return (narrow or list(airline.fleet))[0]
