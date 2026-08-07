"""结构化日志测试。

核心不变量：**可观测性绝不能反过来弄坏业务**。一条只为排查问题而写的日志，
不该有任何机会让请求失败。
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import JsonFormatter, TextFormatter, bind_trip, get_logger


@pytest.fixture
def sink(monkeypatch):
    """把日志收进列表，返回格式化后的字符串。"""
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("app.tests.logging")
    logger.handlers = [Collector()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return records


class TestReservedKeys:
    """`LogRecord` 有一批内置属性名，撞上就抛 KeyError。

    实测踩到的：`extra={"name": ...}` 让整次规划崩掉——景点召回里
    "必去景点没搜到" 那条警告，本意只是留个线索。
    """

    @pytest.mark.parametrize("key", ["name", "module", "filename", "message", "args"])
    def test_a_colliding_key_does_not_raise(self, key, sink):
        log = get_logger("app.tests.logging")

        log.warning("撞了保留字", extra={key: "值"})  # 不抛就是通过

        assert len(sink) == 1

    def test_the_value_is_kept_under_a_renamed_key(self, sink):
        log = get_logger("app.tests.logging")

        log.warning("必去景点没搜到", extra={"name": "不存在的地方"})

        payload = json.loads(JsonFormatter().format(sink[0]))
        assert payload["x_name"] == "不存在的地方"  # 改名保留，不是丢弃

    def test_ordinary_keys_are_untouched(self, sink):
        log = get_logger("app.tests.logging")

        log.info("正常字段", extra={"spot": "西湖", "city": "杭州市"})

        payload = json.loads(JsonFormatter().format(sink[0]))
        assert payload["spot"] == "西湖"
        assert payload["city"] == "杭州市"

    def test_no_extra_at_all_is_fine(self, sink):
        get_logger("app.tests.logging").info("光秃秃一句")

        assert len(sink) == 1


class TestFormatters:
    def test_json_carries_the_trip_id(self, sink):
        # 必须在上下文内格式化：trip_id 存在 ContextVar 里，出了 with 就没了。
        # 生产环境天然满足——handler.emit() 是在 log.info() 里同步跑的。
        with bind_trip("trp_abc"):
            get_logger("app.tests.logging").info("干活")
            payload = json.loads(JsonFormatter().format(sink[0]))

        assert payload["trip_id"] == "trp_abc"
        assert payload["msg"] == "干活"
        assert payload["level"] == "INFO"

    def test_text_format_is_readable(self, sink):
        with bind_trip("trp_abc"):
            get_logger("app.tests.logging").info("干活", extra={"city": "杭州市"})
            line = TextFormatter().format(sink[0])

        assert "干活" in line
        assert "trip_id=trp_abc" in line
        assert "city=杭州市" in line

    def test_exceptions_are_included(self, sink):
        try:
            raise ValueError("炸了")
        except ValueError:
            get_logger("app.tests.logging").exception("出事了")

        payload = json.loads(JsonFormatter().format(sink[0]))
        assert "ValueError" in payload["exc"]

    def test_json_survives_unserialisable_values(self, sink):
        # 业务对象混进 extra 不该让日志本身抛异常
        get_logger("app.tests.logging").info("对象", extra={"obj": object()})

        assert "obj" in json.loads(JsonFormatter().format(sink[0]))


def test_trip_id_is_cleared_on_exit(sink):
    from app.core.logging import current_trip_id

    with bind_trip("trp_x"):
        assert current_trip_id() == "trp_x"
    assert current_trip_id() is None
