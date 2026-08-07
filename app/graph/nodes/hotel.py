"""hotel_search：构造锚点 → 搜索 → 按景点重心重排 → 选定。

排在 attraction_search 之后不是随意的：酒店的好坏在行程里主要体现为"离要去的
景点近不近"，所以必须先有景点重心才能重排。代价是这两步不能并行——但它们都是
秒级，而最慢的航班分支在第 7 步会与之并行，关键路径不受影响。
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.agents.hotel_agent import (
    MIN_EXPECTED,
    attach_addresses,
    attach_commute,
    build_query,
    drop_over_budget,
    fallback_to_amap,
    nights_between,
    pick_options,
    rerank_hotels,
    search_hotels,
)
from app.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.graph.nodes._common import fail
from app.graph.state import TripState
from app.models.errors import ErrorCode, PlanWarning
from app.models.events import InterruptQuestion, QuestionOption
from app.models.hotel import HotelBranch, HotelCandidate, price_text

log = get_logger(__name__)

__all__ = ["hotel_search"]


def _label(c: HotelCandidate, nights: int) -> str:
    """选项文案。**每晚价和总价必须一起给**，只给一个用户就没法比：
    ads 只有单晚价、organic 才有 total_rate，混排会让「总价 ¥301」看着比
    「¥190/晚」贵，而前者每晚其实才 ¥100。"""
    stars = f"{c.hotel_class}★ " if c.hotel_class else ""
    price = _price_label(c, nights)

    rating = f" · {c.overall_rating:.1f}分" if c.overall_rating else ""
    commute = (
        f" · 到景点重心 {c.commute_to_centroid_min} 分钟"
        if c.commute_to_centroid_min is not None
        else ""
    )
    ad = " · 广告" if c.is_ad else ""
    return f"{stars}{c.name} · {price}{rating}{commute}{ad}"


def _price_label(c: HotelCandidate, nights: int) -> str:
    if c.price_unavailable:
        return "价格暂无"

    return price_text(c.total_price, c.nightly_price, nights)


async def hotel_search(state: TripState) -> dict:
    request = state["request"]
    city = state["dest_city"]
    locale = state["locale"]
    attractions = state.get("attractions")
    selected_attractions = attractions.selected if attractions else []

    warnings: list[PlanWarning] = []
    query = build_query(city, selected_attractions)

    try:
        candidates = await search_hotels(
            request,
            query,
            gl=locale.gl,
            hl=locale.hl,
            currency=locale.currency,
        )
    except AppError as exc:
        return fail(exc.code, exc.message, city=city.name)

    if not candidates:
        # 大陆中小城市 Google Hotels 常常没有房源；有坐标就够路径规划用了
        log.info("Google Hotels 无结果，降级到高德", extra={"city": city.name, "q": query})
        try:
            candidates, warning = await fallback_to_amap(city)
            warnings.append(warning)
        except AppError as exc:
            return fail(exc.code, exc.message, city=city.name)

    if not candidates:
        return fail(ErrorCode.NO_HOTELS, f"{city.name} 没有查到任何酒店", city=city.name)

    # 放在算通勤之前：超预算的先扔掉，省得给它们白算距离
    nights = nights_between(request.outbound_date, request.return_date)
    candidates, budget_warning = drop_over_budget(candidates, request.budget_per_night, nights)
    if budget_warning:
        warnings.append(budget_warning)

    if attractions and attractions.centroid:
        try:
            candidates = await attach_commute(candidates, attractions.centroid)
        except AppError as exc:
            # 拿不到通勤时长不致命，重排退化成价格+评分
            log.warning("酒店通勤时长计算失败", extra={"err": exc.message})
            warnings.append(
                PlanWarning.of(
                    "HOTEL_COMMUTE_UNKNOWN",
                    "没能算出酒店到景点的通勤时长，排序仅参考价格与评分",
                    stage="hotel",
                )
            )

    top = rerank_hotels(candidates, nights)
    # 补地址放在重排之后：只给最终要展示的 8 家反查，一次批量调用搞定，
    # 而不是给上游返回的几十家全查一遍
    top = await attach_addresses(top)

    if len(top) < MIN_EXPECTED:
        # 说清是"这地方就这么多"，而不是我们挑剩的
        warnings.append(
            PlanWarning.of(
                "HOTEL_FEW_CANDIDATES",
                f"该城市只找到 {len(top)} 家符合条件的酒店",
                stage="hotel",
            )
        )

    if request.auto_select or len(top) == 1:
        index = 0
        if len(top) > 1:
            warnings.append(
                PlanWarning.of(
                    "HOTEL_AUTO_PICKED",
                    f"共 {len(top)} 家候选酒店，已自动选择综合评分最高的「{top[0].name}」",
                    stage="hotel",
                )
            )
    else:
        # 候选保留 8 家（都在 patch 里返回给前端），但只拿几家问用户；
        # pick_options 会保证非广告位有位置——只列广告等于没得选
        asked = pick_options(top)
        question = InterruptQuestion.build(
            "hotel.selection",
            f"为你筛出 {len(top)} 家酒店，推荐这 {len(asked)} 家，住哪家？",
            [
                QuestionOption(
                    key=str(i), label=_label(c, nights), detail=c.model_dump(mode="json")
                )
                for i, c in enumerate(asked, 1)
            ],
            timeout_s=settings.interrupt_timeout_s,
        )
        answer = str(interrupt(question.model_dump(mode="json")))
        # 上界是 asked 而不是 top：只列了几个选项，回答 "7" 属于越界
        choice = int(answer) - 1 if answer.isdigit() and 0 < int(answer) <= len(asked) else 0
        # asked 不一定是 top 的前几个（pick_options 会换掉广告位），所以必须
        # 把选项序号映回 top 里的真实下标，否则用户的选择会落到别的酒店上
        position = {id(c): i for i, c in enumerate(top)}
        index = position[id(asked[choice])]

    patch: dict = {
        "hotel": HotelBranch(candidates=top, selected_index=index),
        "phase": "planning",
    }
    if warnings:
        patch["warnings"] = warnings

    log.info(
        "酒店已选定",
        extra={
            "city": city.name,
            "q": query,
            "candidates": len(top),
            "picked": top[index].name,
            "commute_min": top[index].commute_to_centroid_min,
        },
    )
    return patch
