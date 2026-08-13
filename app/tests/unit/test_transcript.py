"""对话记录：JSON 为主、Markdown 为视图。

两条不变量：
1. **JSON 无损** —— ReAct 轨迹、延迟、降级原因都要在，否则复放不出这次运行；
2. **崩了也要有记录** —— 每条都即时落盘，不等正常退出。
"""

from __future__ import annotations

import json

import pytest

from app.core.md_console import display_width
from app.models.intake import ReactStep
from main import Transcript, describe_step

STEPS = [
    ReactStep(thought="先查记忆", action="recall_preference",
              action_input={"field": "departure_city"},
              observation='{"ok": true, "found": false}'),
    ReactStep(thought="记忆里没有，问出发地", action="Response"),
]


@pytest.fixture
def log(tmp_path) -> Transcript:
    return Transcript(tmp_path, session_id="ses_test")


def _load(log: Transcript) -> dict:
    return json.loads(log.json_path.read_text(encoding="utf-8"))


class TestJsonIsComplete:
    def test_writes_both_files(self, log):
        log.say("user", "去成都")
        assert log.json_path.exists()
        assert log.md_path.exists()

    def test_carries_schema_and_session(self, log):
        log.say("user", "去成都")
        data = _load(log)
        assert data["schema"] == Transcript.SCHEMA
        assert data["session_id"] == "ses_test"
        assert data["sources"]  # 哪些真哪些假，必须记下来

    def test_react_trace_survives(self, log):
        """**这是 JSON 存在的理由**——轨迹是"agent 为什么这么答"的唯一线索。"""
        log.say("agent", "从哪出发？", steps=STEPS, missing=["出发地"])
        turn = _load(log)["turns"][0]

        assert [s["action"] for s in turn["react"]] == ["recall_preference", "Response"]
        assert turn["react"][0]["observation"]  # observation 不能被丢掉
        assert turn["react"][0]["action_input"] == {"field": "departure_city"}
        assert turn["missing"] == ["出发地"]

    def test_degraded_reason_is_recorded(self, log):
        """降级的那些回合 `react` 是空的——原因是复盘时唯一的线索。"""
        log.say("agent", "从哪出发？", degraded_reason="模型响应超时（当前上限 120s）")
        turn = _load(log)["turns"][0]

        assert "超时" in turn["degraded_reason"]

    def test_normal_turns_carry_no_reason_key(self, log):
        """没降级就不该有这个键，免得看日志的人以为每轮都出了事。"""
        log.say("agent", "从哪出发？")
        assert "degraded_reason" not in _load(log)["turns"][0]

    def test_records_latency(self, log):
        """助手侧的 elapsed_ms 就是模型延迟——benchmark 要的就是这个数。"""
        log.say("user", "去成都")
        log.say("agent", "从哪出发？")
        assert all("elapsed_ms" in t for t in _load(log)["turns"])

    def test_flushes_on_every_entry(self, log):
        """崩溃时最想看记录——所以不能等退出才写。"""
        log.say("user", "第一句")
        assert len(_load(log)["turns"]) == 1
        log.say("agent", "第二句")
        assert len(_load(log)["turns"]) == 2


class TestMarkdownIsReadable:
    def test_renders_the_conversation(self, log):
        log.say("user", "去成都")
        log.say("agent", "从哪出发？", steps=STEPS)
        text = log.md_path.read_text(encoding="utf-8")
        assert "去成都" in text
        assert "从哪出发？" in text
        assert "recall_preference" in text  # 轨迹压成小字，但仍可见

    def test_agent_answer_stays_markdown(self, log):
        """agent 交付的就是 Markdown，md 视图里原样保留。"""
        log.say("agent", "# 成都四日\n\n- 第一天：宽窄巷子")
        text = log.md_path.read_text(encoding="utf-8")
        assert "# 成都四日" in text
        assert "- 第一天：宽窄巷子" in text


class TestDisabled:
    def test_no_log_writes_nothing(self, tmp_path):
        log = Transcript(tmp_path / "nope", enabled=False)
        log.say("user", "去成都")
        assert not (tmp_path / "nope").exists()


class TestStepDescription:
    """终端只说"在查什么"，不倒 JSON。

    工具返回是几 KB 的 JSON，倒进终端会把行程本身淹掉——真要看的时候在
    同名 .json 里，比在翻回滚的终端里好读。
    """

    def _line(self, action: str, args: dict, observation: str = "{}") -> str:
        return describe_step(
            ReactStep(action=action, action_input=args, observation=observation)
        )

    def test_says_what_was_looked_up(self):
        assert "查城市" in self._line("district_lookup", {"keywords": "深圳"})
        assert "深圳" in self._line("district_lookup", {"keywords": "深圳"})

    def test_never_leaks_raw_json(self):
        """**这是这条改动的核心**：观测原文一个字都不该出现在这行里。"""
        raw = '{"pois":[{"name":"深圳湾公园","location":"113.9,22.5"}],"count":847}'
        line = self._line("poi_keyword", {"keywords": "深圳湾公园"}, raw)
        assert "location" not in line
        assert "{" not in line and "}" not in line

    def test_flight_search_shows_both_airports(self):
        """只报出发机场等于没说。"""
        line = self._line("flights_search", {"departure_id": "PEK", "arrival_id": "SZX"})
        assert "PEK→SZX" in line

    def test_route_mode_is_translated(self):
        assert "公交地铁" in self._line("route_between", {"mode": "transit", "from_lng": 1})

    def test_failure_is_visible(self):
        """失败会被回灌给模型自己纠正，但用户有权看到发生过。"""
        ok = self._line("poi_keyword", {"keywords": "锦里"})
        bad = self._line("poi_keyword", {"keywords": "锦里"}, "错误：上游超时")
        assert ok.startswith("✓")
        assert bad.startswith("✗")

    def test_unknown_tool_falls_back_to_its_name(self):
        assert "book_flight" in self._line("book_flight", {})

    def test_long_subject_is_truncated(self):
        line = self._line("poi_keyword", {"keywords": "很长的地名" * 20})
        assert len(line) < 80

    def test_labels_align_across_tools(self):
        """标签是中文，按字符数补齐会歪——列必须按显示宽度对齐。"""
        lines = [
            self._line("district_lookup", {"keywords": "深圳"}),  # 查城市
            self._line("hotels_autocomplete", {"q": "青旅"}),      # 查酒店名
        ]
        starts = {display_width(ln.split("✓ ")[1].split("深圳")[0].split("青旅")[0])
                  for ln in lines}
        assert len(starts) == 1
