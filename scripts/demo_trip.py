"""跑完整链路并打印行程：机票 → 酒店 → 景点 → 逐日路线 → 说明。

    python scripts/demo_trip.py 成都                  # 完整链路（约 5 次 SerpAPI）
    python scripts/demo_trip.py 成都 --dry-run        # 全假数据，零额度，只看输出长什么样
    python scripts/demo_trip.py 成都 --interactive    # 在终端里手动回答机场/酒店的选择
    python scripts/demo_trip.py 杭州 --days 5 --must 西湖 --budget 800

一句话需求（代替 city 和各项参数）：

    python scripts/demo_trip.py --prompt "9月5号从北京去成都玩5天，预算600一晚，想看大熊猫"
    python scripts/demo_trip.py --prompt "下周三从上海飞西安待四天" --parse-only  # 只看解析结果

⚠️ 非 --dry-run 时会消耗 SerpAPI 额度（免费版 250 次/月，每次规划约 5 次）。
   相同参数 1 小时内命中缓存不再扣额度，所以反复调同一条行程是安全的。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 中文 Windows 控制台默认 GBK，打印 ✈️/─ 会直接 UnicodeEncodeError 崩掉
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
# 管道进来的中文同样按 GBK 解码会变乱码；真控制台不用动，Windows 下 Python
# 走的是 console API，本来就是 Unicode 安全的，改了反而可能出问题
if hasattr(sys.stdin, "reconfigure") and not sys.stdin.isatty():
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

from app.agents.prompt_parser import parse_prompt  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.graph.builder import TripRunner  # noqa: E402
from app.graph.state import to_plan  # noqa: E402
from app.models.hotel import price_text  # noqa: E402
from app.models.trip import TripRequest  # noqa: E402
from app.tools.registry import close_clients  # noqa: E402

RULE = "─" * 64


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="完整行程规划 demo", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("city", nargs="?", help="目的地城市（限中国大陆），如 成都")
    p.add_argument(
        "--prompt",
        nargs="?",
        const="",  # 只写 --prompt 不给值 → 运行后再让用户敲，省掉 shell 引号转义
        default=None,
        help='一句话需求，代替 city 及各项参数，如 "9月5号从北京去成都玩5天，预算600一晚"；'
        "不带值则启动后再输入",
    )
    p.add_argument(
        "--parse-only", action="store_true", help="只解析 --prompt 并打印，不真的去规划"
    )
    p.add_argument(
        "--from", dest="origin", default="PEK", help="出发地；填 IATA 三字码可省一次额度"
    )
    p.add_argument("--days", type=int, default=4, help="行程天数")
    p.add_argument("--start-in", type=int, default=30, help="几天后出发")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--budget", type=int, default=None, help="每晚预算上限（CNY）")
    p.add_argument("--pace", default="standard", choices=["relaxed", "standard", "packed"])
    p.add_argument("--transport", default="transit", choices=["transit", "driving", "walking"])
    p.add_argument("--must", action="append", default=[], help="必去景点，可重复")
    p.add_argument("--avoid", action="append", default=[], help="要排除的景点，可重复")
    p.add_argument("--interactive", action="store_true", help="手动回答中断问题（默认自动选最优）")
    p.add_argument("--dry-run", action="store_true", help="全部用假数据，不发任何真实请求")
    p.add_argument("--verbose", action="store_true", help="打印结构化日志")
    args = p.parse_args()
    if not args.city and args.prompt is None:
        p.error("要么给个目的地城市，要么用 --prompt 说一句话")
    return args


EXAMPLE_PROMPTS = (
    "9月5号从北京去成都玩5天，预算600一晚，想看大熊猫",
    "国庆假期带家人从深圳去西安，玩一周，商务舱，想去兵马俑",
    "下周三从上海飞杭州待四天，节奏悠闲，自驾",
)


async def _ask_prompt() -> str:
    """在程序里读需求，而不是从命令行参数——中文在 shell 里转义太容易出岔子。"""
    print(RULE)
    print("说说你的行程需求，比如：")
    for example in EXAMPLE_PROMPTS:
        print(f"    {example}")
    print(RULE)
    while True:
        text = (await asyncio.to_thread(input, "🗣  > ")).strip()
        if text:
            return text
        print("   （说点什么吧，Ctrl+C 退出）")


def _pad(text: str, width: int) -> str:
    """按终端列宽补空格——中文占两列，用 len() 对齐会歪。"""
    used = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(width - used, 1)


def show_draft(draft) -> None:
    """把解析结果摊开给用户看：每个值是它说的、我们推的，还是默认的。"""
    origin_tag = {"prompt": "原话", "derived": "推算", "default": "默认"}
    print(RULE)
    print(f"🗣  {draft.prompt}")
    print(f"\n🧾 解析结果{'（模型不可用，走的规则抽取）' if draft.degraded else ''}")
    for f in draft.fields:
        note = f"  {f.note}" if f.note else ""
        print(f"    {_pad(f.label, 10)}{_pad(f.value, 22)}[{origin_tag[f.origin]}]{note}")
    if draft.missing:
        print(f"\n❗ 还缺：{'、'.join(draft.missing)}")
    for q in draft.questions:
        print(f"    ? {q}")
    print(RULE)


def build_request(args: argparse.Namespace) -> TripRequest:
    outbound = date.today() + timedelta(days=args.start_in)
    return TripRequest(
        departure_city=args.origin,
        destination_city=args.city,
        outbound_date=outbound,
        return_date=outbound + timedelta(days=max(args.days, 1)),
        adults=args.adults,
        budget_per_night=args.budget,
        pace=args.pace,
        transport=args.transport,
        must_visit=args.must,
        avoid=args.avoid,
        auto_select=not args.interactive,
    )


def fake_upstreams(request: TripRequest):
    """--dry-run：复用测试里的假响应，零额度跑通全链路。

    调的是测试模块——这是个开发脚本，图的就是不用再维护第二套假数据。
    返回已经启动的 respx router，调用方负责 `.stop()`。
    """
    import respx

    from app.tests.e2e._mocks import mock_downstream, mock_flights, outbound_payload

    # 模型调用没法用 respx 假装（那是另一个服务商的真实端点），直接关掉走模板，
    # 否则"不发任何真实请求"就是句空话，还要白等一次连接超时
    settings.llm_enabled = False

    router = respx.mock
    router.start()
    mock_downstream()
    mock_flights(
        request.outbound_date,
        request.return_date,
        outbound_result=outbound_payload(request.outbound_date, count=2),
    )
    return router


# ---------------------------------------------------------------- 渲染
def show_flights(plan) -> None:
    branch = plan.flights
    if not branch or not branch.selected:
        print("\n✈️  航班：无")
        return
    it = branch.selected
    # 标明"往返总价"：SerpAPI 往返搜索给的 price 覆盖两个方向，但同一条目里的
    # flights 只有去程航段。不写清楚，看起来就像单独这一班要 ¥2300
    label = "往返总价" if branch.params.is_round_trip else "单程"
    price = f"{label} ¥{it.price:.0f}" if it.price is not None else "价格暂无"
    stops = "直飞" if it.stops == 0 else f"中转 {it.stops} 次"
    print(f"\n✈️  航班　{price} · {stops}")
    print("    去程")
    for leg in it.flights:
        dep, arr = leg.departure_airport, leg.arrival_airport
        print(f"    {leg.flight_number:<8} {dep.id} {dep.time} → {arr.id} {arr.time}")
    if branch.arrive_at:
        print(f"    落地 {branch.arrive_at:%m-%d %H:%M}", end="")
    if branch.depart_at:
        print(f"　返程起飞 {branch.depart_at:%m-%d %H:%M}", end="")
    print()


def hotel_price(hotel, nights: int) -> str:
    if hotel.price_unavailable:
        return "价格暂无（地图数据）"
    return price_text(hotel.total_price, hotel.nightly_price, nights)


def show_hotel(plan) -> None:
    branch = plan.hotel
    hotel = branch.selected if branch else None
    if hotel is None:
        print("\n🏨 酒店：无")
        return

    nights = plan.request.nights
    print(f"\n🏨 酒店候选（{len(branch.candidates)} 家 · {nights} 晚，已按综合评分排序）")
    for i, c in enumerate(branch.candidates, 1):
        mark = "★" if i == (branch.selected_index or 0) + 1 else " "
        stars = f" · {c.hotel_class}星" if c.hotel_class else ""
        rating = f" · {c.overall_rating:.1f}分" if c.overall_rating else ""
        ad = " · 广告" if c.is_ad else ""
        print(f"  {mark} {i}. {_pad(c.name, 40)}{_pad(hotel_price(c, nights), 26)}"
              f"{rating}{stars}{ad}")

        # Google Hotels 不返回门牌号地址，只给周边地标 + 到那里的耗时。
        # 对"这地方方不方便"这个真问题，它比一串街道号更管用。
        # address 只有高德降级来源才有。
        where = c.address or "　".join(p.label for p in c.nearby_places[:2]) or "位置信息暂缺"
        commute = (
            f"距景点集中区 {c.commute_to_centroid_min} 分钟"
            if c.commute_to_centroid_min is not None
            else "通勤时长未知"
        )
        print(f"     {_pad(where, 45)}{commute}")
    print("  ★ = 已选中")


def show_itinerary(plan) -> None:
    itinerary = plan.itinerary
    if not itinerary:
        print("\n🗓  行程：未生成")
        return

    for day in itinerary.days:
        print(f"\n🗓  第 {day.day_index} 天　{day.day}")
        if not day.items:
            print("    （航班时刻所限，这天没有可用游览时间）")
            continue
        legs = {leg.to_ref: leg for leg in day.legs}
        # 只列顺序，不报钟点。排期算法内部照样算精确时刻（要卡营业时间和航班
        # 窗口），但印出来的"09:20-11:20"精确得像承诺，实际路上一堵就全错位。
        # 门票同理不显示：高德极少返回 cost，多数景点只能印「门票?」，纯噪音。
        # 两者的数据都还在 DayItem 里，接口照常返回。
        order = 0
        for item in day.items:
            if leg := legs.get(item.ref_id):
                detail = f" · {leg.detail}" if leg.detail else ""
                print(f"      ↓ {leg.mode} {leg.duration_min} 分钟{detail}")
            icon = {"airport": "✈️ ", "hotel": "🏨", "attraction": "📍", "meal": "🍽 "}[item.kind]
            if item.kind == "attraction":
                order += 1
                print(f"    {icon} {order}. {item.name}")
            else:
                print(f"    {icon} {item.name}")

    if itinerary.unscheduled:
        names = "、".join(a.name for a in itinerary.unscheduled[:8])
        print(f"\n💤 时间没排开的备选：{names}")


def show_costs(plan) -> None:
    """只算机票和住宿。市内交通金额小、误差大，混进总价反而拉低可信度。"""
    costs = plan.costs
    flight = f"机票 ¥{costs.flight_cny:.0f}" if costs.flight_cny is not None else "机票（价格暂无）"
    if costs.hotel_cny is not None:
        hotel = f"住宿 ¥{costs.hotel_cny:.0f}"
        if costs.nightly_cny and costs.nights:
            hotel += f"（{costs.nights} 晚 × ¥{costs.nightly_cny:.0f}）"
    else:
        hotel = "住宿（价格暂无）"

    line = f"\n💰 预估花费　{flight} + {hotel}"
    if (total := costs.total_cny) is not None:
        line += f" = ¥{total:.0f}"
    else:
        # 把缺失当 0 算，总价就成了谎报
        line += f"　（{'、'.join(costs.missing)}价格缺失，无法合计）"
    print(line)

    if plan.itinerary:
        print(f"📊 行程强度　全程通勤 {plan.itinerary.total_commute_min} 分钟")


def show_tail(plan) -> None:
    if plan.summary:
        print(f"\n{RULE}\n{plan.summary}")
    if plan.warnings:
        print(f"\n{RULE}")
        for w in plan.warnings:
            print(f"⚠️  {w.code}: {w.message}")
    q = plan.quota
    print(
        f"\n📈 配额　SerpAPI {q.serpapi} 次 · 高德 {q.amap} 次 · "
        f"LLM {q.llm} 次 · 缓存命中 {q.cache_hits} 次"
    )


async def ask_user(question) -> str:
    print(f"\n❓ {question.title}")
    for i, option in enumerate(question.options, 1):
        # 机场选项的 key 就是三字码，而 label 已经以 [CTU] 开头——再印一遍
        # 就成了「[CTU] [CTU] 成都双流国际机场」
        prefix = "" if option.label.startswith(f"[{option.key}]") else f"[{option.key}] "
        print(f"    {i}. {prefix}{option.label}")
    try:
        raw = await asyncio.to_thread(input, f"   选择（回车用默认 {question.default}）> ")
    except EOFError:
        # 管道喂的答案用完了（`printf '1\n1\n' | ...`）。回车都能用默认值，
        # EOF 更该如此——不能拿一串 traceback 招呼用户
        print("（无输入，用默认值）")
        return question.default
    if not (raw := raw.strip()):
        return question.default
    if raw.isdigit() and 1 <= int(raw) <= len(question.options):
        return question.options[int(raw) - 1].key
    return raw


async def main() -> int:
    args = parse_args()
    setup_logging("DEBUG" if args.verbose else "WARNING", json_output=False)

    if args.prompt is not None:
        prompt = args.prompt.strip() or await _ask_prompt()
        draft = await parse_prompt(prompt)
        show_draft(draft)
        if args.parse_only:
            await close_clients()
            return 0 if draft.ok else 1
        if not draft.ok:
            print("\n信息不全，补齐后再试。")
            await close_clients()
            return 1
        request = draft.request.model_copy(update={"auto_select": not args.interactive})
    else:
        request = build_request(args)

    print(RULE)
    print(f"{request.departure_city} → {request.destination_city}　"
          f"{request.outbound_date} 至 {request.return_date}　"
          # 用户口径的天数 = 返程日 − 出发日；travel_days 是横跨的日历天数，不对外说
          f"{request.duration_days} 天 · {request.adults} 人 · {request.pace}")
    if args.dry_run:
        print("（--dry-run：全部使用假数据，不消耗任何额度）")
    print(RULE)

    runner = TripRunner()
    mocks = fake_upstreams(request) if args.dry_run else None
    try:
        state = await runner.start(request)
        while state.get("pending"):
            answers = {q.id: await ask_user(q) for q in state["pending"]}
            state = await runner.resume(state["trip_id"], answers)
    finally:
        if mocks is not None:
            mocks.stop()
        await close_clients()

    plan = to_plan(state)
    if plan.error:
        print(f"\n❌ {plan.error.code}: {plan.error.user_message}")
        print(f"   技术信息：{plan.error.message}")
        show_tail(plan)
        return 1

    show_flights(plan)
    show_hotel(plan)
    show_itinerary(plan)
    show_costs(plan)
    show_tail(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
