"""LangGraph 装配与执行。

图结构（架构文档 §4.2）：

    intake → resolve_city ─┬─ flight ×3 ─┐
                           └─ 景点 → 酒店 ─┴→ route_planner → summarize

两条分支并行：最慢的是航班（3 次 API + 可能两次人工确认），让它与「景点→酒店」
同时跑。酒店必须排在景点之后——它要用景点重心做重排锚点。
"""

from __future__ import annotations

import importlib
import inspect
import uuid
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel

from app.config import settings
from app.core.exceptions import AnswerMismatch
from app.core.logging import bind_trip, get_logger
from app.core.metrics import track_quota
from app.graph.nodes._common import continue_or_fail
from app.graph.nodes.attraction import attraction_search
from app.graph.nodes.flight import flight_arrival, flight_departure, flight_search
from app.graph.nodes.hotel import hotel_search
from app.graph.nodes.intake import intake
from app.graph.nodes.planning import route_planner
from app.graph.nodes.resolve_city import resolve_city
from app.graph.nodes.summarize import summarize_node
from app.graph.state import TripState, initial_state
from app.models.common import QuotaCounter
from app.models.errors import PlanWarning
from app.models.events import InterruptQuestion
from app.models.trip import TripRequest

log = get_logger(__name__)

__all__ = ["build_graph", "TripRunner", "plan_trip", "new_trip_id"]

FLIGHT_ENTRY = "flight_departure"
LOCAL_ENTRY = "attraction_search"
JOIN = "route_planner"
SUMMARIZE = "summarize"


# TripState 里装的都是我们自己的 Pydantic 模型，checkpointer 存取时要反序列化它们。
# langgraph 默认只对未登记的类型发警告，但明确说了未来版本会直接拒绝；而"拒绝"的表现
# 是**静默退化成 dict**，比报错难查得多（第一次配错格式时就是这样，靠测试才抓出来）。
# 这里从模型模块自动枚举，避免以后加了新模型忘记登记。
_MODEL_MODULES = (
    "attraction",
    "common",
    "errors",
    "events",
    "flight",
    "hotel",
    "route",
    "trip",
)


def _allowed_models() -> tuple[tuple[str, str], ...]:
    """枚举需要登记的类型。

    **不能只收 BaseModel**：ErrorCode 这类 Enum 也会被序列化，漏登记同样会被
    静默降级成裸值（实测在真实运行的日志里抓到过）。
    """
    found: set[tuple[str, str]] = set()
    for name in _MODEL_MODULES:
        module = importlib.import_module(f"app.models.{name}")
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            if issubclass(obj, BaseModel | Enum):
                found.add((obj.__module__, obj.__name__))
    return tuple(sorted(found))


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_allowed_models())


def new_trip_id() -> str:
    return f"trp_{uuid.uuid4().hex[:16]}"


def _fan_out(state: TripState) -> list[str]:
    """resolve_city 之后扇出到两条并行分支；已经出错就直接收尾。"""
    if state.get("errors"):
        return [END]
    return [FLIGHT_ENTRY, LOCAL_ENTRY]


def build_graph(checkpointer=None):
    """装配并编译状态图。

    每个节点后面都挂条件边：写了 errors 就直接收尾，不再往下烧配额。
    汇合节点 route_planner 自己还有一道守卫——并行下另一条分支可能已经失败，
    但本分支的条件边看到的状态未必包含那次写入。
    """
    graph = StateGraph(TripState)
    for name, fn in (
        ("intake", intake),
        ("resolve_city", resolve_city),
        (FLIGHT_ENTRY, flight_departure),
        ("flight_arrival", flight_arrival),
        ("flight_search", flight_search),
        (LOCAL_ENTRY, attraction_search),
        ("hotel_search", hotel_search),
        (JOIN, route_planner),
        (SUMMARIZE, summarize_node),
    ):
        graph.add_node(name, fn)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", continue_or_fail, {"continue": "resolve_city", "failed": END}
    )
    graph.add_conditional_edges("resolve_city", _fan_out, [FLIGHT_ENTRY, LOCAL_ENTRY, END])

    # 航班分支
    graph.add_conditional_edges(
        FLIGHT_ENTRY, continue_or_fail, {"continue": "flight_arrival", "failed": END}
    )
    graph.add_conditional_edges(
        "flight_arrival", continue_or_fail, {"continue": "flight_search", "failed": END}
    )
    graph.add_conditional_edges(
        "flight_search", continue_or_fail, {"continue": JOIN, "failed": END}
    )

    # 景点 → 酒店分支
    graph.add_conditional_edges(
        LOCAL_ENTRY, continue_or_fail, {"continue": "hotel_search", "failed": END}
    )
    graph.add_conditional_edges(
        "hotel_search", continue_or_fail, {"continue": JOIN, "failed": END}
    )

    graph.add_conditional_edges(
        JOIN, continue_or_fail, {"continue": SUMMARIZE, "failed": END}
    )
    graph.add_edge(SUMMARIZE, END)

    return graph.compile(checkpointer=checkpointer)


