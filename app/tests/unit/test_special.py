"""特殊出行需求的识别与提示。

这个模块的价值全在**兑现**上：认出来但什么都不改，等于没做。而兑现方式只有
一条——把需求变成一句写进 prompt 的可执行指令。所以测试盯两件事：认得准不准，
以及认出来之后那句指令有没有真的带上。
"""

from __future__ import annotations

from app.models.special import (
    SPECIAL_NEEDS,
    detect_needs,
    needs_hint_text,
    normalize_requests,
)


class TestTaxonomyIsSelfConsistent:
    def test_every_label_matches_its_own_patterns(self):
        """**存取之间不能丢效果。**

        存进 `TripRequest.special_requests` 的是 label，判断效果时重新扫文本。
        label 认不出自己，"带老人"就会在一次存取之后退化成一句没用的备注。
        """
        for need in SPECIAL_NEEDS:
            assert detect_needs(need.label) == [need], f"{need.label} 认不出自己"

    def test_keys_and_labels_are_unique(self):
        assert len({n.key for n in SPECIAL_NEEDS}) == len(SPECIAL_NEEDS)
        assert len({n.label for n in SPECIAL_NEEDS}) == len(SPECIAL_NEEDS)

    def test_every_need_carries_an_instruction(self):
        """没有 hint 的条目就是死代码——它是需求唯一的兑现方式。"""
        for need in SPECIAL_NEEDS:
            assert need.hint


class TestDetect:
    def test_elderly(self):
        assert [n.key for n in detect_needs("带着爸妈和爷爷一起去")] == ["mobility"]

    def test_luggage(self):
        assert [n.key for n in detect_needs("行李多，两个大箱子")] == ["luggage"]

    def test_several_at_once(self):
        keys = {n.key for n in detect_needs("带宝宝，不早起，我们吃素")}
        assert keys == {"infant", "late_start", "vegetarian"}

    def test_no_false_positive_on_plain_request(self):
        assert detect_needs("9月5号从北京去成都玩5天") == []

    def test_empty_input(self):
        assert detect_needs() == []
        assert detect_needs("") == []


class TestNormalize:
    def test_known_phrases_collapse_to_labels(self):
        assert normalize_requests("我妈腿脚不太方便") == ["行动不便"]

    def test_unknown_phrases_are_kept_verbatim(self):
        """**taxonomy 管的是效果，不是准入。**

        认不出来就丢掉的话，用户说过的话等于没说——而模型能理解的比代码多。
        """
        assert normalize_requests("我对花粉过敏") == ["我对花粉过敏"]

    def test_mixed(self):
        out = normalize_requests("行李多", "我对花粉过敏")
        assert out == ["行李多", "我对花粉过敏"]

    def test_no_duplicates(self):
        assert normalize_requests("带老人", "长辈同行") == ["行动不便"]


class TestHintText:
    def test_empty_when_nothing_special(self):
        assert needs_hint_text([]) == ""
        assert needs_hint_text(None) == ""

    def test_luggage_hint_still_says_to_take_a_car(self):
        """**这一条原先是代码硬拦的**（机场段改按驾车算）。管线删掉之后
        效果没丢，只是改由模型兑现——指令必须仍然说得出口。"""
        assert "打车" in needs_hint_text(["行李多"])

    def test_late_start_hint_names_the_hour(self):
        """同上：原先是把时间窗推到 10 点，现在得由这句话讲清楚。"""
        assert "10 点" in needs_hint_text(["不早起"])

    def test_says_it_is_mandatory(self):
        """写成背景描述模型会当耳旁风，得明说必须满足。"""
        assert "必须满足" in needs_hint_text(["素食"])

    def test_free_text_is_passed_through(self):
        text = needs_hint_text(["我对花粉过敏"])
        assert "花粉过敏" in text
        assert "用户原话" in text

    def test_known_need_uses_its_curated_hint(self):
        text = needs_hint_text(["行动不便"])
        assert "电梯" in text or "台阶" in text
        assert "用户原话" not in text  # 有专门的提示语就不必再附原话
