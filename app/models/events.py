"""SSE 事件与人机交互问题（架构文档 §8.2 / §8.3）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.errors import ApiError, PlanWarning

__all__ = [
    "QuestionKind",
    "QuestionOption",
    "FormField",
    "InterruptQuestion",
    "TripEvent",
    "EventType",
]

QuestionKind = Literal["single_choice", "multi_choice", "confirm", "form"]
"""`form` 是为追问新增的（记忆与追问文档 §4）：**一次收多个字段**。

其余三种一次只能问一个字段。人数、预算、节奏拆成三次中断，用户要点三轮，
体验比一次问完差得多——而这三件事恰恰该一起问。
"""

EventType = Literal["stage", "partial", "question", "warning", "done", "error"]


class QuestionOption(BaseModel):
    key: str
    label: str
    detail: dict[str, Any] = Field(default_factory=dict, description="供前端渲染卡片的原始字段")


class FormField(BaseModel):
    """`kind="form"` 时的一个子字段。

    每个子字段都必须有 `default`——理由和 `InterruptQuestion.default` 一样：
    超时清扫要能按默认值放行，追问不能成为新的卡死点。
    """

    key: str = Field(description="对应 TripRequest 的字段名，如 adults / pace")
    label: str
    kind: QuestionKind = "single_choice"
    options: list[QuestionOption] = Field(default_factory=list)
    default: str
    hint: str = Field(default="", description="为什么问这个，如「影响机票总价」")


class InterruptQuestion(BaseModel):
    """LangGraph `interrupt()` 抛给前端的问题。

    `default` 是超时后自动采用的答案——没有默认值的问题不允许存在，
    否则用户离开页面就会让行程永久卡死（架构文档 §4.3）。
    """

    id: str = Field(description="如 flight.arrival_airport；同时用作 resume 的幂等键")
    kind: QuestionKind = "single_choice"
    title: str
    options: list[QuestionOption] = Field(default_factory=list)
    fields: list[FormField] = Field(
        default_factory=list, description="仅 kind=form：一次要收的多个字段"
    )
    skippable: bool = Field(
        default=False,
        description="可跳过。追问用的「可选」级问题都是可跳过的——"
        "缺这几个字段不影响能不能规划，只影响规划得准不准",
    )
    default: str
    expires_at: datetime

    @classmethod
    def build_form(
        cls,
        id: str,
        title: str,
        fields: list[FormField],
        *,
        timeout_s: int = 600,
    ) -> InterruptQuestion:
        """一次收多个字段的表单式提问。

        `default` 存成 JSON：超时清扫走的是统一的「用 default 恢复」路径，
        表单也必须能塞进那个字符串口子里，不然追问会变成新的卡死点。
        """
        if not fields:
            raise ValueError("表单问题必须至少有一个字段")
        import json

        return cls(
            id=id,
            kind="form",
            title=title,
            fields=fields,
            skippable=True,
            default=json.dumps({f.key: f.default for f in fields}, ensure_ascii=False),
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout_s),
        )

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
        if self.kind == "form":
            # 表单的答案是 {字段: 选项key} 的映射，逐字段校验
            import json

            try:
                answers = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError):
                return False
            if not isinstance(answers, dict):
                return False
            by_key = {f.key: f for f in self.fields}

            def _ok(key: str, value: Any) -> bool:
                field = by_key.get(key)
                if field is None:
                    return False
                return not field.options or any(o.key == value for o in field.options)

            return all(_ok(k, v) for k, v in answers.items())
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
