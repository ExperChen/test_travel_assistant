"""终端 Markdown 渲染。

这是纯展示层，所以测试盯着两件事：
1. **内容一个字都不能少**——渲染失败可以难看，不能吞字；
2. **中文按两格宽对齐**——行程表格全是中文，用 `len()` 算宽度会全歪。
"""

from __future__ import annotations

from app.core.md_console import display_width, render_markdown

# 所有断言都显式关掉颜色：ANSI 转义会把断言写成一堆 \033[，读不出意图
NO_COLOR = {"color": False}


class TestDisplayWidth:
    def test_cjk_counts_double(self):
        assert display_width("深圳湾公园") == 10
        assert display_width("Shenzhen") == 8

    def test_mixed(self):
        assert display_width("深圳 Bay") == 8  # 4 + 1 + 3


class TestInline:
    def test_markers_are_stripped_even_without_color(self):
        """**这是不上色时最容易出错的地方**：留着 `**` 比不渲染还难看。"""
        out = render_markdown("这家酒店**性价比很高**，`checkin` 15:00", **NO_COLOR)
        assert "**" not in out and "`" not in out
        assert "性价比很高" in out and "checkin" in out

    def test_links_keep_both_text_and_url(self):
        """终端里链接点不了，但地址本身有用，不能只留文字。"""
        out = render_markdown("详见[官网](https://a.cn/b)", **NO_COLOR)
        assert "官网" in out and "https://a.cn/b" in out

    def test_color_wraps_but_keeps_text(self):
        out = render_markdown("**很重要**", color=True)
        assert "\033[" in out
        assert "很重要" in out
        assert "**" not in out


class TestBlocks:
    def test_headings_show_hierarchy(self):
        out = render_markdown("# 成都四日游\n## 第一天\n### 上午", **NO_COLOR)
        assert "成都四日游" in out
        assert "▸▸ 第一天" in out
        assert "▸▸▸ 上午" in out

    def test_h1_underline_matches_cjk_width(self):
        """下划线按显示宽度画，否则中文标题下面的线只有一半长。"""
        out = render_markdown("# 成都", **NO_COLOR)
        assert out.splitlines()[1] == "─" * 4

    def test_bullets_become_dots(self):
        out = render_markdown("- 宽窄巷子\n- 锦里", **NO_COLOR)
        assert "• 宽窄巷子" in out
        assert "• 锦里" in out

    def test_nested_bullets_keep_indent(self):
        out = render_markdown("- 第一天\n  - 上午：武侯祠", **NO_COLOR)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert lines[1].index("•") > lines[0].index("•")

    def test_ordered_lists_keep_numbers(self):
        out = render_markdown("1. 出发\n2. 入住", **NO_COLOR)
        assert "1. 出发" in out
        assert "2. 入住" in out

    def test_rule(self):
        assert "─" in render_markdown("---", **NO_COLOR)

    def test_quote(self):
        assert "│ 提前预约" in render_markdown("> 提前预约", **NO_COLOR)

    def test_code_block_is_kept_verbatim(self):
        out = render_markdown("```\nGET /x\n```", **NO_COLOR)
        assert "GET /x" in out
        assert "```" not in out

    def test_plain_text_passes_through(self):
        """看不懂的语法宁可原样吐出去，也不能吞内容。"""
        assert "就这样" in render_markdown("就这样", **NO_COLOR)

    def test_empty_input_is_safe(self):
        assert render_markdown("") == ""
        assert render_markdown(None) == ""  # type: ignore[arg-type]


class TestTable:
    SRC = (
        "| 时间 | 地点 | 交通 |\n"
        "|---|---|---|\n"
        "| 09:00 | 深圳湾公园 | 地铁 |\n"
        "| 14:00 | 中英街 | 公交 |\n"
    )

    def test_columns_align_by_display_width(self):
        """**模型给的表格源码列宽从来对不齐**——它不知道中文占两格。

        重新排版之后每行的显示宽度必须一致，否则终端里看着是锯齿状。
        """
        lines = [ln for ln in render_markdown(self.SRC, **NO_COLOR).splitlines() if ln.strip()]
        widths = {display_width(ln) for ln in lines}
        assert len(widths) == 1, f"各行宽度不一致：{widths}"

    def test_no_pipes_left_over(self):
        out = render_markdown(self.SRC, **NO_COLOR)
        assert "|" not in out  # 重画成 │，不留 Markdown 原文的竖线
        assert "---" not in out

    def test_all_cells_survive(self):
        out = render_markdown(self.SRC, **NO_COLOR)
        for cell in ("时间", "深圳湾公园", "中英街", "公交"):
            assert cell in out

    def test_ragged_rows_do_not_crash(self):
        """模型少写一个单元格是常事，不能因此崩掉。"""
        out = render_markdown("| a | b |\n|---|---|\n| 1 |\n", **NO_COLOR)
        assert "1" in out

    def test_text_after_table_resumes(self):
        out = render_markdown(self.SRC + "\n收尾说明", **NO_COLOR)
        assert "收尾说明" in out
