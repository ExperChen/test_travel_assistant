"""行程相关端点：**只剩参数收集**。

规划本身已经从固定管线改成自主 agent（`app/agents/planner_agent.py`），
而自主循环是分钟级、几十次工具调用、结果是一段 Markdown——它没有
"逐节点推进的状态"可供订阅，原来的 `POST /trips` + SSE + 中断问答那一套
（trip_service / TripChannel / TripEvent）也就随管线一起删掉了。

留下的两条都不烧 SerpAPI 额度、都只做"把话变成参数"：

    POST /trips/chat    多轮累积，缺什么主动问（ReAct）
    POST /trips/parse   单次解析，一句话说不全就在 missing 里报出来

路径前缀保持 `/trips` 没动——改路径会白白弄坏现有调用方，而这两条本来
就是行程创建流程的一部分。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agents.prompt_parser import TripDraft, parse_prompt
from app.agents.react_intake import ReactIntakeAgent, session_store
from app.api.deps import get_profile_id, require_api_key
from app.api.limits import limiter
from app.config import settings
from app.core.logging import get_logger
from app.models.intake import IntakeReply
from app.models.memory import MemorySnapshot
from app.store import get_store

log = get_logger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"], dependencies=[Depends(require_api_key)])


async def _memory_for(profile_id: str) -> MemorySnapshot | None:
    """取记忆快照。任何失败都退化成"没有记忆"，绝不让解析失败。"""
    if not profile_id or not settings.memory_enabled:
        return None
    try:
        return await get_store().snapshot(profile_id)
    except Exception:  # noqa: BLE001 —— 记忆是增量特性
        log.warning("读取记忆失败，按无记忆处理", extra={"profile_id": profile_id})
        return None


class ParseRequest(BaseModel):
    prompt: str = Field(min_length=1, description="一句话需求，如「9月5号从北京去成都玩5天」")


class ChatRequest(BaseModel):
    """多轮参数收集的一轮输入。`session_id` 留空 = 开一段新对话。"""

    message: str = Field(min_length=1, description="用户这一轮说的话")
    session_id: str = Field(default="", description="上一轮返回的 session_id")


@router.post("/chat", response_model=IntakeReply)
@limiter.limit(settings.rate_limit_create)
async def chat_intake(
    payload: ChatRequest, request: Request, profile_id: str = Depends(get_profile_id)
) -> IntakeReply:
    """ReAct 多轮参数收集（Flight ReAct Agent 设计文档 §2）。

    和 `/parse` 的区别是**会累积**：用户第一轮说目的地、第三轮才说日期，
    两者都留在同一个会话里，不需要重复。缺什么由 Agent 主动问，
    记忆里已经有的不问。

    `reply.draft` 有值时说明参数齐了，`draft.request` 可以直接交给规划 agent。

    **不烧 SerpAPI 额度**：这里只用高德和纯计算。
    """
    session = session_store.get_or_create(payload.session_id, profile_id=profile_id)
    memory = await _memory_for(profile_id or session.profile_id)
    reply = await ReactIntakeAgent().run(session, payload.message, memory=memory)
    log.info(
        "intake 一轮",
        extra={"session": reply.session_id, "done": reply.done,
               "missing": reply.missing, "degraded": reply.degraded,
               "steps": len(reply.steps)},
    )
    return reply


@router.post("/parse", response_model=TripDraft)
@limiter.limit(settings.rate_limit_create)
async def parse_prompt_endpoint(
    payload: ParseRequest, request: Request, profile_id: str = Depends(get_profile_id)
) -> TripDraft:
    """把自然语言解析成 `TripRequest` 草稿，**不烧 SerpAPI 额度**。

    按 D2 决策走"解析 → 给用户看 → 确认后再规划"：返回的每个字段都带 `origin`
    （原话 / 推算 / 默认值），`missing` 和 `questions` 告诉前端还缺什么。

    限流按创建档：这条会调模型，比读接口贵得多。
    """
    draft = await parse_prompt(payload.prompt, memory=await _memory_for(profile_id))
    log.info(
        "解析需求",
        extra={"ok": draft.ok, "degraded": draft.degraded, "missing": draft.missing},
    )
    return draft
