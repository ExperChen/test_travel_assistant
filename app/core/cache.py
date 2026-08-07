"""带 TTL 的进程内缓存。

存在的理由很实际：SerpAPI 免费额度只有 250 次/月（架构文档 §6.1），一次完整
规划要烧 2~5 次。同一用户反复提交相同参数如果每次都真发请求，额度几天就没了。
接口与 Redis 一致，后续换掉实现不影响调用方。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["TTLCache", "CacheStats", "make_key", "cache"]

_MISS = object()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


def make_key(namespace: str, payload: Any) -> str:
    """把任意参数结构压成稳定的缓存键。

    dict 按 key 排序后序列化，保证 `{a,b}` 与 `{b,a}` 命中同一条缓存。
    """
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"{namespace}:{digest}"


class TTLCache:
    """线程安全的 LRU + TTL 缓存。"""

    def __init__(self, max_entries: int = 2048, *, clock: Callable[[], float] = time.monotonic):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key, _MISS)
            if item is _MISS:
                self.stats.misses += 1
                return default
            expires_at, value = item  # type: ignore[misc]
            if expires_at <= self._clock():
                del self._data[key]
                self.stats.misses += 1
                return default
            self._data.move_to_end(key)
            self.stats.hits += 1
            return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            return
        with self._lock:
            self._data[key] = (self._clock() + ttl_s, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
                self.stats.evictions += 1

    def has(self, key: str) -> bool:
        return self.get(key, _MISS) is not _MISS

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.stats = CacheStats()

    def __len__(self) -> int:
        return len(self._data)


cache = TTLCache()
"""全局默认缓存实例。Provider 层直接用它，测试里调 `cache.clear()` 隔离用例。"""
