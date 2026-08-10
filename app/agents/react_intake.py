"""ReAct 参数收集 Agent（Flight ReAct Agent 设计文档 §2 / §6）。

**这是本项目唯一的 agent 循环。** 其余的 `app/agents/*` 都是确定性算法——
行程骨架用算法算是刻意的决策（见 `route_planner` 文件头），这里不改变那条纪律。
ReAct 只负责**参数收集**：把多轮对话里零散说出来的信息拼成一个完整的
`TripRequest`，并主动问缺的部分。

    Thought  → 看当前已收集到什么、还缺什么
    Action   → 调工具（算日期 / 校验城市 / 查记忆）
    Observation → 工具结果
    ...循环...
    Response → 问用户，或 Finish 收尾

## 三条边界

1. **不调 SerpAPI。** 机场消歧留在图里的 `flight_departure` / `flight_arrival`
   节点——那里已经有成熟的中断问答，而 SerpAPI 免费额度只有 250 次/月，
   在参数收集阶段就烧它是浪费。本 Agent 的工具只用高德（5000/天）和纯计算。
2. **日期一律由代码算。** `resolve_date` 工具包的是 `core.dates`，模型只负责
   把原话摘出来。LLM 做日期算术出了名地不可靠，而算错日期意味着整条链路去查
   错日子的机票——错得既贵又不显眼。
3. **模型不可用就退回规则。** `LLM_ENABLED=false` 或调用失败时走
   `extract_by_rules`，功能降级但不中断。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from app.agents.prompt_parser import (
    Extraction,
    TripDraft,
    extract_by_rules,
    loads_json,
    resolve,
)
from app.config import settings
from app.core.dates import format_cn, parse_relative_date
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.models.intake import SLOT_LABELS, IntakeReply, IntakeSession, ReactStep
from app.models.memory import MemorySnapshot

log = get_logger(__name__)

__all__ = [
    "ReactIntakeAgent",
    "SessionStore",
    "session_store",
    "SYSTEM_PROMPT",
    "TOOLS",
    "new_session_id",
]


def new_session_id() -> str:
    return f"ses_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- 工具

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

TOOLS: dict[str, dict[str, Any]] = {}


def _tool(name: str, description: str, parameters: dict[str, Any]):
    """注册一个 intake 工具。

    和 `tools.registry` 分开：那边的工具是给编排层用的（会烧 SerpAPI 额度、
    收 GeoPoint 这类内部类型），这边是给模型用的、面向对话的小工具集。
    混在一起会让模型看到一堆它不该碰的东西。
    """

    def decorator(fn: ToolFn) -> ToolFn:
        TOOLS[name] = {"description": description, "parameters": parameters, "fn": fn}
        return fn

    return decorator


@_tool(
    "resolve_date",
    "把日期原话（如「下周三」「9月5号」「明天」）换算成具体日期。"
    "任何时候需要确定日期都必须调用它，不要自己心算。",
    {"text": "日期原话，照抄用户说的", "today": "可选，基准日期 YYYY-MM-DD"},
)
async def _resolve_date_tool(text: str, today: str = "", **_: Any) -> dict[str, Any]:
    base = date.fromisoformat(today) if today else date.today()
    parsed = parse_relative_date(str(text), base)
    if parsed.value is None:
        return {"ok": False, "reason": f"没看懂「{text}」是哪天"}
    out: dict[str, Any] = {
        "ok": True,
        "date": parsed.value.isoformat(),
        "display": format_cn(parsed.value),
    }
    if parsed.ambiguous:
        # 「下周日」有两种读法，差整整一周——必须让模型知道要跟用户确认
        out["ambiguous"] = True
        out["note"] = parsed.note
    return out


@_tool(
    "lookup_city",
    "校验目的地城市是否可用，返回规范城市名。"
    "本服务只覆盖中国大陆，境外或省级行政区都不能作为目的地——"
    "在创建行程之前用它拦下来，比事后失败好。",
    {"name": "城市名"},
)
async def _lookup_city_tool(name: str, **_: Any) -> dict[str, Any]:
    from app.graph.nodes.resolve_city import is_too_broad, pick_city
    from app.tools.amap_poi import district_lookup

    try:
        candidates = await district_lookup(str(name))
    except AppError as exc:
        return {"ok": False, "reason": exc.message}

    best = pick_city(candidates)
    if best is None:
        return {"ok": False, "reason": f"没有解析出城市：{name}"}

    from app.models.common import CityRef

    city = CityRef(
        name=best.name, adcode=best.adcode, citycode=best.citycode, center=best.center
    )
    if not city.is_mainland_china:
        return {"ok": False, "reason": f"{city.name}不在服务覆盖范围内（仅支持中国大陆）"}
    if is_too_broad(best):
        return {"ok": False, "reason": f"{city.name}是{best.level}级行政区，请具体到城市"}
    return {"ok": True, "city": city.name, "adcode": city.adcode}


@_tool(
    "recall_preference",
    "查这个用户以往的出行习惯（出发城市、舱位、节奏、人数、预算档位等）。"
    "在向用户提问之前先查一次——记忆里已经有的就不要再问了。",
    {"field": "字段名，留空则返回全部"},
)
async def _recall_preference_tool(
    field: str = "", *, _memory: MemorySnapshot | None = None, **_: Any
) -> dict[str, Any]:
    if _memory is None or _memory.profile is None or not _memory.profile.preferences:
        return {"ok": True, "found": False, "note": "这位用户还没有历史偏好"}

    prefs = _memory.profile.preferences
    keys = [field] if field and field in prefs else list(prefs)
    return {
        "ok": True,
        "found": bool(keys),
        "preferences": {
            k: {
                "value": prefs[k].value,
                "confidence": prefs[k].confidence,
                "samples": prefs[k].samples,
                # 模型不该自己判断阈值，直接告诉它能不能用
                "usable": prefs[k].is_confident,
            }
            for k in keys
            if k in prefs
        },
    }


# ---------------------------------------------------------------- Prompt

SYSTEM_PROMPT = """你是行程需求收集助手。通过多轮对话把用户的出行需求补全。

