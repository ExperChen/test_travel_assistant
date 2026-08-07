"""日期解析与行程时间窗计算。

相对日期的解析规则对齐 `docs/flight-agent/flight-data-specification.md` §4.3：
以 2026-08-04（周二）为基准，"周一/本周一/下周一"都解析成 2026-08-10，
因为**已经过去的日期对订票没有意义**，一律向后取最近的将来。
"下周日"存在 08-09 / 08-16 两种读法，标记为 ambiguous 交给 Agent 追问。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

__all__ = [
    "ParsedDate",
    "DayWindow",
    "parse_relative_date",
    "coerce_date",
    "format_cn",
    "trip_day_count",
    "build_day_windows",
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


# --------------------------------------------------------------------------
# 每日时间窗（架构文档 §5.4 Step 1）
# --------------------------------------------------------------------------
DayKind = Literal["arrival", "full", "departure", "single"]


@dataclass(frozen=True)
class DayWindow:
    """某一天可用于游览的时间区间。

    用 datetime 而不是 time 存储，避免跨零点落地（航班常见）时的边界歧义。
    """

    day_index: int  # 从 1 开始
    day: date
    start: datetime
    end: datetime
    kind: DayKind

    @property
    def usable_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))

    @property
    def is_usable(self) -> bool:
        return self.usable_minutes > 0


def build_day_windows(
    arrive_at: datetime,
    depart_at: datetime,
    *,
    airport_to_hotel_min: int,
    hotel_to_airport_min: int,
    checkin_buffer_min: int = 60,
    predeparture_buffer_min: int = 120,
    day_start: time = time(9, 0),
    day_end: time = time(21, 0),
    departure_day_start: time = time(8, 30),
) -> list[DayWindow]:
    """根据落地/返程时间切出逐日时间窗。

    首日从"落地 + 机场到酒店通勤 + 入住办理"之后开始；
    末日必须在"返程起飞 - 值机安检 buffer - 酒店到机场通勤"之前结束。
    通勤时长由调用方用高德实测值传入，不在这里估算。
    """
    if depart_at < arrive_at:
        raise ValueError("depart_at 不能早于 arrive_at")

    first_day, last_day = arrive_at.date(), depart_at.date()

    ready_at = arrive_at + timedelta(minutes=airport_to_hotel_min + checkin_buffer_min)
    must_leave_at = depart_at - timedelta(
        minutes=predeparture_buffer_min + hotel_to_airport_min
    )

    if first_day == last_day:
        start = ready_at
        end = max(start, must_leave_at)
        return [DayWindow(1, first_day, start, end, "single")]

    windows: list[DayWindow] = []
    total_days = (last_day - first_day).days + 1

    for offset in range(total_days):
        day = first_day + timedelta(days=offset)
        if offset == 0:
            start = ready_at
            end = datetime.combine(day, day_end)
            kind: DayKind = "arrival"
        elif offset == total_days - 1:
            start = datetime.combine(day, departure_day_start)
            end = must_leave_at
            kind = "departure"
        else:
            start = datetime.combine(day, day_start)
            end = datetime.combine(day, day_end)
            kind = "full"
        # 落地太晚 / 返程太早时窗口会是负的，压成零长度而不是抛错，
        # 由 route_planner 决定这天不排景点。
        windows.append(DayWindow(offset + 1, day, start, max(start, end), kind))

    return windows
