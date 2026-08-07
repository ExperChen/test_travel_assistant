"""SSE 事件与人机交互问题（架构文档 §8.2 / §8.3）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.errors import ApiError, PlanWarning

__all__ = [
    "QuestionKind",
    "QuestionOption",
    "InterruptQuestion",
    "TripEvent",
    "EventType",
]

QuestionKind = Literal["single_choice", "multi_choice", "confirm"]
EventType = Literal["stage", "partial", "question", "warning", "done", "error"]


class QuestionOption(BaseModel):
    key: str
    label: str
    detail: dict[str, Any] = Field(default_factory=dict, description="供前端渲染卡片的原始字段")


class InterruptQuestion(BaseModel):
    """LangGraph `interrupt()` 抛给前端的问题。

    `default` 是超时后自动采用的答案——没有默认值的问题不允许存在，
    否则用户离开页面就会让行程永久卡死（架构文档 §4.3）。
    """

    id: str = Field(description="如 flight.arrival_airport；同时用作 resume 的幂等键")
    kind: QuestionKind = "single_choice"
    title: str
    options: list[QuestionOption] = Field(default_factory=list)
    default: str
    expires_at: datetime

    @classmethod
    def build(
        cls,
        id: str,
        title: str,
        options: list[QuestionOption],
        *,
        default: str | None = None,
        kind: QuestionKind = "single_choice",
        timeout_s: int = 600,
    ) -> InterruptQuestion:
        if not options:
            raise ValueError("中断问题必须至少有一个选项")
        return cls(
            id=id,
            kind=kind,
            title=title,
            options=options,
            default=default or options[0].key,
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout_s),
        )

    def accepts(self, value: str) -> bool:
        return any(o.key == value for o in self.options)

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class TripEvent(BaseModel):
    """SSE 单条事件。`seq` 用于断线重连时的 Last-Event-ID 补发。"""

    seq: int
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def stage(cls, seq: int, phase: str, label: str) -> TripEvent:
        return cls(seq=seq, type="stage", data={"phase": phase, "label": label})

    @classmethod
    def partial(cls, seq: int, key: str, value: Any) -> TripEvent:
        return cls(seq=seq, type="partial", data={"key": key, "value": value})

    @classmethod
    def question(cls, seq: int, q: InterruptQuestion) -> TripEvent:
        return cls(seq=seq, type="question", data=q.model_dump(mode="json"))

    @classmethod
    def warning(cls, seq: int, w: PlanWarning) -> TripEvent:
        return cls(seq=seq, type="warning", data=w.model_dump(mode="json"))

    @classmethod
    def error(cls, seq: int, e: ApiError) -> TripEvent:
        return cls(seq=seq, type="error", data=e.model_dump(mode="json"))
