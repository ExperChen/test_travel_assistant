"""结构化日志。

每条日志都带 trip_id，这样一次规划从 intake 到 done 的全过程可以按 trip_id 串起来；
SerpAPI 是稀缺资源，`tool` / `cache_hit` / `quota_used` 字段用于事后追账（§9.4）。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

__all__ = ["setup_logging", "get_logger", "bind_trip", "current_trip_id"]

_trip_id: ContextVar[str | None] = ContextVar("trip_id", default=None)

# logging.LogRecord 的内置属性，格式化时要排除掉，剩下的才是调用方传的 extra
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"message", "asctime", "taskName"}
)


def current_trip_id() -> str | None:
    return _trip_id.get()


@contextmanager
def bind_trip(trip_id: str) -> Iterator[None]:
    """在这个上下文里打的所有日志自动带上 trip_id。"""
    token = _trip_id.set(trip_id)
    try:
        yield
    finally:
        _trip_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if trip_id := _trip_id.get():
            payload["trip_id"] = trip_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """开发时用的可读格式。"""

    def format(self, record: logging.LogRecord) -> str:
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if trip_id := _trip_id.get():
            extras = {"trip_id": trip_id, **extras}
        suffix = ("  " + " ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"{record.name}  {record.getMessage()}"
        )
        if record.exc_info:
            return base + suffix + "\n" + self.formatException(record.exc_info)
        return base + suffix


def setup_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx 每次请求都打一条 INFO，会把真实业务日志淹掉
    logging.getLogger("httpx").setLevel(logging.WARNING)


class _SafeAdapter(logging.LoggerAdapter):
    """把和 `LogRecord` 内置属性重名的 extra 键改个名，而不是让日志把请求打挂。

    实测踩到的：`log.warning("必去景点没搜到", extra={"name": name})` 会抛
    `KeyError: Attempt to overwrite 'name' in LogRecord`——一个只为排查问题而
    写的日志，把整次规划搞崩了。**可观测性绝不能反过来弄坏业务**，所以这里
    统一兜底，而不是指望每个调用点都记得避开 name/module/filename 这些词。
    """

    def process(self, msg, kwargs):
        extra = kwargs.get("extra")
        if extra:
            kwargs["extra"] = {
                (f"x_{k}" if k in _RESERVED else k): v for k, v in extra.items()
            }
        return msg, kwargs


def get_logger(name: str) -> logging.LoggerAdapter:
    return _SafeAdapter(logging.getLogger(name), {})
