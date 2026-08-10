# NexAU Agent 框架文档（爬取归档）

> 来源（爬取日期：2026-08-07）：
> - 仓库主页：<https://github.com/nex-agi/NexAU>
> - 官方文档目录：<https://github.com/nex-agi/NexAU/tree/main/docs>（`getting-started.md` / `core-concepts/` / `advanced-guides/`）
> - DeepWiki（**AI 自动生成**，非官方，仅用于补全官方文档未覆盖的内部实现细节）：<https://deepwiki.com/nex-agi/NexAU>
>
> **可信度标注**：下文凡标 `【官方】` 的段落，内容逐字来自仓库内 `docs/` 或 `README`；标 `【DeepWiki】` 的段落来自 AI 生成的代码索引，**未经源码核对，benchmark 前需要二次验证**。

---

## 0. 仓库元信息（GitHub API，2026-08-07 抓取）

| 项目 | 值 |
|------|-----|
| 全名 | `nex-agi/NexAU` |
| 定位 | NexAU (AU for Agent Universe), a general-purpose agent framework for building intelligent agents with tool capabilities |
| License | Apache-2.0 |
| 语言 | Python（`.python-version` = **3.12**） |
| Star / Fork | 194 / 32 |
| Open issues | 4 |
| 创建时间 | 2025-11-12 |
| 最近 push | 2026-08-03 |
| 最新 release | **v0.4.1**（2026-04-01）；上一个 v0.4.0（2026-03-24） |
| PyPI | **无**（`https://pypi.org/pypi/nexau/json` 返回 404，只能从 Git / whl 安装） |

**归属**：Nex-AGI（上海创智学院 Shanghai Innovation Institute 发起的开源联盟）。同生态还有 `NexA4A`（meta-agent，自动生成 agent 架构）、`NexGAP`（agentic 训练数据流水线）、`NexHTML`（基于 NexAU 的 HTML Agent）、NexRL、NexVenusCL。配套论文：Nex-N1，<https://arxiv.org/abs/2512.04987>。

### 仓库目录结构

```
NexAU/
├── .skills/            # 仓库自带 skills
├── cli/                # CLI 实现
├── docs/               # 官方文档（本档主要来源）
│   ├── getting-started.md
│   ├── index.md
│   ├── core-concepts/  # agents.md / tools.md / llms.md
│   ├── advanced-guides/# hooks.md / mcp.md / skills.md / sandbox.md / tracer.md
│   │                   # agent-team.md / async.md / context_compaction.md
│   │                   # global-storage.md / image.md / session-management.md
│   │                   # streaming-events.md / templating.md / tool-formatters.md
│   │                   # transports.md / sensitive-word-middleware.md
│   ├── development/ , testing/ , rfcs/
│   ├── windows.md , windows-support-baseline.md , cross-platform-guidelines.md
├── examples/           # cc_agent, code_agent, deep_research, mcp,
│                       # nexau_building_team, plugin_adapter, sensitive_word, simple_research
├── nexau/              # 主包
├── rfcs/               # 设计提案（RFC-0006 / RFC-0017 等在文档中被引用）
├── run-agent , run-agent.cmd   # CLI 包装脚本（后者为 Windows）
├── Makefile , pyproject.toml , pytest.ini , uv.lock
```

---

## 1. 安装 【官方】

> ⚠️ **文档与现状不一致**：官方 `getting-started.md` 仍写着 "you need to use ssh because nexau is a private repo"，但仓库**已转公开**。2026-08-07 实测 `git ls-remote https://github.com/nex-agi/NexAU.git v0.4.1` 匿名返回 `265805b86d89081e8ec9f026ee7807d2be0ee853`，因此 **https 免密安装可用，不必配 ssh key**：
>
> ```bash
> pip install git+https://github.com/nex-agi/NexAU.git@v0.4.1
> ```
>
> 下面保留官方原文的 ssh 写法以备查。

```bash
# 从 release tag 安装（官方推荐，文档原文用 ssh）
pip install git+ssh://git@github.com/nex-agi/NexAU.git@v0.4.1
# 或下载 whl：https://github.com/nex-agi/nexau/releases/
pip install nexau-0.4.1-py3-none-any.whl

# main 分支
pip install git+ssh://git@github.com/nex-agi/NexAU.git

# uv 版本
uv pip install git+ssh://git@github.com/nex-agi/NexAU.git@v0.4.1

# 从源码
git clone git@github.com:nex-agi/NexAU.git
cd NexAU
pip install uv
uv sync
```

