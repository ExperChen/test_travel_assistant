"""多轮参数收集的会话模型（Flight ReAct Agent 设计文档 §2 / §4）。

这里的"会话"和 checkpointer 管的那个不是一回事：

    IntakeSession  —— 行程**创建之前**的对话，收集参数
    checkpointer   —— 行程**创建之后**的执行状态（L1 会话记忆）

分开是因为它们的生命周期不同：一次 intake 对话可能最终没有创建任何行程
（用户聊了两句走了），而 checkpointer 的 thread 必须由 trip_id 起头。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.prompt_parser import Extraction, TripDraft

__all__ = [
    "Turn",
    "ReactStep",
    "IntakeSession",
    "IntakeReply",
    "SLOT_LABELS",
    "REQUIRED_SLOTS",
]

Role = Literal["user", "agent"]

SLOT_LABELS: dict[str, str] = {
    "departure_city": "出发地",
    "destination_city": "目的地",
    "outbound_date_text": "出发日期",
    "return_date_text": "返程日期",
    "travel_days": "玩几天",
    "adults": "成人数",
    "children": "儿童数",
    "children_ages": "儿童年龄",
    "budget_per_night": "每晚预算",
    "travel_class": "舱位",
    "pace": "节奏",
    "transport": "市内交通",
    "must_visit": "必去",
    "avoid": "排除",
}

REQUIRED_SLOTS: tuple[str, ...] = (
    "departure_city",
    "destination_city",
    "outbound_date_text",
)
"""缺任何一个都不能建行程（记忆与追问文档 §4「阻断」级）。

返程不在其中——它可以由 `travel_days` 推出来，两者有一个即可。
"""


class Turn(BaseModel):
    role: Role
    content: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReactStep(BaseModel):
    """一步 Thought → Action → Observation。

    完整留痕是刻意的：ReAct 的价值有一半在**可解释**上——出了错要能看出
    模型是在哪一步想歪的，而不是只看到一个错误的最终答案。
    """

    thought: str = ""
    action: str = ""
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    error: str = ""


class IntakeSession(BaseModel):
    """一次参数收集对话的全部状态。

    `collected` 是**累积**的——这正是"搜集前面提问中的遗漏信息"的落点：
    用户第一轮说了目的地、第三轮才说日期，两者都留在同一个 `Extraction` 里，
    不需要用户重复。
    """

    session_id: str
    turns: list[Turn] = Field(default_factory=list)
    collected: Extraction = Field(default_factory=Extraction)
    profile_id: str = ""
    asked: list[str] = Field(
        default_factory=list,
        description="已经问过的槽位。同一个字段不追问第二次——问了没答说明用户"
        "不想说，再问就是查户口",
    )
    steps: list[ReactStep] = Field(default_factory=list, description="最近一轮的 ReAct 轨迹")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def record(self, role: Role, content: str) -> None:
        self.turns.append(Turn(role=role, content=content))

    def merge(self, extraction: Extraction) -> list[str]:
        """把新一轮抽到的信息并进累积槽位。返回本轮新填上的字段名。

        **后说的覆盖先说的**——"算了我还是从上海走"必须能改掉之前的北京
        （设计文档 §7「用户改变主意」）。但 `None` 不覆盖：没提到不等于要清空。
        """
        filled: list[str] = []
        for key, value in extraction.model_dump().items():
            if value is None or value == [] or value == "":
                continue
            if getattr(self.collected, key) != value:
                filled.append(key)
            setattr(self.collected, key, value)
        return filled

    def missing_required(self) -> list[str]:
        """还缺哪些**阻断级**字段。"""
        missing = [k for k in REQUIRED_SLOTS if not getattr(self.collected, k, None)]
        # 返程与天数二选一
        if not self.collected.return_date_text and not self.collected.travel_days:
            missing.append("return_date_text")
        return missing

    @property
    def history_text(self) -> str:
        """喂给模型的对话历史。只取最近若干轮，避免无限增长。"""
        recent = self.turns[-12:]
        return "\n".join(
            f"{'用户' if t.role == 'user' else '助手'}：{t.content}" for t in recent
        )


class IntakeReply(BaseModel):
    """一轮对话的产物。"""

    session_id: str
    reply: str = Field(description="给用户看的自然语言回复")
    draft: TripDraft | None = Field(
        default=None, description="参数齐全时才有；可直接 POST 到 /trips"
    )
    missing: list[str] = Field(default_factory=list, description="还缺的阻断级字段（中文标签）")
    steps: list[ReactStep] = Field(default_factory=list, description="本轮 ReAct 轨迹，便于调试")
    degraded: bool = Field(default=False, description="true = 模型没参与，走的规则兜底")

    @property
    def done(self) -> bool:
        return self.draft is not None and self.draft.ok
