# LangChain v1 vs NexAU：能力对比与 benchmark 设计

> 编写日期：2026-08-07
> 依据：[langchain-agent-framework.md](langchain-agent-framework.md)、[nexau-agent-framework.md](nexau-agent-framework.md)、[ahe-agentic-harness-engineering.md](ahe-agentic-harness-engineering.md)（同日爬取）
> 本项目基线：`app/graph/`（LangGraph 手写 DAG）+ `app/tools/`（SerpAPI 机票/酒店、高德 POI/路径）

---

## 1. 一句话结论

**两者的中间件层已经收敛到几乎同一套接口；真正的差异是「harness 用什么表示」——LangChain 是 Python 对象，NexAU 是文件。**

- **中间件**：hook 名字（`before_agent`/`before_model`/`wrap_model_call`/`wrap_tool_call`/…）与执行顺序（before 首→尾、after 尾→首、wrap 洋葱嵌套）**逐条对得上**。
- **编排**：LangChain 底下是 LangGraph，**图拓扑任意可定义**（本项目就是这么用的）；NexAU 是**固定循环 + sub-agent 递归树**，不提供用户自定义图。
- **harness 表示**：NexAU 把 harness 拆成 **7 个文件级组件**（systemprompt.md / tool_descriptions/ / tools/ / middleware/ / skills/ / sub_agents/ / MEMORY.md），可 diff、可 git-track、**可被另一个 agent 写**。LangChain 的等价物散在 Python 代码里，粒度是函数与对象。**AHE 论文选 NexAU 正是为了这一点**，不是为了性能。
- **成熟度**：LangChain 143.6k star / MIT / PyPI 有包；NexAU 194 star / Apache-2.0 / **PyPI 无包，只能 git 装**。

---

## 2. 逐维度对照表

