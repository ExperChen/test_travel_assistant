"""重试与熔断（架构文档 §9.3）。

策略：5xx / 超时 / 限流 指数退避 3 次（0.5s、1.5s、4s）；4xx 不重试；
单个 provider 连续 5 次失败就熔断 60s，期间直接走降级路径而不是继续排队等超时。
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

__all__ = ["retry_async", "CircuitBreaker", "CircuitOpenError"]

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """熔断器打开期间拒绝请求。调用方应立即走降级分支。"""

    def __init__(self, name: str, retry_after_s: float):
        super().__init__(f"{name} 暂时不可用（熔断中），{retry_after_s:.0f}s 后重试")
        self.name = name
        self.retry_after_s = retry_after_s


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    delays: Sequence[float] = (0.5, 1.5, 4.0),
    should_retry: Callable[[BaseException], bool] = lambda _: True,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    jitter: float = 0.1,
) -> T:
    """按 `delays` 重试异步调用。

    `delays` 的长度即最多重试次数（总调用次数 = len(delays) + 1）。
    `should_retry` 返回 False 时立刻抛出——4xx 这类确定性错误重试只是浪费额度。
    """
    last: BaseException
    for attempt, delay in enumerate([*delays, None]):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 —— 由 should_retry 决定放行还是重试
            last = exc
            if delay is None or not should_retry(exc):
                raise
            wait = delay * (1 + random.uniform(-jitter, jitter))
            if on_retry:
                on_retry(attempt + 1, exc, wait)
            await asyncio.sleep(wait)
    raise last  # pragma: no cover —— 循环必然 return 或 raise


class CircuitBreaker:
    """极简熔断器：closed -> (连续失败达阈值) -> open -> (冷却结束) -> half-open。

    half-open 状态放一个请求过去探路：成功则关闭，失败则重新打开。
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self._threshold = failure_threshold
        self._reset_after = reset_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._opened_at is not None and not self._cooled_down()

    def _cooled_down(self) -> bool:
        return self._opened_at is not None and self._clock() - self._opened_at >= self._reset_after

    def check(self) -> None:
        """进入受保护调用前调用；熔断中直接抛 CircuitOpenError。"""
        with self._lock:
            if self._opened_at is None:
                return
            if self._cooled_down():
                return  # half-open：放行这一个请求探路
            raise CircuitOpenError(
                self.name, self._reset_after - (self._clock() - self._opened_at)
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