**Windows**：支持 Windows 10/11，默认后端为 **PowerShell**；Git Bash 可选，仅在需要显式 bash 兼容模式或 bash-only 命令时使用。源码树内跑 YAML 用原生包装脚本：

```powershell
.\run-agent.cmd examples/code_agent/code_agent.yaml
```

### 环境变量（`.env`）【官方】

```dotenv
LLM_MODEL="your-llm-model"
LLM_BASE_URL="your-llm-api-base-url"
LLM_API_KEY="your-llm-api-key"
SERPER_API_KEY="api key from serper.dev"   # 需要 web search 时必填

LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_HOST="https://us.cloud.langfuse.com"
```

Langfuse 为可选，仅在需要 trace 时配置。

### 运行示例 【官方】

```bash
# 需先 uv pip install python-dotenv
dotenv run uv run examples/code_agent/start.py
# > Enter your task: Build an algorithm art about 3-body problem

# CLI 一行跑任意 agent yaml
./run-agent examples/code_agent/code_agent.yaml
```

CLI 支持多轮人机交互、tool call trace、sub-agent trace。

### 开发命令 【官方 README】

`make lint`（Ruff）、`make format`、`make typecheck`（MyPy + Pyright）、`make test`（pytest + coverage）、`make ci`。

---

## 2. 核心抽象 【DeepWiki + 官方交叉】

| 组件 | 作用 |
|------|------|
| **Agent** | 入口对象，持有 config 与服务（`SessionManager`、`LLMCaller`、`Tracer`） |
| **AgentConfig** | Pydantic 容器，承载 agent 全部元信息与设置 |
| **Tool** | 外部能力单元：**YAML 描述 + Python binding 分离** |
| **Executor** | 编排引擎，跑 thought–action–observation 循环 |
| **AgentState** | 运行时"ground truth"：`AgentContext`（短期）+ `GlobalStorage`（持久）+ `ToolRegistry`（可动态 `add_tool()`）+ `SandboxManager` |
| **Middleware** | 拦截/改写执行的钩子层 |
| **UMP** | Unified Message Protocol，统一消息协议 |

### 设计要点

- **工具定义/实现解耦**：YAML 声明 schema，Python 函数做 binding，可动态加载。
- **UMP 统一消息协议**：消息含 Role（User/Assistant/System/Tool）与 ContentBlocks（`TextBlock` / `ToolUseBlock` / `ToolResultBlock`），使同一套逻辑跨 GPT / Claude / Gemini 通用。
- **Late adaptation（RFC-0006）**：内部保持 provider-agnostic 的工具定义，只在**请求边界**才适配成 OpenAI Tools / Anthropic XML 等具体格式。
- **Middleware-first**：横切关注点从核心逻辑移出到中间件。
- **递归委派**：sub-agent 构成层级化任务分解树。

---

## 3. 定义 Agent

### 3.1 Python 方式 【官方 `core-concepts/agents.md`】

```python
import os
from datetime import datetime
from nexau import Agent, AgentConfig, Tool, LLMConfig
from nexau.archs.tool.builtin.web_tools import google_web_search, web_fetch

def main():
    # Create tools from YAML configurations
    web_search_tool = Tool.from_yaml("tools/WebSearch.yaml", binding=google_web_search)
    web_read_tool = Tool.from_yaml("tools/WebRead.yaml", binding=web_fetch)

    # Configure the LLM
    llm_config = LLMConfig(
        model=os.getenv("LLM_MODEL"),
        base_url=os.getenv("LLM_BASE_URL"),
        api_key=os.getenv("LLM_API_KEY")
    )

    # Create the agent instance
    agent_config = AgentConfig(
        name="research_agent",
        tools=[web_search_tool, web_read_tool],
        llm_config=llm_config,
        system_prompt="You are a research agent. Use web_search and web_read tools to find information.",
    )
    research_agent = Agent(config=agent_config)

    # Run the agent
    response = research_agent.run(
        "What's the latest news about AI developments?"
    )
    print(response)
```

### 3.2 完整 Python 配置（code_agent，含 Skill + Middleware）【官方 `getting-started.md`】

