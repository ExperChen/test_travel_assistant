"""SSE 事件总线：每个 trip 一个环形缓冲 + 订阅者广播。

断线重连靠 `Last-Event-ID`：服务端为每个 trip 保留最近 N 条事件，重连时先补发
遗漏的部分再接上实时流（架构文档 §8.2）。缓冲是进程内的——多 worker 上线时
要换成 Redis Stream 之类的共享存储。
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from app.core.logging import get_logger
from app.models.events import TripEvent

log = get_logger(__name__)

__all__ = ["EventBus", "TripChannel", "BUFFER_SIZE"]

BUFFER_SIZE = 200
"""每个 trip 保留的历史事件数，够覆盖一次完整规划。"""

TERMINAL = ("done", "error")

HEARTBEAT_S = 15.0
"""多久没有新事件就发一次心跳，好让路由层有机会发现客户端已断开。"""


class TripChannel:
    """单个 trip 的事件序列。"""

    def __init__(self, buffer_size: int = BUFFER_SIZE):
        self._events: deque[TripEvent] = deque(maxlen=buffer_size)
        self._subscribers: set[asyncio.Queue[TripEvent]] = set()
        self._seq = 0
        self.closed = False

    def publish(self, event_type, data: dict) -> TripEvent:
        self._seq += 1
        event = TripEvent(seq=self._seq, type=event_type, data=data)
        self._events.append(event)
        if event.type in TERMINAL:
            self.closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        return event

    def replay(self, after_seq: int) -> list[TripEvent]:
        """补发 seq > after_seq 的历史事件。

        缓冲被挤掉的部分补不回来——这时前端应当退回 `GET /trips/{id}` 拉一次全量。
        """
        return [e for e in self._events if e.seq > after_seq]

    async def subscribe(
        self, after_seq: int = 0, *, heartbeat_s: float = HEARTBEAT_S
    ) -> AsyncIterator[TripEvent | None]:
        """订阅事件流。

        **会周期性 yield None 作为心跳**：挂起等用户回答时可能几分钟没有新事件，
        如果一直阻塞在 queue.get()，路由层就没机会检查客户端是否已经断开——
        生成器会一直挂着不释放。心跳同时也能防止中间的反向代理掐掉空闲连接。
        """
        queue: asyncio.Queue[TripEvent] = asyncio.Queue()
        # 先注册再补发，避免两者之间新产生的事件丢掉
        self._subscribers.add(queue)
        try:
            replayed = self.replay(after_seq)
            for event in replayed:
                yield event
            if self.closed and replayed:
                return

            seen = replayed[-1].seq if replayed else after_seq
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                except TimeoutError:
                    yield None
                    continue
                if event.seq <= seen:
                    continue  # 补发与实时流的重叠部分
                seen = event.seq
                yield event
                if event.type in TERMINAL:
                    return
        finally:
            self._subscribers.discard(queue)

    @property
    def last_seq(self) -> int:
        return self._seq


class EventBus:
    def __init__(self, buffer_size: int = BUFFER_SIZE):
        self._channels: dict[str, TripChannel] = {}
        self._buffer_size = buffer_size

    def channel(self, trip_id: str) -> TripChannel:
        if trip_id not in self._channels:
            self._channels[trip_id] = TripChannel(self._buffer_size)
        return self._channels[trip_id]

    def get(self, trip_id: str) -> TripChannel | None:
        return self._channels.get(trip_id)

    @property
    def channels(self) -> list[str]:
        """当前所有 trip_id。超时清扫任务要遍历它们。"""
        return list(self._channels)

    def drop(self, trip_id: str) -> None:
        self._channels.pop(trip_id, None)
