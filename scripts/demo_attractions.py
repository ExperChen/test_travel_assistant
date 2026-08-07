"""跑一遍 intake → resolve_city → attraction_search，打印真实结果。

只调用高德（免费额度 5000 次/日），**不消耗任何 SerpAPI 额度**。

    python scripts/demo_attractions.py 杭州
    python scripts/demo_attractions.py 成都 --days 5 --pace packed --must 宽窄巷子
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 中文 Windows 控制台默认 GBK，打印表情符号/制表符会直接 UnicodeEncodeError 崩掉
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.core.logging import setup_logging  # noqa: E402
from app.graph.builder import plan_trip  # noqa: E402
from app.models.trip import TripRequest  # noqa: E402
from app.tools.registry import close_clients  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="景点召回链路 demo（只花高德额度）")
    p.add_argument("city", help="目的地城市，如 杭州")
    p.add_argument("--from", dest="origin", default="北京", help="出发城市")
    p.add_argument("--days", type=int, default=4, help="行程天数")
    p.add_argument("--start-in", type=int, default=30, help="几天后出发")
    p.add_argument("--pace", default="standard", choices=["relaxed", "standard", "packed"])
    p.add_argument("--must", action="append", default=[], help="必去景点，可重复")
    p.add_argument("--avoid", action="append", default=[], help="要排除的景点，可重复")
    p.add_argument("--verbose", action="store_true", help="打印结构化日志")
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    setup_logging("INFO" if args.verbose else "WARNING", json_output=False)

    outbound = date.today() + timedelta(days=args.start_in)
    request = TripRequest(
        departure_city=args.origin,
        destination_city=args.city,
        outbound_date=outbound,
        return_date=outbound + timedelta(days=max(args.days, 1)),
        pace=args.pace,
        must_visit=args.must,
        avoid=args.avoid,
    )

    try:
        state = await plan_trip(request)
    finally:
        await close_clients()

    if state.get("errors"):
        err = state["errors"][0]
        print(f"\n❌ {err.code}: {err.user_message}")
        print(f"   技术信息：{err.message}")
        return 1

    city = state["dest_city"]
    branch = state["attractions"]
    quota = state["quota"]

    print(f"\n📍 {city.name}  adcode={city.adcode}  citycode={city.citycode}")
    print(f"   中心点 {city.center.to_amap()}（GCJ-02）")
    print(f"\n🎯 召回 {len(branch.pool)} 个 → 入选 {len(branch.selected)} 个：\n")

    # 不列门票：高德极少返回 business.cost，这一列几乎永远是「-」
    print(f"{'#':>3}  {'分数':>5}  {'评分':>4}  {'停留':>5}  景点")
    print("-" * 78)
    for i, a in enumerate(branch.selected, 1):
        rating = f"{a.rating:.1f}" if a.rating is not None else "  -"
        flag = " ★" if a.must_visit else ""
        entrance = " ⌖" if a.entrance else ""
        print(
            f"{i:>3}  {a.score:>5.3f}  {rating:>4}  "
            f"{a.suggested_duration_min:>4}m  {a.name}{flag}{entrance}"
        )

    if branch.centroid:
        print(f"\n⊙ 景点重心 {branch.centroid.to_amap()}  ← 下一步酒店搜索的锚点")

    for w in state.get("warnings", []):
        print(f"\n⚠️  {w.code}: {w.message}")

    print(
        f"\n📊 配额：高德 {quota.amap} 次 · SerpAPI {quota.serpapi} 次 · "
        f"缓存命中 {quota.cache_hits} 次"
    )
    print("   （★=必去  ⌖=已拿到导航入口坐标）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
