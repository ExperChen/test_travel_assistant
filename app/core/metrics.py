"""配额计数。

SerpAPI 免费额度只有 250 次/月，必须能回答"这次规划到底烧了几次"。
用 ContextVar 而不是把 counter 一路传参：Provider 在最底层，中间隔着 Tool、
Agent、编排三层，穿参数会污染每一个签名。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from app.models.common import QuotaCounter

__all__ = ["track_quota", "record_call", "current_quota"]

Provider = Literal["serpapi", "amap", "llm"]

_counter: ContextVar[QuotaCounter | None] = ContextVar("quota_counter", default=None)


def current_quota() -> QuotaCounter | None:
    return _counter.get()


@contextmanager
def track_quota(counter: QuotaCounter | None = None) -> Iterator[QuotaCounter]:
    """在这个上下文里发生的所有 provider 调用都会记到 counter 上。"""
    counter = counter or QuotaCounter()
    token = _counter.set(counter)
    try:
        yield counter
    finally:
        _counter.reset(token)


def record_call(provider: Provider, *, cached: bool = False) -> None:
    """Provider 层在每次调用后回报。没有活动的 counter 时静默忽略。"""
    if (counter := _counter.get()) is not None:
        counter.bump(provider, cached=cached)
