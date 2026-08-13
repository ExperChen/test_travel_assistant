"""把酒店模拟数据导出成 JSON 文件（对应 scripts/dump_mock_flights.py）。

    uv run python scripts/dump_mock_hotels.py            # 默认写 fixtures/serpapi/hotels/
    uv run python scripts/dump_mock_hotels.py --seed 42  # 可复现
    uv run python scripts/dump_mock_hotels.py --cities 成都 三亚

导出的场景刻意覆盖了各种**行为**而不只是各种城市——空结果、广告位不守预算、
民宿无星级这些分支，正是本地兜底逻辑的触发条件（见
`docs/architecture/serpapi-usage-and-mocking.md` §3.3）。

⚠️ 房价默认**每次导出都不同**（真随机）。要可复现的样本传 `--seed`。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.providers.mock.airports import CITY_TIER, DEFAULT_CITY_TIER  # noqa: E402
from app.providers.mock.hotels import STAR_BASE_CNY, HotelMockGenerator  # noqa: E402

DEFAULT_OUT = ROOT / "fixtures" / "serpapi" / "hotels"

DEFAULT_CITIES = (
    # 一线（系数 1.30~1.40）
    "北京", "上海", "广州", "深圳",
    # 新一线 / 强二线（1.05~1.15）
    "成都", "杭州", "西安", "厦门", "南京", "武汉",
    # 旅游城市（1.50）
    "三亚",
    # 二三线（0.85）
    "兰州", "贵阳", "呼和浩特", "长沙",
    # 特殊：供给少、系数偏高
    "拉萨",
)
"""覆盖全部四个档位，房价系数从 0.85 到 1.50 差 1.76 倍。"""

HOTEL_COUNT = 20
ADS_COUNT = 3
"""每份响应里的房源数。

真实 Google Hotels 一页返回二十来条，之前 12+2 偏少。注意下游
`MAX_HOTEL_RESULTS=10` 只取前 10 条——多出来的不改变程序行为，
纯粹让 fixture 更接近真实响应体。
"""


def dump(out_dir: Path, cities, *, seed: int | None, check_in: date, check_out: date):
    gen = HotelMockGenerator(seed=seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, str]] = []

    def write(name: str, payload: dict, note: str) -> None:
        path = out_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append((path, note))

    dates = {"check_in_date": check_in, "check_out_date": check_out}
    size = {"count": HOTEL_COUNT, "ads_count": ADS_COUNT}

    # --- 各城市：房价与星级构成随档位变化 ---
    for city in cities:
        tier = CITY_TIER.get(city, DEFAULT_CITY_TIER)
        write(
            f"search_{city}_商圈.json",
            gen.search(q=f"{city}市市中心附近酒店", **dates, **size),
            f"{city}·带商圈（系数 {tier}）",
        )
    write("search_成都_无商圈.json", gen.search(q="成都市酒店", **dates, **size),
          "景点分散时的查询形态")

    # --- 各种筛选与边界，每个都对应一条本地兜底逻辑 ---
    write("search_预算600.json",
          gen.search(q="成都市酒店", max_price=600, **dates, **size),
          "⚠️ organic 守 max_price，ads 不守 → drop_over_budget() 的触发场景")
    write("search_预算300.json",
          gen.search(q="北京市酒店", max_price=300, **dates, **size),
          "一线城市 + 低预算 → 候选偏少，触发 HOTEL_FEW_CANDIDATES")
    write("search_四五星.json",
          gen.search(q="成都市酒店", hotel_class=[4, 5], **dates, **size), "星级筛选")
    write("search_经济型.json",
          gen.search(q="上海市酒店", hotel_class=[2, 3], **dates, **size), "只要 2-3 星")
    write("search_民宿.json",
          gen.search(q="成都市酒店", vacation_rentals=True, **dates, **size),
          "民宿模式：type=vacation rental 且**无星级字段**")
    write("search_带儿童.json",
          gen.search(q="成都市酒店", adults=2, children=1, **dates, **size), "2 大 1 小")
    write("search_多人.json",
          gen.search(q="三亚市酒店", adults=4, children=2, **dates, **size), "4 大 2 小")
    write("search_价格区间.json",
          gen.search(q="杭州市酒店", min_price=300, max_price=800, **dates, **size),
          "min + max 双向约束")
    write("search_empty.json", gen.search(q="不存在的地方xyz酒店", **dates),
          "空结果 → 触发降级到高德 POI 的路径")

    # --- autocomplete ---
    for q in ("西湖", "成都", "外滩", "亚朵"):
        write(f"autocomplete_{q}.json", gen.autocomplete(q), f"补全「{q}」")

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=None, help="给定则可复现")
    parser.add_argument("--days-ahead", type=int, default=30)
    parser.add_argument("--nights", type=int, default=5)
    parser.add_argument("--cities", nargs="*", default=list(DEFAULT_CITIES))
    args = parser.parse_args()

    check_in = date.today() + timedelta(days=args.days_ahead)
    check_out = check_in + timedelta(days=args.nights)

    written = dump(args.out, args.cities, seed=args.seed,
                   check_in=check_in, check_out=check_out)

    total = sum(p.stat().st_size for p, _ in written)
    print(f"已写出 {len(written)} 个文件到 {args.out}（合计 {total / 1024:,.0f} KB）")
    for path, note in written:
        if not path.name.startswith("search_") or "商圈" not in path.name:
            print(f"  {path.name:28} {path.stat().st_size:>7,} B   {note}")

    note = (
        f"（seed={args.seed}，可复现）" if args.seed is not None
        else "（未固定 seed，房价每次不同）"
    )
    print(f"\n入住 {check_in} / 离店 {check_out} · 共 {args.nights} 晚{note}")

    print("\n各城市基准价（单晚，未加 ±20% 波动）：")
    print("  城市        系数  " + "  ".join(f"{s}★".rjust(6) for s in (2, 3, 4, 5)))
    for city in sorted(args.cities, key=lambda c: CITY_TIER.get(c, DEFAULT_CITY_TIER)):
        tier = CITY_TIER.get(city, DEFAULT_CITY_TIER)
        row = "  ".join(f"¥{STAR_BASE_CNY[s] * tier:5.0f}" for s in (2, 3, 4, 5))
        print(f"  {city:<10}{tier:5.2f}  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
