# AHE（Agentic Harness Engineering）文档（爬取归档）

> 来源（爬取日期：2026-08-07）：
> - 仓库：<https://github.com/china-qijizhifeng/agentic-harness-engineering>（`README.md`、`configs/`、`agents/`、`skills/` 逐字爬取）
> - 论文：<https://arxiv.org/abs/2604.25850> — *Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses*（复旦 & 北大，2026）
> - 博客：<https://dawning-road.github.io/blog/agentic-harness-engineering>
> - 排行榜：<https://www.tbench.ai/leaderboard/terminal-bench/2.0>
>
> 关联档案：[nexau-agent-framework.md](nexau-agent-framework.md)、[langchain-agent-framework.md](langchain-agent-framework.md)、[comparison-and-benchmark.md](comparison-and-benchmark.md)

---

## 0. 仓库元信息（GitHub API，2026-08-07 抓取）

| 项目 | 值 |
|------|-----|
| 全名 | `china-qijizhifeng/agentic-harness-engineering` |
| License | MIT |
| 语言 | Python（`requires-python = ">=3.13"`） |
| Star / Fork | **806 / 89** |
| 创建 / 最近 push | 2026-04-23 / 2026-08-03 |
| homepage | arxiv.org/abs/2604.25850 |

**核心成绩（README 原文）**：

- AHE（GPT-5.5）在 [Terminal-Bench 2.0 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0) **排名 #3，84.7%**（截至 2026-05-15）；仓库描述给出 **84.7% ± 2.1 pass@1**
- GPT-5.4 上 10 轮迭代把 Terminal-Bench 2 pass@1 从 **69.7% → 77.0%**
- 超过手写的 Codex（**71.9%**）、以及自演化基线 ACE 与 TF-GRPO
- 冻结后的 harness **无需重新演化即可迁移**到 SWE-bench-Verified 和另外 4 个基座模型

> 论文结论的落点：演化出来的组件编码的是**通用工程经验**，而不是针对某个 benchmark 的过拟合调参。

---

## 1. AHE 是什么

**固定基座模型，演化模型外面那层 harness。** 被演化的对象是七类组件：system prompt、tool descriptions、tool implementations、middleware、skills、sub-agents、long-term memory。

三层可观测性（README 原文）：

