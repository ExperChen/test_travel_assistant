"""跨模块共用的基础模型。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from app.core import geo

__all__ = ["CRS", "GeoPoint", "CityRef", "LocaleCtx", "QuotaCounter"]

CRS = Literal["GCJ02", "WGS84"]


class GeoPoint(BaseModel):
    """带坐标系标注的坐标点。

    坐标系必须显式携带——项目里高德（GCJ-02）和 Google（WGS-84）两套并存，
    裸浮点数传来传去迟早会串（架构文档 §9.1）。要送进高德接口时一律先
    `.as_gcj02()`。
    """

    model_config = ConfigDict(frozen=True)

    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    crs: CRS = "GCJ02"

    # ---- 构造 ----
    @classmethod
    def gcj02(cls, lng: float, lat: float) -> Self:
        return cls(lng=lng, lat=lat, crs="GCJ02")

    @classmethod
    def wgs84(cls, lng: float, lat: float) -> Self:
        return cls(lng=lng, lat=lat, crs="WGS84")

    @classmethod
    def from_amap(cls, value: str) -> Self:
        """解析高德的 `"经度,纬度"` 字符串（永远是 GCJ-02）。"""
        lng, lat = geo.parse_amap(value)
        return cls(lng=lng, lat=lat, crs="GCJ02")

    @classmethod
    def from_google(cls, gps: dict | None) -> Self | None:
        """解析 SerpAPI 的 `gps_coordinates`（永远是 WGS-84）。"""
        if not gps or gps.get("longitude") is None or gps.get("latitude") is None:
            return None
        return cls(lng=float(gps["longitude"]), lat=float(gps["latitude"]), crs="WGS84")

    # ---- 转换 ----
    def as_gcj02(self) -> Self:
        if self.crs == "GCJ02":
            return self
        lng, lat = geo.wgs84_to_gcj02(self.lng, self.lat)
        return type(self)(lng=lng, lat=lat, crs="GCJ02")

    def as_wgs84(self) -> Self:
        if self.crs == "WGS84":
            return self
        lng, lat = geo.gcj02_to_wgs84(self.lng, self.lat)
        return type(self)(lng=lng, lat=lat, crs="WGS84")

    # ---- 输出 ----
    def to_amap(self) -> str:
        """高德接口入参格式。非 GCJ-02 会先转换，杜绝静默偏移。"""
        p = self.as_gcj02()
        return geo.to_amap(p.lng, p.lat)

    @property
    def coordinate(self) -> geo.Coordinate:
        return (self.lng, self.lat)

    def distance_to(self, other: GeoPoint) -> float:
        """直线距离（米）。两点会先统一到 GCJ-02 再算。"""
        return geo.haversine_m(self.as_gcj02().coordinate, other.as_gcj02().coordinate)


class CityRef(BaseModel):
    """目的地城市的解析结果，由 resolve_city 节点产出。"""

    name: str
    adcode: str = ""
    citycode: str = ""
    """高德公交接口的 city 参数用它（如北京 "010"）。"""
    center: GeoPoint
    province: str = ""

    @property
    def is_mainland_china(self) -> bool:
        """港澳台与境外不在高德 Web 服务覆盖内（架构文档 §1.4）。

        adcode 前两位是省级编码：81=香港、82=澳门、71=台湾。
        """
        return bool(self.adcode) and self.adcode[:2] not in {"81", "82", "71"}


class LocaleCtx(BaseModel):
    """一次会话内恒定的地区/语言/币种。

    SerpAPI 的 autocomplete 和 search 必须用同一套，否则展示的价格区间和
    搜索结果不是一个币种，用户会懵（hotels 文档 §7.4）。
    """

    model_config = ConfigDict(frozen=True)

    gl: str = "cn"
    hl: str = "zh-CN"
    currency: str = "CNY"


class QuotaCounter(BaseModel):
    """本次规划各 provider 的调用计数，随结果一起回传，便于事后追账。"""

    serpapi: int = 0
    amap: int = 0
    llm: int = 0
    cache_hits: int = 0

    def bump(self, provider: Literal["serpapi", "amap", "llm"], *, cached: bool = False) -> None:
        if cached:
            self.cache_hits += 1
            return
        setattr(self, provider, getattr(self, provider) + 1)