| 维度 | LangChain v1 | NexAU v0.4.1 |
|------|-------------|--------------|
| **入口 API** | `create_agent(model, tools, ...)` | `Agent(config=AgentConfig(...))` / `AgentConfig.from_yaml()` |
| **配置形态** | Python 命令式为主 | **YAML 声明式为主**，Python 等价可用 |
| **底层编排** | LangGraph `StateGraph`，**可自定义任意图** | `Executor` 固定循环，**不可自定义拓扑** |
| **循环上限** | `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` | `max_iterations`（默认 100）+ `stop_tools` |
| **工具定义** | `@tool` 装饰器，schema 从**类型标注 + docstring** 推导 | **YAML `input_schema` + Python `binding` 分离**，JSON Schema 手写 |
| **工具动态注册** | `wrap_model_call` + `wrap_tool_call` 组合 | `AgentState.ToolRegistry.add_tool()`；`defer_loading` + `ToolSearch` |
| **工具懒加载** | `ProviderToolSearchMiddleware`（依赖 provider 侧） | `lazy: true`（延迟 import）、`defer_loading: true`（延迟注入 schema） |
| **预置参数** | 闭包 / `functools.partial` | `extra_kwargs`（YAML 或 `from_yaml`） |
| **中间件 hooks** | `before_agent` / `before_model` / `after_model` / `after_agent` / `wrap_model_call` / `wrap_tool_call`（+ `before_tool` / `after_tool`） | **同名同语义的 8 个** |
| **hook 顺序** | before: 首→尾；after: 尾→首；wrap 嵌套 | **完全一致** |
| **hook 返回值** | `dict[str, Any] \| None`（state 增量）+ `jump_to` | `HookResult.no_changes()` / `HookResult.with_modifications(...)` |
| **内置中间件数量** | ~15 个核心 + deepagents 侧 5+ | 6 个 + sensitive-word |
| **短期状态** | Graph `State`（`AgentState` 子类，带 reducer） | `AgentState.AgentContext` |
| **跨会话状态** | `Store`（InMemory / Postgres / Mongo / Redis） | `GlobalStorage`（线程安全）+ `SessionManager` |
| **持久化 / 断点续跑** | **Checkpointer**（InMemory / Sqlite / Postgres），支持 time travel | `SessionManager` + DB engine（`InMemoryDatabaseEngine` / `SQLDatabaseEngine`） |
| **Human-in-the-loop** | `HumanInTheLoopMiddleware` + `interrupt` / `Command(resume=)` | `ask_user` 工具 + `stop_tools`；CLI 支持多轮交互 |
| **结构化输出** | `response_format=PydanticModel` → `result["structured_response"]` | 无一等公民 API；靠 prompt + 工具 schema |
| **多 agent** | `SubAgentMiddleware`（**在 `deepagents` 独立包**） | **核心内置**：`RecallSubAgent` + `SubAgentManager` 递归树 + Agent Team（实验性） |
| **并发** | async 原生（`ainvoke` / `astream`），LangGraph 并行节点 | `ThreadPoolExecutor` + `copy_context()`；另有 async 指南 |
| **Provider 适配** | 独立 provider 包，`init_chat_model("provider:model")` | 单包内 `api_type` 四选一：`openai_chat_completion` / `openai_responses` / `anthropic_chat_completion` / `gemini_rest` |
| **无原生 function calling 的模型** | 无内置方案 | **`tool_call_mode: xml`**，工具塞进 system prompt，带截断修复 |
| **上下文压缩** | `SummarizationMiddleware`、`ContextEditingMiddleware` | `ContextCompactionMiddleware`（tool-result 替换 / LLM 总结 / 溢出应急 50-50） |
| **Skills** | `SkillsMiddleware`（deepagents） | `Skill.from_folder()`，**兼容 Claude Skills**，自动注入 `LoadSkill` |
| **MCP** | 通过 `langchain-mcp-adapters` 等集成 | 核心内置 `mcp_servers`，HTTP + stdio，`asyncio.gather` 并行初始化 |
| **沙箱** | `ShellToolMiddleware` + `HostExecutionPolicy` | `LocalSandbox` / `E2BSandbox`，大输出头尾截断落盘 |
| **Tracing** | LangSmith（一等公民）+ OTel | Langfuse、`InMemoryTracer`、`CompositeTracer`；**span 带 `time_to_first_token_ms`** |
| **流式** | `stream_mode`: values/updates/messages/custom/debug；`stream_events(version="v3")` | SSE transport + `AgentEventsMiddleware` 事件总线 |
| **CLI** | LangGraph CLI（偏部署） | `run-agent` / `run-agent.cmd`，带 tool-call 与 sub-agent trace 可视化 |
| **模型打桩（测试）** | **`LLMToolEmulator`** | 无内置；可用 `wrap_model_call` 自己返回罐头响应 |
| **harness 文件化程度** | 低——散在 Python 里 | **高——7 组件逐个成文件**（见 §4） |
| **License** | MIT | Apache-2.0 |
| **分发** | PyPI `langchain==1.3.14` | **仅 git / whl，无 PyPI** |
| **Python** | `>=3.10,<4` | `.python-version` = 3.12 |
| **社区** | 143.6k star / 23.9k fork | 194 star / 32 fork |

---

## 3. 中间件层的"收敛"——benchmark 的最大便利

| Hook | LangChain 签名 | NexAU 签名 |
|------|---------------|-----------|
| `before_agent` | `(state, runtime) -> dict \| None` | `(hook_input) -> HookResult` |
| `before_model` | 同上 | 同上 |
| `after_model` | 同上 | 同上 |
| `after_agent` | 同上 | 同上 |
| `before_tool` | 同上 | 同上 |
| `after_tool` | 同上 | 同上 |
| `wrap_model_call` | `(request, handler) -> ModelResponse` | `(params, call_next) -> ModelResponse` |
| `wrap_tool_call` | `(request, handler) -> ToolMessage \| Command` | `(params, call_next)` |

**这意味着**：计时、计 token、记录 tool-call 序列这类插桩，可以用**同一套逻辑**在两边各写一个薄适配器，不会因观测手段不同引入偏差。

NexAU 独有的坑：`after_tool` 里有 **`tool_output`（原始）/ `llm_tool_output`（格式化后、给模型看的）双通道**，顺序是"工具执行 → formatter → `after_tool`"。**记录"模型实际看到了什么"必须读 `llm_tool_output`**，否则两边口径不一致。

---

## 4. AHE 带来的重定向：真正该测的是什么

[AHE 论文与代码](ahe-agentic-harness-engineering.md)（806 star，Terminal-Bench 2 上 GPT-5.5 达 84.7%、排名 #3）把问题重新定义了一遍，有三条直接影响你的 benchmark 设计：