class TripRunner:
    """持有 checkpointer 与编译后的图，支持中断-恢复。

    checkpointer 必须跨 start/resume 存活，所以图只编译一次。生产环境把
    MemorySaver 换成 Sqlite/Postgres——否则多 worker 下 resume 会找不到 thread。
    """

    def __init__(self, checkpointer=None):
        self.checkpointer = checkpointer or MemorySaver(serde=make_serializer())
        self.graph = build_graph(self.checkpointer)
        # 配额按 trip 累加。不放进图状态里是因为节点从不写它，snapshot 出来的
        # 永远是 initial_state 里那个空计数器，写回去还得额外动 checkpointer。
        # 注意：这份记账和 MemorySaver 一样是进程内的——换持久化 checkpointer 上
        # 多 worker 时，这里也要跟着挪到共享存储。
        self._quota: dict[str, QuotaCounter] = {}
        # 超时自动应答产生的警告。和 _quota 一样是进程内记账，原因见 resume_expired。
        self._timeouts: dict[str, list[PlanWarning]] = {}

    def _config(self, trip_id: str) -> dict:
        return {"configurable": {"thread_id": trip_id}}

    async def start(self, request: TripRequest, *, trip_id: str | None = None) -> TripState:
        trip_id = trip_id or new_trip_id()
        return await self._run(trip_id, initial_state(trip_id, request))

    async def iter_run(
        self, trip_id: str, payload: Any
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """逐节点产出 (节点名, 状态补丁)，供 SSE 边跑边推。

        跑完后必须调 `finalize()` 拿终态——中断信息和累计配额都在那里补齐。
        """
        config = self._config(trip_id)
        with bind_trip(trip_id), track_quota() as quota:
            async for chunk in self.graph.astream(payload, config=config, stream_mode="updates"):
                for node, patch in chunk.items():
                    if node != "__interrupt__" and isinstance(patch, dict):
                        yield node, patch
        self._accumulate_quota(trip_id, quota)

    async def finalize(self, trip_id: str) -> TripState:
        """流式跑完之后补齐终态：挂起问题、状态、配额、超时警告。"""
        snapshot = await self.graph.aget_state(self._config(trip_id))
        state: TripState = dict(snapshot.values)  # type: ignore[assignment]
        pending = _parse_interrupts(snapshot.interrupts or ())

        state["quota"] = self._quota.setdefault(trip_id, QuotaCounter())
        if timeouts := self._timeouts.get(trip_id):
            state["warnings"] = [*state.get("warnings", []), *timeouts]
        state["pending"] = pending
        if pending:
            state["status"] = "waiting_input"
        elif state.get("status") != "failed":
            state["status"] = "done"
        return state

    async def start_stream(
        self, request: TripRequest, *, trip_id: str | None = None
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        trip_id = trip_id or new_trip_id()
        async for update in self.iter_run(trip_id, initial_state(trip_id, request)):
            yield update

    async def resume_stream(
        self, trip_id: str, answers: dict[str, Any]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        payload = Command(resume=await self.translate_answers(trip_id, answers))
        async for update in self.iter_run(trip_id, payload):
            yield update

    async def resume(self, trip_id: str, answers: dict[str, Any]) -> TripState:
        """回答挂起的问题并继续。`answers` 的键是**我们的** question.id。

        并行分支下可能同时挂着多个问题，langgraph 要求按它自己的 interrupt id
        定向恢复。那个 id 是内部哈希，不该泄露到 API 契约里——所以每次都从
        当前快照现场重建 `question.id → interrupt.id` 的映射。这样即使进程重启
        （只要 checkpointer 还在），映射也不会失效。
        """
        payload = await self.translate_answers(trip_id, answers)
        return await self._run(trip_id, Command(resume=payload))

    async def translate_answers(self, trip_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        """把我们的 question.id 换成 langgraph 的 interrupt id；顺带校验合法性。

        API 层在起后台任务之前先调它，好让非法答案同步回 409 而不是异步失败。
        """
        by_question = await self.pending_ids(trip_id)
        unknown = set(answers) - set(by_question)
        if unknown:
            raise AnswerMismatch(
                f"这些问题当前并未挂起：{sorted(unknown)}",
                pending=sorted(by_question),
            )
        return {by_question[qid]: value for qid, value in answers.items()}

    async def pending_ids(self, trip_id: str) -> dict[str, str]:
        """{我们的 question.id: langgraph 的 interrupt.id}"""
        snapshot = await self.graph.aget_state(self._config(trip_id))
        mapping: dict[str, str] = {}
        for item in snapshot.interrupts or ():
            value = item.value
            if isinstance(value, dict) and "id" in value:
                mapping[str(value["id"])] = item.id
        return mapping

    async def pending_questions(self, trip_id: str) -> list[InterruptQuestion]:
        snapshot = await self.graph.aget_state(self._config(trip_id))
        return _parse_interrupts(snapshot.interrupts or ())

    async def note_expired(self, trip_id: str) -> list[InterruptQuestion]:
        """挑出已过期的挂起问题并记下超时警告，返回它们（没有则空列表）。

        和恢复动作拆开，是因为调用方有两种恢复方式：脚本用阻塞的
        `resume_expired`，服务层要走流式的 `resume_stream` 才能继续推 SSE。
        警告只能记一次，所以记账放在这里。
        """
        expired = [q for q in await self.pending_questions(trip_id) if q.is_expired]
        if not expired:
            return []

        # 警告记在 runner 自己账上，**不能**用 aupdate_state 写回图状态：
        # 在有挂起中断的线程上调 aupdate_state 会把那些中断一并清掉，
        # 于是紧接着的 resume 会找不到要恢复的问题（这是实测踩到的）。
        self._timeouts.setdefault(trip_id, []).extend(
            PlanWarning.of(
                "ANSWER_TIMED_OUT",
                f"「{q.title}」超过 {settings.interrupt_timeout_s // 60} 分钟没有回答，"
                f"已自动选择默认项 {q.default}",
                stage=q.id.split(".")[0],
            )
            for q in expired
        )
        log.info("中断超时，按默认值继续", extra={"questions": [q.id for q in expired]})
        return expired

    async def resume_expired(self, trip_id: str) -> TripState | None:
        """超时的问题用它的默认值自动回答（架构文档 §4.3）。

        没有这一步，用户关掉页面就会让行程永久卡在 waiting_input。
        返回 None 表示当前没有过期的问题。
        """
        expired = await self.note_expired(trip_id)
        if not expired:
            return None
        return await self.resume(trip_id, {q.id: q.default for q in expired})

    async def snapshot(self, trip_id: str) -> TripState:
        state = await self.graph.aget_state(self._config(trip_id))
        return dict(state.values)  # type: ignore[return-value]

    def _accumulate_quota(self, trip_id: str, run: QuotaCounter) -> QuotaCounter:
        """把本轮的调用数累加到该 trip 的总账上（一次规划可能跨多轮 resume）。"""
        total = self._quota.setdefault(trip_id, QuotaCounter())
        total.serpapi += run.serpapi
        total.amap += run.amap
        total.llm += run.llm
        total.cache_hits += run.cache_hits
        return total

    async def _run(self, trip_id: str, payload: Any) -> TripState:
        config = self._config(trip_id)

        with bind_trip(trip_id), track_quota() as quota:
            result = await self.graph.ainvoke(payload, config=config)

        # 被 interrupt 打断时 ainvoke 只返回 {"__interrupt__": [...]}，
        # 累积到此刻的状态得单独取一次快照。
        pending = _parse_interrupts(result.get("__interrupt__") or ())
        state = await self.snapshot(trip_id)

        state["quota"] = self._accumulate_quota(trip_id, quota)
        if timeouts := self._timeouts.get(trip_id):
            state["warnings"] = [*state.get("warnings", []), *timeouts]
        state["pending"] = pending
        if pending:
            state["status"] = "waiting_input"
        elif state.get("status") != "failed":
            state["status"] = "done"

        log.info(
            "本轮结束",
            extra={
                "status": state.get("status"),
                "pending": [q.id for q in pending],
                "serpapi": state["quota"].serpapi,
                "amap": state["quota"].amap,
                "cache_hits": state["quota"].cache_hits,
            },
        )
        return state


def _parse_interrupts(interrupts) -> list[InterruptQuestion]:
    out: list[InterruptQuestion] = []
    for item in interrupts:
        try:
            out.append(InterruptQuestion.model_validate(item.value))
        except Exception:  # noqa: BLE001 —— 非本项目产生的中断，忽略而不是崩掉
            log.warning("跳过无法解析的中断", extra={"raw": str(item.value)[:200]})
    return out




async def plan_trip(
    request: TripRequest, *, trip_id: str | None = None, checkpointer=None
) -> TripState:
    """一次性跑完（不需要恢复时用这个）。"""
    return await TripRunner(checkpointer).start(request, trip_id=trip_id)
