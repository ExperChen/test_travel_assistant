"""把模型输出的 Markdown 渲染成终端可读的文本。

**为什么要自己写而不是用 rich**：这个项目除了 CLI 之外没有任何地方需要终端渲染，
为一个入口拉一个依赖不划算；而且需要的子集很小——模型的行程输出无非是
标题、粗体、列表、表格四样。

三条约束决定了实现方式：

1. **中文按两格宽算。** 行程表格里全是中文，用 `len()` 对齐会歪得没法看
   （"深圳湾公园" 是 5 个字符、10 格宽）。
2. **不确定能上色时就不上色。** 重定向到文件、管道给 less、CI 里跑，
   ANSI 转义序列都会变成一堆乱码。判据是 `isatty()` 加 `NO_COLOR` 约定。
3. **渲染不了的原样吐出去。** 这是展示层——看不懂的语法宁可让用户看到原文，
   也不能吞掉内容或者抛异常。
"""

from __future__ import annotations

import os
import re
import sys
from unicodedata import east_asian_width

__all__ = ["render_markdown", "display_width", "supports_color"]

# ---------------------------------------------------------------- ANSI

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"


def supports_color(stream=None) -> bool:
    """这个流上色安全吗。

    `NO_COLOR` 是跨工具的约定（no-color.org），优先级高于一切自动判断——
    用户明确说了不要就别自作聪明。
    """
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    try:
        if not stream.isatty():
            return False
    except Exception:  # noqa: BLE001 —— 被替换过的流可能连 isatty 都没有
        return False
    if os.name == "nt":
        return _enable_windows_ansi()
    return True


