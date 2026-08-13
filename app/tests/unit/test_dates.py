"""日期解析与时间窗测试。

相对日期用例逐条对齐 `docs/flight-agent/flight-data-specification.md` §4.3 的表格，
基准日固定为 2026-08-04（周二）。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.core.dates import (
    coerce_date,
    format_cn,
    parse_relative_date,
    trip_day_count,
)

TODAY = date(2026, 8, 4)  # 周二


class TestParseRelativeDate:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("今天", date(2026, 8, 4)),
            ("今日", date(2026, 8, 4)),
            ("明天", date(2026, 8, 5)),
            ("后天", date(2026, 8, 6)),
            ("大后天", date(2026, 8, 7)),
        ],
    )
    def test_offset_words(self, text, expected):
        assert parse_relative_date(text, TODAY).value == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            # 本周一(08-03)已经过去，订票场景一律向后取——文档表格的结论
            ("周一", date(2026, 8, 10)),
            ("本周一", date(2026, 8, 10)),
            ("星期一", date(2026, 8, 10)),
            ("礼拜一", date(2026, 8, 10)),
            ("下周一", date(2026, 8, 10)),
            # 本周三(08-05)还没到，取本周
            ("周三", date(2026, 8, 5)),
            ("下周三", date(2026, 8, 12)),
            ("下下周三", date(2026, 8, 19)),
        ],
    )
    def test_weekday_words(self, text, expected):
        parsed = parse_relative_date(text, TODAY)
        assert parsed.value == expected
        assert not parsed.ambiguous

    def test_next_sunday_is_flagged_ambiguous(self):
        # 文档明确：08-09 与 08-16 都讲得通，必须回头问用户
        parsed = parse_relative_date("下周日", TODAY)
        assert parsed.value == date(2026, 8, 16)
        assert parsed.ambiguous
        assert "2026-08-09" in parsed.note
        assert "2026-08-16" in parsed.note
        assert not parsed.ok

    def test_sunday_and_tian_are_the_same_weekday(self):
        assert parse_relative_date("周日", TODAY).value == parse_relative_date("周天", TODAY).value

    @pytest.mark.parametrize(
        "text",
        ["8月10号", "8月10日", "8/10", "8-10", "2026-08-10", "2026/8/10", "2026年8月10日"],
    )
    def test_explicit_dates(self, text):
        assert parse_relative_date(text, TODAY).value == date(2026, 8, 10)

    def test_month_day_rolls_to_next_year_when_already_past(self):
        assert parse_relative_date("1月5号", TODAY).value == date(2027, 1, 5)

    @pytest.mark.parametrize("text", ["下个月初", "8月中旬", "月底", "随便哪天", ""])
    def test_vague_input_returns_none(self, text):
        parsed = parse_relative_date(text, TODAY)
        assert parsed.value is None
        assert parsed.note

    def test_invalid_calendar_date(self):
        assert parse_relative_date("2026-02-30", TODAY).value is None


class TestCoerceDate:
    def test_passthrough_date(self):
        assert coerce_date(date(2026, 8, 10)) == date(2026, 8, 10)

    def test_datetime_is_truncated(self):
        assert coerce_date(datetime(2026, 8, 10, 15, 30)) == date(2026, 8, 10)

    def test_iso_string(self):
        assert coerce_date("2026-08-10", TODAY) == date(2026, 8, 10)

    def test_chinese_relative(self):
        assert coerce_date("明天", TODAY) == date(2026, 8, 5)

    def test_unparsable_raises(self):
        with pytest.raises(ValueError):
            coerce_date("下个月找个时间", TODAY)


class TestFormatting:
    def test_format_cn(self):
        assert format_cn(date(2026, 8, 10)) == "2026-08-10（周一）"
        assert format_cn(date(2026, 8, 16)) == "2026-08-16（周日）"

    def test_trip_day_count_includes_both_ends(self):
        assert trip_day_count(date(2026, 8, 10), date(2026, 8, 16)) == 7
        assert trip_day_count(date(2026, 8, 10), date(2026, 8, 10)) == 1

    def test_trip_day_count_rejects_reversed_range(self):
        with pytest.raises(ValueError):
            trip_day_count(date(2026, 8, 16), date(2026, 8, 10))