```python
import logging
import os
from pathlib import Path

from nexau import Agent, AgentConfig, LLMConfig, Skill, Tool
from nexau.archs.main_sub.execution.hooks import LoggingMiddleware

from nexau.archs.tool.builtin import (
    google_web_search, list_directory, read_file, read_many_files,
    replace, run_shell_command, search_file_content, web_fetch,
    write_file, write_todos,
)

base_dir = Path("examples/code_agent")

# NexAU decouples the definition and implementation (binding) of tools
tools = [
    Tool.from_yaml(base_dir / "tools/WebSearch.tool.yaml", binding=google_web_search),
    Tool.from_yaml(base_dir / "tools/WebFetch.tool.yaml", binding=web_fetch),
    Tool.from_yaml(base_dir / "tools/write_todos.tool.yaml", binding=write_todos),
    Tool.from_yaml(base_dir / "tools/search_file_content.tool.yaml", binding=search_file_content),
    Tool.from_yaml(base_dir / "tools/read_file.tool.yaml", binding=read_file),
    Tool.from_yaml(base_dir / "tools/write_file.tool.yaml", binding=write_file),
    Tool.from_yaml(base_dir / "tools/replace.tool.yaml", binding=replace),
    Tool.from_yaml(base_dir / "tools/run_shell_command.tool.yaml", binding=run_shell_command),
    Tool.from_yaml(base_dir / "tools/list_directory.tool.yaml", binding=list_directory),
    Tool.from_yaml(base_dir / "tools/read_many_files.tool.yaml", binding=read_many_files),
]

# NexAU supports Skills (compatible with Claude Skills)
skills = [
    Skill.from_folder(base_dir / "skills/skill-creator"),
    Skill.from_folder(base_dir / "skills/template-skill"),
]

agent_config = AgentConfig(
    name="nexau_code_agent",
    max_context_tokens=100000,
    system_prompt=str(base_dir / "systemprompt.md"),
    system_prompt_type="jinja",
    tool_call_mode="structured",   # xml or structured
    llm_config=LLMConfig(
        temperature=0.7,
        max_tokens=4096,
        model=os.getenv("LLM_MODEL"),
        base_url=os.getenv("LLM_BASE_URL"),
        api_key=os.getenv("LLM_API_KEY"),
        api_type="openai_chat_completion",
    ),
    tools=tools,
    skills=skills,
    middlewares=[
        LoggingMiddleware(
            model_logger="nexau_code_agent",
            tool_logger="nexau_code_agent",
            log_model_calls=True,
        ),
    ],
)

agent = Agent(config=agent_config)
print(agent.run("Build an algorithm art about 3-body problem",
                context={"working_directory": os.getcwd()}))
```

### 3.3 YAML 方式 【官方 `core-concepts/agents.md`】

```yaml
type: agent
name: my_research_agent
max_context_tokens: 100000
system_prompt: |
  Date: {{date}}. You are a research agent specialized in finding and analyzing information.
  Use web_search to find relevant information, then web_read to get detailed content.
system_prompt_type: string
llm_config:
  temperature: 0.7
  max_tokens: 4096
tools:
  - name: web_search
    yaml_path: ./tools/WebSearch.yaml
    binding: nexau.archs.tool.builtin.web_tools:google_web_search
  - name: web_read
    yaml_path: ./tools/WebRead.yaml
    binding: nexau.archs.tool.builtin.web_tools:web_fetch
sub_agents: []
```

加载：

```python
from pathlib import Path
from nexau import Agent, AgentConfig, LLMConfig

agent_config = AgentConfig.from_yaml(Path("agent/my_agent.yaml"))
agent_config.llm_config = LLMConfig(
    model=os.getenv("LLM_MODEL"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("LLM_API_KEY"),
)
agent = Agent(config=agent_config)
response = agent.run(
    "Research the latest developments in quantum computing",
    context={"date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
)
```

> 注意：`system_prompt` 里的 `{{date}}` 由 `agent.run(..., context={...})` 注入；`system_prompt_type` 取 `string` / `file` / `jinja`。

### 3.4 YAML 顶层字段速查 【DeepWiki】

| Key | 类型 | 说明 |
|-----|------|------|
| `name` | string | agent 标识 |
| `description` | string | 用途描述 |
| `system_prompt` | string/list | 支持 string / file 路径 / jinja |
| `system_prompt_type` | string | `string` \| `file` \| `jinja` |
| `llm_config` | object | **必填** |
| `tools` | array | 工具定义 |
| `sub_agents` | array | 子 agent 引用 |
| `skills` | array | skill 目录路径 |
| `mcp_servers` | array | MCP 服务器配置 |
| `max_iterations` | int | Executor 循环上限（默认 **100**） |
| `tool_call_mode` | string | `structured` \| `xml` \| `openai` \| `anthropic` |
| `stop_tools` | array | 触发即停机的工具名（见 agent-team 示例：`stop_tools: [ask_user]`） |
| `middlewares` | array | 中间件 |
| `tracers` | array | tracer |
| `max_context_tokens` | int | 上下文 token 上限 |

**变量替换**（解析前生效）：`${env.VAR_NAME}`、`${this_file_dir}`、`${variables.key}`。