| 层 | 承担者 | 作用 |
|----|--------|------|
| **组件可观测性** | **[NexAU](https://github.com/nex-agi/NexAU.git)** | 把 harness 拆成 **7 个正交的、文件级组件**，逐个 git-tracked，每次编辑都可审计、可回滚 |
| **经验可观测性** | *Agent Debugger* | 把 ~10M token 的原始 trace 蒸馏成分层、带出处的报告；优化器默认读摘要，但随时能下钻到任一 rollout 的原始 trace |
| **决策可观测性** | *Evolve Agent* | 提出带证据的编辑、预测其影响，并在下一轮被 flipped tasks **自动证伪** |

> ⚠️ **这一条是理解「为什么选 NexAU」的关键**：AHE 选 NexAU 不是因为它跑得快，而是因为它**把 harness 表达成文件**——文件才能 diff、才能 git-track、才能被另一个 agent 写。

> **Agent Debugger 授权说明**（README 原文）：当前 release 只**部分开源** Agent Debugger，因公司策略暂时无法完全开源。

---

## 2. 演化循环：evaluate → analyze → improve

### 2.1 Evaluate — 产出 trace，而不只是分数

`harbor` 在隔离的 E2B 沙箱里用当前 `code_agent` 跑数据集。每个 task 写出：

| 文件 | 内容 |
|------|------|
| `agent/nexau_in_memory_tracer.cleaned.json` | 完整 step 级 trace（messages、tool calls、middleware events） |
| `agent/nexau.txt` | 运行时日志（middleware 报错、崩溃、告警） |
| `verifier/reward.txt` | pass/fail |

> README 原文强调："The **trace, not the pass rate**, is the unit every later step operates on."

### 2.2 Analyze — 把 >10M token 的 trace 蒸馏成带出处的证据

*Agent Debugger* 产出分层报告：

- `analysis/overview.md` — 跨 task 的根因汇总
- `analysis/detail/{task}.md` — 单 task 深度分析

每条结论都链回原始 trace，优化器可以在下决心前下钻。

### 2.3 Improve — 带证据、可证伪的编辑

*Evolve Agent* **只能写 `workspace/` 目录**，该目录暴露 NexAU 的七个组件：

```
workspace/
├── systemprompt.md
├── code_agent.yaml
├── tool_descriptions/
├── tools/
├── middleware/
├── skills/
├── sub_agents/
└── LongTermMEMORY.md
```

每次编辑**必须提交四个字段**：

1. **Failure evidence** — 哪些 task 失败了 + trace 摘录
2. **Root cause** — 为什么失败（不是"什么失败了"）
3. **Targeted fix** — 直接针对该根因的改动
4. **Predicted impact** — 哪些 task 应该翻成 pass、哪些有回归风险

### 2.4 Loop — 错代（staggered generations）实现证伪

每个 `runs/iteration_NNN/` 里混着两代：

- `input/` — 第 `NNN-1` 轮产出的 workspace（刚刚被评测过）
- `evolve/` — 第 `NNN` 轮写出的内容（下一轮才评测）

下一轮评测的 flip（pass↔fail）在 `change_evaluation.json` 里**归因回本轮的编辑**——预测不成立的改动被回滚或修订。循环在达到 `target_pass_rate` 或 `max_iterations` 时终止。

---

## 3. HARNESS.md v1.0 规范（`skills/agentic-harness-engineering/references/HARNESS.md`）

> 这是仓库里最有复用价值的产物：**一份跨 Agent 框架的 harness 结构规范**，目标是让 Hermes / OpenClaw / Claude Code / Codex / Cursor 的"外挂系统"有统一的语言、结构和可演化能力。

规范开篇的判断：

> "2026 年的行业共识已经形成：**决定 AI Agent 能不能稳定干活的，不是模型本身，而是模型外面那套 Harness。**"

要解决的三个问题：各框架 harness 结构各自为政 / 没有统一语言描述改动 / 改了好不好无法验证。

### 3.1 七个正交组件

| # | 组件 | 职责 | 示例文件 | 类比 |
|---|------|------|---------|------|
| 1 | **System Rules** | 行为规则、工作流指引、边界定义 | `AGENTS.md`, `SOUL.md`, `systemprompt.md`, `.cursorrules` | 宪法 |
| 2 | **Tool Descriptions** | 每个工具的 Schema、用途、使用陷阱 | `tool_descriptions/*.yaml` | 产品说明书 |
| 3 | **Tool Implementations** | 工具执行代码 | `tools/*.py`, `tools/*.js` | 机器人工厂 |
| 4 | **Middleware** | 执行管道钩子——拦截、转换、增强 | `middleware/*.py` | 安检通道 |
| 5 | **Skills** | 可复用工作流模式 | `skills/*/SKILL.md` | SOP 手册 |
| 6 | **Sub-Agents** | 可委托的子代理单元 | `sub_agents/*/` | 外包团队 |
| 7 | **Long-Term Memory** | 跨会话持久知识 | `MEMORY.md`, `experiences.md` | 个人笔记 |

**正交**的定义：改其中一个不影响其他。

### 3.2 组件交互

```
用户输入
    ▼
┌──────────────────────────────────────────────────────────────┐
│  System Rules ───── 定义 Agent 怎么思考、怎么决策               │
│  Middleware ──────── 拦截/转换输入输出                          │
│  Skills ─────────── 匹配复用工作流                             │
│  Tool Descriptions ─ 告诉 LLM 有什么工具、怎么用                │
│  Tool Implementations ─ 执行工具逻辑                           │
│  Sub-Agents ─────── 委托专注子任务                             │
│  Long-Term Memory ─ 读取/写入跨会话知识                        │
└──────────────────────────────────────────────────────────────┘
    ▼  输出
```

### 3.3 反模式

- ❌ 把工具实现逻辑写进 System Rules
- ❌ 把长期记忆塞进 System Rules（每次加载都膨胀上下文）
- ❌ 多个组件做同一件事（prompt 和 middleware 都在做输出格式化）
- ❌ 改了工具不更新描述

### 3.4 最小化起始状态

> "一个可工作的 harness 最小只需要 2 个组件：System Rules + 至少一个工具（描述 + 实现）。其他组件应**在需要时才添加**。**AHE 论文证明：从最小化状态开始演化的效果最好——所有改进都是'挣来的'，不是'预设的'。**"

**这一条对 benchmark 设计是硬约束**：起始 harness 越丰富，越难分辨提升来自框架还是来自预设。

### 3.5 Change Manifest

规范的核心。每次改动附一份 manifest，使改动可验证、可归因、可回滚：

```json
{
  "manifest_version": "1.0",
  "harness_spec_version": "1.0",
  "iteration": 3,
  "timestamp": "2026-05-21T10:30:00+08:00",
  "author": "agent-name-or-human",
  "changes": [
    {
      "change_id": "ch_001",
      "component": "tool_descriptions",
      "subtype": "update",
      "file_path": "tool_descriptions/search.tool.yaml",
      "summary": "为 search 工具添加分页参数说明",
      "failure_evidence": "Task T-042 在搜索超过50条结果时失败，trace 显示 agent 尝试传入 page=2 参数但工具描述未声明该参数",
      "root_cause": "search 工具实际支持分页，但 tool description 未声明 page_size 和 offset 参数，导致 LLM 不知道可以分页",
      "targeted_fix": "在 search.tool.yaml 的 input_schema.properties 中添加 page_size (integer) 和 offset (integer) 参数，更新 description 说明分页用法",
      "predicted_impact": {
        "expected_fixes": ["T-042", "T-057"],
        "at_risk_regressions": ["T-013"],
        "rationale": "T-042 和 T-057 都因搜索结果截断失败；T-013 对 search 有依赖但调用模式不同"
      }
    }
  ],
  "verification": {"status": "pending", "scheduled_at": "2026-05-22T10:30:00+08:00"}
}
```

`component` 必须是七个之一；`failure_evidence` / `root_cause` / `targeted_fix` / `predicted_impact` 全部必填。

**核心原则：Falsifiable** — "每次修改都是一个可被证伪的假设。" 下轮评估后回写：

```json
{
  "verification": {
    "status": "verified",
    "completed_at": "2026-05-22T10:45:00+08:00",
    "result": {
      "expected_fixes_verified": ["T-042", "T-057"],
      "unexpected_fixes": [],
      "regressions_observed": [],
      "false_predictions": []
    },
    "verdict": "keep"
  }
}
```

三种 verdict：`keep` / `revert` / `partial`。

### 3.6 合规检查清单

**必须（Hard）**
- [ ] 七个组件清晰分离到不同文件/目录
- [ ] 每个工具都有独立的 description 文件和实现文件
- [ ] 每次修改附带 Change Manifest
- [ ] Manifest 含 failure_evidence + root_cause + targeted_fix + predicted_impact
- [ ] 每次修改后都经过验证（verified / reverted / partial）

**推荐（Soft）**
- [ ] 从最小化 harness 开始演化
- [ ] 目录结构符合标准模板
- [ ] Evaluate / Analyze 流程自动化（cronjob / CI）
- [ ] Manifest 存进版本控制

### 3.7 跨框架 profile

`skills/agentic-harness-engineering/profiles/` 下有 `codex.yaml`、`hermes.yaml`、`openclaw.yaml`——把七组件规范映射到具体框架的目录约定，并给每个组件打 `required` 与 `weight`，供 `scripts/validate_harness.py` 打合规分。示例（`codex.yaml`）：

```yaml
check:
  system_rules:
    files: ["AGENTS.md", "SOUL.md", "systemprompt.md"]
    required: true
    weight: 2
  tool_descriptions:
    dir: tool_descriptions
    extensions: [".yaml", ".yml", ".json"]
    required: false
    weight: 1
  tool_implementations:
    dir: tools
    extensions: [".py", ".js", ".ts", ".sh"]
    required: false
    weight: 1
  middleware: {dir: middleware, extensions: [".py", ".js", ".ts"], required: false, weight: 1}
  skills: {dir: skills, indicator: "SKILL.md", required: false, weight: 1}
  sub_agents: {dir: sub_agents, required: false, weight: 1}
  long_term_memory: {files: ["MEMORY.md", "experiences.md"], required: false, weight: 1}
```

**没有 langchain.yaml profile——如果要把 LangChain 纳入这套规范，这是需要自己补的第一块。**

### 3.8 配套脚本

`skills/agentic-harness-engineering/scripts/`：`init_harness.py`（按模板初始化 workspace）、`validate_harness.py`（合规打分）、`generate_manifest.py`、`verify_manifest.py`、`ci.sh`。

---

## 4. 工程实现细节

### 4.1 依赖与环境（`pyproject.toml`）

```toml
requires-python = ">=3.13"
dependencies = [
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "e2b>=1.0.0",
    "nexau @ git+https://github.com/nex-agi/NexAU.git@v0.3.9",
    "harbor @ git+https://github.com/Curry09/harbor-LJH.git",
    "agent_debugger_core",
]
[tool.uv]
constraint-dependencies = ["claude-agent-sdk<0.1.49"]
```

> 注意：**AHE 锁的是 NexAU `v0.3.9`，不是最新的 v0.4.1**；`harbor` 是 `Curry09/harbor-LJH` 这个 fork，不是上游。

前置：Python ≥ 3.13、`uv`、`tmux`。

### 4.2 启动

```bash
git clone https://github.com/china-qijizhifeng/agentic-harness-engineering.git
cd agentic-harness-engineering
uv sync
cp .env.example .env      # 填 LLM_API_KEY / LLM_BASE_URL / E2B_API_KEY / SERPER_API_KEY / GITHUB_TOKEN

# 一次性：为数据集构建 E2B 模板（16 并发）
uv run python scripts/build_templates.py --dataset-dir /path/to/dataset -j 16

# 跑实验（tmux 后台）
./scripts/evolve.sh configs/experiments/exp-simple-code-gpt54.yaml
./scripts/evolve.sh --attach configs/experiments/exp-simple-code-gpt54.yaml
./scripts/evolve.sh --batch
```

数据集来自 [`laude-institute/harbor-datasets`](https://github.com/laude-institute/harbor-datasets)；每个 task 一个子目录，`task.toml` 里声明 `[environment].docker_image`（或退回 `environment/Dockerfile`）。模板 alias = task 名把 `.` 换成 `-`。

> ⚠️ **E2B SaaS 有账户级并发沙箱上限**。harbor 想 spawn 的沙箱数超过配额时，多出的沙箱起不来、整轮迭代会卡住。调 `n_concurrent` 前先看配额。自建集群无此限制，但受硬件容量限制。

### 4.3 目录结构

```
agentic-harness-engineering/
├── evolve.py                       # 主循环编排
├── trace_converter.py              # rollout trace → debugger 友好的 JSON
├── agents/
│   ├── code_agent_simple/          # 被演化的编码 agent（起点）
│   ├── evolve_agent/               # 演化 meta-agent（构建在 NexAU 上）
│   │   ├── evolve_prompt.md
│   │   ├── compact_prompt.md
│   │   ├── middleware/             # context compaction / failover / ralph loop …
│   │   ├── skills/                 # agent-debugger-cli / nexau-evolution-guide
│   │   └── tools/                  # file / shell / web / session tools
│   └── explore_agent/              # 数据集与源码探索 agent
├── configs/
│   ├── base.yaml                   # 共享默认
│   └── experiments/                # exp-simple-code-gpt54.yaml 等 4 个 overlay
├── skills/agentic-harness-engineering/   # HARNESS.md 规范 + 校验脚本 + profiles
├── experiments/evolved_harness/    # 演化后的成品 harness
└── scripts/                        # evolve.sh / evolve-resume.sh / build_templates.py
```

### 4.4 起点 harness：`agents/code_agent_simple/code_agent.yaml`

**只有一个工具**——严格遵守 §3.4 的"最小化起始状态"：

```yaml
type: agent
name: nexau_code_agent
max_context_tokens: 200000
system_prompt: ./systemprompt.md
system_prompt_type: jinja
tool_call_mode: openai
max_iterations: 300

llm_config:
  model: ${env.LLM_MODEL}
  base_url: ${env.LLM_BASE_URL}
  api_key: ${env.LLM_API_KEY}
  max_tokens: 32000
  temperature: 0.7
  top_p: 0.95
  stream: true
  api_type: openai_responses

tools:
  # Shell
  - name: run_shell_command
    yaml_path: ./tool_descriptions/run_shell_command.tool.yaml
    binding: tools.shell_tools:run_shell_command

tracers:
  - import: nexau.archs.tracer.adapters.in_memory:InMemoryTracer
```

目录里还有 `LongTermMEMORY.md`、`ShortTermMEMORY.md`、`systemprompt.md`、`nexau.json`、`start.py`。

### 4.5 终点 harness：`experiments/evolved_harness/code_agent.yaml`

10 轮演化后的成品。与起点的**结构性差异只有两处**（字段顺序被 YAML dump 重排了）：

```yaml
  reasoning:                                   # ← 新增：推理档位
    effort: high
    summary: detailed
middlewares:                                   # ← 新增：唯一一个自研 middleware
- import: middleware.execution_risk_hints:ExecutionRiskHintsMiddleware
```

工具集**仍然只有 `run_shell_command`**。也就是说：**69.7% → 77.0% 的提升，主要来自 systemprompt / tool description / 一个 middleware 的内容演化，而不是堆工具。** 这对"harness 决定上限"的论点是很强的佐证，也提示 benchmark 应该把"组件内容"和"组件数量"分开测。

### 4.6 `configs/base.yaml` 关键字段

| 字段 | 默认 | 说明 |
|------|------|------|
| `dataset` / `path` | `terminal-bench@2.0` | 二选一：harbor 内置数据集引用 / 本地目录 |
| `target_pass_rate` | 0.95 | 达到即停 |
| `max_iterations` | 100 | 最大迭代数 |
| `harbor.agent` / `.env` | `nexau` / `e2b` | agent 适配器 / 环境 |
| `harbor.k` | **2** | **每个 task 的 rollout 次数** |
| `harbor.n_concurrent` | 64（gpt54 overlay 里 96） | 并发度 |
| `harbor.e2b_sandbox_timeout` | 3600 | 沙箱最大存活秒数 |
| `source_config_dir` | `agents/code_agent_simple` | 起点 harness 目录 |
| `agent_debugger.max_concurrent` | 16 | 并行 `adb ask` 进程 |
| `agent_debugger.max_tasks` | 90 | 最多分析几个 task |
| `agent_debugger.timeout_per_task` | 600 | 单 task 超时（秒） |
| `agent_debugger.retry_attempts` | 3 | 重试次数（指数退避 2.0s 起） |
| `best_of_n.enabled` | false | 开启后每轮 spawn N 个带不同策略约束的 evolve agent，并行评测取最优 |
| `post_evolve.datasets` | `swebench-verified` | 演化后的迁移验证 |

`best_of_n.strategy_hints` 很值得注意——它把改动强行分成两类，防止 evolve agent 每轮都只改 prompt：

```yaml
- "You MUST focus on STRUCTURAL changes: middleware, tool implementations, or sub-agents. Do NOT modify system prompt or skill content this iteration."
- "You MUST focus on GUIDANCE changes: system prompt rules, skill packages, tool descriptions, or LongTermMEMORY. Do NOT create or modify middleware Python files this iteration."
```

### 4.7 Explore-Agent 的知识来源（`base.yaml`）

实验开始前自动探索源码和文档，为 evolve agent 生成知识 skill；**与 iteration 1 的 harbor eval 并行执行，零额外时间成本**。

源码来源：`https://github.com/nex-agi/NexAU.git` @ `v0.3.9`。

Web 来源里包含**顶尖 coding agent 架构调研**——注意第一条：

| URL | focus（原文） |
|-----|--------------|
| `blog.langchain.dev/how-we-built-the-1-agent-on-terminal-bench/` | **LangChain deepagents 架构详解：multi-agent、context 管理、tool 设计，52.8%→66.5% 的每个改进步骤和消融数据** |
| `humanlayer.dev/blog/building-coding-agents` | HumanLayer harness 设计：context 优化、hook vs prompt、harness 层 vs agent 层优化的区分 |
| `anthropic.com/engineering/claude-code-best-practices` | Claude Code：skill 系统、compaction 策略、hook 设计、prompt 结构、tool 调用模式 |
| `anthropic.com/engineering/swe-bench-sonnet` | Anthropic SWE-bench 技术报告 |
| `docs.all-hands.dev/modules/usage/architecture` | OpenHands：event stream、AgentController、sandbox 集成 |
| `aider.chat/docs/leaderboards/` | 不同 LLM 在 coding benchmark 的得分、edit format 影响 |
| `cognition.ai/blog/devin-terminal-bench` | Devin Terminal Bench 架构 |
| `terminal-bench.com` | 官方排行榜与评测方法 |
| `e2b.dev/docs/sandbox/{overview,api}` | 沙箱生命周期、预装软件、资源限制 |

> **对你的对比很关键**：AHE 自己就把 **LangChain deepagents 在 Terminal-Bench 上从 52.8% → 66.5%** 当作必读参考。也就是说这两条技术路线在同一张榜上已经有可比数字了（AHE-GPT5.4 77.0% vs LangChain deepagents 66.5%，但**基座模型不同，不能直接相减**）。

### 4.8 CLI

```bash
python evolve.py --config <file> [--batch [dir|files...]] [--experiment <name>]
                 [--start-iteration N] [--skip-eval]

./scripts/evolve.sh <config> [--experiment <name>] [--start-iteration N]
                    [--skip-eval] [--session <name>] [--batch] [--attach]
```

断点续跑：

```bash
./scripts/evolve.sh --experiment 2026-04-10__23-20-14__gpt54 --start-iteration 16 \
  configs/experiments/exp-simple-code-gpt54.yaml
```

只跑 evolve 不重评：加 `--skip-eval`。

---

## 5. 未爬取、需要时再补

- `agents/evolve_agent/evolve_prompt.md`、`compact_prompt.md` — evolve agent 的完整 prompt
- `agents/evolve_agent/middleware/` — context compaction / failover / ralph loop 的实现
- `agents/evolve_agent/skills/nexau-evolution-guide` — 演化指南 skill
- `agents/code_agent_simple/systemprompt.md`、`tool_descriptions/run_shell_command.tool.yaml` — 起点组件全文
- `experiments/evolved_harness/systemprompt.md`、`middleware/execution_risk_hints.py`、`LongTermMEMORY.md` — **演化成品全文（对比起点即可看到 10 轮到底改了什么，价值最高）**
- `evolve.py`、`trace_converter.py`
- `skills/agentic-harness-engineering/references/{directory-template.md, change-manifest-schema.json, examples/, analysis/, docs/}`
- 论文 PDF：仓库根目录 `agentic_harness_engineering.pdf`

补爬模板：

```bash
R=china-qijizhifeng/agentic-harness-engineering
curl -s "https://raw.githubusercontent.com/$R/main/experiments/evolved_harness/systemprompt.md"
```
