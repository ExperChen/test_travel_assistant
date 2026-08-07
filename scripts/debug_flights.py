"""航班链路排查：把**原始响应**和**我们解析出来的结果**并排放，一眼看出谁的锅。

    python scripts/debug_flights.py PEK CTU                    # 默认 30 天后出发，玩 4 天
    python scripts/debug_flights.py PEK CTU --gl cn            # 换销售地对照
    python scripts/debug_flights.py 北京 成都 --start-in 60     # 城市名会先走机场补全
    python scripts/debug_flights.py PEK CTU --raw              # 打印原始 JSON 全文
    python scripts/debug_flights.py PEK CTU --return-leg       # 连返程那次查询一起看

每次运行约 1~3 次 SerpAPI 额度（免费版 250 次/月）。

⚠️ **同一航线的价格和航班号会随时间变**——实测隔一小时从 ¥4685/3U 8890 变成
¥1927/TV 9956。要对比就在同一次运行里对比，别拿历史输出比。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from app.agents.flight_agent import looks_like_iata, resolve_airports  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.models.flight import FlightSearchParams  # noqa: E402
from app.tools.registry import close_clients  # noqa: E402
from app.tools.serpapi_flights import flights_search  # noqa: E402

RULE = "─" * 72


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="航班查询排查工具", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("origin", help="出发地：IATA 三字码或城市名")
    p.add_argument("destination", help="目的地：IATA 三字码或城市名")
    p.add_argument("--start-in", type=int, default=30, help="几天后出发")
    p.add_argument("--days", type=int, default=4, help="行程天数")
    p.add_argument(
        "--gl",
        default=None,
        help="销售地。不给则用 settings.serpapi_flights_gl（默认空=不发）",
    )
    p.add_argument("--travel-class", default="economy")
    p.add_argument("--raw", action="store_true", help="打印原始 JSON 全文")
    p.add_argument("--return-leg", action="store_true", help="连返程查询一起跑")
    p.add_argument("--verbose", action="store_true", help="打印结构化日志")
    return p.parse_args()


async def resolve(text: str, role: str) -> str:
    """城市名 → IATA。已经是三字码就直接用，省一次额度。"""
    if looks_like_iata(text):
        return text.upper()
    options = await resolve_airports(text)
    if not options:
        raise SystemExit(f"❌ {role}「{text}」没解析出机场")
    print(f"🛫 {role}「{text}」→ {len(options)} 个机场")
    for a in options:
        print(f"     {a.id}  {a.name}  {a.distance}")
    print(f"   取第一个：{options[0].id}\n")
    return options[0].id


def raw_query(params: FlightSearchParams, gl: str) -> dict:
    """直连 SerpAPI，绕开 Tool 层的裁剪与缓存。"""
    query = params.to_serpapi(
        currency=settings.default_currency, hl=settings.default_hl, gl=gl
    )
    response = httpx.get(
        settings.serpapi_base_url, params={**query, "api_key": settings.serpapi_key}, timeout=60.0
    )
    response.raise_for_status()
    payload = response.json()
    # search_metadata 里的回链可能带 key
    for field in ("json_endpoint", "raw_html_file", "prettify_html_file"):
        payload.get("search_metadata", {}).pop(field, None)
    payload.get("search_parameters", {}).pop("api_key", None)
    return payload


def show_raw(payload: dict, dep: str, arr: str) -> None:
    print(f"{RULE}\n📡 原始响应　顶层字段：{', '.join(payload)}\n")
    for group in ("best_flights", "other_flights"):
        items = payload.get(group) or []
        print(f"  {group}（{len(items)} 条）")
        for i, it in enumerate(items):
            legs = it.get("flights") or []
            route_ok = (
                legs
                and legs[0]["departure_airport"]["id"] == dep
                and legs[-1]["arrival_airport"]["id"] == arr
            )
            flag = "" if route_ok else "  ⚠️ 航线不符，会被丢弃"
            price = f"¥{it['price']}" if it.get("price") else "价格缺失"
            print(f"    [{i}] {price:>10}  {len(legs)} 段  {it.get('type', '')}{flag}")
            for leg in legs:
                d, a = leg["departure_airport"], leg["arrival_airport"]
                print(f"          {leg.get('flight_number', '?'):9} {leg.get('airline', ''):8}"
                      f" {d['id']} {d['time']} → {a['id']} {a['time']}")
        print()

    if insights := payload.get("price_insights"):
        print(f"  price_insights: 最低 ¥{insights.get('lowest_price')}"
              f" · 水位 {insights.get('price_level')}"
              f" · 常见区间 {insights.get('typical_price_range')}\n")


async def show_parsed(params: FlightSearchParams, gl: str) -> str:
    """走我们自己的代码路径。和上面的原始响应对不上就是解析层的问题。"""
    print(f"{RULE}\n🔍 我们解析出来的\n")
    results = await flights_search(
        departure_id=params.departure_airport_id or "",
        arrival_id=params.arrival_airport_id or "",
        outbound_date=params.departure_date,  # type: ignore[arg-type]
        return_date=params.return_date,
        is_round_trip=True,
        passengers=params.passengers,
        travel_class=params.travel_class,
        gl=gl,
    )
    print(f"  best_flights {len(results.best_flights)} 条"
          f" · other_flights {len(results.other_flights)} 条"
          f"　（航线不符的已被丢弃）\n")
    token = ""
    for i, it in enumerate(results.all()):
        legs = " | ".join(
            f"{f.flight_number} {f.departure_airport.id} {f.departure_airport.time}"
            f" → {f.arrival_airport.id} {f.arrival_airport.time}"
            for f in it.flights
        )
        price = f"¥{it.price:.0f}" if it.price is not None else "价格缺失"
        stops = "直飞" if it.stops == 0 else f"中转{it.stops}次"
        print(f"  [{i}] {price:>10} · {stops}　{legs}")
        if not token:
            token = it.departure_token
    print()
    return token


async def show_return_leg(params: FlightSearchParams, token: str, gl: str) -> None:
    """往返搜索的 best_flights 里**只有去程**，返程要拿 departure_token 再查一次。"""
    print(f"{RULE}\n🔁 返程（带 departure_token 的第二次查询）\n")
    if not token:
        print("  拿不到 departure_token，跳过\n")
        return
    results = await flights_search(
        departure_id=params.departure_airport_id or "",
        arrival_id=params.arrival_airport_id or "",
        outbound_date=params.departure_date,  # type: ignore[arg-type]
        return_date=params.return_date,
        is_round_trip=True,
        passengers=params.passengers,
        travel_class=params.travel_class,
        departure_token=token,
        gl=gl,
    )
    for i, it in enumerate(results.all()):
        legs = " | ".join(
            f"{f.flight_number} {f.departure_airport.id} {f.departure_airport.time}"
            f" → {f.arrival_airport.id} {f.arrival_airport.time}"
            for f in it.flights
        )
        # 价格不会翻倍——它自始至终是同一个往返总价
        print(f"  [{i}] ¥{it.price:.0f} 　{legs}" if it.price else f"  [{i}] 价格缺失　{legs}")
    print()


async def main() -> int:
    args = parse_args()
    setup_logging("DEBUG" if args.verbose else "WARNING", json_output=False)
    settings.require("serpapi_key")

    outbound = date.today() + timedelta(days=args.start_in)
    params = FlightSearchParams(
        departure_airport_id=await resolve(args.origin, "出发地"),
        arrival_airport_id=await resolve(args.destination, "目的地"),
        departure_date=outbound,
        return_date=outbound + timedelta(days=max(args.days, 1)),
        is_round_trip=True,
        passengers=1,
        travel_class=args.travel_class,
    )
    gl = args.gl if args.gl is not None else settings.serpapi_flights_gl

    print(RULE)
    print(f"{params.departure_airport_id} → {params.arrival_airport_id}　"
          f"{params.departure_date} 至 {params.return_date}　{args.travel_class}")
    print(f"gl = {gl or '（不发）'}")
    print("发出去的参数：", json.dumps(
        params.to_serpapi(currency=settings.default_currency, hl=settings.default_hl, gl=gl),
        ensure_ascii=False))

    payload = raw_query(params, gl)
    if args.raw:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    show_raw(payload, params.departure_airport_id or "", params.arrival_airport_id or "")

    try:
        token = await show_parsed(params, gl)
        if args.return_leg:
            await show_return_leg(params, token, gl)
    finally:
        await close_clients()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