---

## 4. 工具系统

### 4.1 内置工具 【官方 `core-concepts/tools.md`】

| 分组 | 模块 | 工具 |
|------|------|------|
| 文件 | `nexau.archs.tool.builtin.file_tools` | `read_file`（分页）、`read_visual_file`（图/视频，需 vision 模型）、`write_file`、`replace`、`apply_patch`（Codex 风格 diff，支持增删改）、`multiedit_tool`、`glob`（支持 `.gitignore`）、`list_directory`、`read_many_files`、`search_file_content` |
| 网络 | `nexau.archs.tool.builtin.web_tools` | `google_web_search`、`web_fetch` |
| Shell / 代码 | `...shell_tools`、`...run_code_tool` | `run_shell_command`（沙箱内）、`run_code_tool`（带超时） |
| 会话 / 任务 | `...session_tools` | `write_todos`、`complete_task`、`save_memory`、`ask_user`、`background_task_manage_tool` |
| 工具检索 | `...tool_search` | `ToolSearch` — 按需搜索并注入 deferred tools；**任一工具设 `defer_loading: true` 时自动注册** |

### 4.2 自定义工具三步走 【官方】

**Step 1 — Python 函数**（要求类型标注；docstring 用来告诉 agent 如何使用）

```python
# my_tools/calculator.py
def simple_calculator(expression: str) -> str:
    """
    Evaluates a simple mathematical expression.
    Supports addition (+), subtraction (-), multiplication (*), and division (/).

    Args:
        expression: The mathematical expression to evaluate (e.g., "10 + 5*2").

    Returns:
        The result of the calculation as a string, or an error message.
    """
    try:
        result = eval(expression, {"__builtins__": None}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"
```

**Step 2 — YAML 描述** `tools/SimpleCalculator.tool.yaml`

```yaml
type: tool
name: SimpleCalculator
description: >-
  A tool to evaluate simple mathematical expressions like "10 + 5*2".
  It supports addition, subtraction, multiplication, and division.

input_schema:
  type: object
  properties:
    expression:
      type: string
      description: The mathematical string to evaluate.
  required:
    - expression
  additionalProperties: false
  $schema: http://json-schema.org/draft-07/schema#
```

**Step 3 — 绑定并挂载**

```python
from nexau import Agent, AgentConfig, Tool
from my_tools.calculator import simple_calculator

calculator_tool = Tool.from_yaml("tools/SimpleCalculator.tool.yaml", binding=simple_calculator)
agent_config = AgentConfig(name="calculator_agent", tools=[calculator_tool])
agent = Agent(config=agent_config)
```

### 4.3 `Tool.from_yaml` 与 schema 细节 【DeepWiki，待源码核对】

```python
tool = Tool.from_yaml(
    yaml_path="tools/WebSearch.tool.yaml",
    binding=None,        # None 则用 YAML 里的 binding 字段
    lazy=True,           # 覆盖 YAML 的 lazy
    extra_kwargs={"api_key": "..."},
)
```

YAML 可用字段：`name`、`description`、`input_schema`（JSON Schema）、`binding`（点号导入路径或 callable）、`lazy`（延迟导入实现）、`as_skill`（注册为可发现能力）、`skill_description`（skill 注册表里的简短描述）、`defer_loading`。

**参数注入**：函数签名除 `input_schema` 里的参数外，可选接收框架对象 `agent_state: AgentState`、`ctx: FrameworkContext`（**必须有类型标注**，否则告警）。返回值须是 dict 或可 JSON 序列化。

**保留字**：`agent_state`、`global_storage`、`ctx` 不能出现在 `input_schema.properties`；`agent_state`、`global_storage` 不能出现在 `extra_kwargs`。

**`extra_kwargs`（预置参数）**【官方】：用于预填 `base_url` / `api_key` / `model` 等固定参数，让调用方省略。调用时同名参数覆盖预置值。额外字段不被 schema 校验拦截且会传给函数，签名不接受就抛 `TypeError`；想提前拒绝未知字段，在 `input_schema` 里加 `additionalProperties: false`。

**校验**：初始化时用 `jsonschema.validators.validator_for()` 校验 schema，执行前用 `validate_params()` 校验参数。

**优先级**：`from_yaml(binding=)` > YAML `binding`；`from_yaml(lazy=)` > YAML `lazy`。

### 4.4 Skills 【官方 + DeepWiki】

- 两类：**文件夹型**（`Skill.from_folder(...)`，带文档，**兼容 Claude Skills 格式**）与 **工具型**（工具 YAML 标 `as_skill: true`）。
- 框架自动注入 `LoadSkill` 工具，让 agent 按需拉取详细文档，**省 context token**。

