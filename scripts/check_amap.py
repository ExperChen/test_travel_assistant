"""高德接口连通性自检：把项目用到的 9 个端点各打一次，逐条打印结果。

    python scripts/check_amap.py                  # 默认查深圳
    python scripts/check_amap.py 成都
    python scripts/check_amap.py 深圳 --route 深圳宝安国际机场 华强北
    python scripts/check_amap.py --mock            # 走模拟层，不消耗额度（用来对照）

⚠️ 默认**会产生真实调用**（约 10~14 次）。高德日配额 5000，可忽略；
   但若 `.env` 里 `AMAP_MOCK=true`，本脚本会照它走模拟——开头会明确打印当前模式，
   免得拿着假数据当真的看。

用途：换 key、排查"通勤时间对不对"、验证坐标系转换，都先跑这个。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.config import settings  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.core.geo import haversine_m  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.metrics import track_quota  # noqa: E402
from app.tools import registry  # noqa: E402
from app.tools.amap_poi import (  # noqa: E402
    district_lookup,
    poi_around,
    poi_detail,
    poi_keyword,
    regeo_batch,
)
from app.tools.amap_route import (  # noqa: E402
    direction_driving,
    direction_transit,
    direction_walking,
    distance_batch,
)

RULE = "─" * 72
OK, BAD = "✓", "✗"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("city", nargs="?", default="深圳", help="要查的城市，默认深圳")
    p.add_argument("--route", nargs=2, metavar=("起点", "终点"),
                   help="额外测一条路线，两个都填 POI 名，如 深圳宝安国际机场 华强北")
    p.add_argument("--mock", action="store_true", help="强制走模拟层（不消耗额度）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印 HTTP 日志")
    return p.parse_args()


class Report:
    """逐项记录成败，最后汇总——一处失败不该中断整轮自检。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, float]] = []

    async def run(self, name: str, coro_factory):
        started = time.perf_counter()
        try:
            result = await coro_factory()
        except AppError as exc:
            took = (time.perf_counter() - started) * 1000
            self.rows.append((name, False, f"{exc.code}: {exc.message}", took))
            print(f"{BAD} {name}　{took:.0f}ms")
            print(f"    {exc.code}: {exc.message}")
            return None
        except Exception as exc:  # noqa: BLE001 —— 自检脚本要把任何异常都报出来
            took = (time.perf_counter() - started) * 1000
            self.rows.append((name, False, f"{type(exc).__name__}: {exc}", took))
            print(f"{BAD} {name}　{took:.0f}ms")
            print(f"    {type(exc).__name__}: {exc}")
            return None
        took = (time.perf_counter() - started) * 1000
        self.rows.append((name, True, "", took))
        print(f"{OK} {name}　{took:.0f}ms")
        return result

    def summary(self) -> int:
        ok = sum(1 for _, good, _, _ in self.rows if good)
        print(RULE)
        print(f"通过 {ok}/{len(self.rows)}　总耗时 "
              f"{sum(r[3] for r in self.rows) / 1000:.1f}s")
        for name, good, err, _ in self.rows:
            if not good:
                print(f"  {BAD} {name}：{err}")
        return 0 if ok == len(self.rows) else 1


async def find_poi(name: str, region: str):
    """按名字查一个 POI，返回第一条。给 --route 用。"""
    pois = await poi_keyword(keywords=name, region=region, types="", page_size=3)
    return pois[0] if pois else None


