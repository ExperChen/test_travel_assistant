"""对话式旅行助手 —— 直接运行这个文件即可。

    python main.py                      # 多轮对话，说到哪儿算哪儿
    python main.py "9月20号从北京去成都玩4天"   # 带一句话开场，能补齐就直接开跑
    python main.py --offline            # 全假数据，零额度零网络

编排只有一种：**LLM 自主调工具**。它自己决定查什么、查几次、怎么排，
代价是慢（分钟级、几十次工具调用），营业时间与返程约束靠模型自觉。
原来那条固定管线（节点顺序写死、约束由代码保证）已经删除。

两段对话是分开的：
    intake  多轮收集参数（ReAct，只用高德和纯计算，不烧 SerpAPI）
    agent   参数齐了之后自主规划（会烧额度）

数据来源由 `.env` 决定（`AMAP_MOCK` / `SERPAPI_MOCK` / `HOTEL_SOURCE`），
启动时会打印当前分工，免得对着假数据以为是真的。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 中文 Windows 控制台默认 GBK，打印 ✈️/─ 会直接 UnicodeEncodeError 崩掉
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure") and not sys.stdin.isatty():
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

from app.agents.react_intake import ReactIntakeAgent, session_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.core.md_console import display_width, render_markdown  # noqa: E402
from app.models.special import needs_hint_text  # noqa: E402
from app.tools.registry import close_clients  # noqa: E402

RULE = "─" * 64

BANNER = """
╭──────────────────────────────────────────────╮
│  旅行助手 · 说说你想去哪儿                    │
│  例：「下个月想去成都玩几天」                 │
│  随时输入 q 退出                              │
╰──────────────────────────────────────────────╯"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", nargs="*", help="开场白，留空则进入交互提问")
    p.add_argument("--offline", action="store_true",
                   help="全假数据：零额度、零网络（覆盖 .env 的开关）")
    p.add_argument("--profile", default="",
                   help="记忆身份（对应 X-Profile-Id），带上才会读写长期偏好")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="调试日志 + 工具返回的原始 JSON（平时只打人话摘要，"
                        "完整数据在对话记录 .json 里）")
    p.add_argument("--log-dir", default="conversations",
                   help="对话记录目录，默认 ./conversations")
    p.add_argument("--no-log", action="store_true", help="不保存对话记录")
    p.add_argument("--skip-llm-check", action="store_true",
                   help="跳过启动时的模型自检（省一次调用）")
    p.add_argument("--route-api", choices=["v3", "v5"], default="",
                   help="公交换乘走高德哪一版接口，默认 v3（两版数值实测一致）")
    p.add_argument("--transit-strategy", type=int, choices=range(9), default=None,
                   metavar="0-8",
                   help="公交策略：0推荐 1最经济 2最少换乘 3最少步行 4最舒适 "
                        "5不乘地铁 6地铁图(仅v5) 7地铁优先(仅v5) 8时间最短(仅v5)")
    return p.parse_args()


def show_sources() -> None:
    """把"哪些是真的、哪些是假的"摆在最前面。

    人最容易犯的错是拿着模拟数据当真结果看——所以这一行必须显眼、必须准确。
    """
    print("数据来源：" + " · ".join(f"{k} {v}" for k, v in _source_map().items()))


async def probe_llm() -> tuple[bool, str]:
    """启动时真打一次模型，确认它能用。返回 (是否可用, 说明)。

    **为什么要专门探一次**：ReAct 循环里任何异常都会被吞掉、静默退回规则抽取
    （这是刻意的——对话不能因为模型抽风就断掉）。代价是用户只看到一句
    "模型不可用"，不知道是 key 错了、余额没了、还是连不上。
    在这里提前打一次，把真实原因摆到台面上。

    探测本身花一次很短的调用（约 3 秒 / 十几 token），比事后猜便宜得多。
    """
    if not settings.llm_enabled:
        return False, "LLM_ENABLED=false，按配置走确定性模板"
    if not settings.active_llm_key:
        key_name = "GOOGLE_API_KEY" if settings.llm_provider == "gemini" else "LLM_API_KEY"
        return False, f"{key_name} 未配置"

    from app.providers.llm import get_llm

    started = time.perf_counter()
    try:
        response = await get_llm().ainvoke(
            [{"role": "user", "content": "回复两个字：可用"}]
        )
    except Exception as exc:  # noqa: BLE001 —— 探测就是要把任何失败都说清楚
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"

    took = (time.perf_counter() - started) * 1000
    text = (getattr(response, "content", "") or "").strip()
    if not text:
        return False, "模型返回了空内容"
    return True, f"{settings.llm_model} 可用（{took:.0f}ms）"