### 4.5 MCP 集成 【DeepWiki】

- 由 `MCPManager` 管理生命周期，`MCPClient` 维护会话与工具映射。
- 两种 transport：
  - **HTTP**（`HTTPMCPSession`）：支持 streamable HTTP 与标准 JSON-RPC，自动回退。参数 `url`、`headers`、`timeout`（默认 30s）。
  - **stdio**：基于 MCP Python SDK 的 `StdioServerParameters`。参数 `command`（npx / uvx / python）、`args`、`env`。
- MCP 工具 schema 在服务器初始化时自动转成 NexAU 内部 `Tool` 格式。
- `MCPManager.initialize_servers()` 用 `asyncio.gather()` **并行初始化**，降低启动延迟。
- 密钥用 `${env.VAR_NAME}` 注入。
- 常见坑：缺 npx/uvx 运行时、MCP 协议版本不匹配、重型 stdio server 需要调大 timeout。
- 官方细节见 `docs/advanced-guides/mcp.md`（本次未逐字爬取）。

---

## 5. 执行循环（Executor）【DeepWiki，benchmark 前建议对源码核实】

### 流水线

1. **消息合并** — 迭代开始时把排队消息追加进历史
2. **Token 计费** — `TokenCounter` 比对当前 prompt tokens 与 `max_context_tokens`
3. **LLM 调用** — `LLMCaller` 发送消息，动态计算 `max_tokens`
4. **响应解析** — `ResponseParser` 抽取 tool call 与 sub-agent 调用
5. **并行分发** — `ThreadPoolExecutor` 并发执行工具与子 agent
6. **结果回填** — 工具输出写回消息历史
7. **停机判定**

### 停机条件（`AgentStopReason`）

- **max_iterations**（默认 100）
- **上下文超限**：`current_prompt_tokens > max_context_tokens`
- **stop tool 触发**：`AgentStopReason.STOP_TOOL_TRIGGERED`
- **Team 模式**：默认不停，靠 `_message_available` 事件驱动

### 并发与 trace 传播

```python
copy_context().run(...)   # 跨线程保留 contextvars（tracing 等）
```

---

## 6. 中间件与 Hooks 【官方 `advanced-guides/hooks.md`】

> 官方原文：NexAU **已不再**暴露独立的 `before_model_hooks` / `after_model_hooks` / `after_tool_hooks`，运行时**完全由 middleware 驱动**。

### 6.1 接口

middleware 可实现以下任意可选方法：

| Hook | 时机 |
|------|------|
| `before_agent(hook_input)` | 首次 LLM 调用前跑一次，调整初始历史 / 播种 run 级状态 |
| `after_agent(hook_input)` | 执行结束后跑一次（成功、stop-tool、报错都算），定稿返回值 |
| `before_model(hook_input)` | LLM 调用前检查/改写消息列表 |
| `after_model(hook_input)` | LLM 调用后检查/改写解析结果与会话状态 |
| `before_tool(hook_input)` | 工具执行前调整入参（或取消调用） |
| `after_tool(hook_input)` | 工具输出回灌循环前检查/改写 |
| `wrap_model_call(params, call_next)` | 拦截底层 LLM 调用（自定义 provider、重试、tracing） |
| `wrap_tool_call(params, call_next)` | 拦截每次工具执行 |

**执行顺序（确定性）**：

- `before_agent` / `before_model` / `before_tool`：**first → last**
- `after_agent` / `after_model` / `after_tool`：**last → first**
- `wrap_*`：**嵌套**，第一个 middleware 包住其余全部（outermost wins）

### 6.2 最小示例

```python
from nexau.archs.main_sub.execution.hooks import HookResult, Middleware

class AuditMiddleware(Middleware):
    def after_model(self, hook_input):
        print("Model emitted", len(hook_input.parsed_response.tool_calls or []), "tool calls")
        return HookResult.no_changes()

    def after_tool(self, hook_input):
        print("Tool", hook_input.tool_name, "returned", hook_input.tool_output)
        return HookResult.no_changes()

    def wrap_model_call(self, params, call_next):
        print("Calling LLM with", len(params.messages), "messages")
        return call_next(params)
```

### 6.3 middleware 能改什么（返回 `HookResult`）

