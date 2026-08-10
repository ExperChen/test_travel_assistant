"""clarify：把静默套默认值的关键字段，问用户一次（记忆与追问文档 §4）。

当前的失败模式是这样的：用户说「去成都玩几天」，系统默默按 1 位成人、
标准节奏、不限预算规划完，用户拿到行程才发现全不对。**它没说错，但它没问。**

而这几个字段恰恰最影响结果——人数决定机票钱，预算决定酒店档次，
节奏决定一天排几个景点。

## 位置

在 `intake` 之后、`resolve_city` 之前：

- 早于任何花额度的调用（机场补全是第一个烧 SerpAPI 的动作）；
- `intake` 已经校验完日期，此时缺什么是确定的；
- 用户改了人数/预算，后面的机票和酒店查询才用得上。

阻断级的缺失（连目的地都没有）比这更早——在 `/trips/parse` 就该拦下，
根本不该走到创建行程。

## 三条不变量

1. **最多问一轮**，一次把该问的都列出来，不挤牙膏（所以要 `kind="form"`）；
2. **每项都能跳过**，且都有默认值——超时清扫按默认值放行，
   追问不能成为新的卡死点；
3. **记忆已经填上的不问**——这正是记忆的价值所在。
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.types import interrupt

from app.config import settings
from app.core.logging import get_logger
from app.graph.state import TripState
from app.models.events import FormField, InterruptQuestion, QuestionOption
from app.models.memory import BUDGET_LABELS, bucket_to_budget
from app.models.trip import TripRequest

log = get_logger(__name__)

__all__ = ["clarify", "ASKABLE", "build_form_fields", "apply_answers"]

ASKABLE: tuple[str, ...] = ("adults", "budget_per_night", "pace")
"""只问影响大的三项（文档 §4）。

**刻意不问** `travel_class` 和 `transport`：经济舱 + 公共交通的默认值足够安全，
问了只是把追问变成查户口。判断标准是"默认值错了会不会毁掉行程"——
舱位错了贵一点，人数错了整个机票预算就不对。
"""


def _adults_options() -> list[QuestionOption]:
    return [
        QuestionOption(key="1", label="1 人"),
        QuestionOption(key="2", label="2 人"),
        QuestionOption(key="3", label="3 人"),
        QuestionOption(key="4", label="4 人及以上"),
    ]


def _budget_options() -> list[QuestionOption]:
    return [
        QuestionOption(key=key, label=BUDGET_LABELS[key])
        for key in ("under_300", "300_600", "600_1000", "over_1000", "any")
    ]


def _pace_options() -> list[QuestionOption]:
    return [
        QuestionOption(key="relaxed", label="悠闲（一天 2-3 个景点）"),
        QuestionOption(key="standard", label="标准（一天 3-4 个）"),
        QuestionOption(key="packed", label="紧凑（一天 4-5 个）"),
    ]


_SPEC: dict[str, dict[str, Any]] = {
    "adults": {
        "label": "几个人出行",
        "options": _adults_options,
        "default": "1",
        "hint": "影响机票总价与酒店房型",
    },
    "budget_per_night": {
        "label": "每晚住宿预算",
        "options": _budget_options,
        "default": "any",
        "hint": "影响酒店候选的档次",
    },
    "pace": {
        "label": "行程节奏",
        "options": _pace_options,
        "default": "standard",
        "hint": "决定一天排几个景点",
    },
}


def build_form_fields(origins: dict[str, str]) -> list[FormField]:
    """挑出"全靠默认值兜底"的字段。

    判据是 `origins`——它由 `/trips/parse` 产出、创建行程时一并带过来。
    **没有 origins 就不问**：这时我们没有证据说明某个值是用户定的还是系统定的，
    宁可不问（老的调用方、测试、直接构造 TripRequest 的脚本都属于这一类）。
    """
    fields: list[FormField] = []
    for key in ASKABLE:
        if origins.get(key) != "default":
            continue  # 用户说过（prompt）或记忆填的（memory），都不该再问
        spec = _SPEC[key]
        fields.append(
            FormField(
                key=key,
                label=spec["label"],
                options=spec["options"](),
                default=spec["default"],
                hint=spec["hint"],
            )
        )
    return fields


def apply_answers(request: TripRequest, answers: Any) -> tuple[TripRequest, list[str]]:
    """把表单答案落到 `TripRequest` 上。返回 (新请求, 实际改动的字段)。

    容错优先：单个字段答得不合法就跳过它，保留其余的。用户答了三项里的两项，
    不该因为第三项拼错而全丢。
    """
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (TypeError, ValueError):
            return request, []
    if not isinstance(answers, dict):
        return request, []

    updates: dict[str, Any] = {}
    for key, raw in answers.items():
        if key not in ASKABLE or raw is None:
            continue
        value = _coerce(key, str(raw))
        if value is _SKIP:
            continue
        updates[key] = value

    if not updates:
        return request, []
    # 有非法值时 model_copy 不会校验，得显式重建一次让 pydantic 把关
    try:
        merged = request.model_dump()
        merged.update(updates)
        return TripRequest.model_validate(merged), sorted(updates)
    except Exception:  # noqa: BLE001 —— 答案不合法就当没答，别让追问毁掉行程
        log.warning("追问答案校验失败，忽略", extra={"answers": answers})
        return request, []


_SKIP = object()


def _coerce(key: str, raw: str) -> Any:
    if key == "adults":
        return int(raw) if raw.isdigit() and 1 <= int(raw) <= 9 else _SKIP
    if key == "pace":
        return raw if raw in ("relaxed", "standard", "packed") else _SKIP
    if key == "budget_per_night":
        # "any" / "over_1000" 都表示不设上限 → None，和"没填"是同一个意思
        return bucket_to_budget(raw) if raw in BUDGET_LABELS else _SKIP
    return _SKIP


async def clarify(state: TripState) -> dict:
    """追问节点。没什么可问的就直接放行——绝大多数请求走的是这条路。"""
    if not settings.clarify_enabled or state.get("clarified"):
        return {}

    fields = build_form_fields(state.get("origins") or {})
    if not fields:
        return {"clarified": True}

    question = InterruptQuestion.build_form(
        "intake.clarify",
        "还有几件事想确认一下，也可以直接跳过",
        fields,
        timeout_s=settings.interrupt_timeout_s,
    )
    # 挂起；resume 后本节点从头重放，interrupt() 直接返回用户的答案
    answer = interrupt(question.model_dump(mode="json"))

    request, changed = apply_answers(state["request"], answer)
    patch: dict = {"clarified": True}
    if changed:
        patch["request"] = request
        log.info("追问已应用", extra={"changed": changed})
    else:
        log.info("追问被跳过或答案为空，沿用默认值")
    return patch