### 4.1 「决定 Agent 能不能干活的不是模型，是 harness」

AHE 的做法是**冻结基座模型**，只演化 harness，10 轮把 GPT-5.4 的 Terminal-Bench 2 pass@1 从 **69.7% 抬到 77.0%**，超过手写的 Codex（71.9%）。

推论：**"LangChain vs NexAU 谁跑得快"是个低价值问题。**跑得快慢在 agent 场景里被 LLM 延迟淹没了（框架开销通常 <1% wall-clock）。高价值的问题是：**同样的工程投入下，哪个框架能让 harness 演化得更好。**

### 4.2 NexAU 被选中的唯一理由是「组件可观测性」

AHE README 原文把 NexAU 定位为三层可观测性的第一层：

> "**NexAU** decomposes the harness into seven orthogonal, file-level components, each git-tracked so every edit is auditable and revertible."

七组件（HARNESS.md v1.0 规范）：System Rules / Tool Descriptions / Tool Implementations / Middleware / Skills / Sub-Agents / Long-Term Memory。

**这才是与 LangChain 的实质分歧**：

| | LangChain | NexAU |
|---|---|---|
| system prompt | `create_agent(system_prompt="...")` 里的字符串字面量 | `systemprompt.md` 独立文件（支持 jinja） |
| tool description | 从 docstring / `args_schema` 推导，**与实现同处一个 `.py`** | `tool_descriptions/*.tool.yaml`，**与实现物理分离** |
| tool 实现 | 同上，同一个函数 | `tools/*.py`，`binding:` 指过去 |
| middleware | Python 类，`middleware=[...]` 列表 | `middleware/*.py` + YAML `import:` 注册 |
| skills | `SkillsMiddleware(sources=["./skills/"])`（deepagents） | `skills/*/SKILL.md`，兼容 Claude Skills |
| sub-agents | `SubAgentMiddleware(subagents=[{dict}])` — **内联 dict** | `sub_agents/*/` 目录 + YAML 引用 |
| 长期记忆 | `Store` 后端（DB） | `LongTermMEMORY.md` 文件 |

一句话：**LangChain 的 harness 是"代码"，NexAU 的 harness 是"数据"。** 让 agent 自动改代码 vs 自动改数据文件——后者的 diff 粒度、回滚成本、可审计性都好一档。这是可以设计实验去量化的，而不是靠感觉。

### 4.3 "从最小化 harness 开始演化"是硬约束

HARNESS.md 原文：

> "一个可工作的 harness 最小只需要 2 个组件：System Rules + 至少一个工具。**AHE 论文证明：从最小化状态开始演化的效果最好——所有改进都是'挣来的'，不是'预设的'。**"

AHE 的起点 `agents/code_agent_simple/code_agent.yaml` **只注册了 `run_shell_command` 一个工具**；10 轮后的 `experiments/evolved_harness/code_agent.yaml` **工具集仍然只有这一个**，结构性新增只有一个自研 middleware（`ExecutionRiskHintsMiddleware`）+ `reasoning: {effort: high}`。

**换言之：+7.3pp 几乎全部来自 systemprompt / tool description / 一个 middleware 的"内容"演化，而不是堆组件。** 你的 benchmark 必须把「组件内容质量」和「组件数量」拆开测，否则测出来的是谁默认给的东西多。

### 4.4 已经存在的可比数字（但不能直接相减）

AHE 的 `explore_agent` 配置里，**必读 web 源第一条就是 LangChain**：

```yaml
- url: "https://blog.langchain.dev/how-we-built-the-1-agent-on-terminal-bench/"
  focus: "LangChain deepagents 架构详解：multi-agent、context 管理、tool 设计，52.8%→66.5% 的每个改进步骤和消融数据"
```

| 方案 | Terminal-Bench | 基座 |
|------|---------------|------|
| LangChain deepagents | 52.8% → **66.5%**（TB 1.0，手工迭代） | 见原文 |
| Codex（手写 harness） | **71.9%**（TB 2.0） | GPT-5.4 |
| **AHE / NexAU（自动演化）** | 69.7% → **77.0%**（TB 2.0） | GPT-5.4 |
| **AHE / NexAU** | **84.7% ± 2.1**，榜 #3 | GPT-5.5 |