| 目标 | 做法 |
|------|------|
| 会话 | `messages=[...]` 重写下一轮 prompt |
| 解析结果 | `parsed_response=...` 增删 tool call、切并行标志、置 `force_continue=True` 强制继续迭代 |
| 工具入参 | `before_tool` 返回 `tool_input=...`（补默认值、脱敏） |
| 工具输出 | `tool_output=...` 改运行时原始结果；`llm_tool_output=...` 改回灌给模型的内容 |
| 最终回复 | `after_agent` 返回 `agent_response="..."` |
| Agent 状态 | `hook_input.agent_state` 可变，可存计数器/开关/trace id |

更多示例：

```python
class PrefixMiddleware(Middleware):
    def before_model(self, hook_input):
        updated = hook_input.messages + [{
            "role": "system",
            "content": "Reminder: stay within budget.",
        }]
        return HookResult.with_modifications(messages=updated)

class ToolFilter(Middleware):
    def after_model(self, hook_input):
        parsed = hook_input.parsed_response
        if not parsed:
            return HookResult.no_changes()
        parsed.tool_calls = [c for c in parsed.tool_calls if c.tool_name != "system_command"]
        return HookResult.with_modifications(parsed_response=parsed)

class ClampInputMiddleware(Middleware):
    def before_tool(self, hook_input):
        updated = dict(hook_input.tool_input)
        updated.setdefault("timeout", 30)
        return HookResult.with_modifications(tool_input=updated)
```

### 6.4 Formatter 与 `after_tool` 的顺序（RFC-0017）【官方】

工具执行后保留两条通道：`tool_output`（运行时原始归一化输出）与 `llm_tool_output`（formatter 产出、给模型看的）。**顺序固定为：工具执行 → formatter → `after_tool` middleware**，所以 middleware 通常看到的已是格式化后的 `llm_tool_output`。

- 想保留/重塑原始结构化结果 → 改 `tool_output`
- 想改模型看到的内容 → 改 `llm_tool_output`
- **只改 `tool_output` 的话，模型可能仍收到原来的格式化结果**

```python
class RedactModelFacingToolOutput(Middleware):
    def after_tool(self, hook_input):
        llm_output = hook_input.llm_tool_output
        if isinstance(llm_output, str):
            return HookResult.with_modifications(
                llm_tool_output=llm_output.replace("secret-token", "***"),
            )
        return HookResult.no_changes()
```

### 6.5 注册方式 【官方】

```yaml
middlewares:
  - import: my_project.middleware:AuditMiddleware
    params:
      log_file: "/tmp/audit.log"
```

代码方式直接传实例：`Agent(config=AgentConfig(..., middlewares=[...]))`。

### 6.6 自定义 LLM 调用 【官方】

想换 provider / 加缓存 / 改参数，实现 `wrap_model_call`：

```python
from nexau.archs.main_sub.execution.hooks import Middleware, ModelCallParams
from nexau.archs.main_sub.execution.model_response import ModelResponse

class ProviderSwitchMiddleware(Middleware):
    def __init__(self, fallback_client):
        self.fallback_client = fallback_client

    def wrap_model_call(self, params: ModelCallParams, call_next):
        try:
            return call_next(params)
        except Exception as primary_error:
            print("Primary client failed, falling back:", primary_error)
        return self._call_fallback(params)
```

### 6.7 内置 middleware 【DeepWiki】

| 名称 | 作用 | Hook |
|------|------|------|
| `LoggingMiddleware` | 记录模型调用与工具执行 | `after_model` / `after_tool` / `wrap_model_call` |
| `ContextCompactionMiddleware` | 接近 token 上限时压缩历史 | `after_model` / `wrap_model_call` |
| `RoundAndTokenReminderMiddleware` | 往 prompt 注入"第 4/5 轮"与预算提示；剩 1 轮或剩余 token < 3×期望 max 时告警 | `before_model` |
| `LongToolOutputMiddleware` | 超 `max_output_chars` 的工具输出截断并落盘到沙箱临时文件 | `after_tool` |
| `LLMFailoverMiddleware` | 失败时切备用 provider | `wrap_model_call` |
| `AgentEventsMiddleware` | 向 AgentState 事件总线发 `iteration_start` / `tool_call` / `compaction_started` 等结构化事件，供 CLI 实时刷新 | 全部 |

`ContextCompactionMiddleware` 用 trigger + strategy 工厂模式，压缩策略含：**Tool Result Compaction**（旧工具结果替换为占位符，快且零成本）、**LLM Summary**（额外 LLM 调用总结旧轮次）、**Emergency Overflow**（溢出报错时 50/50 切分总结）。

另有 `sensitive-word-middleware`（见 `docs/advanced-guides/sensitive-word-middleware.md`）。

---

## 7. LLM 层 【DeepWiki】

