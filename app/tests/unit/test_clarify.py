"""追问节点：只问该问的，且绝不成为新的卡死点。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.graph.nodes.clarify import ASKABLE, apply_answers, build_form_fields, clarify
from app.models.events import InterruptQuestion
from app.models.trip import TripRequest

REQUEST = TripRequest(
    departure_city="北京",
    destination_city="成都",
    outbound_date=date(2026, 9, 5),
    return_date=date(2026, 9, 10),
)


class TestWhatGetsAsked:
    def test_asks_only_the_fields_the_system_guessed(self):
        fields = build_form_fields({"adults": "default", "pace": "default",
                                    "budget_per_night": "default"})
        assert [f.key for f in fields] == list(ASKABLE)

    def test_never_asks_what_the_user_already_said(self):
        fields = build_form_fields({"adults": "prompt", "pace": "default",
                                    "budget_per_night": "default"})
        assert "adults" not in [f.key for f in fields]

    def test_never_asks_what_memory_filled(self):
        """这正是记忆的价值所在——记住了就别再问。"""
        fields = build_form_fields({"adults": "memory", "pace": "memory",
                                    "budget_per_night": "memory"})
        assert fields == []

    def test_no_origins_means_no_questions(self):
        """没有出处信息就没有证据，宁可不问也不凭猜打断用户。"""
        assert build_form_fields({}) == []

    def test_travel_class_and_transport_are_deliberately_not_asked(self):
        """默认值足够安全的字段不问——问了只是把追问变成查户口。"""
        fields = build_form_fields({k: "default" for k in
                                    ("adults", "pace", "budget_per_night",
                                     "travel_class", "transport")})
        assert {f.key for f in fields} == set(ASKABLE)

    def test_every_field_has_a_default(self):
        """没有默认值的问题不允许存在——超时清扫要能按默认值放行。"""
        fields = build_form_fields({k: "default" for k in ASKABLE})
        assert all(f.default for f in fields)
        assert all(any(o.key == f.default for o in f.options) for f in fields)


class TestFormQuestion:
    def test_form_default_is_a_json_map(self):
        """表单也得能塞进统一的「用 default 恢复」口子里。"""
        q = InterruptQuestion.build_form(
            "intake.clarify", "确认一下", build_form_fields({k: "default" for k in ASKABLE})
        )
        assert q.kind == "form"
        assert q.skippable
        assert set(json.loads(q.default)) == set(ASKABLE)

    def test_accepts_validates_each_subfield(self):
        q = InterruptQuestion.build_form(
            "intake.clarify", "确认一下", build_form_fields({k: "default" for k in ASKABLE})
        )
        assert q.accepts(json.dumps({"adults": "2", "pace": "relaxed"}))
        assert not q.accepts(json.dumps({"adults": "99"}))
        assert not q.accepts(json.dumps({"unknown_field": "x"}))
        assert not q.accepts("不是JSON")

    def test_empty_form_is_rejected(self):
        with pytest.raises(ValueError, match="至少有一个字段"):
            InterruptQuestion.build_form("intake.clarify", "空的", [])


class TestApplyAnswers:
    def test_applies_all_three(self):
        updated, changed = apply_answers(
            REQUEST, {"adults": "3", "pace": "relaxed", "budget_per_night": "300_600"}
        )
        assert updated.adults == 3
        assert updated.pace == "relaxed"
        assert updated.budget_per_night == 600
        assert changed == ["adults", "budget_per_night", "pace"]

    def test_accepts_a_json_string(self):
        """超时路径送来的是 `default` 那个 JSON 串。"""
        updated, changed = apply_answers(REQUEST, json.dumps({"adults": "2"}))
        assert updated.adults == 2
        assert changed == ["adults"]

    def test_one_bad_field_does_not_discard_the_others(self):
        """答了三项里的两项，不该因为第三项拼错而全丢。"""
        updated, changed = apply_answers(
            REQUEST, {"adults": "2", "pace": "超音速", "budget_per_night": "300_600"}
        )
        assert updated.adults == 2
        assert updated.pace == "standard"  # 非法值被跳过，保留原值
        assert changed == ["adults", "budget_per_night"]

    def test_unlimited_budget_means_no_cap(self):
        updated, _ = apply_answers(REQUEST, {"budget_per_night": "any"})
        assert updated.budget_per_night is None

    def test_skipping_changes_nothing(self):
        updated, changed = apply_answers(REQUEST, {})
        assert changed == []
        assert updated is REQUEST

    def test_garbage_is_ignored(self):
        assert apply_answers(REQUEST, "不是JSON")[1] == []
        assert apply_answers(REQUEST, None)[1] == []

    def test_fields_outside_the_askable_set_are_rejected(self):
        """追问只能改它问过的东西，不能被拿来改目的地。"""
        updated, changed = apply_answers(REQUEST, {"destination_city": "三亚"})
        assert changed == []
        assert updated.destination_city == "成都"


@pytest.mark.asyncio
class TestClarifyNode:
    async def test_passes_through_when_nothing_to_ask(self):
        """绝大多数请求走的是这条路——不该有任何中断。"""
        patch = await clarify({"request": REQUEST, "origins": {"adults": "prompt"}})
        assert patch == {"clarified": True}

    async def test_only_asks_once(self):
        patch = await clarify(
            {"request": REQUEST, "origins": {k: "default" for k in ASKABLE},
             "clarified": True}
        )
        assert patch == {}

    async def test_disabled_by_config(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.clarify_enabled", False)
        patch = await clarify(
            {"request": REQUEST, "origins": {k: "default" for k in ASKABLE}}
        )
        assert patch == {}