⚠️ **基座模型与 benchmark 版本都不同，这几个数字之间不能直接相减。** 但它给了你一个现成的公共擂台：**Terminal-Bench 是目前唯一能把两条路线摆在一起的场地。**

---

## 5. 对本项目而言的关键差异

本项目 `app/graph/builder.py` 的图是：

```
intake → resolve_city ─┬─ flight ×3 ─────────┐
                       └─ 景点 → 酒店 ───────┴→ route_planner → summarize
```

两条分支并行，酒店必须排在景点之后（要用景点重心做重排锚点），最后 join。

**这个拓扑 NexAU 表达不了。** NexAU 只有"主 agent 循环 + sub-agent 递归"。要复刻只能：

1. 把 flight / attraction+hotel 做成两个 sub-agent，靠 LLM 决定并行调用（`parallel_execution_id`），**顺序与并行由模型决定而非代码保证**；或
2. 把编排写死进一个自定义工具——那测的就不是框架了。

**所以"把现有 pipeline 移植到 NexAU 再对比"不成立**，必须换口径（见 §6）。

其他影响：

- 本项目是 **async 全链路**（`AsyncIterator`、`ainvoke`）；NexAU 主循环是 `ThreadPoolExecutor` 同步模型，`docs/advanced-guides/async.md` 未爬，**异步支持程度待核实**。
- 本项目用 `respx` mock HTTP；NexAU 侧需自建等价打桩层。
- 本项目 `TripState` 是 Pydantic + 自定义 serde 注册；NexAU 的 state 是字典语义，**类型安全度低一档**。
- **本项目的 harness 目前不符合 HARNESS.md**：system prompt 散在 `app/agents/*.py` 里，tool description 与实现同处一个模块。要做 harness 层对比，第一步是先把本项目的 harness 拆成七组件——**这件事本身对项目也有价值，与 benchmark 无关**。

---

## 6. Benchmark 设计

### 6.1 三个层次，分开测

**Track A —「框架机械开销」**（窄、可控、结论硬，但价值最低）
同一个 ReAct 单 agent 循环，两边各实现一次：同模型、同 prompt、同工具集、`temperature=0`、同轮次上限，**模型和工具全部打桩**。测纯框架开销。

- LangChain 打桩：`LLMToolEmulator` + `wrap_model_call` 返回预录响应
- NexAU 打桩：自定义 `Middleware.wrap_model_call` 返回同一批预录响应
- 打桩点在**同一个 hook** 上，可比性最好
- **预期结论：差异 <1% wall-clock，被 LLM 延迟淹没。跑一次确认量级即可，别在这上面花时间。**

**Track B —「端到端任务质量」**（宽、真实、结论软）
让两个框架各用**自己最地道的架构**解同一批旅行规划任务。承认这里混合了"框架"与"架构"两个变量——**报告时必须写明**。

**Track C —「harness 可演化性」**（价值最高，AHE 路线）
两边都按 HARNESS.md 七组件拆好起始 harness（**最小化：system rules + 1 个工具**），跑同样的 `evaluate → analyze → improve` 循环 N 轮，比较：

- N 轮后的 pass@1 增量（Δpp）
- 每轮 Change Manifest 的**预测命中率**（`expected_fixes_verified` / `expected_fixes`）
- **回滚率**（`verdict == "revert"` 的比例）——衡量框架的改动是否易于验证
- 每次编辑的 **diff 行数**与**触及文件数**——衡量组件正交性
- evolve agent 改坏后的**恢复成本**

> Track C 才是真正回答"NexAU 的文件化 harness 到底值不值"的实验。前提是**你得先给 LangChain 写一个 `langchain.yaml` profile**——AHE 的 `profiles/` 里只有 codex / hermes / openclaw，**没有 langchain**。

### 6.2 任务集（基于本项目领域）

| 档位 | 样例 | 考察点 |
|------|------|--------|
| L1 单工具 | "北京有哪些必去景点" | 工具选择、参数抽取 |
| L2 双工具串行 | "8/20 从上海飞曼谷，找航班和市中心酒店" | 顺序依赖、日期解析 |
| L3 并行 + join | "吉隆坡到曼谷 3 天，机票+酒店+景点+逐日路线" | 并行调度、结果合并 |
| L4 需澄清 | "下个月想去个海边城市玩几天" | HITL 中断/恢复 |
| L5 失败恢复 | 工具注入 429 / 超时 / 畸形 JSON | 重试、降级、错误回灌 |
| L6 长上下文 | 20+ 轮追问改需求 | 上下文压缩策略 |