### `LLMConfig` 字段

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `model` | str | 环境变量 | 模型 id |
| `base_url` | str | 环境变量 | API 端点 |
| `api_key` | str | 环境变量 | 凭据 |
| `temperature` | float | None | 0.0–2.0 |
| `max_tokens` | int | None | 生成上限 |
| `top_p` | float | None | nucleus sampling |
| `timeout` | float | None | 秒 |
| `max_retries` | int | **3** | 自动重试 |
| `api_type` | str | `openai_chat_completion` | provider 选择 |
| `stream` | bool | False | 流式 |
| `cache_control_ttl` | str | None | Anthropic 缓存 TTL |

另支持 `frequency_penalty`、`presence_penalty` 及 provider 专有 kwargs（如 Gemini 的 `thinking_budget`）。

### provider 选择（`api_type`）

| `api_type` | Provider | 端点 | 工具格式 |
|-----------|----------|------|---------|
| `openai_chat_completion` | OpenAI、vLLM | `/v1/chat/completions` | function calls |
| `openai_responses` | OpenAI | `/v1/responses` | function call items |
| `anthropic_chat_completion` | Anthropic | `/v1/messages` | tool use blocks |
| `gemini_rest` | Google Gemini | `v1beta/models/...:generateContent` | functionCall parts |

### 环境变量优先级

- `model`：`MODEL` → `OPENAI_MODEL` → `LLM_MODEL`
- `base_url`：`OPENAI_BASE_URL` → `BASE_URL` → `LLM_BASE_URL`
- `api_key`：`LLM_API_KEY` → `OPENAI_API_KEY` → `API_KEY` → `ANTHROPIC_API_KEY`

### `tool_call_mode`

两种语义模式（`openai` / `anthropic` 为遗留名，内部映射到 structured）：

- **structured** — 走 provider 原生 function calling。工具定义存"中立结构化格式"，请求时才 late adaptation：OpenAI 映射到 `properties`/`required`，Anthropic 映射到 `input_schema`，Gemini 映射到 REST function declarations。**优先选它**。
- **xml** — provider 无关的文本分隔符回退方案。工具作为指令嵌进 system prompt，模型用 XML 标签发起调用；截断时框架会尝试修复未闭合标签。适用于无原生 function calling 的模型或调试。代价：完全依赖模型的指令遵循、API 层无校验、更容易幻觉/畸形调用。停机序列涉及 `</tool_use>`、`</use_parallel_tool_calls>`、`</use_batch_agent>`。

两种模式都归一到统一的 `ModelResponse`。

---

## 8. Sub-Agent 与 Agent Team

### 8.1 Sub-Agent 【DeepWiki】

- `SubAgentManager` 持有 `sub_agents: dict[str, AgentConfig]`。
- LLM 通过内置的 **`RecallSubAgent`** 工具发起委派；`call_sub_agent` 参数：`sub_agent_name`、`sub_agent_id`（可选，恢复已有会话）、`parallel_execution_id`（并行批次分组）。返回字符串结果含子 agent 输出与可供 recall 的元信息。
- **可任意深度嵌套**：`CLIEnabledSubAgentManager` 保证嵌套子 agent 也用 CLI-enabled manager。官方文档未给出显式深度上限。
- **状态共享**：未显式传 context 时会尝试拷贝当前 `AgentContext`；`SubAgentManager` 接收 `global_storage` 与 `session_manager`，实现跨层级共享。
- `running_sub_agents: dict[str, Agent]` 跟踪活动实例；关停时逐个 cleanup 整棵树。

### 8.2 Agent Team（⚠️ 官方标注 **Experimental**，API 可能变）【官方 `advanced-guides/agent-team.md`】

Leader agent 并行协调多个 teammate：

- **Leader** — 协调者，spawn teammate、建/派任务、监控进度，完成时调 `finish_team`。团队工具自动注入，只需定义领域工具。
- **Teammates** — 从预配置角色模板（`candidates`）spawn，各自跑 forever-loop 等消息/任务。
- **Task Board** — DB 落地的共享任务列表，状态 `pending → in_progress → completed`，带优先级与依赖。
- **Message Bus** — 持久化的点对点与广播消息。
- **TeamSSEMultiplexer** — 把所有 agent 流聚合到单条 SSE 连接，事件按 `agent_id` 打标。

```yaml
# leader_agent.yaml
type: agent
name: team_leader
system_prompt: ./systemprompt_leader.md
max_iterations: 200
llm_config:
  model: ${env.LLM_MODEL}
  api_key: ${env.LLM_API_KEY}
  stream: true
tools:
  - name: read_file
    yaml_path: ./tools/read_file.tool.yaml
    binding: nexau.archs.tool.builtin.file_tools:read_file
stop_tools: [ask_user]
```