## 你要收集的字段
必需：departure_city（出发城市）、destination_city（目的地城市）、
      outbound_date_text（出发日期原话）、以及 return_date_text 或 travel_days 之一
可选：adults、children、children_ages、budget_per_night、travel_class、
      pace（relaxed/standard/packed）、transport（transit/driving/walking）、
      must_visit、avoid

## 可用工具
{tools}

## 输出格式（严格遵守，每次只输出一段）
需要调用工具时：
Thought: <你的推理>
Action: <工具名>
Action Input: <JSON 对象>

需要向用户提问或告知时：
Thought: <你的推理>
Response: <给用户的话>

信息齐全、可以开始规划时：
Thought: <你的推理>
Finish: <JSON 对象，含所有已收集字段>

## 铁律
1. **日期必须调 resolve_date 换算**，绝不自己心算。填进 Finish 的
   outbound_date_text / return_date_text 仍然写**用户的原话**，换算结果只用于你判断合理性。
2. **提问前先调 recall_preference**，记忆里有的（usable=true）直接用，不要问。
3. 一次只问**一个**核心问题，问完等用户回答。不要一口气列五个问题。
4. 已经问过但用户没答的字段，**不要再问第二次**——他不想说。
5. 必需字段齐了就 Finish，可选字段缺了不影响开始规划。
6. 绝不编造用户没说过的信息。