每档 ≥10 条用例。**rollout 次数参考 AHE 的 `harbor.k = 2`**；但旅行规划的方差比 coding 大，建议 **k=5**。

> 若想接公共擂台，直接用 **Terminal-Bench 2.0**（AHE 的 `dataset: "terminal-bench@2.0"`）而不是自建任务集——有现成 leaderboard、现成对手数字、现成 verifier。代价是要搭 E2B + harbor，且与本项目领域无关。**建议：自建任务集做主实验，Terminal-Bench 做一次对外可比的锚点。**

### 6.3 指标

**正确性**
- 任务成功率（对照 `docs/architecture/output-spec.md` 校验结构化输出）
- 工具调用准确率：调对工具 / 参数正确 / 无多余调用
- 幻觉率：编造航班号、景点、坐标的比例
- 结构化输出合法率（Pydantic 校验通过率）

**效率**
- LLM 调用次数、迭代轮次
- 输入/输出 token → **成本（元/任务）**
- 端到端 wall-clock（p50 / p95）
- **TTFT**（NexAU span 直接有 `time_to_first_token_ms`；LangChain 从 `stream_mode="messages"` 首 chunk 算）

**稳定性**
- k 次运行的成功率方差、token 方差（**AHE 报的是 `84.7% ± 2.1`，方差必须一起报**）
- 注入故障后的恢复率
- 超时 / 死循环触发率

**可演化性（Track C 专属）**
- Δpass@1 / 轮
- Change Manifest 预测命中率、回滚率
- 单次编辑的 diff 行数 / 触及文件数
- HARNESS.md 合规分（用 `scripts/validate_harness.py`）

**工程性**
- 实现同一 agent 的 LOC / 文件数 / 配置行数
- 新增一个工具的改动量
- 冷启动时间、依赖体积

### 6.4 必须控住的混淆变量

| 变量 | 做法 |
|------|------|
| 模型 | 同 model id、同 base_url、`temperature=0`、同 `max_tokens`；**推理档位（`reasoning.effort`）也要对齐**——AHE 的 gpt54 overlay 用 `effort: high` |
| 工具 schema | **JSON Schema 逐字节一致**（LangChain 侧用 `args_schema` 显式给 Pydantic，别让它从 docstring 推） |
| system prompt | 逐字节一致；NexAU 的 jinja 变量要渲染成与 LangChain 相同的最终文本 |
| tool_call_mode | 两边都用 structured/native；XML 模式单列为 NexAU 专项 |
| 起始 harness | **两边都从最小化开始**（system rules + 1 个工具），否则测的是默认配置的丰俭 |
| 重试策略 | 都关掉或都设 `max_retries=0`，由 benchmark 层统一重试 |
| 上下文压缩 | Track A/B 全关；单列为一项对比 |
| 轮次上限 | 同值（AHE 用 `max_iterations: 300`、`max_context_tokens: 200000`） |
| 网络 | 工具全部打桩 / 录制回放，避免 SerpAPI、高德抖动进入 wall-clock |
| Prompt 缓存 | 显式关闭 |
| 沙箱 | 若要对标 AHE，用 E2B；注意 **SaaS 版有账户级并发沙箱上限**，超了整轮迭代会卡住 |

### 6.5 落地目录建议

```
benchmarks/
├── README.md
├── cases/                    # 任务集（框架无关的 JSON/YAML）
├── fixtures/
│   ├── llm_responses/        # 预录 LLM 响应
│   └── tool_responses/       # SerpAPI / 高德 罐头响应
├── harness/                  # ★ 不依赖任何 agent 框架
│   ├── metrics.py
│   ├── runner.py
│   └── report.py
├── workspaces/               # ★ HARNESS.md 七组件形态的起始 harness
│   ├── langchain/            #   需自己补 profiles/langchain.yaml
│   │   ├── systemprompt.md
│   │   ├── tool_descriptions/
│   │   ├── tools/
│   │   └── middleware/
│   └── nexau/
│       └── ...（同构）
├── adapters/
│   ├── langchain_impl/       # 读 workspace → create_agent + 插桩 middleware
│   └── nexau_impl/           # 读 workspace → AgentConfig + 插桩 Middleware
├── manifests/                # Change Manifest（git-tracked）
└── results/
```

