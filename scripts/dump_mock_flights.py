"""把机票模拟数据导出成 JSON 文件。

    uv run python scripts/dump_mock_flights.py            # 默认写 fixtures/serpapi/flights/
    uv run python scripts/dump_mock_flights.py --seed 42  # 可复现
    uv run python scripts/dump_mock_flights.py --routes PEK:CTU SHA:CAN

导出的文件可以直接当录像种子用（见
`docs/architecture/serpapi-usage-and-mocking.md` §5「建议的落点」）。

⚠️ 票价默认**每次导出都不同**（真随机，贴近真实）。要生成可复现的样本传 `--seed`。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.providers.mock import FlightMockGenerator  # noqa: E402
from app.providers.mock.airports import by_iata, distance_km  # noqa: E402

# (出发, 到达, 是否同时导出返程, 用途)
# 返程结构与去程同构，只对有代表性的几条导出，避免文件量翻倍。
DEFAULT_ROUTES: tuple[tuple[str, str, bool, str], ...] = (
    # ---- 干线：一线互飞，票价与航司最密集 ----
    ("PEK", "PVG", True, "京沪，全国最繁忙"),
    ("PEK", "CAN", True, "京广"),
    ("PVG", "CAN", False, "沪广"),
    ("PEK", "SZX", False, "京深"),
    ("SHA", "SZX", False, "虹桥→深圳"),
    ("PEK", "CTU", True, "京蓉，成都两场之一"),
    ("SHA", "CAN", False, "沪广（虹桥出发）"),
    # ---- 一城多场：验证同城换机场兜底 ----
    ("PKX", "CTU", False, "大兴出发"),
    ("SHA", "TFU", False, "虹桥→天府（成都另一场）"),
    ("PEK", "SHA", False, "首都→虹桥"),
    # ---- 超短途：不该出现中转 ----
    ("PEK", "TSN", True, "京津，125 km"),
    ("SHA", "HGH", False, "沪杭"),
    ("CAN", "SZX", False, "广深"),
    ("CTU", "CKG", False, "成渝"),
    # ---- 超长途：宽体机 + 会出现中转 ----
    ("PEK", "URC", True, "京乌，2430 km"),
    ("CAN", "HRB", False, "广哈，跨越南北"),
    ("SZX", "HRB", False, "深哈"),
    ("PVG", "URC", False, "沪乌"),
    ("HRB", "SYX", True, "哈三，3412 km，全国最长干线之一"),
    # ---- 高原航线 ----
    ("CTU", "LXA", True, "成都→拉萨"),
    ("XIY", "LXA", False, "西安→拉萨"),
    # ---- 旅游航线 ----
    ("PEK", "SYX", False, "京三"),
    ("SHA", "SYX", False, "沪三"),
    ("HGH", "SYX", False, "杭三"),
    # ---- 区域航线：中小机场 ----
    ("XIY", "CGO", False, "西安→郑州"),
    ("KMG", "NNG", False, "昆明→南宁"),
    ("WUH", "CSX", False, "武汉→长沙"),
    ("SHE", "DLC", False, "沈阳→大连"),
)

BEST_COUNT = 5
OTHER_COUNT = 8
"""每份响应里的行程数。

