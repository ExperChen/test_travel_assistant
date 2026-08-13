"""高德「公交路线规划 2.0」(v5) 原始响应转储 —— 不解析、不裁剪、不改动。

    python scripts/raw_amap_transit.py                     # 默认深圳宝安机场 → 华强北旅舍
    python scripts/raw_amap_transit.py --strategy 2         # 只看「最少换乘」
    python scripts/raw_amap_transit.py --strategy all       # 九种策略各打一次，横向对比
    python scripts/raw_amap_transit.py --date 2026-09-20 --time 9-30
    python scripts/raw_amap_transit.py --night --alternatives 10
    python scripts/raw_amap_transit.py --save out/          # 另存原始字节

文档：<https://developer.amap.com/api/webservice/guide/api/newroute#t8>
端点：`https://restapi.amap.com/v5/direction/transit/integrated`

⚠️ **与项目主链路无关**。`app/tools/amap_route.py` 用的是 **v3**
   （`/v3/direction/transit/integrated`），本脚本打的是 **v5**。
   写它是为了看清 v5 公交到底返回什么——所以刻意**不经过任何 Pydantic 模型**，
   直接把 httpx 拿到的 JSON 原样吐出来。

⚠️ 会产生真实调用（每种策略 1 次，`--strategy all` 就是 9 次）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

ENDPOINT = "https://restapi.amap.com/v5/direction/transit/integrated"

STRATEGIES: dict[int, str] = {
    0: "推荐（默认）",
    1: "最经济",
    2: "最少换乘",
    3: "最少步行",
    4: "最舒适",
    5: "不乘地铁",
    6: "地铁图模式",
    7: "地铁优先",
    8: "时间最短",
}
"""文档 §公交路线规划 的 strategy 取值。v3 只有 0~5，v5 多了 6/7/8。"""

ALL_SHOW_FIELDS = "cost,navi,polyline"
"""v5 的可选字段。**不传 show_fields 就没有时长和票价**——
`duration` / `transit_fee` 都挂在 `cost` 下，这是 v5 和 v3 最大的差别。"""

# 默认起终点：前几轮一直在排查的那条深圳路线（GCJ-02）
DEFAULT_ORIGIN = "113.814920,22.624770"       # 深圳宝安国际机场（入口）
DEFAULT_DESTINATION = "114.082260,22.546420"  # 栖游太空舱青年旅舍(中心公园华强北店)
DEFAULT_CITY = "0755"                          # 深圳


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--origin", default=DEFAULT_ORIGIN, help="起点 经度,纬度（GCJ-02）")
    p.add_argument("--destination", default=DEFAULT_DESTINATION, help="终点 经度,纬度")
    p.add_argument("--city1", default=DEFAULT_CITY, help="起点 citycode（必填）")
    p.add_argument("--city2", default="", help="终点 citycode，跨城才需要；留空同 city1")
    p.add_argument("--strategy", default="0",
                   help="0~8 或 all（九种都打一次）。" +
                        "　".join(f"{k}={v}" for k, v in STRATEGIES.items()))
    p.add_argument("--alternatives", type=int, default=5,
                   help="返回几套方案，1~10，默认 5")
    p.add_argument("--night", action="store_true", help="考虑夜班车（nightflag=1）")
    p.add_argument("--date", default="", help="出发日期，如 2026-09-20")
    p.add_argument("--time", default="", help="出发时刻，格式 9-30（九点半）")
    p.add_argument("--show-fields", default=ALL_SHOW_FIELDS,
                   help=f"可选返回字段，默认 {ALL_SHOW_FIELDS}")
    p.add_argument("--save", default="", help="把原始响应另存到该目录")
    p.add_argument("--summary", action="store_true",
                   help="每套方案额外打一行摘要（原始 JSON 照常输出）")
    return p.parse_args()


async def call(client: httpx.AsyncClient, strategy: int, args: argparse.Namespace):
    """打一次 v5 公交，返回 (URL不含key, HTTP状态, 原始文本)。"""
    params: dict[str, str] = {
        "key": settings.amap_key,
        "origin": args.origin,
        "destination": args.destination,
        "city1": args.city1,
        "city2": args.city2 or args.city1,
        "strategy": str(strategy),
        "AlternativeRoute": str(args.alternatives),
        "show_fields": args.show_fields,
    }
    if args.night:
        params["nightflag"] = "1"
    # date/time 只在都给了才有意义——只给一个高德会忽略
    if args.date and args.time:
        params["date"] = args.date
        params["time"] = args.time

    response = await client.get(ENDPOINT, params=params)
    # 回显 URL 时摘掉 key——这类输出很容易被贴进 issue 或聊天记录
    shown = str(response.url).replace(settings.amap_key, "***")
    return shown, response.status_code, response.text


def summarize(text: str) -> list[str]:
    """从原始响应里读几个关键数字。**只读不改**，纯粹为了肉眼比对策略差异。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ["（响应不是 JSON，无法摘要）"]
    if data.get("status") != "1":
        return [f"（status={data.get('status')} info={data.get('info')}）"]

    route = data.get("route") or {}
    lines = []
    for i, transit in enumerate(route.get("transits") or [], 1):
        cost = transit.get("cost") or {}
        # 换乘线路名：从 segments 里把公交/地铁段的名字捡出来
        names = []
        for seg in transit.get("segments") or []:
            for line in ((seg.get("bus") or {}).get("buslines") or []):
                if name := line.get("name"):
                    names.append(name.split("(")[0])
        walk = sum(
            int((seg.get("walking") or {}).get("distance") or 0)
            for seg in transit.get("segments") or []
        )
        duration = cost.get("duration")
        minutes = f"{int(duration) // 60}分" if duration else "—"
        lines.append(
            f"  方案{i}: {minutes:>6}  ¥{cost.get('transit_fee', '—'):>5}  "
            f"步行{walk:>5}m  {len(names)}段  {' → '.join(names[:4])}"
        )
    return lines or ["  （没有返回任何方案）"]