关键设计：

1. **`harness/` 完全不依赖任何框架**，两个 adapter 实现同一接口 `run(case) -> RunTrace`。
2. **`workspaces/` 是两边共享的 harness 表示**——同一份 `systemprompt.md`、同一批 `tool_descriptions/*.yaml`，adapter 负责翻译成各框架的形态。这样 Track C 才能公平：evolve agent 改的是同一种文件。
3. 工具**实现共用**（直接 import `app/tools/` 里的纯函数），只有**声明层**不同。

---

## 7. 开工前必须先解决的问题

1. ~~**NexAU 能否 pip 安装？**~~ **已确认**：仓库已公开，`git ls-remote https://github.com/nex-agi/NexAU.git v0.4.1` 匿名可读，`pip install git+https://github.com/nex-agi/NexAU.git@v0.4.1` 即可，无需 ssh key（官方文档那句 "private repo" 已过期）。
   **但有版本与环境风险**：本项目 `.venv` 是 **Python 3.14.6**；NexAU `.python-version` 锁 **3.12**；AHE 要求 **≥3.13** 且锁 NexAU **v0.3.9**（不是 v0.4.1）。**给 NexAU 单开一个 venv，并先定死用哪个版本。**
2. **Track C 要不要做？** 它价值最高但成本也最高（要写 evolve agent + trace 分析 + manifest 验证）。可以先复用 AHE 的 `skills/agentic-harness-engineering/scripts/`（`init_harness.py` / `validate_harness.py` / `generate_manifest.py` / `verify_manifest.py`）打底，而不是从零写。
3. **`profiles/langchain.yaml` 得自己写。** AHE 只提供了 codex / hermes / openclaw 三个 profile。这是把 LangChain 纳入 HARNESS.md 体系的必要一步，也是 Track C 的前置。
4. **NexAU 的 async 支持程度。** 本项目全 async，NexAU 主循环是线程池。补爬 `docs/advanced-guides/async.md` 并跑通一个 async 工具。
5. **`langchain` 主包没装。** 当前 `.venv` 只有 `langchain-core 1.5.3` + `langgraph 1.2.10`。要用 `create_agent` 得先 `pip install -U langchain`（会拉 `langgraph>=1.2.5,<1.3.0`，与现有 1.2.10 兼容）；要 `SubAgentMiddleware` 另需 `deepagents`。
6. **本项目 harness 尚未七组件化。** system prompt 散在 `app/agents/*.py`，tool description 与实现同模块。Track B/C 的前置工作。

---

## 8. 待补爬清单

**AHE**（价值最高：起点 vs 终点 harness 的全文 diff，直接看到 10 轮改了什么）
```bash
R=china-qijizhifeng/agentic-harness-engineering
curl -s "https://raw.githubusercontent.com/$R/main/agents/code_agent_simple/systemprompt.md"
curl -s "https://raw.githubusercontent.com/$R/main/experiments/evolved_harness/systemprompt.md"
curl -s "https://raw.githubusercontent.com/$R/main/experiments/evolved_harness/middleware/execution_risk_hints.py"
curl -s "https://raw.githubusercontent.com/$R/main/experiments/evolved_harness/LongTermMEMORY.md"
```
另：`agents/evolve_agent/evolve_prompt.md`、`skills/.../references/change-manifest-schema.json`、`scripts/validate_harness.py`

**LangChain**
`blog.langchain.dev/how-we-built-the-1-agent-on-terminal-bench/`（**AHE 自己列为必读，含 52.8%→66.5% 的逐步消融数据，是最直接的对手材料**）、`/oss/python/releases/langchain-v1`、`/oss/python/deepagents/*`

**NexAU**（`https://raw.githubusercontent.com/nex-agi/NexAU/main/docs/advanced-guides/<name>.md`）
`async.md`、`streaming-events.md`、`session-management.md`、`global-storage.md`、`context_compaction.md`、`tracer.md`