class Transcript:
    """对话记录。**JSON 是主，Markdown 是它的一个视图。**

    理由：JSON 是唯一能无损承载 ReAct 轨迹（thought / action / observation）、
    配额、耗时的格式，而这些正是排查"agent 为什么这么想"和做 benchmark 时要的。
    Markdown 从 JSON 生成得出来，反过来不行——所以真相存 JSON，同时落一份 md 供阅读。

    每收到一条就落盘，不等程序正常退出：调试中最想看记录的时候，
    往往正是程序崩了或被 Ctrl-C 掐掉的时候。
    """

    SCHEMA = 1
    """记录格式版本。将来改了结构，读的人能据此分支，而不是靠猜。"""

    def __init__(self, directory: str | Path, *, enabled: bool = True,
                 session_id: str = ""):
        self.enabled = enabled
        self.session_id = session_id
        self.turns: list[dict] = []
        self.started = datetime.now()
        self._since = self.started
        if not enabled:
            return
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.started:%Y%m%d-%H%M%S}"
        self.json_path = self.dir / f"{stem}.json"
        self.md_path = self.dir / f"{stem}.md"

    def say(self, role: str, text: str, *, steps=None, missing=None,
            degraded_reason: str = "") -> None:
        if not self.enabled:
            return
        now = datetime.now()
        turn: dict = {
            "at": now.isoformat(timespec="seconds"),
            "role": role,
            "text": text,
            # 距上一条的间隔。用户侧是"思考+打字"，助手侧就是**模型延迟**——
            # 做 benchmark 时这是最想要的那个数
            "elapsed_ms": round((now - self._since).total_seconds() * 1000),
        }
        self._since = now
        if steps:
            # ReAct 轨迹只有 JSON 存得下——它是"agent 为什么这么答"的唯一线索
            turn["react"] = [s.model_dump(mode="json") for s in steps]
        if missing:
            turn["missing"] = list(missing)
        if degraded_reason:
            # 降级是最值得复盘的一类回合：轨迹是空的，只有这一句能解释为什么
            turn["degraded_reason"] = degraded_reason
        self.turns.append(turn)
        self._flush()

    def _flush(self) -> None:
        payload = {
            "schema": self.SCHEMA,
            "session_id": self.session_id,
            "started_at": self.started.isoformat(timespec="seconds"),
            "sources": _source_map(),
            "turns": self.turns,
        }
        self.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.md_path.write_text(self._as_markdown(), encoding="utf-8")

    def _as_markdown(self) -> str:
        lines = [
            f"# 对话记录 · {self.started:%Y-%m-%d %H:%M}",
            "",
            "| 数据 | 来源 |",
            "|---|---|",
            *[f"| {k} | {v} |" for k, v in _source_map().items()],
            "",
            "---",
            "",
        ]
        for turn in self.turns:
            who = "你" if turn["role"] == "user" else "助手"
            took = turn.get("elapsed_ms", 0)
            stamp = f"{turn['at'][11:]}" + (f" · {took / 1000:.1f}s" if took > 1500 else "")
            lines += [f"**{who}**（{stamp}）", "", turn["text"], ""]
            if turn.get("missing"):
                lines += [f"> 还缺：{'、'.join(turn['missing'])}", ""]
            for step in turn.get("react") or []:
                action = step.get("action") or "Finish"
                lines += [f"<sub>`{action}` {step.get('thought', '')[:120]}</sub>", ""]

        return "\n".join(lines)