真实 Google Flights 一次返回十几条，之前只生成 3+3 显得单薄。
注意下游 `MAX_CANDIDATES_PER_GROUP=3` 只取前 3 条——多出来的部分
不改变程序行为，纯粹是让 fixture 更接近真实响应体。
"""

DEFAULT_OUT = ROOT / "fixtures" / "serpapi" / "flights"


def dump(out_dir: Path, routes, *, seed: int | None, outbound: date, ret: date) -> list[Path]:
    gen = FlightMockGenerator(seed=seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write(name: str, payload: dict) -> None:
        path = out_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(path)

    # --- autocomplete：一城多场的三个 + 单场的几个 + 查不到的 ---
    for city in ("北京", "上海", "成都", "杭州", "西安", "乌鲁木齐"):
        write(f"autocomplete_{city}.json", gen.autocomplete(city))
    write("autocomplete_empty.json", gen.autocomplete("不存在的地方xyz"))

    # --- search：往返去程（+ 有代表性的几条导出返程）---
    for dep, arr, with_return, _note in routes:
        out = gen.search(
            departure_id=dep, arrival_id=arr,
            outbound_date=outbound, return_date=ret, trip_type=1,
            best_count=BEST_COUNT, other_count=OTHER_COUNT,
        )
        write(f"search_{dep}_{arr}_roundtrip.json", out)

        if with_return and out["best_flights"]:
            token = out["best_flights"][0]["departure_token"]
            back = gen.search(
                departure_id=dep, arrival_id=arr,
                outbound_date=outbound, return_date=ret,
                trip_type=1, departure_token=token,
                best_count=BEST_COUNT, other_count=OTHER_COUNT,
            )
            write(f"search_{dep}_{arr}_return.json", back)

    # --- 边界与其它舱位 ---
    write("search_empty.json", gen.search(
        departure_id="PEK", arrival_id="XXX", outbound_date=outbound, trip_type=2))
    write("search_PEK_CTU_oneway.json", gen.search(
        departure_id="PEK", arrival_id="CTU", outbound_date=outbound, trip_type=2,
        best_count=BEST_COUNT, other_count=OTHER_COUNT))
    for code, name in ((2, "超经"), (3, "商务"), (4, "头等")):
        write(f"search_PEK_PVG_{name}.json", gen.search(
            departure_id="PEK", arrival_id="PVG", outbound_date=outbound,
            return_date=ret, trip_type=1, travel_class=code,
            best_count=3, other_count=3))

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=None, help="给定则可复现")
    parser.add_argument("--days-ahead", type=int, default=30)
    parser.add_argument("--nights", type=int, default=5)
    parser.add_argument(
        "--routes", nargs="*", metavar="DEP:ARR",
        help="形如 PEK:CTU；不给则用内置的 6 条代表性航线",
    )
    args = parser.parse_args()

    routes = DEFAULT_ROUTES
    if args.routes:
        routes = []
        for item in args.routes:
            dep, _, arr = item.partition(":")
            if by_iata(dep) is None or by_iata(arr) is None:
                print(f"跳过未知航线 {item}（机场表里没有）", file=sys.stderr)
                continue
            routes.append((dep.upper(), arr.upper(), True, "命令行指定"))
        if not routes:
            print("没有有效航线", file=sys.stderr)
            return 1

    outbound = date.today() + timedelta(days=args.days_ahead)
    ret = outbound + timedelta(days=args.nights)

    written = dump(args.out, routes, seed=args.seed, outbound=outbound, ret=ret)

    total = sum(p.stat().st_size for p in written)
    print(f"已写出 {len(written)} 个文件到 {args.out}（合计 {total / 1024:,.0f} KB）")

    note = (
        f"（seed={args.seed}，可复现）"
        if args.seed is not None
        else "（未固定 seed，票价每次不同）"
    )
    print(f"出发 {outbound} / 返程 {ret}{note}\n")

    from app.providers.mock.flights import BASE_FARE_CNY, PER_KM_CNY

    print("各航线基准价（单程经济舱，未加 ±20% 波动）：")
    print(f"  {'航线':<12}{'距离':>8}{'基准价':>9}  {'±20% 区间':<20}用途")
    for dep, arr, _with_return, note_text in sorted(
        routes, key=lambda r: distance_km(by_iata(r[0]), by_iata(r[1]))
    ):
        km = distance_km(by_iata(dep), by_iata(arr))
        base = BASE_FARE_CNY + PER_KM_CNY * km
        span = f"¥{base * 0.8:.0f} ~ ¥{base * 1.2:.0f}"
        print(f"  {dep}→{arr:<8}{km:7.0f}km{base:8.0f}   {span:<20}{note_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
