"""日期解析与行程时间窗计算。

相对日期的解析规则对齐 `docs/flight-agent/flight-data-specification.md` §4.3：
以 2026-08-04（周二）为基准，"周一/本周一/下周一"都解析成 2026-08-10，
因为**已经过去的日期对订票没有意义**，一律向后取最近的将来。
"下周日"存在 08-09 / 08-16 两种读法，标记为 ambiguous 交给 Agent 追问。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

__all__ = [
    "ParsedDate",
    "parse_relative_date",
    "coerce_date",
    "format_cn",
    "trip_day_count",
]

_WEEKDAY_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_RE_ISO = re.compile(r"^\s*(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*[日号]?\s*$")
_RE_MD = re.compile(r"^\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*[日号]?\s*$")
_RE_WEEKDAY = re.compile(r"^\s*(下下|下|本|这|这个)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])\s*$")

_OFFSET_WORDS = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
    "大后天": 3,
}

_VAGUE_WORDS = ("月初", "月中", "月底", "月末", "下个月", "下月", "月份", "左右", "前后")


@dataclass(frozen=True)
class ParsedDate:
    """解析结果。value 为 None 表示无法确定，需要向用户追问。"""

    value: date | None
    ambiguous: bool = False
    note: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.ambiguous


def _week_start(d: date) -> date:
    """所在 ISO 周的周一。"""
    return d - timedelta(days=d.isoweekday() - 1)


def parse_relative_date(text: str, today: date) -> ParsedDate:
    """把用户的自然语言日期解析成绝对日期。"""
    raw = (text or "").strip()
    if not raw:
        return ParsedDate(None, note="空输入", raw=raw)

    if raw in _OFFSET_WORDS:
        return ParsedDate(today + timedelta(days=_OFFSET_WORDS[raw]), raw=raw)

    if m := _RE_ISO.match(raw):
        y, mo, d = (int(g) for g in m.groups())
        try:
            return ParsedDate(date(y, mo, d), raw=raw)
        except ValueError:
            return ParsedDate(None, note=f"非法日期 {y}-{mo}-{d}", raw=raw)

    if m := _RE_WEEKDAY.match(raw):
        prefix, wd_char = m.group(1), m.group(2)
        target_wd = _WEEKDAY_CN[wd_char]
        this_week = _week_start(today) + timedelta(days=target_wd - 1)

        if prefix in (None, "本", "这", "这个"):
            # 已经过去就顺延到下周——过去的日期订不了票
            value = this_week if this_week >= today else this_week + timedelta(days=7)
            return ParsedDate(value, raw=raw)

        weeks = 2 if prefix == "下下" else 1
        value = this_week + timedelta(days=7 * weeks)
        # 只有"下周日"才真的有歧义：ISO 周以周日结尾，而口语里"下周日"常指本周末
        # 那个周日。"下周三"这类没人会理解成明天，不要误判成歧义去烦用户。
        if weeks == 1 and target_wd == 7 and this_week > today:
            return ParsedDate(
                value,
                ambiguous=True,
                note=f"可能是 {this_week.isoformat()} 或 {value.isoformat()}，建议向用户确认",
                raw=raw,
            )
        return ParsedDate(value, raw=raw)

    if m := _RE_MD.match(raw):
        mo, d = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, mo, d)
            except ValueError:
                return ParsedDate(None, note=f"非法日期 {mo}月{d}日", raw=raw)
            if candidate >= today:
                return ParsedDate(candidate, raw=raw)
        return ParsedDate(None, note=f"无法确定年份：{raw}", raw=raw)

    if holiday := _match_holiday(raw, today):
        return holiday

    if any(w in raw for w in _VAGUE_WORDS):
        return ParsedDate(None, note=f"日期过于模糊：{raw}，需要用户给出具体日期", raw=raw)

    return ParsedDate(None, note=f"无法解析：{raw}", raw=raw)


_SOLAR_HOLIDAYS: tuple[tuple[tuple[str, ...], int, int, str], ...] = (
    (("元旦",), 1, 1, "元旦"),
    (("五一", "劳动节"), 5, 1, "五一"),
    (("国庆", "十一"), 10, 1, "国庆"),
)
"""公历固定日期的节假日。**只收固定的**——农历节日见 `_LUNAR_HOLIDAYS`。"""

_LUNAR_HOLIDAYS = ("春节", "除夕", "元宵", "清明", "端午", "中秋", "重阳")
"""农历节日每年公历日期都不同，本项目不带农历表，一律交回用户确认具体日期。
猜错一天就是查错日子的机票，宁可多问一句。"""


def _match_holiday(raw: str, today: date) -> ParsedDate | None:
    """节假日 → 假期首日。取当年或次年里第一个还没过去的。"""
    if any(name in raw for name in _LUNAR_HOLIDAYS):
        name = next(n for n in _LUNAR_HOLIDAYS if n in raw)
        return ParsedDate(
            None, note=f"{name}是农历节日，每年公历日期不同，需要用户给出具体日期", raw=raw
        )

    for aliases, month, day, label in _SOLAR_HOLIDAYS:
        if not any(alias in raw for alias in aliases):
            continue
        for year in (today.year, today.year + 1):
            candidate = date(year, month, day)
            if candidate >= today:
                return ParsedDate(candidate, note=f"{label}假期首日", raw=raw)
    return None


def coerce_date(value: date | datetime | str, today: date | None = None) -> date:
    """统一入口：date / datetime / ISO 字符串 / 中文相对日期 -> date。

    解析不出来就抛 ValueError，由调用方转成 INVALID_PARAMS 错误。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_relative_date(str(value), today or date.today())
    if parsed.value is None:
        raise ValueError(parsed.note or f"无法解析日期：{value}")
    return parsed.value


def format_cn(d: date) -> str:
    """`2026-08-10（周一）`——面向用户展示时统一用这个格式（数据规范 §4.2）。"""
    return f"{d.isoformat()}（{_WEEKDAY_NAMES[d.isoweekday() - 1]}）"


def trip_day_count(outbound: date, return_date: date) -> int:
    """游玩天数（含落地日与返程日）。"""
    if return_date < outbound:
        raise ValueError("return_date 不能早于 outbound_date")
    return (return_date - outbound).days + 1