```python
from nexau.archs.main_sub.config import AgentConfig
from nexau.archs.session.orm import InMemoryDatabaseEngine
from nexau.archs.transports.http import HTTPConfig, SSETransportServer

leader_config = AgentConfig.from_yaml("leader_agent.yaml")
builder_config = AgentConfig.from_yaml("builder_agent.yaml")

engine = InMemoryDatabaseEngine()
server = SSETransportServer(
    engine=engine,
    config=HTTPConfig(host="0.0.0.0", port=8000),
    default_agent_config=leader_config,
)
registry = server.team_registry
if registry is not None:
    registry.register_config(
        "default",
        leader_config=leader_config,
        candidates={"rfc_writer": rfc_writer_config, "builder": builder_config},
    )
server.run()
```

生产环境把 `InMemoryDatabaseEngine` 换成 `SQLDatabaseEngine.from_url("sqlite+aiosqlite:///team.db")`。

调用：

```bash
curl -X POST http://localhost:8000/team/stream \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u1", "session_id": "s1", "message": "Build a TODO app"}'
```

`variables` 字段可传运行时上下文变量，会应用到 leader 和所有 teammate。

---

## 9. 沙箱 【DeepWiki】

- `LocalSandbox`（开发用）
- `E2BSandbox`（云端安全隔离）
- 大日志智能截断：保留头尾，同时给出完整文件路径

官方细节见 `docs/advanced-guides/sandbox.md`。

---

## 10. 可观测性 / Tracing 【DeepWiki】

- **`LangfuseTracer`**：root agent → Trace，嵌套 agent/tool → Span，LLM 调用 → Generation。
- **`InMemoryTracer`**：测试与本地调试，字典存 span 并跟踪父子关系。
- **`CompositeTracer`**：同时投递多个后端。
- Span 类型：`SpanType.AGENT` / `SUB_AGENT` / `TOOL` / `LLM`；每个 span 记录 input、output、error、timing，流式场景额外记 `time_to_first_token_ms`（**TTFT，benchmark 可直接用**）。
- 用 `contextvars` 跨 async/线程边界维持父子关系，子 agent 的 span 层级自动成形，无需手动传 parent-id。
- 支持凭据 lazy init（多租户），对非整型 usage 字段做 sanitization。

YAML 配置示例（来自 deep_research 示例）：

```yaml
tracers:
  - type: LangfuseTracer
    public_key: ${env.LANGFUSE_PUBLIC_KEY}
    secret_key: ${env.LANGFUSE_SECRET_KEY}
```

---

## 11. 示例工程

| 目录 | 内容 |
|------|------|
| `examples/code_agent` | 文件操作 + bash + 运行代码的编码 agent，含 skills |
| `examples/deep_research` | 多 agent 研究工作流：web_search / web_read / todo_write / file_write + sub-agent + Langfuse |
| `examples/simple_research` | 精简版研究 agent |
| `examples/mcp` | MCP 集成 |
| `examples/nexau_building_team` | Agent Team |
| `examples/cc_agent` | Claude Code 风格 agent |
| `examples/plugin_adapter` | 插件适配 |
| `examples/sensitive_word` | 敏感词 middleware |

deep_research 目录结构：

```
deep_research/
├── deep_research_agent.yaml
├── subagent.yaml
├── quickstart.py
├── quickstart_yaml.py
└── tools/
    └── TodoWrite.tool.yaml
```

---

## 12. 爬取时未覆盖、benchmark 前建议补爬的页面

官方 `docs/advanced-guides/` 下这些文件本次**未逐字爬取**，但对 benchmark 直接相关：

- `async.md` — 异步执行模型（影响吞吐测量）
- `streaming-events.md` — 流式事件协议（影响 TTFT / 首包延迟测量）
- `session-management.md` — 会话持久化（对标 LangGraph checkpointer）
- `global-storage.md` — 跨递归树的线程安全状态
- `context_compaction.md` — 上下文压缩策略细节
- `tool-formatters.md` — 工具输出格式化
- `transports.md` — HTTP/SSE 传输层
- `tracer.md` — tracing 官方版（本档 §10 目前是 DeepWiki 来源）
- `sandbox.md` / `skills.md` / `mcp.md`
- `rfcs/` — RFC-0006（provider-agnostic 工具定义）、RFC-0017（formatter 双通道）

补爬命令模板：

```bash
curl -s "https://raw.githubusercontent.com/nex-agi/NexAU/main/docs/advanced-guides/async.md"
```