async def main() -> int:
    if not settings.amap_key:
        print("缺少 AMAP_KEY，请先在 .env 里配置。", file=sys.stderr)
        return 1

    args = parse_args()
    if args.strategy == "all":
        strategies = list(STRATEGIES)
    else:
        try:
            strategies = [int(args.strategy)]
        except ValueError:
            print(f"--strategy 只能是 0~8 或 all，收到 {args.strategy!r}", file=sys.stderr)
            return 1

    out_dir = Path(args.save) if args.save else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"起点 {args.origin}　city1={args.city1}")
    print(f"终点 {args.destination}　city2={args.city2 or args.city1}")
    print(f"AlternativeRoute={args.alternatives}　show_fields={args.show_fields}"
          + ("　nightflag=1" if args.night else "")
          + (f"　date={args.date} time={args.time}" if args.date and args.time else ""))

    async with httpx.AsyncClient(timeout=25.0) as client:
        for strategy in strategies:
            print("\n" + "=" * 78)
            print(f"  strategy={strategy}　{STRATEGIES.get(strategy, '?')}")
            print("=" * 78)
            try:
                url, status, text = await call(client, strategy, args)
            except Exception as exc:  # noqa: BLE001 —— 转储脚本要把任何失败都报出来
                print(f"请求失败：{type(exc).__name__}: {exc}")
                continue

            print(f"GET {url}")
            print(f"HTTP {status}　{len(text)} 字节")
            if args.summary:
                print("\n".join(summarize(text)))
            print()

            # **原样输出**：能解析成 JSON 就缩进打印（内容一字不改），
            # 解析不了就打原文——绝不做任何字段挑选或裁剪
            try:
                print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                print(text)

            if out_dir:
                # 落盘存的是**原始字节**，连缩进都不加，与服务端返回逐字节一致
                path = out_dir / f"v5_transit_s{strategy}.json"
                path.write_text(text, encoding="utf-8")
                print(f"\n[已存 {path}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
