"""行程相关端点（架构文档 §8）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agents.prompt_parser import TripDraft, parse_prompt
from app.agents.react_intake import ReactIntakeAgent, session_store
from app.api.deps import get_profile_id, get_service, require_api_key
from app.api.limits import limiter
from app.config import settings
from app.core.logging import get_logger
from app.models.intake import IntakeReply
from app.models.memory import MemorySnapshot
from app.models.trip import TripPlan, TripRequest
from app.services.trip_service import TripService
from app.store import get_store

log = get_logger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"], dependencies=[Depends(require_api_key)])


class CreateTripResponse(BaseModel):
    trip_id: str
    status: str = "running"
    stream_url: str


class CreateTripBody(TripRequest):
    """创建行程的请求体 = `TripRequest` + 可选的字段出处。

    继承而不是包一层，是为了**向后兼容**：老的调用方直接 POST 一个裸
    `TripRequest` 依然合法，`origins` 默认为空。

    `origins` 从 `/trips/parse` 或 `/trips/chat` 的结果里原样带过来，
    追问节点靠它判断哪些值是系统替用户定的。拿不到就不追问——
    没有证据时宁可沿用默认值，也不能凭猜去打断用户。
    """

    origins: dict[str, str] = Field(
        default_factory=dict,
        description="{字段: prompt|memory|derived|default}，来自解析结果",
    )

    def to_request(self) -> TripRequest:
        return TripRequest.model_validate(self.model_dump(exclude={"origins"}))


async def _memory_for(profile_id: str) -> MemorySnapshot | None:
    """取记忆快照。任何失败都退化成"没有记忆"，绝不让解析失败。"""
    if not profile_id or not settings.memory_enabled:
        return None
    try:
        return await get_store().snapshot(profile_id)
    except Exception:  # noqa: BLE001 —— 记忆是增量特性
        log.warning("读取记忆失败，按无记忆处理", extra={"profile_id": profile_id})
        return None


class AnswerRequest(BaseModel):
    """一次可以回答多个问题——并行分支会同时挂起航班和酒店两个。"""

    answers: dict[str, Any] = Field(
        default_factory=dict, description="{question_id: 选中的 option key}"
    )
    question_id: str = Field(default="", description="单问题时的简写，与 value 搭配")
    value: Any = Field(default=None)

    def resolved(self) -> dict[str, Any]:
        if self.answers:
            return self.answers
        if self.question_id:
            return {self.question_id: self.value}
        return {}


class AnswerResponse(BaseModel):
    status: str = "running"
    accepted: list[str]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateTripResponse)
@limiter.limit(settings.rate_limit_create)
async def create_trip(
    payload: CreateTripBody,
    request: Request,
    service: TripService = Depends(get_service),
    profile_id: str = Depends(get_profile_id),
) -> CreateTripResponse:
    """接单即返回，规划在后台跑。

    一次完整规划要几十秒且中途可能停下来问用户，不能占着 HTTP 连接——
    进度走 SSE。

    限流比读接口严得多：**每次创建烧 5 次 SerpAPI**，按读接口的 60/分钟放行，
    一分钟就能把 250 次/月的免费额度打掉 1.2 倍。
    """
    trip_id = service.create(
        payload.to_request(), profile_id=profile_id, origins=payload.origins
    )
    log.info(
        "已受理行程",
        extra={"trip_id": trip_id, "to": payload.destination_city,
               "has_profile": bool(profile_id)},
    )
    return CreateTripResponse(
        trip_id=trip_id,
        stream_url=request.url_for("stream_trip", trip_id=trip_id).path,
    )


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

    `reply.draft` 有值时说明参数齐了，把 `draft.request` 连同
    `draft` 里各字段的 `origin` 一起 POST 到 `/trips` 即可开始规划。

    **不烧 SerpAPI 额度**：机场消歧留在图里做，这里只用高德和纯计算。
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
    """把自然语言解析成 `TripRequest` 草稿，**不创建行程、不烧 SerpAPI 额度**。

    按 D2 决策走"解析 → 给用户看 → 确认后再建"：返回的每个字段都带 `origin`
    （原话 / 推算 / 默认值），`missing` 和 `questions` 告诉前端还缺什么。
    确认无误后把 `draft.request` 原样 POST 到 `/trips` 即可。

    限流按创建档：这条会调模型，比读接口贵得多。
    """
    draft = await parse_prompt(payload.prompt, memory=await _memory_for(profile_id))
    log.info(
        "解析需求",
        extra={"ok": draft.ok, "degraded": draft.degraded, "missing": draft.missing},
    )
    return draft


@router.get("/{trip_id}", response_model=TripPlan)
async def get_trip(trip_id: str, service: TripService = Depends(get_service)) -> TripPlan:
    return await service.get(trip_id)


@router.get("/{trip_id}/stream", name="stream_trip")
async def stream_trip(
    trip_id: str,
    request: Request,
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
    service: TripService = Depends(get_service),
) -> EventSourceResponse:
    """SSE 事件流。断线重连带上 `Last-Event-ID` 即可补发遗漏的事件。"""
    channel = service.channel(trip_id)
    after = int(last_event_id) if last_event_id.isdigit() else 0

    async def publisher():
        async for event in channel.subscribe(after):
            if await request.is_disconnected():
                break
            if event is None:
                yield {"comment": "keepalive"}  # 心跳，只为让上面那行有机会执行
                continue
            yield {
                "id": str(event.seq),
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(publisher())


@router.post("/{trip_id}/answer", response_model=AnswerResponse)
async def answer_trip(
    trip_id: str, payload: AnswerRequest, service: TripService = Depends(get_service)
) -> AnswerResponse:
    answers = payload.resolved()
    await service.answer(trip_id, answers)
    return AnswerResponse(accepted=sorted(answers))
