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
from app.models.memory import BUDGET_LABELS, MemorySnapshot, bucket_to_budget
from app.models.route import TransportMode
from app.models.special import detect_needs, normalize_requests
from app.models.trip import TripRequest

log = get_logger(__name__)

__all__ = [
    "Extraction",
    "DraftField",
    "TripDraft",
    "parse_prompt",
    "extract_by_rules",
    "resolve",
    "loads_json",
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
    transport: TransportMode | None = None
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    special_requests: list[str] = Field(
        default_factory=list, description="特殊需求：带老人、行李多、不早起、素食…"
    )


Origin = Literal["prompt", "memory", "derived", "default"]
"""优先级从高到低（记忆与追问文档 §2）：

    prompt   用户这次说的        ← 最高
    memory   从长期记忆里取的
    derived  由其它字段推算的
    default  系统默认值          ← 最低

⚠️ **记忆只填空，绝不覆盖。** 用户这次说了什么就是什么，哪怕和记忆冲突——
冲突本身是有价值的信号（去更新记忆），不是需要"纠正"的错误。
"""


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
- transport: transit / driving / walking（公交地铁/自驾/步行）
- must_visit: 必去的景点名数组
- avoid: 明确不想去的景点名数组

铁律：
1. 日期**照抄原话**，绝对不要自己换算成具体年月日。
2. 用户没说的字段不要猜，直接省略。
3. 只输出一个 JSON 对象。"""


async def parse_prompt(
    prompt: str,
    *,
    today: date | None = None,
    llm=None,
    memory: MemorySnapshot | None = None,
) -> TripDraft:
    """把一句话解析成 `TripRequest` 草稿。绝不抛异常。

    `memory` 是可选的长期偏好快照：只用来**填空**，永远不覆盖用户这次说的话。
    不传就是没有记忆时的原行为。
    """
    today = today or date.today()
    text = (prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not text:
        return TripDraft(prompt="", missing=["出发地", "目的地", "出发日期"],
                         questions=["想去哪儿、什么时候出发？"])

    extraction, degraded = await _extract(text, llm)
    draft = resolve(text, extraction, today=today, memory=memory)
    draft.degraded = degraded
    log.info(
        "需求解析完成",
        extra={
            "ok": draft.ok,
            "degraded": degraded,
            "missing": draft.missing,
            "memory_filled": [f.key for f in draft.fields if f.origin == "memory"],
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
        return Extraction.model_validate(loads_json(raw)), False
    except Exception as exc:  # noqa: BLE001 —— 模型不给力就退回规则，不能让解析失败
        log.warning("模型解析失败，改用规则抽取", extra={"err": str(exc) or type(exc).__name__})
        return extract_by_rules(text), True


def _default_llm():
    from app.providers.llm import get_llm

    return get_llm()


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def loads_json(raw: str) -> dict:
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

# 三种句式都要认，用户答"从哪出发"时这几种说法都很常见：
#   从北京出发 / 由北京飞  → 前置介词
#   北京出发 / 北京起飞     → 无介词，城市直接打头
#   出发地北京 / 起点是北京  → 字段名打头
_RE_FROM = re.compile(
    r"(?:从|由)\s*([一-龥]{2,8}?)\s*(?:出发|到|去|飞|走)"
    # 无介词分支必须排除「从/由」打头：否则"算了，从上海出发"会从「，」起匹配，
    # 把「从上海」整个捕获进来（第一分支本可以正确捕获「上海」，但它起始位置更靠后，
    # 正则取的是最左匹配）
    r"|(?:^|[，,。；;\s])(?![从由])([一-龥]{2,8}?)\s*(?:出发|起飞)"
    r"|(?:出发地|始发地|起点)\s*(?:是|：|:)?\s*([一-龥]{2,8})"
)
# 捕获组不能以「去到飞往」开头：「北京起飞去成都」里 `飞` 会先命中前缀，
# 若不排除，捕获到的就是「去成都」而不是「成都」
_RE_TO = re.compile(
    r"(?:去|到|飞往?|前往)\s*(?![去到飞往])([一-龥]{2,8}?)\s*"
    r"(?:玩|旅游|出差|待|游|逛|[，,。；;]|$)"
)
_RE_DAYS = re.compile(r"(?:玩|待|呆|停留)?\s*([0-9]+|[一二两三四五六七八九十]+)\s*(?:天|日游|晚)")
_RE_BUDGET = re.compile(
    r"(?:预算|不超过|控制在)?\s*([0-9]+)\s*(?:块|元)?\s*(?:一晚|每晚|/\s*晚|一夜)"
)
_RE_BUDGET2 = re.compile(r"预算\s*(?:每晚|一晚)?\s*([0-9]+)")
_RE_ADULTS = re.compile(r"([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|位|名)?\s*(?:大人|成人|人)")
# 收尾边界和 _RE_TO 保持一致：不加边界的话「想去成都玩5天」会把整段
# 「成都玩5天」当成景点名。单轮解析时只是条噪音，但 ReAct 的槽位是**累积**的，
# 这条垃圾会一直粘在会话里，最后当成必去景点塞进规划任务里。
_TERM = r"(?:玩|旅游|出差|待|呆|停留|游|逛|看看|[，,。；;、\s]|$)"
# 负向后顾挡掉「不想去」——否则同一个短语会同时落进 must 和 avoid，自相矛盾
_RE_MUST = re.compile(
    rf"(?<![不别])(?:想去|必去|一定要去|想看|要去)\s*([一-龥A-Za-z0-9]{{2,15}}?)\s*{_TERM}"
)
_RE_AVOID = re.compile(rf"(?:不去|不想去|别去|避开|不要去)\s*([一-龥A-Za-z0-9]{{2,15}}?)\s*{_TERM}")

_DATE_PATTERNS = (
    re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*[日号]?"),
    re.compile(r"\d{1,2}\s*[-/月.]\s*\d{1,2}\s*[日号]?"),
    re.compile(r"(?:下下|下|本|这|这个)?(?:周|星期|礼拜)[一二三四五六日天]"),
    re.compile(r"大?后天|明天|明日|今天|今日"),
)

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


def _first_group(match: re.Match[str] | None) -> str | None:
    """取多分支正则里第一个命中的捕获组。

    `_RE_FROM` 用 `|` 串了三种句式，命中哪一支就只有那一组非空。
    """
    if match is None:
        return None
    return next((g for g in match.groups() if g), None)


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
        departure_city=_clean_city(_first_group(_RE_FROM.search(text))),
        destination_city=_clean_city(m.group(1) if (m := _RE_TO.search(text)) else None),
        outbound_date_text=dates[0] if dates else None,
        return_date_text=dates[1] if len(dates) > 1 else None,
        travel_days=_to_int(days_match.group(1)) if days_match else None,
        adults=_to_int(adults.group(1)) if adults else None,
        budget_per_night=int(budget.group(1)) if budget else None,
        travel_class=_match_keyword(text, _CLASS_WORDS),  # type: ignore[arg-type]
        transport=_match_keyword(text, _TRANSPORT_WORDS),  # type: ignore[arg-type]
        must_visit=[m.group(1) for m in _RE_MUST.finditer(text)],
        avoid=[m.group(1) for m in _RE_AVOID.finditer(text)],
        # 特殊需求靠关键词认，认不出的留给模型——规则层不猜自由文本
        special_requests=[n.label for n in detect_needs(text)],
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
    "hotel_class": "酒店星级",
    "travel_class": "舱位",
    "transport": "市内交通",
    "must_visit": "必去",
    "avoid": "排除",
    "special_requests": "特殊需求",
}


def _recall(memory: MemorySnapshot | None, key: str):
    """从记忆里取一条偏好；没有或空值返回 None。

    空值也当没有：`children_ages: []`、`hotel_class: []` 这些填了等于没填。
    """
    if memory is None or memory.profile is None:
        return None
    pref = memory.profile.get(key)
    if pref is None or pref.value is None or pref.value == "" or pref.value == []:
        return None
    return pref


def resolve(
    prompt: str,
    extraction: Extraction,
    *,
    today: date,
    memory: MemorySnapshot | None = None,
) -> TripDraft:
    """把抽取结果落成 `TripRequest`，并逐字段记下出处。纯函数，不碰网络。

    优先级 `prompt > memory > derived > default`（记忆与追问文档 §2）。
    记忆分两档用：

    - **高置信度**（`confidence ≥ 0.6`，约等于说过三次）→ 直接填，`origin="memory"`
    - **低置信度** → 只拿来**建议**，进 `questions`，值仍走默认——
      说过一次就当成习惯，比不记还糟
    """
    draft = TripDraft(prompt=prompt)
    add = draft.fields.append

    departure = extraction.departure_city
    destination = extraction.destination_city

    recalled_departure = _recall(memory, "departure_city")
    if departure:
        add(DraftField(key="departure_city", label=LABELS["departure_city"],
                       value=departure, origin="prompt"))
    elif recalled_departure is not None and recalled_departure.is_confident:
        departure = str(recalled_departure.value)
        add(DraftField(
            key="departure_city", label=LABELS["departure_city"],
            value=departure, origin="memory",
            note=f"你最近 {recalled_departure.samples} 次都从这儿出发",
        ))
    else:
        draft.missing.append(LABELS["departure_city"])
        if recalled_departure is not None:
            # 低置信度：给建议而不是替他做决定
            draft.questions.append(
                f"上次你是从{recalled_departure.value}出发的，这次也一样吗？"
            )
        else:
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

    optional = _resolve_optional(extraction, add, memory=memory, draft=draft)

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
    "transport": ("transit", "公共交通"),
}

DISPLAY = {
    "economy": "经济舱", "premium_economy": "超级经济舱",
    "business": "商务舱", "first": "头等舱",
    "transit": "公共交通", "driving": "自驾", "walking": "步行",
}
"""枚举值给用户看时要用中文——「packed」谁看得懂。"""


def _show(key: str, value: object) -> str:
    if key == "adults":
        return f"{value} 位"
    if key == "hotel_class" and isinstance(value, list):
        return "、".join(f"{v}★" for v in value)
    return DISPLAY.get(str(value), str(value))


def _resolve_optional(
    extraction: Extraction,
    add,
    *,
    memory: MemorySnapshot | None = None,
    draft: TripDraft | None = None,
) -> dict:
    """可选字段：说了就用，记忆够可信就用记忆，都没有才取默认值。

    每一档都在 `origin` 里如实标出来，用户要能一眼分辨"这是我说的"
    还是"这是系统替我定的"。
    """
    values: dict = {}
    suggest = draft.questions.append if draft is not None else (lambda _: None)

    for key, (fallback, label) in DEFAULTS.items():
        given = getattr(extraction, key)
        if given is not None:
            values[key] = given
            add(DraftField(key=key, label=LABELS[key], value=_show(key, given),
                           origin="prompt"))
            continue

        pref = _recall(memory, key)
        if pref is not None and pref.is_confident:
            values[key] = pref.value
            add(DraftField(
                key=key, label=LABELS[key], value=_show(key, pref.value),
                origin="memory", note=f"你惯常的选择（说过 {pref.samples} 次）",
            ))
            continue

        values[key] = fallback
        add(DraftField(key=key, label=LABELS[key], value=label, origin="default",
                       note="未提及，用默认值"))
        if pref is not None:
            suggest(f"{LABELS[key]}上次你选的是{_show(key, pref.value)}，这次也一样吗？")

    values.update(_resolve_children(extraction, add, memory))
    values.update(_resolve_budget(extraction, add, memory))
    values.update(_resolve_hotel_class(add, memory))

    # 「想去杭州」里的杭州是目的地，不是景点——同名的必去项要摘掉，
    # 否则规划任务里会出现「必去：杭州」这种把目的地当景点的要求
    #
    # must_visit / avoid **刻意不进记忆**：它们强绑定目的地，是 L3 的活。
    # 记住"这个人必去兵马俑"然后在去三亚时也加上，是明显的错。
    # 特殊需求**不进记忆**，理由同 must_visit：素食这类确实稳定，但"带老人"
    # 「行李多」是这一趟的事，记住了下次单独出差也会被当成带老人
    if requests := normalize_requests(*extraction.special_requests):
        values["special_requests"] = requests
        add(DraftField(key="special_requests", label=LABELS["special_requests"],
                       value="、".join(requests), origin="prompt"))

    cities = {c for c in (extraction.departure_city, extraction.destination_city) if c}
    for key in ("must_visit", "avoid"):
        items = [x for x in getattr(extraction, key) if _clean_city(x) not in cities]
        if items:
            values[key] = items
            add(DraftField(key=key, label=LABELS[key],
                           value="、".join(items), origin="prompt"))

    return values


def _resolve_children(extraction: Extraction, add, memory: MemorySnapshot | None) -> dict:
    """儿童人数与年龄。

    记忆里的年龄在 `MemorySnapshot` 生成时已经按经过年数推进过了
    （`Profile.advance_children_ages`）——存死数字会让去年 5 岁的孩子永远 5 岁。
    """
    if extraction.children:
        # 年龄对不上会被 TripRequest 拦下，这里先按缺省年龄补齐再交给它校验
        ages = extraction.children_ages[: extraction.children]
        add(DraftField(key="children", label=LABELS["children"],
                       value=f"{extraction.children} 位", origin="prompt",
                       note="" if len(ages) == extraction.children else "年龄没说全"))
        return {"children": extraction.children, "children_ages": ages}

    pref = _recall(memory, "children")
    if pref is None or not pref.is_confident or not pref.value:
        return {}

    count = int(pref.value)
    ages_pref = _recall(memory, "children_ages")
    ages = [int(a) for a in ages_pref.value][:count] if ages_pref is not None else []
    note = "记忆中的家庭结构"
    if ages:
        note += f"；年龄已按时间推进为 {'、'.join(str(a) for a in ages)} 岁"
    add(DraftField(key="children", label=LABELS["children"], value=f"{count} 位",
                   origin="memory", note=note))
    # 年龄数量对不上就整个不填——宁可不带儿童，也不能凭空编年龄去查票价
    return {"children": count, "children_ages": ages} if len(ages) == count else {}


def _resolve_budget(extraction: Extraction, add, memory: MemorySnapshot | None) -> dict:
    """每晚预算。

    记忆里存的是**档位**不是数字（文档 §2）：去三亚和去县城的预算不是一回事，
    只有档位跨行程稳定。取用时换回该档的上界。
    """
    if extraction.budget_per_night is not None:
        add(DraftField(key="budget_per_night", label=LABELS["budget_per_night"],
                       value=f"¥{extraction.budget_per_night}", origin="prompt"))
        return {"budget_per_night": extraction.budget_per_night}

    pref = _recall(memory, "budget_per_night")
    if pref is None or not pref.is_confident:
        return {}

    bucket = str(pref.value)
    amount = bucket_to_budget(bucket)
    if amount is None:
        return {}  # any / over_1000 都是"不设上限"，等同于不填
    add(DraftField(key="budget_per_night", label=LABELS["budget_per_night"],
                   value=f"¥{amount} 以内", origin="memory",
                   note=f"你惯常的档位：{BUDGET_LABELS.get(bucket, bucket)}"))
    return {"budget_per_night": amount}


def _resolve_hotel_class(add, memory: MemorySnapshot | None) -> dict:
    """酒店星级。抽取层不解析它，所以只可能来自记忆。"""
    pref = _recall(memory, "hotel_class")
    if pref is None or not pref.is_confident:
        return {}
    stars = [int(s) for s in pref.value if int(s) in (2, 3, 4, 5)]
    if not stars:
        return {}
    add(DraftField(key="hotel_class", label=LABELS["hotel_class"],
                   value=_show("hotel_class", stars), origin="memory",
                   note="你惯常住的档次"))
    return {"hotel_class": stars}