async def main() -> int:
    args = parse_args()
    setup_logging("DEBUG" if args.verbose else "WARNING", json_output=False)
    if args.mock:
        settings.amap_mock = True
    registry.reset_clients()

    mode = "模拟层（假数据，不消耗额度）" if settings.amap_mock else "真实调用"
    print(RULE)
    print(f"高德接口自检　城市={args.city}　模式={mode}")
    if not settings.amap_mock:
        key = settings.amap_key
        print(f"AMAP_KEY: {'已配置 ' + key[:4] + '…' + key[-4:] if key else '** 未配置 **'}")
    print(RULE)

    report = Report()
    with track_quota() as quota:
        # ---- 1. 行政区：城市名 → adcode / citycode / 中心坐标 ----
        districts = await report.run(
            "district_lookup　行政区解析", lambda: district_lookup(args.city))
        city_center = None
        citycode = ""
        if districts:
            d = districts[0]
            city_center = d.center
            citycode = d.citycode
            print(f"    {d.name}　adcode={d.adcode}　citycode={d.citycode}　"
                  f"level={d.level}")
            print(f"    中心 {d.center.lng:.6f},{d.center.lat:.6f}　({d.center.crs})")

        if city_center is None:
            print("\n城市解析失败，后续依赖坐标的检查无法进行。")
            return report.summary()

        # ---- 2. 关键字检索 ----
        pois = await report.run(
            "poi_keyword　　 景点检索",
            lambda: poi_keyword(region=args.city, city_limit=True, page_size=5))
        if pois:
            for p in pois[:5]:
                cost = f"¥{p.ticket_cost:.0f}" if p.ticket_cost else "免费/无"
                print(f"    {p.name[:22]:24} 评分{p.rating or '-':<4} 门票{cost:<8}"
                      f" {p.location.lng:.4f},{p.location.lat:.4f}")

        # ---- 3. 周边检索 ----
        around = await report.run(
            "poi_around　　　周边检索（3km）",
            lambda: poi_around(lng=city_center.lng, lat=city_center.lat,
                               radius=3000, page_size=5))
        if around:
            for p in around[:3]:
                print(f"    {p.name[:22]:24} 距圆心 {p.distance_m} m")

        # ---- 4. POI 详情（补入口坐标）----
        if pois:
            ids = [p.poi_id for p in pois[:3]]
            details = await report.run(
                "poi_detail　　　详情（入口坐标）", lambda: poi_detail(ids))
            if details:
                for p in details:
                    entr = (f"{p.entrance.lng:.5f},{p.entrance.lat:.5f}"
                            if p.entrance else "无入口坐标")
                    print(f"    {p.name[:22]:24} 入口 {entr}")

        # ---- 5. 逆地理编码 ----
        addresses = await report.run(
            "regeo_batch　　 逆地理编码",
            lambda: regeo_batch([city_center]))
        if addresses:
            print(f"    {city_center.lng:.4f},{city_center.lat:.4f} → {addresses[0]}")

        # ---- 6. 批量距离（酒店重排用的就是它）----
        if pois and len(pois) >= 2:
            origins = [p.routing_point for p in pois[:3]]
            dest = pois[-1].routing_point
            results = await report.run(
                "distance_batch　批量距离（驾车）",
                lambda: distance_batch(origins, dest))
            if results:
                for r, p in zip(results, pois[:3], strict=False):
                    if r.ok:
                        straight = haversine_m(p.routing_point.coordinate,
                                               dest.coordinate) / 1000
                        print(f"    {p.name[:18]:20} 路网 {r.distance_m / 1000:5.1f}km"
                              f"（直线 {straight:5.1f}km）　{r.duration_min} 分钟")

        # ---- 7~9. 三种出行方式 ----
        if pois and len(pois) >= 2:
            a, b = pois[0].routing_point, pois[1].routing_point
            km = haversine_m(a.coordinate, b.coordinate) / 1000
            print(f"\n  路线测试：{pois[0].name[:16]} → {pois[1].name[:16]}"
                  f"（直线 {km:.1f} km）")
            for label, fn in (
                ("direction_walking　步行", lambda: direction_walking(a, b)),
                ("direction_driving　驾车", lambda: direction_driving(a, b)),
                ("direction_transit　公交", lambda: direction_transit(a, b, city=citycode)),
            ):
                leg = await report.run(label, fn)
                if leg:
                    speed = (leg.distance_m / 1000) / (leg.duration_min / 60) \
                        if leg.duration_min else 0
                    extra = f"　{leg.detail}" if leg.detail else ""
                    print(f"    {leg.distance_m / 1000:5.1f}km　{leg.duration_min:3d}分钟"
                          f"　均速 {speed:4.1f} km/h{extra}")

        # ---- 可选：指定的一条路线 ----
        if args.route:
            start_name, end_name = args.route
            print(f"\n  指定路线：{start_name} → {end_name}")
            start = await report.run(f"查 POI　{start_name}",
                                     lambda: find_poi(start_name, args.city))
            end = await report.run(f"查 POI　{end_name}",
                                   lambda: find_poi(end_name, args.city))
            if start and end:
                sp, ep = start.routing_point, end.routing_point
                km = haversine_m(sp.coordinate, ep.coordinate) / 1000
                print(f"    {start.name}　{sp.lng:.5f},{sp.lat:.5f}")
                print(f"    {end.name}　{ep.lng:.5f},{ep.lat:.5f}")
                print(f"    直线距离 {km:.1f} km")
                for label, fn in (
                    ("　└ 驾车", lambda: direction_driving(sp, ep)),
                    ("　└ 公交", lambda: direction_transit(sp, ep, city=citycode)),
                ):
                    leg = await report.run(label, fn)
                    if leg:
                        print(f"      {leg.distance_m / 1000:.1f}km　"
                              f"**{leg.duration_min} 分钟**"
                              + (f"　{leg.detail}" if leg.detail else ""))

    print(RULE)
    print(f"配额消耗　高德 {quota.amap} 次　缓存命中 {quota.cache_hits} 次")
    code = report.summary()
    await registry.close_clients()
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
