"""高德「路线规划 2.0」(v5) 原始响应转储 —— 不解析、不裁剪、不改动。

    python scripts/raw_amap_route.py                       # 默认深圳宝安机场 → 华强北旅舍
    python scripts/raw_amap_route.py --origin 113.814920,22.624770 \
                                     --destination 114.082260,22.546420
    python scripts/raw_amap_route.py --mode driving        # 只打一种
    python scripts/raw_amap_route.py --save out/           # 另存原始字节

文档：<https://developer.amap.com/api/webservice/guide/api/newroute>

⚠️ **与项目主链路无关**。`app/tools/amap_route.py` 用的是 **v3**
   （`/v3/direction/driving` 等），本脚本打的是 **v5**（`/v5/direction/*`）。
   写它是为了看清 v5 到底返回什么，以便评估要不要迁移——所以刻意
   **不经过任何 Pydantic 模型**，直接把 httpx 拿到的 JSON 原样吐出来。

⚠️ 会产生真实调用（每种方式 1 次）。响应里 `polyline` 可能上万字符，
   默认 `show_fields=` 全开就是要看"原样"；嫌长用 `--show-fields cost,duration`。
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

BASE = "https://restapi.amap.com"

# v5 的五个端点（文档 §路线规划2.0）。transit 需要 city1/city2。
ENDPOINTS: dict[str, str] = {
    "driving": "/v5/direction/driving",
    "walking": "/v5/direction/walking",
    "bicycling": "/v5/direction/bicycling",
    "electrobike": "/v5/direction/electrobike",
    "transit": "/v5/direction/transit/integrated",
}

ALL_SHOW_FIELDS = "cost,duration,tolls,tmcs,navi,cities,polyline"
"""v5 的可选字段全开。**不传 show_fields 时 v5 连时长都不返回**——
这是它和 v3 最大的差别，也是最容易踩的坑。"""

# 默认起终点：上一轮排查过的那条深圳路线（GCJ-02）
DEFAULT_ORIGIN = "113.814920,22.624770"       # 深圳宝安国际机场
DEFAULT_DESTINATION = "114.082260,22.546420"  # 栖游太空舱青年旅舍(中心公园华强北店)
DEFAULT_CITY = "0755"                          # 深圳 citycode


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--origin", default=DEFAULT_ORIGIN, help="起点 经度,纬度（GCJ-02）")
    p.add_argument("--destination", default=DEFAULT_DESTINATION, help="终点 经度,纬度")
    p.add_argument("--mode", choices=[*ENDPOINTS, "all"], default="all",
                   help="出行方式，默认全部")
    p.add_argument("--city", default=DEFAULT_CITY, help="transit 用的 citycode")
    p.add_argument("--show-fields", default=ALL_SHOW_FIELDS,
                   help=f"可选返回字段，默认全开：{ALL_SHOW_FIELDS}")
    p.add_argument("--strategy", default="", help="路线策略，留空用接口默认")
    p.add_argument("--save", default="", help="把原始响应另存到该目录")
    return p.parse_args()


async def call(client: httpx.AsyncClient, mode: str, args: argparse.Namespace):
    """打一次 v5，返回 (最终URL不含key, HTTP状态, 原始文本)。"""
    params: dict[str, str] = {
        "key": settings.amap_key,
        "origin": args.origin,
        "destination": args.destination,
        "show_fields": args.show_fields,
    }
    if mode == "transit":
        # 跨城要给 city1/city2；同城两个填一样
        params["city1"] = args.city
        params["city2"] = args.city
    if args.strategy:
        params["strategy"] = args.strategy

    response = await client.get(f"{BASE}{ENDPOINTS[mode]}", params=params)
    # 回显 URL 时把 key 摘掉——这个输出很可能被贴进 issue 或聊天记录
    shown = str(response.url).replace(settings.amap_key, "***")
    return shown, response.status_code, response.text


async def main() -> int:
    if not settings.amap_key:
        print("缺少 AMAP_KEY，请先在 .env 里配置。", file=sys.stderr)
        return 1

    args = parse_args()
    modes = list(ENDPOINTS) if args.mode == "all" else [args.mode]
    out_dir = Path(args.save) if args.save else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"起点 {args.origin}")
    print(f"终点 {args.destination}")
    print(f"show_fields={args.show_fields}")

    async with httpx.AsyncClient(timeout=20.0) as client:
        for mode in modes:
            print("\n" + "=" * 78)
            print(f"  {mode.upper()}　{ENDPOINTS[mode]}")
            print("=" * 78)
            try:
                url, status, text = await call(client, mode, args)
            except Exception as exc:  # noqa: BLE001 —— 转储脚本要把任何失败都报出来
                print(f"请求失败：{type(exc).__name__}: {exc}")
                continue

            print(f"GET {url}")
            print(f"HTTP {status}　{len(text)} 字节\n")

            # **原样输出**：能解析成 JSON 就缩进打印（内容一字不改），
            # 解析不了就打原文——绝不做任何字段挑选或裁剪
            try:
                print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                print(text)

            if out_dir:
                # 落盘存的是**原始字节**，连缩进都不加，保证与服务端返回逐字节一致
                path = out_dir / f"v5_{mode}.json"
                path.write_text(text, encoding="utf-8")
                print(f"\n[已存 {path}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
