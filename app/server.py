import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# main_agent (and the agents/tools it calls) print() Chinese text and emoji for
# debugging. On Windows, uvicorn's stdout/stderr default to the console's active
# codepage (often GBK / cp936), which can't encode most of that output and raises
# UnicodeEncodeError, crashing the request. Force UTF-8 regardless of how the
# process was launched or what PYTHONIOENCODING is set to.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 导入 main_agent 统一入口
from app.agents.main_agent import run_test_main_agent_flow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.server")

_MAX_INPUT_LENGTH = 2000
_MAX_MUST_VISIT_ITEMS = 10
_MAX_ATTRACTION_NAME_LENGTH = 200

# Shared-secret gate on the main endpoint. Not per-user auth -- just enough to stop
# anonymous internet-wide scripted abuse from burning paid Gemini/SerpAPI quota.
# Left unset, the endpoint is open (matches this project's existing "missing key ->
# degrade, don't hard-fail" convention) -- set it for anything beyond local dev.
_APP_API_KEY = os.getenv("APP_API_KEY", "").strip()

# Comma-separated allowed frontend origins; defaults to the local Vite dev server.
_CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

_RATE_LIMIT = os.getenv("APP_RATE_LIMIT", "20/hour")

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="AI Travel Assistant API")
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _empty_data() -> Dict[str, Any]:
    return {"input": "", "output": "", "flights": [], "hotels": [], "views": []}


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message, "data": _empty_data()},
    )


@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return _error_response(429, "Too many requests. Please try again later.")


def _require_api_key(x_api_key: str = Header(default="")) -> None:
    if not _APP_API_KEY:
        return
    if not secrets.compare_digest(x_api_key or "", _APP_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class BudgetModel(BaseModel):
    min: float = 0
    max: float = 10000
    currency: str = "MYR"


class TimeRangeModel(BaseModel):
    start_date: str = ""
    end_date: str = ""


class GenerateItineraryRequest(BaseModel):
    input: Optional[str] = Field(default=None, max_length=_MAX_INPUT_LENGTH)
    # Structured fallback fields, used only when `input` is not provided.
    departure: Optional[str] = None
    destination: Optional[List[str]] = None
    pax: Optional[int] = Field(default=None, ge=1, le=20)
    time: Optional[TimeRangeModel] = None
    # Accepted regardless of whether `input` or the structured fields were used.
    budget: Optional[BudgetModel] = None
    must_visit_attractions: Optional[List[str]] = Field(default=None, max_length=_MAX_MUST_VISIT_ITEMS)


@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/agent/generate_itinerary", dependencies=[Depends(_require_api_key)])
@limiter.limit(_RATE_LIMIT)
def generate_itinerary(request: Request, payload: GenerateItineraryRequest):
    user_input = payload.input.strip() if payload.input else ""
    if not user_input:
        # 兼容旧逻辑，如果没有 input，则拼接一个自然语言 input 传给 main_agent
        dest = payload.destination[0] if payload.destination else ""
        dep = payload.departure or ""
        if not dest and not dep:
            return _error_response(400, "Request rejected: 'input' is required.")

        pax = payload.pax or 1
        start = payload.time.start_date if payload.time else ""
        end = payload.time.end_date if payload.time else ""
        user_input = f"From {dep} to {dest}, {pax} person. From {start} to {end}"

    must_visit = None
    if payload.must_visit_attractions:
        must_visit = [
            name.strip()[:_MAX_ATTRACTION_NAME_LENGTH]
            for name in payload.must_visit_attractions
            if isinstance(name, str) and name.strip()
        ] or None

    # 将请求委托给 main_agent
    try:
        result = run_test_main_agent_flow(
            user_input,
            budget=payload.budget.model_dump() if payload.budget else None,
            pax=payload.pax,
            must_visit_attractions=must_visit,
        )
    except Exception:
        logger.exception("generate_itinerary failed for input=%r", user_input)
        return _error_response(500, "行程生成失败，请稍后重试。")

    # main_agent 返回的 stored_payload 已经符合前端所需格式
    stored_payload = result.get("stored_payload", {})

    # 如果 code 不是 200，说明内部（LLM/第三方服务）报错
    if stored_payload.get("code") != 200:
        logger.warning("main_agent reported a non-200 payload: %r", stored_payload.get("message"))
        return _error_response(502, "上游服务异常，请稍后重试。")

    return stored_payload
