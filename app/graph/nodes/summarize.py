"""summarize：把排好的行程翻译成一段自然语言说明。

图的最后一个节点。它失败也只是少一段文案，行程数据本身早已成型——
所以这里不产生任何 fail()。
"""

from __future__ import annotations

from app.agents.summarizer import build_digest, summarize
from app.core.logging import get_logger
from app.graph.state import TripState, to_plan

log = get_logger(__name__)

__all__ = ["summarize_node"]


async def summarize_node(state: TripState) -> dict:
    if state.get("errors"):
        return {}
    # 上游 route_planner 会被汇合语义触发多次，本节点跟着被触发多次。
    # 没有这道守卫就会重复调用 LLM——那是真金白银。
    if state.get("summary"):
        log.info("说明已生成，跳过重复触发")
        return {}
    if not state.get("itinerary"):
        # 可能只是上游还没跑完（汇合语义下本节点会被触发多次）。这里**不能**
        # 顺手把 phase 置成 done——行程还没生成就宣告完成是错的。真的没有行程时
        # finalize() 也会把 status 收成终态，不差这一笔。
        log.info("还没有行程可供描述，等下一次触发")
        return {}

    digest = build_digest(to_plan(state))
    text, warning = await summarize(digest)

    patch: dict = {"summary": text, "phase": "done"}
    if warning:
        patch["warnings"] = [warning]
    log.info("行程说明已生成", extra={"chars": len(text), "fallback": warning is not None})
    return patch