## 当前状态
今天是 {today}。
已经收集到的字段：{collected}
已经问过的字段：{asked}
还缺的必需字段：{missing}"""


def _render_tools() -> str:
    lines = []
    for name, spec in TOOLS.items():
        params = "、".join(f"{k}（{v}）" for k, v in spec["parameters"].items())
        lines.append(f"- {name}({params})：{spec['description']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 解析

_RE_THOUGHT = re.compile(r"Thought\s*[:：]\s*(.*?)(?=\n\s*(?:Action|Response|Finish)\s*[:：]|$)",
                         re.S | re.I)
_RE_ACTION = re.compile(r"Action\s*[:：]\s*([A-Za-z_][A-Za-z0-9_]*)", re.I)
_RE_ACTION_INPUT = re.compile(r"Action\s*Input\s*[:：]\s*(\{.*)", re.S | re.I)
_RE_RESPONSE = re.compile(r"Response\s*[:：]\s*(.*)", re.S | re.I)
_RE_FINISH = re.compile(r"Finish\s*[:：]\s*(\{.*)", re.S | re.I)


class _Decision:
    """模型这一步想干什么。"""

    def __init__(
        self,
        thought: str = "",
        action: str = "",
        action_input: dict | None = None,
        response: str = "",
        finish: dict | None = None,
    ):
        self.thought = thought.strip()
        self.action = action
        self.action_input = action_input or {}
        self.response = response.strip()
        self.finish = finish


def parse_decision(raw: str) -> _Decision:
    """解析模型输出。

    模型不会永远守规矩，所以三种形态都要能兜住：
    Action / Response / Finish 都没匹配上时，**把整段当成 Response**——
    宁可把一句思考漏给用户看，也不能让对话卡死在解析失败上。
    """
    text = (raw or "").strip()
    thought = m.group(1) if (m := _RE_THOUGHT.search(text)) else ""

    if m := _RE_FINISH.search(text):
        try:
            return _Decision(thought=thought, finish=loads_json(m.group(1)))
        except Exception:  # noqa: BLE001 —— Finish 的 JSON 坏了就当没说完，继续问
            log.warning("Finish 的 JSON 解析失败", extra={"raw": m.group(1)[:200]})

    if m := _RE_ACTION.search(text):
        args: dict[str, Any] = {}
        if mi := _RE_ACTION_INPUT.search(text):
            try:
                args = loads_json(mi.group(1))
            except Exception:  # noqa: BLE001 —— 参数坏了，让工具层报错并回灌给模型
                args = {}
        return _Decision(thought=thought, action=m.group(1), action_input=args)

    if m := _RE_RESPONSE.search(text):
        return _Decision(thought=thought, response=m.group(1))

    # 完全不守格式：整段当回复，别卡死
    return _Decision(thought=thought, response=text or "能再说详细一点吗？")


# ---------------------------------------------------------------- Agent


class ReactIntakeAgent:
    """多轮参数收集。

    每轮调用 `run()`，内部最多跑 `REACT_MAX_STEPS` 步 Thought/Action/Observation，
    直到产出给用户的 Response 或 Finish。
    """

    def __init__(self, *, llm=None, max_steps: int | None = None):
        self._llm = llm
        self.max_steps = max_steps or settings.react_max_steps

    async def run(
        self,
        session: IntakeSession,
        message: str,
        *,
        memory: MemorySnapshot | None = None,
        today: date | None = None,
    ) -> IntakeReply:
        """处理用户的一句话，返回回复（可能带着已完成的草稿）。"""
        today = today or date.today()
        text = (message or "").strip()
        session.record("user", text)
        session.steps = []

        if not settings.react_enabled or (self._llm is None and not settings.llm_enabled):
            return self._fallback(session, text, memory=memory, today=today)

        try:
            return await self._loop(session, memory=memory, today=today)
        except Exception as exc:  # noqa: BLE001 —— 对话不能因为模型抽风就断掉
            log.warning(
                "ReAct 循环失败，退回规则抽取",
                extra={"err": str(exc) or type(exc).__name__},
            )
            return self._fallback(session, text, memory=memory, today=today)

    # ------------------------------------------------------------------
    async def _loop(
        self, session: IntakeSession, *, memory: MemorySnapshot | None, today: date
    ) -> IntakeReply:
        scratchpad: list[str] = []

        for step_no in range(self.max_steps):
            raw = await self._think(session, scratchpad, today=today)
            decision = parse_decision(raw)
            step = ReactStep(thought=decision.thought)

            if decision.finish is not None:
                session.steps.append(step)
                return self._finish(session, decision.finish, memory=memory, today=today)

            if decision.response:
                step.action = "Response"
                session.steps.append(step)
                session.record("agent", decision.response)
                self._mark_asked(session, decision.response)
                return IntakeReply(
                    session_id=session.session_id,
                    reply=decision.response,
                    missing=self._missing_labels(session),
                    steps=session.steps,
                )

            step.action = decision.action
            step.action_input = decision.action_input
            observation = await self._act(decision, memory=memory, today=today)
            step.observation = observation
            session.steps.append(step)

            scratchpad.append(
                f"Thought: {decision.thought}\n"
                f"Action: {decision.action}\n"
                f"Action Input: {json.dumps(decision.action_input, ensure_ascii=False)}\n"
                f"Observation: {observation}"
            )
            log.info(
                "react step",
                extra={"step": step_no + 1, "action": decision.action,
                       "session": session.session_id},
            )

        # 步数用尽：带着已有的东西收尾，而不是继续烧 token
        log.info("ReAct 步数用尽，按当前已收集的信息收尾",
                 extra={"session": session.session_id, "steps": self.max_steps})
        return self._finish(session, {}, memory=memory, today=today, exhausted=True)

    async def _think(
        self, session: IntakeSession, scratchpad: list[str], *, today: date
    ) -> str:
        collected = {
            k: v for k, v in session.collected.model_dump().items()
            if v is not None and v != [] and v != ""
        }
        system = SYSTEM_PROMPT.format(
            tools=_render_tools(),
            today=f"{today.isoformat()}（{format_cn(today)}）",
            collected=json.dumps(collected, ensure_ascii=False) or "（还没有）",
            asked="、".join(session.asked) or "（还没问过）",
            missing="、".join(self._missing_labels(session)) or "（齐了）",
        )
        user = session.history_text
        if scratchpad:
            user += "\n\n" + "\n".join(scratchpad)

        client = self._llm or self._default_llm()
        record_call("llm")
        response = await client.ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return getattr(response, "content", "") or ""

    async def _act(
        self, decision: _Decision, *, memory: MemorySnapshot | None, today: date
    ) -> str:
        spec = TOOLS.get(decision.action)
        if spec is None:
            # 报错回灌给模型，让它自己纠正——比直接崩掉有用
            return f"错误：没有名为 {decision.action} 的工具，可用的是 {sorted(TOOLS)}"

        args = dict(decision.action_input)
        if decision.action == "resolve_date":
            args.setdefault("today", today.isoformat())
        if decision.action == "recall_preference":
            args["_memory"] = memory

        try:
            result = await spec["fn"](**args)
        except TypeError as exc:
            return f"错误：参数不对（{exc}）"
        except Exception as exc:  # noqa: BLE001 —— 工具失败要让模型看见并绕过
            log.warning("intake 工具失败", extra={"tool": decision.action, "err": str(exc)})
            return f"错误：{decision.action} 执行失败（{exc}）"
        return json.dumps(result, ensure_ascii=False)

    # ------------------------------------------------------------------
    def _finish(
        self,
        session: IntakeSession,
        payload: dict,
        *,
        memory: MemorySnapshot | None,
        today: date,
        exhausted: bool = False,
    ) -> IntakeReply:
        """把 Finish 的内容并进累积槽位，然后落成草稿。"""
        if payload:
            try:
                session.merge(Extraction.model_validate(_coerce(payload)))
            except Exception:  # noqa: BLE001 —— 模型给的字段可能有脏值，能用多少用多少
                log.warning("Finish 载荷校验失败，按已有槽位收尾",
                            extra={"session": session.session_id})

        draft = resolve(
            session.history_text, session.collected, today=today, memory=memory
        )
        missing = self._missing_labels(session)

        if draft.ok:
            reply = _confirm_text(draft)
        elif exhausted:
            reply = "我还差这些信息：" + "、".join(missing) + "。能一起告诉我吗？"
        else:
            reply = ("还差：" + "、".join(missing)) if missing else "还需要一点信息才能开始规划。"

        session.record("agent", reply)
        return IntakeReply(
            session_id=session.session_id,
            reply=reply,
            draft=draft if draft.ok else None,
            missing=missing,
            steps=session.steps,
        )

    def _fallback(
        self,
        session: IntakeSession,
        text: str,
        *,
        memory: MemorySnapshot | None,
        today: date,
    ) -> IntakeReply:
        """模型不可用时的确定性路径。

        规则抽取一样能累积多轮：第一轮说目的地、第二轮说日期，`merge` 把它们
        合到同一个 `Extraction` 上。缺的字段按固定话术追问——覆盖不如模型自然，
        但**功能不缺**。
        """
        session.merge(extract_by_rules(text))
        draft = resolve(
            session.history_text, session.collected, today=today, memory=memory
        )
        missing = self._missing_labels(session)

        if draft.ok:
            reply = _confirm_text(draft)
        else:
            # 一次只问一个，且不重复问已经问过的
            still = session.missing_required()
            pending = [k for k in still if SLOT_LABELS.get(k) not in session.asked]
            key = pending[0] if pending else (still or [""])[0]
            reply = _ASK_TEXT.get(key, "还需要一点信息，能再说说吗？")
            if label := SLOT_LABELS.get(key):
                if label not in session.asked:
                    session.asked.append(label)

        session.record("agent", reply)
        return IntakeReply(
            session_id=session.session_id,
            reply=reply,
            draft=draft if draft.ok else None,
            missing=missing,
            degraded=True,
            steps=session.steps,
        )

    # ------------------------------------------------------------------
    def _missing_labels(self, session: IntakeSession) -> list[str]:
        seen: list[str] = []
        for key in session.missing_required():
            label = SLOT_LABELS.get(key, key)
            if label not in seen:
                seen.append(label)
        return seen

    def _mark_asked(self, session: IntakeSession, reply: str) -> None:
        """回复里提到哪个槽位，就记下"问过了"。

        用关键词而不是让模型自报——模型报不准，而重复追问是最招人烦的失败方式。
        """
        for label in SLOT_LABELS.values():
            if label in reply and label not in session.asked:
                session.asked.append(label)

    def _default_llm(self):
        from app.providers.llm import get_llm

        return get_llm()


_ASK_TEXT: dict[str, str] = {
    "departure_city": "从哪个城市出发？",
    "destination_city": "想去哪个城市？",
    "outbound_date_text": "哪天出发？",
    "return_date_text": "玩几天？或者哪天返程？",
}


def _coerce(payload: dict) -> dict:
    """把模型给的字段名归一到 `Extraction` 的字段。

    模型经常写 `outbound_date` 而不是 `outbound_date_text`——这是最常见的一种
    偏差，值得单独兜一下而不是整包丢弃。
    """
    alias = {
        "outbound_date": "outbound_date_text",
        "return_date": "return_date_text",
        "departure": "departure_city",
        "destination": "destination_city",
        "days": "travel_days",
        "passengers": "adults",
    }
    out = dict(payload)
    for wrong, right in alias.items():
        if wrong in out and right not in out:
            out[right] = out.pop(wrong)
    return {k: v for k, v in out.items() if k in Extraction.model_fields}


def _confirm_text(draft: TripDraft) -> str:
    """参数齐全时的汇总（设计文档 §5 Turn 4：展示汇总请用户确认）。"""
    lines = ["信息齐了，我确认一下："]
    for f in draft.fields:
        tag = {"memory": "（记忆）", "default": "（默认）", "derived": "（推算）"}.get(f.origin, "")
        lines.append(f"· {f.label}：{f.value}{tag}")
    lines.append("没问题的话就开始规划了，要改哪项直接说。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 会话存储


class SessionStore:
    """进程内会话表。

    ⚠️ 和 checkpointer / EventBus 一样是**进程内**的，多 worker 下同一个
    session 可能落到没有它的进程上。上多副本时要和那两样一起迁到共享存储
    （架构文档 §4.4）。
    """

    def __init__(self, max_sessions: int = 500):
        self._sessions: dict[str, IntakeSession] = {}
        self._max = max_sessions

    def get_or_create(self, session_id: str = "", *, profile_id: str = "") -> IntakeSession:
        if session_id and (existing := self._sessions.get(session_id)) is not None:
            return existing
        session = IntakeSession(
            session_id=session_id or new_session_id(), profile_id=profile_id
        )
        self._sessions[session.session_id] = session
        # 简单的 FIFO 淘汰：intake 会话是短命的，没必要做 LRU
        while len(self._sessions) > self._max:
            self._sessions.pop(next(iter(self._sessions)))
        return session

    def get(self, session_id: str) -> IntakeSession | None:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def clear(self) -> None:
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)


session_store = SessionStore()