def _enable_windows_ansi() -> bool:
    """老 conhost 默认不认 ANSI，得显式打开虚拟终端处理。

    Windows Terminal / VS Code 终端本来就是开的，这个调用是幂等的；
    真正的老 cmd.exe 上会失败，那就老实不上色。
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE, 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 —— 非 Windows 或受限环境
        return False


# ---------------------------------------------------------------- 宽度

_WIDE = frozenset("WF")


def display_width(text: str) -> int:
    """字符串占多少个终端格子。

    中文/日文/全角标点占两格。行程表格几乎全是中文，这个函数是表格对齐的
    全部依据——用 `len()` 的话「深圳湾公园」会被当成 5 格，实际占 10 格。
    """
    return sum(2 if east_asian_width(ch) in _WIDE else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


# ---------------------------------------------------------------- 行内

_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+?)\*(?![*\w])")
_RE_CODE = re.compile(r"`([^`\n]+?)`")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RE_STRIKE = re.compile(r"~~(.+?)~~")


def _inline(text: str, color: bool) -> str:
    """行内标记。不上色时也要**把标记符号去掉**——留着 `**` 比渲染失败还难看。"""
    # 链接先处理：模型偶尔给参考链接，终端里点不了，保留文字和地址
    text = _RE_LINK.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", text)
    if color:
        text = _RE_BOLD.sub(lambda m: f"{BOLD}{m.group(1)}{RESET}", text)
        text = _RE_CODE.sub(lambda m: f"{CYAN}{m.group(1)}{RESET}", text)
        text = _RE_ITALIC.sub(lambda m: f"{ITALIC}{m.group(1)}{RESET}", text)
        text = _RE_STRIKE.sub(lambda m: f"{DIM}{m.group(1)}{RESET}", text)
    else:
        text = _RE_BOLD.sub(r"\1", text)
        text = _RE_CODE.sub(r"\1", text)
        text = _RE_ITALIC.sub(r"\1", text)
        text = _RE_STRIKE.sub(r"\1", text)
    return text


# ---------------------------------------------------------------- 块级

_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_RE_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_RE_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_RE_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")

_RULE_CHAR = "─"
_RULE_WIDTH = 60


def render_markdown(text: str, *, color: bool | None = None, width: int = 0) -> str:
    """渲染一段 Markdown。`color=None` 表示自动判断。

    `width` 只用于分隔线，正文不做重排——终端宽度千变万化，硬折行经常把
    表格和长地址切坏，交给终端自己软换行更安全。
    """
    if color is None:
        color = supports_color()
    rule_width = width or _RULE_WIDTH

    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_code = False

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            # 代码块原样保留缩进，只做整体着色
            out.append(f"{DIM}    {line}{RESET}" if color else f"    {line}")
            i += 1
            continue

        # 表格要连着好几行一起看，先探一探
        if "|" in line and i + 1 < len(lines) and _RE_TABLE_SEP.match(lines[i + 1]):
            block, i = _collect_table(lines, i)
            out.extend(_render_table(block, color))
            continue

        out.append(_render_line(line, color, rule_width))
        i += 1

    return "\n".join(out).strip("\n")


def _render_line(line: str, color: bool, rule_width: int) -> str:
    if not line.strip():
        return ""

    if _RE_RULE.match(line):
        return f"{DIM}{_RULE_CHAR * rule_width}{RESET}" if color else _RULE_CHAR * rule_width

    if m := _RE_HEADING.match(line):
        level, body = len(m.group(1)), _inline(m.group(2).strip(), color)
        if not color:
            # 一级用下划线、其余用前缀符号——没有颜色时层级只能靠形状表达
            if level == 1:
                return f"{body}\n{_RULE_CHAR * display_width(body)}"
            return f"{'▸' * level} {body}"
        if level == 1:
            return f"\n{BOLD}{YELLOW}{body}{RESET}\n{DIM}{_RULE_CHAR * display_width(body)}{RESET}"
        if level == 2:
            return f"\n{BOLD}{CYAN}{body}{RESET}"
        return f"{BOLD}{body}{RESET}"

    if m := _RE_QUOTE.match(line):
        body = _inline(m.group(1), color)
        return f"{DIM}│ {body}{RESET}" if color else f"│ {body}"

    if m := _RE_BULLET.match(line):
        indent, body = m.group(1), _inline(m.group(2), color)
        dot = f"{CYAN}•{RESET}" if color else "•"
        return f"{indent}  {dot} {body}"

    if m := _RE_ORDERED.match(line):
        indent, num, body = m.group(1), m.group(2), _inline(m.group(3), color)
        head = f"{CYAN}{num}.{RESET}" if color else f"{num}."
        return f"{indent}  {head} {body}"

    return _inline(line, color)


# ---------------------------------------------------------------- 表格


def _collect_table(lines: list[str], start: int) -> tuple[list[str], int]:
    i = start + 2  # 表头 + 分隔行
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
        i += 1
    return [lines[start], *lines[start + 2 : i]], i


def _split_row(row: str) -> list[str]:
    cells = row.strip().split("|")
    # 行首行尾的 `|` 会切出两个空串，去掉；中间的空单元格要留着
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _render_table(block: list[str], color: bool) -> list[str]:
    """按列宽对齐重画。

    模型给的表格源码列宽从来对不齐（它不知道中文占两格），直接打出来是歪的。
    这里按**显示宽度**重新排版——行程表格是最常被人逐行读的部分。
    """
    rows = [_split_row(r) for r in block]
    if not rows:
        return []

    cols = max(len(r) for r in rows)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    widths = [max(display_width(r[c]) for r in rows) for c in range(cols)]

    header, *body = rows
    sep = "─┼─".join(_RULE_CHAR * w for w in widths)
    head_cells = " │ ".join(_pad(c, w) for c, w in zip(header, widths, strict=True))

    out = [f"{BOLD}{head_cells}{RESET}" if color else head_cells]
    out.append(f"{DIM}{sep}{RESET}" if color else sep)
    for row in body:
        cells = [_pad(_inline(c, False), w) for c, w in zip(row, widths, strict=True)]
        line = " │ ".join(cells)
        out.append(_inline(line, color) if color else line)
    return out
