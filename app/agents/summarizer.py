"""行程说明生成（架构文档 §5.5）。

LLM 只负责**复述**，不负责计算。喂进去的是裁剪后的确定性 JSON（没有 polyline、
没有原始 API 响应），system prompt 明令禁止编造任何时间、价格、航班号、景点。

拿不到 LLM 时退回确定性模板——这不是残次品：模板文案里的每个数字都来自
`Itinerary`，而 LLM 的输出还得反过来校验。在 Gemini 调不通的网络环境里
（中国大陆直连会返回 `User location is not supported`），模板就是实际跑的路径。
"""

from __future__ import annotations

import re

from app.config import settings
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.models.errors import PlanWarning
from app.models.trip import TripPlan

log = get_logger(__name__)

__all__ = [
    "build_digest",
    "render_fallback",
    "strip_markup",
    "summarize",
    "SYSTEM_PROMPT",
    "MAX_SUMMARY_CHARS",
]

MAX_SUMMARY_CHARS = 400

SYSTEM_PROMPT = """你是行程助手，负责把一份**已经排好的**行程翻译成一段自然语言说明。

硬性规则：
1. 只能使用输入 JSON 里出现的数字与名称。**禁止**编造或推算任何时间、价格、
   航班号、酒店名、景点名——包括"大约""预计"这类模糊化的编造。
2. JSON 里没有的信息就不要提。缺价格就别谈钱。
3. 输出纯文本 + 轻量 Markdown（可用 ** 和 -）。**禁止输出任何 HTML 标签**。
4. 不超过 400 字。按天组织，先说整体节奏，再点出每天的主线。
5. 用中文，语气平实，不要营销腔。
6. 景点只说**先后顺序**，不要给具体几点几分——JSON 里本来就没有，编一个出来
   等于把内部中间值说成对用户的承诺。航班时刻例外，那是真实存在的。
7. 提到"合计"时，**机票和住宿两个分项必须同时写出来**。只写一项再给合计，
   读起来就像算错了（"机票1927元，合计2228元"）。要么三个数都给，要么都不给。
8. 说房价必须带上住几晚，"总价301元"没有参照系——住 3 晚和住 10 晚差别巨大。"""

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|iframe).*?</\1>")


def strip_markup(text: str) -> str:
    """去掉 HTML 标签。

    prompt 里已经禁止输出 HTML，但**提示不是约束**——模型跑偏或被行程里的
    景点名注入时照样会吐标签，前端直接渲染就是 XSS。这道是真正的防线。
    """
    cleaned = _SCRIPT_RE.sub("", text or "")
    cleaned = _TAG_RE.sub("", cleaned)
    return cleaned.strip()


_SENTENCE_END = "。！？!?\n"