def _source_map() -> dict[str, str]:
    hotel = {
        "serpapi": "模拟" if settings.serpapi_mock else "真实(Google)",
        "hybrid": "位置真实(高德) + 房价模拟",
        "mock": "模拟",
    }[settings.hotel_source]
    strategy_cn = {0: "推荐", 1: "最经济", 2: "最少换乘", 3: "最少步行", 4: "最舒适",
                   5: "不乘地铁", 6: "地铁图", 7: "地铁优先", 8: "时间最短"}
    transit = (f"高德{settings.amap_route_version}·"
               f"{strategy_cn.get(settings.transit_strategy, settings.transit_strategy)}")
    return {
        "机票": "模拟" if settings.serpapi_mock else "真实(SerpAPI)",
        "景点/路线": "模拟" if settings.amap_mock else "真实(高德)",
        "酒店": hotel,
        "公交换乘": transit,
        "规划": f"LLM 自主调工具（{settings.llm_model}）",
    }


async def read_line(prompt: str) -> str:
    """读一行。管道喂完/Ctrl-D 时返回空串而不是抛 EOFError。"""
    try:
        return (await asyncio.to_thread(input, prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


QUIT_WORDS = frozenset({"q", "quit", "exit", "退出", "再见"})
RESET_WORDS = frozenset({"新行程", "重来", "new", "reset", "清空"})
YES_WORDS = frozenset({"", "y", "yes", "好", "好的", "确认", "ok", "行", "可以"})


class Quit(Exception):  # noqa: N818 —— 这是控制流，不是错误
    """用户要退出。用异常而不是层层返回 None，省掉四处哨兵判断。"""


async def prompt_user(hint: str) -> str:
    """读一行。**只有 q 才退出**——空回车重新问，EOF 也退出（管道喂完了）。"""
    while True:
        line = await read_line(hint)
        if line is None:  # EOF / Ctrl-C
            raise Quit
        if line.lower() in QUIT_WORDS:
            raise Quit
        if line:
            return line
        # 空回车：不当退出，也不当有效输入，重新问


async def collect_request(agent, session, memory, opening: str, log: Transcript):
    """多轮对话直到参数齐全，返回 (TripRequest, origins)。

    `session` 由调用方持有并跨轮复用——这正是"规划完还能接着改"的基础：
    上一趟的出发地/日期都还在槽位里，用户只说"改成5天"就能重新规划。
    """
    message = opening
    while True:
        if not message:
            message = await prompt_user("\n你> ")

        if message.lower() in RESET_WORDS:
            session.collected = type(session.collected)()
            session.asked.clear()
            print("\n助手> 好，从头开始。想去哪儿？")
            message = ""
            continue

        log.say("user", message)
        reply = await agent.run(session, message, memory=memory)
        message = ""

        log.say("agent", reply.reply, steps=reply.steps, missing=reply.missing,
                degraded_reason=reply.degraded_reason)
        print(f"\n助手> {reply.reply}")
        if reply.degraded:
            print(f"      （模型这轮没答上来，已退回规则解析："
                  f"{reply.degraded_reason or '原因未知'}）")

        if reply.draft is not None and reply.draft.ok:
            confirm = await read_line("\n开始规划？(回车确认 / 直接说要改什么) > ")
            if confirm is None:
                raise Quit
            if confirm.lower() in QUIT_WORDS:
                raise Quit
            if confirm.lower() in YES_WORDS:
                return reply.draft.request, {f.key: f.origin for f in reply.draft.fields}
            message = confirm  # 用户要改，带着这句继续对话
        elif reply.missing:
            print(f"      还缺：{'、'.join(reply.missing)}")


async def _memory(profile_id: str):
    if not profile_id or not settings.memory_enabled:
        return None
    try:
        from app.store import get_store

        return await get_store().snapshot(profile_id)
    except Exception:  # noqa: BLE001 —— 记忆是增量特性，坏了就当没有
        return None


async def plan_once(agent, session, memory, opening: str, args, log: Transcript) -> None:
    """收集参数 → 自主规划。一趟行程的完整流程。"""
    request, _origins = await collect_request(agent, session, memory, opening, log)

    print(RULE)
    transport_cn = {"transit": "公交地铁", "driving": "自驾",
                    "walking": "步行"}.get(request.transport, request.transport)
    print(f"{request.departure_city} → {request.destination_city}　"
          f"{request.outbound_date} 至 {request.return_date}　"
          f"{request.duration_days} 天 · {request.adults} 人 · 市内{transport_cn}")
    if request.special_requests:
        print(f"特殊需求：{'、'.join(request.special_requests)}")
    print(RULE)

    await run_agent(request, log, verbose=args.verbose, profile_id=args.profile)


TOOL_LABELS: dict[str, str] = {
    "district_lookup": "查城市",
    "poi_keyword": "搜地点",
    "poi_around": "搜周边",
    "poi_detail": "查详情",
    "address_of": "反查地址",
    "route_between": "算路线",
    "distance_many": "算距离",
    "flights_autocomplete": "查机场",
    "flights_search": "搜航班",
    "hotels_autocomplete": "查酒店名",
    "hotels_search": "搜酒店",
    "finish_plan": "交付行程",
}

# 每个工具最能说明"在查什么"的那个参数，按优先级取第一个有值的
_STEP_SUBJECT: tuple[str, ...] = ("keywords", "q", "poi_ids", "region")

_MODE_CN = {"transit": "公交地铁", "driving": "驾车", "walking": "步行",
            "bicycling": "骑行", "straight": "直线"}


def describe_step(step) -> str:
    """把一次工具调用说成一句人话。

    **不打原始观测**：工具返回的是几 KB 的 JSON，倒进终端会把行程本身淹掉，
    而且它对人几乎没有信息量——真要看的时候在对话记录 JSON 里，比在
    翻回滚的终端里好读得多。这里只回答两件事：查了什么、成没成。
    """
    label = TOOL_LABELS.get(step.action, step.action or "思考")
    args = step.action_input or {}

    subject = ""
    for key in _STEP_SUBJECT:
        if value := args.get(key):
            subject = "、".join(str(v) for v in value) if isinstance(value, list) else str(value)
            break
    if not subject and step.action == "flights_search":
        # 航班的"查什么"是两端机场，只报出发地等于没说
        subject = f"{args.get('departure_id', '?')}→{args.get('arrival_id', '?')}"
    if not subject and (mode := args.get("mode")):
        subject = _MODE_CN.get(str(mode), str(mode))

    obs = step.observation or ""
    # 工具失败会被回灌给模型自己纠正（见 planner_agent），但用户有权看到
    mark = "✗" if obs.startswith("错误") else "✓"
    # 标签是中文，`f"{label:<8}"` 会按字符数补齐、显示上照样歪——按显示宽度补
    pad = " " * max(1, 10 - display_width(label))
    return f"{mark} {label}{pad}{subject[:40]}".rstrip()


async def remember(request, *, profile_id: str) -> None:
    """规划成功后落盘长期偏好（L2）。

    **只在规划真的跑完时写**（记忆与追问文档 §2）：用户在对话里改了主意
    ——「算了从上海走」「预算提到 800」——以最终生效的 `TripRequest` 为准
    才有意义，中途的值写进去反而会污染记忆。

    全程 best-effort：记忆坏掉不能影响已经交付给用户的行程，所以它跑在
    打印之后，而且任何异常都只记日志。

    ⚠️ L3 履历（去过哪些景点）不再写：它要的是**结构化的逐日行程**，
    而自主 agent 交付的是一段 Markdown，从里面反解景点 poi_id 只会得到
    一堆猜测。宁可不记，也不能往记忆里塞不可靠的数据。
    """
    if not profile_id or not settings.memory_enabled:
        return
    try:
        from datetime import date as _date

        from app.models.memory import Profile, preference_payload
        from app.store import get_store

        store = get_store()
        profile = await store.load_profile(profile_id) or Profile(profile_id=profile_id)
        updated = profile.observe_all(preference_payload(request), on=_date.today())
        await store.save_profile(updated)
    except Exception:  # noqa: BLE001 —— 记忆是增量特性，坏了不该影响行程
        pass


async def run_agent(request, log: Transcript, *, verbose: bool = False,
                    profile_id: str = "") -> None:
    """自主规划：把工具交给模型，逐步打印它的动作。

    输出刻意做成**逐步可见**的——自主循环最需要的就是"它现在在干什么"，
    看不见的话一分钟不出结果就只能干等。但"在干什么"用一句人话就够了：
    工具返回的原始 JSON 只进对话记录文件，不往终端倒（`-v` 才给）。
    """
    from app.agents.planner_agent import PlannerAgent
    from app.core.metrics import track_quota

    task = (
        f"{request.outbound_date} 从{request.departure_city}出发去"
        f"{request.destination_city}，{request.return_date} 返程，"
        f"{request.adults} 人，市内交通用{request.transport}。"
        + (f"必去：{'、'.join(request.must_visit)}。" if request.must_visit else "")
        + (f"每晚住宿预算 ¥{request.budget_per_night} 以内。"
           if request.budget_per_night else "")
        + "请排一份逐日行程。"
        # 特殊需求单独起一段、并写明"必须满足"：混在一句话里模型会当成背景描述
        + (f"\n\n{hint}" if (hint := needs_hint_text(request.special_requests)) else "")
    )
    cap = (f"SerpAPI 上限 {settings.agent_serpapi_budget} 次"
           if settings.agent_serpapi_budget else "SerpAPI 不限次")
    print(f"自主规划中（最多 {settings.agent_max_steps} 步，{cap}）…\n")

    began = datetime.now()
    with track_quota() as quota:
        run = await PlannerAgent().run(task)
        for i, step in enumerate(run.steps, 1):
            print(f"  {i:2d}. {describe_step(step)}")
            if verbose:
                # 原始观测是 JSON，只在显式要调试时给——平时它把行程淹了
                print(f"      {(step.observation or '')[:300]}")

    took = (datetime.now() - began).total_seconds()
    print(f"\n{RULE}")
    # 模型给的是 Markdown，渲染成终端排版；原文照旧进 JSON 记录
    print(render_markdown(run.answer) if run.answer else "（模型没有给出行程）")
    print(RULE)
    print(f"{run.stop_reason} · {run.tool_calls} 次工具调用 · {took:.0f}s"
          f" · SerpAPI {quota.serpapi} 次 · 高德 {quota.amap} 次")
    log.say("agent", run.answer or "（无结果）", steps=run.steps)
    if run.finished:
        await remember(request, profile_id=profile_id)


async def main() -> int:
    args = parse_args()
    setup_logging("DEBUG" if args.verbose else "WARNING", json_output=False)

    if args.offline:
        # 命令行优先级高于 .env——想临时不烧额度不该被迫去改配置文件
        settings.serpapi_mock = True
        settings.amap_mock = True
        settings.hotel_source = "mock"
    if args.route_api:
        settings.amap_route_version = args.route_api
    if args.transit_strategy is not None:
        settings.transit_strategy = args.transit_strategy

    print(BANNER)
    show_sources()

    # 先探一次模型：失败时把**真实原因**摆出来，而不是让用户在对话里
    # 撞见一句没头没尾的"模型不可用"
    if not args.skip_llm_check:
        ok, detail = await probe_llm()
        print(f"模型自检：{'✓ ' if ok else '✗ '}{detail}")
        if not ok:
            print("        对话仍可继续——会退回规则抽取，说得直白些命中率更高。")

    # agent 与 session 在整个进程里复用：上一趟的槽位都还在，
    # 所以规划完之后说一句「改成5天」就能直接重排，不必从头再说一遍。
    agent = ReactIntakeAgent()
    session = session_store.get_or_create(profile_id=args.profile)
    memory = await _memory(args.profile)
    log = Transcript(args.log_dir, enabled=not args.no_log,
                     session_id=session.session_id)
    if log.enabled:
        print(f"对话记录：{log.json_path}  （同名 .md 供阅读）")

    opening = " ".join(args.prompt).strip()
    try:
        while True:
            await plan_once(agent, session, memory, opening, args, log)
            opening = ""  # 后续轮次一律等用户开口
            print(
                "\n还可以接着说——比如「改成5天」「换成杭州」「预算800」，"
                "或「新行程」从头开始；输入 q 退出。"
            )
    except Quit:
        print("\n下次见。")
        return 0
    finally:
        await close_clients()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n已中断。")
        raise SystemExit(130) from None
