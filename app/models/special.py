"""特殊出行需求。

用户说的"带着老人""行李多""不早起""我们吃素"——这些既不是目的地也不是日期，
但每一条都实实在在地改变行程该怎么排。这个模块负责把它们从自由文本里认出来，
并给每一条配一句**能让模型照做**的指令。

**它们现在全部通过 prompt 生效**，代码不再拦截任何一条。原先「行李多 → 机场段
改按驾车算」「不早起 → 每日时间窗推迟到 10 点」是写在固定管线里的硬逻辑，
管线删除后这两条效果并没有丢——它们本来就同时写在 `hint` 里
（"机场往返按打车安排"、"每天从上午 10 点之后开始"），现在由模型来兑现。

`normalize_requests` 认不出的短语原样保留：**taxonomy 管的是效果，不是准入**。
模型能理解的比关键词表多得多，识别不了就丢掉的话，用户说过的话等于没说。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SpecialNeed",
    "SPECIAL_NEEDS",
    "detect_needs",
    "needs_hint_text",
    "normalize_requests",
]


@dataclass(frozen=True)
class SpecialNeed:
    key: str
    label: str
    """规范中文短语。**它自己必须能被 `patterns` 匹配上**——

    存进 `TripRequest.special_requests` 的是这个标签，之后拼 prompt 时会
    重新扫一遍文本取 hint。标签认不出自己，那句指令就在存取之间丢了。
    """
    patterns: tuple[str, ...]
    hint: str = ""
    """写进模型任务描述的一句话。**这是需求唯一的兑现方式**，所以必须写成
    可执行的指令（"单段步行控制在 10 分钟内"），而不是形容词（"照顾老人"）。
    """


SPECIAL_NEEDS: tuple[SpecialNeed, ...] = (
    SpecialNeed(
        key="luggage",
        label="行李多",
        patterns=("行李多", "行李比较多", "大件行李", "行李箱多", "好几个箱子", "托运"),
        hint="行李多，机场往返按打车安排，避免地铁多次换乘。",
    ),
    SpecialNeed(
        key="mobility",
        label="行动不便",
        # 「腿脚」只留两个字：实际说法太杂（不便 / 不太方便 / 不太好 / 不利索），
        # 而这个词出现在出行需求里基本只有一种意思
        patterns=("行动不便", "腿脚", "轮椅", "无障碍", "老人", "长辈",
                  "爷爷", "奶奶", "孕妇"),
        hint="同行有行动不便的人：优先选有电梯、无长段台阶的地点，"
             "避开登山与需要长距离步行的景点，单段步行控制在 10 分钟内。",
    ),
    SpecialNeed(
        key="infant",
        label="带婴幼儿",
        patterns=("婴儿", "宝宝", "幼儿", "推车", "婴儿车", "奶粉", "带娃"),
        hint="带婴幼儿：单次通勤不超过 40 分钟，午后留出回酒店休息的时间，"
             "优先有母婴室的场所。",
    ),
    SpecialNeed(
        key="late_start",
        label="不早起",
        patterns=("不早起", "不想早起", "起不来", "睡懒觉", "晚点出发", "别太早"),
        hint="每天从上午 10 点之后开始安排，不要早班行程。",
    ),
    SpecialNeed(
        key="pet",
        label="带宠物",
        patterns=("带宠物", "宠物", "带狗", "带猫", "携宠"),
        hint="住宿必须可携带宠物；景点需确认允许宠物入内，不确定的标注出来。",
    ),
    SpecialNeed(
        key="halal",
        label="清真饮食",
        patterns=("清真", "穆斯林", "回民"),
        hint="餐饮推荐限清真餐厅。",
    ),
    SpecialNeed(
        key="vegetarian",
        label="素食",
        patterns=("素食", "吃素", "不吃肉"),
        hint="餐饮推荐以素食为主。",
    ),
    SpecialNeed(
        key="no_spicy",
        label="不吃辣",
        patterns=("不吃辣", "不能吃辣", "忌辣", "怕辣"),
        hint="餐饮避开辣菜，川渝湘菜需注明可点微辣或不辣。",
    ),
    SpecialNeed(
        key="crowd_averse",
        label="想去人少的地方",
        patterns=("人少", "小众", "不想人挤人", "别太挤", "冷门"),
        hint="优先小众地点，热门景点安排在开门时段避峰。",
    ),
    SpecialNeed(
        key="photo",
        label="想拍照出片",
        patterns=("拍照", "出片", "摄影", "机位", "打卡点"),
        hint="每天安排至少一个适合拍照的地点，注明较好的光线时段。",
    ),
)


def detect_needs(*texts: str) -> list[SpecialNeed]:
    """从任意文本里认出特殊需求。按 `SPECIAL_NEEDS` 的顺序去重返回。

    收的是可变参数而不是一段拼好的字符串：调用方常常手里是
    `request.special_requests` 那样的列表，拼接的活不该每处各写一遍。
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    return [n for n in SPECIAL_NEEDS if any(p in blob for p in n.patterns)]


def normalize_requests(*texts: str) -> list[str]:
    """文本 → 规范短语列表。识别不出来的原样保留。

    **不丢原话**是这里唯一要守的：模型可能给出"我对花粉过敏"这种没进
    taxonomy 的需求，它照样得传下去——taxonomy 管的是效果，不是准入。
    """
    out = [n.label for n in detect_needs(*texts)]
    for text in texts:
        phrase = (text or "").strip()
        if phrase and phrase not in out and not detect_needs(phrase):
            out.append(phrase[:40])
    return out


def needs_hint_text(requests: list[str] | None) -> str:
    """拼给模型看的说明。没有特殊需求时返回空串。"""
    lines = [f"- {n.hint}" for n in detect_needs(*(requests or [])) if n.hint]
    # taxonomy 外的自由文本原样附上——模型能理解的比代码多
    known = {n.label for n in SPECIAL_NEEDS}
    extra = [r for r in (requests or [])
             if r not in known and not detect_needs(r)]
    lines += [f"- {r}（用户原话，请自行判断如何满足）" for r in extra]
    if not lines:
        return ""
    return "特殊需求（必须满足）：\n" + "\n".join(lines)
