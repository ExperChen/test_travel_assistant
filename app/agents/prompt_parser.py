"""自然语言需求 → `TripRequest` 草稿。

用户说一句「9月5号从北京去成都玩5天，预算600一晚，想看大熊猫」，这里把它变成
结构化的行程参数，并逐字段说明**这个值是从哪来的**——原话、推算，还是默认值。

三条纪律：

1. **模型只摘短语，日期由代码算。** 抽取层输出的是 `"下周三"` 这样的原话，
   落成绝对日期一律走 `core.dates.parse_relative_date`。LLM 做日期算术出了名地
   不可靠，而算错日期意味着整条链路去查错日子的机票——错得既贵又不显眼。
2. **解析失败不抛异常。** 缺什么就列在 `missing` 里交给上层追问，绝不让一句
   没说清的话变成 500。
3. **LLM 不可用时退回规则抽取。** `LLM_ENABLED=false` 是受支持的运行模式，
   这条路径覆盖最常见的句式，覆盖不到的照样进 `missing`。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.core.dates import format_cn, parse_relative_date
from app.core.logging import get_logger
from app.core.metrics import record_call
from app.models.flight import TravelClass
from app.models.route import TransportMode
from app.models.trip import Pace, TripRequest

log = get_logger(__name__)

__all__ = [
    "Extraction",
    "DraftField",
    "TripDraft",
    "parse_prompt",
    "extract_by_rules",
    "resolve",
    "SYSTEM_PROMPT",
]

MAX_PROMPT_CHARS = 1000
"""再长就不是行程需求而是小作文了，截断以免把 token 烧在无关内容上。"""


class Extraction(BaseModel):
    """抽取层的产物：**全部可选**，日期保持用户原话。

    这一层不做任何校验——把"没说"和"说错了"都原样带下去，由 `resolve()`
    统一判断，错误信息才能指向具体字段。
    """

    departure_city: str | None = None
    destination_city: str | None = None
    outbound_date_text: str | None = Field(default=None, description="出发日期的原话")
    return_date_text: str | None = Field(default=None, description="返程日期的原话")
    travel_days: int | None = Field(default=None, description="玩几天，含落地日与返程日")
    adults: int | None = None
    children: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    budget_per_night: int | None = None
    travel_class: TravelClass | None = None
    pace: Pace | None = None
    transport: TransportMode | None = None
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


Origin = Literal["prompt", "derived", "default"]


class DraftField(BaseModel):
    """一个字段的解析结果，带出处——用户要能一眼看出哪些是它说的、哪些是我们替它定的。"""

    key: str
    label: str
    value: str
    origin: Origin
    note: str = ""


class TripDraft(BaseModel):
    """解析结果。`request` 为 None 表示信息不全，看 `missing` / `questions`。"""

    prompt: str
    request: TripRequest | None = None
    fields: list[DraftField] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    degraded: bool = Field(
        default=False, description="true = 走的规则抽取，模型没参与"
    )

    @property
    def ok(self) -> bool:
        return self.request is not None


SYSTEM_PROMPT = """你是行程需求解析器。把用户的话抽取成 JSON。
只输出 JSON 本身，不要解释、不要代码块标记。

字段（全部可选，没提到就省略或填 null）：
- departure_city: 出发城市名或 IATA 三字码
- destination_city: 目的地城市名
- outbound_date_text: 出发日期的**原话**，如 "9月5号"、"下周三"、"明天"
- return_date_text: 返程日期的**原话**
- travel_days: 玩几天（整数，含落地日和返程日）
- adults: 成人数；children: 儿童数；children_ages: 儿童年龄数组
- budget_per_night: 每晚住宿预算上限（整数，人民币）
- travel_class: economy / premium_economy / business / first
- pace: relaxed / standard / packed（悠闲/标准/紧凑）
- transport: transit / driving / walking（公交地铁/自驾/步行）
- must_visit: 必去的景点名数组
- avoid: 明确不想去的景点名数组