def truncate_at_sentence(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """超长时截到最后一个完整句子，别从字中间切开。

    硬切会得到「- **9/」这种断头文本，读起来像是程序崩了。
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(ch) for ch in _SENTENCE_END)
    return (head[: cut + 1] if cut > limit // 2 else head).rstrip()


def build_digest(plan: TripPlan) -> dict:
    """压成喂给 LLM 的精简结构。

    绝不放 polyline、原始响应、候选池——那些进上下文只会烧 token 和喂幻觉。
    """
    itinerary = plan.itinerary
    digest: dict = {
        "目的地": plan.destination.name if plan.destination else plan.request.destination_city,
        "出发地": plan.request.departure_city,
        "日期": f"{plan.request.outbound_date} 至 {plan.request.return_date}",
        "人数": plan.request.adults + plan.request.children,
        "节奏": plan.request.pace,
        "市内交通": plan.request.transport,
    }

    if (flight := plan.flights) and (selected := flight.selected):
        digest["航班"] = {
            "去程落地": flight.arrive_at.strftime("%m-%d %H:%M") if flight.arrive_at else None,
            "返程起飞": flight.depart_at.strftime("%m-%d %H:%M") if flight.depart_at else None,
            "中转次数": selected.stops,
            "价格": selected.price,
        }

    if (hotel := plan.hotel) and (chosen := hotel.selected):
        nights = plan.request.nights
        # 每晚价和总价一起给。只给总价，模型会写出"总价301元"这种没有参照系的
        # 数字——住 3 晚还是 10 晚差别巨大，而 digest 里没有别的地方说得清。
        digest["酒店"] = {
            "名称": chosen.name,
            "星级": chosen.hotel_class,
            "评分": chosen.overall_rating,
            "住几晚": nights,
            "每晚价": None if chosen.price_unavailable else _nightly(chosen, nights),
            "总价": None if chosen.price_unavailable else chosen.total_price,
            "到景点重心": chosen.commute_to_centroid_min,
        }

    if itinerary:
        # 只给顺序，不给钟点。排期算法内部照样算精确时刻（要卡营业时间和航班
        # 窗口），但让模型复述"09:20-11:20"会把一个内部中间值说成对用户的承诺，
        # 路上一堵就全错位。数据仍在 DayItem 里，接口照常返回。
        digest["每日安排"] = [
            {
                "第几天": day.day_index,
                "日期": str(day.day),
                "景点": [
                    {"顺序": i, "名称": item.name}
                    for i, item in enumerate(
                        (x for x in day.items if x.kind == "attraction"), 1
                    )
                ],
                "通勤分钟": day.total_commute_min,
            }
            for day in itinerary.days
        ]
        digest["行程强度"] = {"全程通勤分钟": itinerary.total_commute_min}
        if itinerary.unscheduled:
            digest["未排入"] = [a.name for a in itinerary.unscheduled[:5]]

    # 花费只算机票 + 住宿。门票查不到（高德极少返回 cost），市内交通金额小误差大，
    # 两者进了 digest 模型就会去复述它们，报一个不可信的数字不如不报。
    costs = plan.costs
    if (total := costs.total_cny) is not None:
        digest["预估花费"] = {
            "机票": costs.flight_cny,
            "住宿": costs.hotel_cny,
            "合计": total,
        }

    return digest


def _nightly(hotel, nights: int) -> float | None:
    """总价优先折算，没总价才用起价。和展示层同一口径。"""
    if hotel.total_price is not None:
        return round(hotel.total_price / max(nights, 1), 2)
    return hotel.nightly_price


def render_fallback(digest: dict) -> str:
    """确定性模板。每个数字都直接来自 digest，不经过任何推断。"""
    lines: list[str] = []
    days = digest.get("每日安排") or []
    spots = sum(len(day["景点"]) for day in days)
    lines.append(
        f"**{digest['出发地']} → {digest['目的地']}**，{digest['日期']}，"
        f"共 {len(days)} 天 {spots} 个景点。"
    )

    if hotel := digest.get("酒店"):
        parts = [f"入住 **{hotel['名称']}**"]
        if hotel.get("每晚价"):
            nights = hotel.get("住几晚") or 1
            parts.append(f"每晚 ¥{hotel['每晚价']:.0f} × {nights} 晚")
        if hotel.get("总价"):
            parts.append(f"共 ¥{hotel['总价']:.0f}")
        if hotel.get("到景点重心") is not None:
            parts.append(f"到景点集中区约 {hotel['到景点重心']} 分钟")
        lines.append("，".join(parts) + "。")

    for day in days:
        if not day["景点"]:
            continue
        names = " → ".join(item["名称"] for item in day["景点"])
        lines.append(f"- 第 {day['第几天']} 天（{day['日期']}）：{names}")

    if costs := digest.get("预估花费"):
        lines.append(
            f"预估花费：机票 ¥{costs['机票']:.0f} + 住宿 ¥{costs['住宿']:.0f}"
            f" = ¥{costs['合计']:.0f}（不含市内交通与门票）。"
        )

    if intensity := digest.get("行程强度"):
        lines.append(f"全程通勤约 {intensity['全程通勤分钟']} 分钟。")

    if unscheduled := digest.get("未排入"):
        lines.append(f"时间没排开的备选：{'、'.join(unscheduled)}。")

    return "\n".join(lines)


async def summarize(digest: dict, *, llm=None) -> tuple[str, PlanWarning | None]:
    """生成行程说明。返回 (文案, 降级警告或 None)。

    LLM 失败**绝不能**让整次规划失败——行程本身已经排好了，说明文案只是包装。
    """
    # 显式注入了客户端就用它——开关只管"要不要自己去建一个"
    if llm is None and not settings.llm_enabled:
        return render_fallback(digest), None

    try:
        client = llm or _default_llm()
        # 在 ainvoke 之前记：失败的调用一样烧了 token，配额报表要如实反映
        record_call("llm")
        response = await client.ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _as_prompt(digest)},
            ]
        )
        text = strip_markup(getattr(response, "content", "") or "")
        if not text:
            raise ValueError("模型返回了空内容")
        return truncate_at_sentence(text), None
    except Exception as exc:  # noqa: BLE001 —— 文案生成失败不该拖垮行程
        log.warning("LLM 生成行程说明失败，改用模板", extra={"err": str(exc)})
        return render_fallback(digest), PlanWarning.of(
            "SUMMARY_FALLBACK",
            "行程说明由模板生成（AI 服务暂不可用），内容与行程数据一致",
            stage="summarize",
        )


def _default_llm():
    from app.providers.llm import get_llm

    return get_llm()


def _as_prompt(digest: dict) -> str:
    import json

    return "以下是已排好的行程数据，请据此写说明：\n" + json.dumps(
        digest, ensure_ascii=False, indent=1, default=str
    )
