"""行程编排的应用服务层：把图的执行变成一串 SSE 事件。

HTTP 层只跟这里打交道，不直接碰 LangGraph。
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.api.events import EventBus, TripChannel
from app.config import settings
from app.core.exceptions import AnswerMismatch, NotFoundError
from app.core.logging import bind_trip, get_logger
from app.graph.builder import TripRunner, new_trip_id
from app.graph.state import TripState, to_plan
from app.models.errors import ApiError, ErrorCode, PlanWarning
from app.models.memory import (
    REMEMBERED_FIELDS,
    Profile,
    TripHistory,
    VisitedAttraction,
    budget_bucket,
)
from app.models.trip import TripPlan, TripRequest
from app.store import MemoryStore, get_store

log = get_logger(__name__)

__all__ = ["TripService", "STAGE_LABELS"]

STAGE_LABELS: dict[str, str] = {
    "intake": "正在校验行程参数…",
    "resolve_city": "正在定位目的地城市…",
    "flight_departure": "正在确认出发机场…",
    "flight_arrival": "正在确认到达机场…",
    "flight_search": "正在搜索往返航班…",
    "attraction_search": "正在筛选热门景点…",
    "hotel_search": "正在挑选酒店…",
    "route_planner": "正在编排逐日行程…",
    "summarize": "正在生成行程说明…",
}

# 节点补丁里哪些 key 值得作为 partial 推给前端（其余是控制面字段）
PARTIAL_KEYS = ("dest_city", "flight", "attractions", "hotel", "itinerary", "summary")


class TripService:
    def __init__(
        self,
        runner: TripRunner | None = None,
        bus: EventBus | None = None,
        store: MemoryStore | None = None,
    ):
        self.runner = runner or TripRunner()
        self.bus = bus or EventBus()
        self.store = store
        """记忆存储。None 表示按需取全局单例；测试里注入 :memory: 库。"""
        self._tasks: dict[str, asyncio.Task] = {}
        # trip_id → profile_id。规划结束时要知道把记忆写给谁。
        # 和 _quota / _timeouts 一样是进程内记账（架构文档 §4.4）。
        self._profiles: dict[str, str] = {}
        self._sweeper: asyncio.Task | None = None

    def _memory(self) -> MemoryStore:
        return self.store or get_store()

    # ------------------------------------------------------------------
    def create(
        self,
        request: TripRequest,
        *,
        trip_id: str | None = None,
        profile_id: str = "",
        origins: dict[str, str] | None = None,
    ) -> str:
        """接单即返回 trip_id，规划在后台跑——一次完整规划要几十秒，不能占着 HTTP 连接。"""
        trip_id = trip_id or new_trip_id()
        self.bus.channel(trip_id)  # 先建好通道，避免客户端抢在前面订阅时拿不到
        self._profiles[trip_id] = profile_id
        self._spawn(
            trip_id,
            self.runner.start_stream(
                request, trip_id=trip_id, profile_id=profile_id, origins=origins
            ),
        )
        return trip_id

    async def answer(self, trip_id: str, answers: dict[str, Any]) -> None:
        channel = self._require_channel(trip_id)
        if trip_id in self._tasks and not self._tasks[trip_id].done():
            raise AnswerMismatch("这次行程还在处理中，请等当前问题推送出来再回答")

        # 先校验再起后台任务：答案不合法要能同步回 409，而不是异步地悄悄失败
        await self.runner.translate_answers(trip_id, answers)
        channel.publish("stage", {"phase": "resuming", "label": "已收到你的选择，继续规划…"})
        self._spawn(trip_id, self.runner.resume_stream(trip_id, answers))

    async def expire(self, trip_id: str) -> bool:
        """把超时未答的问题按默认值放行。返回是否真的有问题过期。

        走 `answer()` 而不是 runner 的 `resume_expired()`：后者是阻塞式的，
        还挂在 SSE 上的客户端就看不到后续进度了。超时警告由 `note_expired()`
        记账，两条路径共用。
        """
        if trip_id in self._tasks and not self._tasks[trip_id].done():
            return False
        expired = await self.runner.note_expired(trip_id)
        if not expired:
            return False
        await self.answer(trip_id, {q.id: q.default for q in expired})
        return True

    async def sweep_expired(self) -> int:
        """扫一遍所有挂起的行程，把超时的按默认值放行。返回处理了几个。"""
        swept = 0
        for trip_id in list(self.bus.channels):
            try:
                if await self.expire(trip_id):
                    swept += 1
            except Exception:  # noqa: BLE001 —— 一个行程出错不能中断整轮清扫
                log.exception("超时清扫失败", extra={"trip_id": trip_id})
        return swept

    def start_sweeper(self, interval_s: float | None = None) -> None:
        """启动后台清扫循环。

        **没有它，超时机制形同虚设**：用户看到"选哪个机场"就关掉页面，这次行程
        会永远卡在 waiting_input，checkpointer 里的 thread 也永远不释放。
        `InterruptQuestion.expires_at` 只是个时间戳，得有人来检查它。
        """
        if self._sweeper is not None and not self._sweeper.done():
            return
        period = interval_s or max(settings.interrupt_timeout_s / 4, 15)
        self._sweeper = asyncio.create_task(self._sweep_loop(period))
        log.info("超时清扫任务已启动", extra={"interval_s": period})

    async def _sweep_loop(self, interval_s: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval_s)
                if swept := await self.sweep_expired():
                    log.info("超时自动放行", extra={"trips": swept})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 循环必须活着，否则超时机制又没了
                log.exception("清扫循环异常，继续下一轮")

    async def get(self, trip_id: str) -> TripPlan:
        self._require_channel(trip_id)
        return to_plan(await self.runner.finalize(trip_id))

    def channel(self, trip_id: str) -> TripChannel:
        return self._require_channel(trip_id)

    async def aclose(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

    # ------------------------------------------------------------------
    def _require_channel(self, trip_id: str) -> TripChannel:
        channel = self.bus.get(trip_id)
        if channel is None:
            raise NotFoundError(f"找不到行程 {trip_id}", trip_id=trip_id)
        return channel

    def _spawn(self, trip_id: str, stream) -> None:
        task = asyncio.create_task(self._drive(trip_id, stream))
        self._tasks[trip_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(trip_id, None) if t.done() else None)

    async def _drive(self, trip_id: str, stream) -> None:
        """消费图的更新流，翻译成 SSE 事件。"""
        channel = self.bus.channel(trip_id)
        try:
            with bind_trip(trip_id):
                async for node, patch in stream:
                    channel.publish(
                        "stage", {"phase": node, "label": STAGE_LABELS.get(node, node)}
                    )
                    self._emit_warnings(channel, patch)
                    for key in PARTIAL_KEYS:
                        if key in patch and patch[key] is not None:
                            channel.publish("partial", {"key": key, "value": _dump(patch[key])})

                state = await self.runner.finalize(trip_id)
                self._emit_terminal(channel, state)
                await self._remember(trip_id, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 后台任务里漏掉异常会让客户端永远挂着
            log.exception("行程规划任务异常", extra={"trip_id": trip_id})
            channel.publish(
                "error",
                ApiError.of(ErrorCode.INTERNAL, f"规划过程出错：{exc}").model_dump(mode="json"),
            )

    async def _remember(self, trip_id: str, state: TripState) -> None:
        """规划成功结束时落盘记忆（L2 偏好 + L3 履历）。

        **只在 `status == "done"` 时写**（文档 §2）：解析阶段的值可能被用户在
        中断点改掉——机场选了别的、酒店换了一家、追问里把人数从 1 改成 3——
        以最终生效的 `TripRequest` 为准才有意义。

        整个方法是 best-effort：记忆写失败绝不能影响已经交付给用户的行程。
        它跑在 `_emit_terminal` **之后**，所以哪怕这里整段炸掉，
        用户也已经拿到 `done` 事件了。
        """
        profile_id = self._profiles.pop(trip_id, "")
        if not profile_id or not settings.memory_enabled:
            return
        if state.get("status") != "done":
            return

        try:
            store = self._memory()
            request: TripRequest = state["request"]
            today = date.today()

            profile = await store.load_profile(profile_id) or Profile(profile_id=profile_id)
            updated = profile.observe_all(preference_payload(request), on=today)
            await store.save_profile(updated)

            # L3：只记**最终排进行程的**景点。候选和备选都不算去过。
            itinerary = state.get("itinerary")
            city = state.get("dest_city")
            if itinerary is not None and city is not None:
                visited = [
                    VisitedAttraction(poi_id=item.ref_id, name=item.name)
                    for day in itinerary.days
                    for item in day.items
                    if item.kind == "attraction" and item.ref_id
                ]
                await store.record_trip(
                    profile_id,
                    TripHistory(
                        trip_id=trip_id,
                        city=city.name,
                        adcode=city.adcode,
                        start_date=request.outbound_date,
                        end_date=request.return_date,
                        attractions=visited,
                    ),
                )
            log.info(
                "记忆已更新",
                extra={"profile_id": profile_id,
                       "prefs": sorted(updated.preferences),
                       "visited": len(visited) if itinerary is not None else 0},
            )
        except Exception:  # noqa: BLE001 —— 记忆坏掉不能影响已交付的行程
            log.exception("写入记忆失败", extra={"trip_id": trip_id})

    def _emit_warnings(self, channel: TripChannel, patch: dict) -> None:
        """updates 流给的是**本节点新增的**补丁，所以补丁里的 warnings 直接全推，
        不会重复。"""
        for warning in patch.get("warnings") or []:
            if isinstance(warning, PlanWarning):
                channel.publish("warning", warning.model_dump(mode="json"))

    def _emit_terminal(self, channel: TripChannel, state: TripState) -> None:
        for question in state.get("pending") or []:
            channel.publish("question", question.model_dump(mode="json"))
        if state.get("pending"):
            return  # 等用户回答，通道不关

        plan = to_plan(state)
        if plan.status == "failed" and plan.error is not None:
            channel.publish("error", plan.error.model_dump(mode="json"))
        else:
            channel.publish("done", plan.model_dump(mode="json"))


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def preference_payload(request: TripRequest) -> dict[str, Any]:
    """从最终生效的 `TripRequest` 里摘出该记住的字段（记忆与追问文档 §2）。

    预算**记档位不记数字**：去三亚和去县城的预算不是一回事，只有档位跨行程稳定。
    """
    payload: dict[str, Any] = {}
    for key in REMEMBERED_FIELDS:
        value = getattr(request, key, None)
        if value is None or value == []:
            continue
        payload[key] = budget_bucket(value) if key == "budget_per_night" else value
    return payload