铁律：
1. 日期**照抄原话**，绝对不要自己换算成具体年月日。
2. 用户没说的字段不要猜，直接省略。
3. 只输出一个 JSON 对象。"""


async def parse_prompt(
    prompt: str, *, today: date | None = None, llm=None
) -> TripDraft:
    """把一句话解析成 `TripRequest` 草稿。绝不抛异常。"""
    today = today or date.today()
    text = (prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not text:
        return TripDraft(prompt="", missing=["出发地", "目的地", "出发日期"],
                         questions=["想去哪儿、什么时候出发？"])

    extraction, degraded = await _extract(text, llm)
    draft = resolve(text, extraction, today=today)
    draft.degraded = degraded
    log.info(
        "需求解析完成",
        extra={
            "ok": draft.ok,
            "degraded": degraded,
            "missing": draft.missing,
            "to": extraction.destination_city,
        },
    )
    return draft


# ---------------------------------------------------------------- 抽取层
async def _extract(text: str, llm) -> tuple[Extraction, bool]:
    """返回 (抽取结果, 是否降级到规则)。"""
    if llm is None and not settings.llm_enabled:
        return extract_by_rules(text), True

    try:
        client = llm or _default_llm()
        record_call("llm")
        response = await client.ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ]
        )
        raw = getattr(response, "content", "") or ""
        return Extraction.model_validate(_loads_json(raw)), False
    except Exception as exc:  # noqa: BLE001 —— 模型不给力就退回规则，不能让解析失败
        log.warning("模型解析失败，改用规则抽取", extra={"err": str(exc) or type(exc).__name__})
        return extract_by_rules(text), True


def _default_llm():
    from app.providers.llm import get_llm

    return get_llm()


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _loads_json(raw: str) -> dict:
    """模型经常裹一层 ```json 代码块，或在 JSON 前后加一句废话。"""
    cleaned = _FENCE.sub("", raw.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"模型返回的不是 JSON 对象：{type(parsed).__name__}")
    return parsed


# ---------------------------------------------------------------- 规则抽取
_CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_RE_FROM = re.compile(r"(?:从|由)\s*([一-龥]{2,8}?)\s*(?:出发|到|去|飞|走)")
_RE_TO = re.compile(
    r"(?:去|到|飞往?|前往)\s*([一-龥]{2,8}?)\s*(?:玩|旅游|出差|待|游|逛|[，,。；;]|$)"
)
_RE_DAYS = re.compile(r"(?:玩|待|呆|停留)?\s*([0-9]+|[一二两三四五六七八九十]+)\s*(?:天|日游|晚)")
_RE_BUDGET = re.compile(
    r"(?:预算|不超过|控制在)?\s*([0-9]+)\s*(?:块|元)?\s*(?:一晚|每晚|/\s*晚|一夜)"
)
_RE_BUDGET2 = re.compile(r"预算\s*(?:每晚|一晚)?\s*([0-9]+)")
_RE_ADULTS = re.compile(r"([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|位|名)?\s*(?:大人|成人|人)")
_RE_MUST = re.compile(r"(?:想去|必去|一定要去|想看|要去)\s*([一-龥A-Za-z0-9]{2,15})")
_RE_AVOID = re.compile(r"(?:不去|不想去|别去|避开|不要去)\s*([一-龥A-Za-z0-9]{2,15})")

_DATE_PATTERNS = (
    re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*[日号]?"),
    re.compile(r"\d{1,2}\s*[-/月.]\s*\d{1,2}\s*[日号]?"),
    re.compile(r"(?:下下|下|本|这|这个)?(?:周|星期|礼拜)[一二三四五六日天]"),
    re.compile(r"大?后天|明天|明日|今天|今日"),
)

_PACE_WORDS = {"relaxed": ("悠闲", "轻松", "慢", "休闲"),
               "standard": ("标准", "正常", "适中"),
               "packed": ("紧凑", "赶", "多逛", "密集")}
_TRANSPORT_WORDS = {"driving": ("自驾", "开车", "租车", "打车"),
                    "walking": ("步行", "走路", "citywalk", "City Walk"),
                    "transit": ("地铁", "公交", "公共交通")}
_CLASS_WORDS = {"business": ("商务舱",), "first": ("头等舱",),
                "premium_economy": ("超级经济舱", "豪华经济舱"), "economy": ("经济舱",)}

_CITY_SUFFIX = re.compile(r"(市|地区)$")


def _to_int(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    # 只处理「十X」「X十」这两种口语量级，再复杂的交给模型
    if len(token) == 2 and token[0] == "十":
        return 10 + _CN_DIGITS.get(token[1], 0)
    if len(token) == 2 and token[1] == "十":
        return _CN_DIGITS.get(token[0], 0) * 10
    return _CN_DIGITS.get(token)


def _clean_city(name: str | None) -> str | None:
    if not name:
        return None
    return _CITY_SUFFIX.sub("", name.strip()) or None


def _find_dates(text: str) -> list[str]:
    """按出现位置返回所有日期短语；第一个当出发，第二个当返程。"""
    hits: list[tuple[int, str]] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            if not any(s <= m.start() < e for s, e, _ in
                       ((s, s + len(v), v) for s, v in hits)):
                hits.append((m.start(), m.group()))
    return [value for _, value in sorted(hits)]


def _match_keyword(text: str, table: dict[str, tuple[str, ...]]) -> str | None:
    for value, words in table.items():
        if any(w in text for w in words):
            return value
    return None


def extract_by_rules(text: str) -> Extraction:
    """不依赖模型的兜底抽取。

    只认最常见的句式——覆盖不到的字段留空，由 `resolve()` 报缺失，
    总好过在 `LLM_ENABLED=false` 时整个功能不可用。
    """
    dates = _find_dates(text)
    days_match = _RE_DAYS.search(text)
    budget = _RE_BUDGET.search(text) or _RE_BUDGET2.search(text)
    adults = _RE_ADULTS.search(text)

    return Extraction(
        departure_city=_clean_city(m.group(1) if (m := _RE_FROM.search(text)) else None),
        destination_city=_clean_city(m.group(1) if (m := _RE_TO.search(text)) else None),
        outbound_date_text=dates[0] if dates else None,
        return_date_text=dates[1] if len(dates) > 1 else None,
        travel_days=_to_int(days_match.group(1)) if days_match else None,
        adults=_to_int(adults.group(1)) if adults else None,
        budget_per_night=int(budget.group(1)) if budget else None,
        travel_class=_match_keyword(text, _CLASS_WORDS),  # type: ignore[arg-type]
        pace=_match_keyword(text, _PACE_WORDS),  # type: ignore[arg-type]
        transport=_match_keyword(text, _TRANSPORT_WORDS),  # type: ignore[arg-type]
        must_visit=[m.group(1) for m in _RE_MUST.finditer(text)],
        avoid=[m.group(1) for m in _RE_AVOID.finditer(text)],
    )


# ---------------------------------------------------------------- 归一层
LABELS = {
    "departure_city": "出发地",
    "destination_city": "目的地",
    "outbound_date": "出发日期",
    "return_date": "返程日期",
    "adults": "成人",
    "children": "儿童",
    "budget_per_night": "每晚预算",
    "travel_class": "舱位",
    "pace": "节奏",
    "transport": "市内交通",
    "must_visit": "必去",
    "avoid": "排除",
}


def resolve(prompt: str, extraction: Extraction, *, today: date) -> TripDraft:
    """把抽取结果落成 `TripRequest`，并逐字段记下出处。纯函数，不碰网络。"""
    draft = TripDraft(prompt=prompt)
    add = draft.fields.append

    departure = extraction.departure_city
    destination = extraction.destination_city
    if departure:
        add(DraftField(key="departure_city", label=LABELS["departure_city"],
                       value=departure, origin="prompt"))
    else:
        draft.missing.append(LABELS["departure_city"])
        draft.questions.append("从哪个城市出发？")

    if destination:
        add(DraftField(key="destination_city", label=LABELS["destination_city"],
                       value=destination, origin="prompt"))
    else:
        draft.missing.append(LABELS["destination_city"])
        draft.questions.append("想去哪个城市？")

    outbound = _resolve_date(extraction.outbound_date_text, today, draft, "outbound_date")
    return_date = _resolve_date(extraction.return_date_text, today, draft, "return_date")

    # 只说了"玩 5 天"时按天数推返程；travel_days 含落地日和返程日
    if return_date is None and outbound is not None and extraction.travel_days:
        return_date = outbound + _days_delta(extraction.travel_days)
        add(DraftField(key="return_date", label=LABELS["return_date"],
                       value=format_cn(return_date), origin="derived",
                       note=f"按「玩 {extraction.travel_days} 天」推算"))
    elif return_date is None and outbound is not None:
        draft.missing.append(LABELS["return_date"])
        draft.questions.append("玩几天？或者哪天返程？")

    optional = _resolve_optional(extraction, add)

    if outbound is None or departure is None or destination is None or return_date is None:
        return draft

    try:
        draft.request = TripRequest(
            departure_city=departure,
            destination_city=destination,
            outbound_date=outbound,
            return_date=return_date,
            **optional,
        )
    except ValidationError as exc:
        # 到这一步还失败的是业务规则冲突（比如返程早于出发），不是缺字段
        for error in exc.errors():
            draft.questions.append(str(error.get("msg", "参数不合法")))
        draft.missing.append("参数校验未通过")
    return draft


def _days_delta(days: int):
    """「玩 N 天」= 返程日 − 出发日 = N。1 号来、5 号回就是 4 天。"""
    from datetime import timedelta

    return timedelta(days=max(days, 1))


def _resolve_date(text: str | None, today: date, draft: TripDraft, key: str) -> date | None:
    """短语 → 绝对日期。**这里是全模块唯一算日期的地方。**"""
    label = LABELS[key]
    if not text:
        if key == "outbound_date":
            draft.missing.append(label)
            draft.questions.append("哪天出发？")
        return None

    parsed = parse_relative_date(text, today)
    if parsed.value is None:
        draft.missing.append(label)
        draft.questions.append(f"没看懂「{text}」是哪天，能给个具体日期吗？")
        return None

    note = f"原话「{text}」"
    if parsed.ambiguous:
        # 值照用，但要追问——「下周日」这种两可的读法差整整一周
        note += f"；{parsed.note}"
        draft.questions.append(f"「{text}」是指 {format_cn(parsed.value)} 吗？")
    draft.fields.append(
        DraftField(key=key, label=label, value=format_cn(parsed.value),
                   origin="prompt", note=note)
    )
    return parsed.value


DEFAULTS: dict[str, tuple[object, str]] = {
    "adults": (1, "1 位"),
    "travel_class": ("economy", "经济舱"),
    "pace": ("standard", "标准"),
    "transport": ("transit", "公共交通"),
}

DISPLAY = {
    "economy": "经济舱", "premium_economy": "超级经济舱",
    "business": "商务舱", "first": "头等舱",
    "relaxed": "悠闲", "standard": "标准", "packed": "紧凑",
    "transit": "公共交通", "driving": "自驾", "walking": "步行",
}
"""枚举值给用户看时要用中文——「packed」谁看得懂。"""


def _show(key: str, value: object) -> str:
    if key == "adults":
        return f"{value} 位"
    return DISPLAY.get(str(value), str(value))


def _resolve_optional(extraction: Extraction, add) -> dict:
    """可选字段：说了就用，没说就取默认值并标明这是我们替它定的。"""
    values: dict = {}

    for key, (fallback, label) in DEFAULTS.items():
        given = getattr(extraction, key)
        values[key] = given if given is not None else fallback
        add(DraftField(
            key=key, label=LABELS[key],
            value=_show(key, given) if given is not None else label,
            origin="prompt" if given is not None else "default",
            note="" if given is not None else "未提及，用默认值",
        ))

    if extraction.children:
        values["children"] = extraction.children
        # 年龄对不上会被 TripRequest 拦下，这里先按缺省年龄补齐再交给它校验
        ages = extraction.children_ages[: extraction.children]
        values["children_ages"] = ages
        add(DraftField(key="children", label=LABELS["children"],
                       value=f"{extraction.children} 位", origin="prompt",
                       note="" if len(ages) == extraction.children else "年龄没说全"))

    if extraction.budget_per_night is not None:
        values["budget_per_night"] = extraction.budget_per_night
        add(DraftField(key="budget_per_night", label=LABELS["budget_per_night"],
                       value=f"¥{extraction.budget_per_night}", origin="prompt"))

    # 「想去杭州」里的杭州是目的地，不是景点——同名的必去项要摘掉，
    # 否则 route_planner 会拿着城市名去景点池里强行匹配
    cities = {c for c in (extraction.departure_city, extraction.destination_city) if c}
    for key in ("must_visit", "avoid"):
        items = [x for x in getattr(extraction, key) if _clean_city(x) not in cities]
        if items:
            values[key] = items
            add(DraftField(key=key, label=LABELS[key],
                           value="、".join(items), origin="prompt"))

    return values
