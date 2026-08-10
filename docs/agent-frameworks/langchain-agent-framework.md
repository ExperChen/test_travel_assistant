# LangChain v1 Agent 框架文档（爬取归档）

> 来源（爬取日期：2026-08-07）：
> - <https://docs.langchain.com/oss/python/langchain/agents>
> - <https://docs.langchain.com/oss/python/langchain/tools>
> - <https://docs.langchain.com/oss/python/langchain/models>
> - <https://docs.langchain.com/oss/python/langchain/middleware/overview>
> - <https://docs.langchain.com/oss/python/langchain/middleware/built-in>
> - <https://docs.langchain.com/oss/python/langchain/middleware/custom>
> - <https://docs.langchain.com/oss/python/langchain/streaming>
> - <https://docs.langchain.com/oss/python/langgraph/overview>
> - <https://docs.langchain.com/oss/python/langgraph/persistence>
> - <https://docs.langchain.com/oss/python/langchain/install>
> - PyPI：<https://pypi.org/pypi/langchain/json>
>
> 官方文档示例里的模型名（`gpt-5.5`、`claude-sonnet-4-6`、`claude-opus-4-8` 等）按原文保留，benchmark 时按实际可用模型替换。

---

## 0. 版本与元信息（2026-08-07 抓取）

| 项目 | 值 |
|------|-----|
| 仓库 | `langchain-ai/langchain` |
| License | MIT |
| Star / Fork | 143,604 / 23,928 |
| Open issues | 462 |
| 创建 / 最近 push | 2022-10-17 / 2026-08-07 |
| PyPI 最新版 | **langchain 1.3.14** |
| Python 要求 | `>=3.10.0,<4.0.0` |
| 核心依赖 | `langchain-core >=1.4.9,<2.0.0`、`langgraph >=1.2.5,<1.3.0`、`pydantic >=2.7.4,<3.0.0` |

### 本项目 `.venv` 当前已装版本（`d:\TraeProjects\test-travel-assistant\.venv`）

```
langchain_core     1.5.3
langgraph          1.2.10
langgraph_checkpoint 4.1.1
langgraph_prebuilt   1.1.0
langgraph_sdk        0.4.2
langchain_openai     1.4.1
langchain_protocol   0.0.18
langsmith            0.10.15
openai               2.53.0
```

⚠️ **`langchain` 主包本身没装**（只有 `langchain-core`）。本项目目前直接用 LangGraph 手搓图（`app/graph/`），没走 `create_agent`。要做 benchmark 需要先 `pip install -U langchain`。

### 安装

```bash
pip install -U langchain
pip install -U langchain-openai
pip install -U langchain-anthropic
# uv
uv add langchain
```

Provider 集成散在独立包里（"hundreds of LLMs and thousands of other integrations"）。完整清单见 <https://docs.langchain.com/llms.txt>。

---

## 1. Agent 核心概念

> 官方定义："an agent is a model calling tools in a loop until a given task is complete."

框架分两部分：**model** 本身，以及 **harness**——循环外围的一切（prompt、tools、middleware）。官方原话："the job of a harness: get the model the right context at the right time for the given task."

### 最小 agent

```python
from langchain.agents import create_agent

agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools)
```

### 主要参数

**model** — `provider:model` 字符串（如 `"openai:gpt-5.5"`）或已初始化的 model 实例。

**tools** — Python callable、LangChain tool、或 tool dict：

```python
from langchain.tools import tool

@tool
def search(query: str) -> str:
    """Search for information."""
    return f"Results for: {query}"

agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=[search])
```

**system_prompt** — 字符串或 `SystemMessage`：

```python
agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=tools,
    system_prompt="You are a helpful assistant. Be concise and accurate."
)
```

**response_format** — 结构化输出：

```python
from pydantic import BaseModel

class Answer(BaseModel):
    summary: str
    confidence: float

agent = create_agent(model="anthropic:claude-sonnet-4-6", tools=tools, response_format=Answer)
result = agent.invoke({"messages": [{"role": "user", "content": "Summarize AI trends"}]})
result["structured_response"]
```

**checkpointer** — 持久化会话历史。官方提示："persisting conversation history with `thread_id` requires the agent to be configured with a checkpointer."

```python
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.utils.uuid import uuid7

agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[],
    checkpointer=InMemorySaver(),
)
config = {"configurable": {"thread_id": str(uuid7())}}
result = agent.invoke(
    {"messages": [{"role": "user", "content": "What's the weather?"}]},
    config=config,
)
```

**context_schema / context** — 每次运行的不可变配置（user id、API key、feature flag）：

```python
from dataclasses import dataclass

@dataclass
class Context:
    user_id: str

agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[],
    context_schema=Context,
    checkpointer=InMemorySaver(),
)
result = agent.invoke(
    {"messages": [{"role": "user", "content": "What's the weather?"}]},
    config={"configurable": {"thread_id": str(uuid7())}},
    context=Context(user_id="user-123"),
)
```

---

## 2. 工具系统

### 2.1 `@tool` 装饰器

**类型标注是必需的**（决定 input schema），docstring 变成 tool description。**命名用 snake_case**，避免某些 provider 拒绝空格/特殊字符。

```python
from langchain.tools import tool

@tool
def search_database(query: str, limit: int = 10) -> str:
    """Search the customer database for records matching the query.

    Args:
        query: Search terms to look for
        limit: Maximum number of results to return
    """
    return f"Found {limit} results for '{query}'"
```

自定义名称与描述：

```python
@tool("web_search", description="Performs web searches. Use for current information.")
def search(query: str) -> str:
    """Search the web for information."""
    return f"Results for: {query}"
```

### 2.2 Pydantic `args_schema`

```python
from pydantic import BaseModel, Field
from typing import Literal

class WeatherInput(BaseModel):
    """Input for weather queries."""
    location: str = Field(description="City name or coordinates")
    units: Literal["celsius", "fahrenheit"] = Field(
        default="celsius", description="Temperature unit preference"
    )
    include_forecast: bool = Field(default=False, description="Include 5-day forecast")

@tool(args_schema=WeatherInput)
def get_weather(location: str, units: str = "celsius", include_forecast: bool = False) -> str:
    """Get current weather and optional forecast."""
    ...
```

### 2.3 保留参数名

| 名称 | 用途 |
|------|------|
| `config` | 内部传 `RunnableConfig` |
| `runtime` | 传 `ToolRuntime` |

### 2.4 `ToolRuntime`（v1 统一注入接口）

不出现在给模型看的 schema 里。组成部分：

- **State** — 短期会话记忆（messages + 自定义字段）
- **Context** — 每次运行的不可变配置
- **Store** — 跨会话长期记忆
- **Stream Writer** — 实时进度输出
- **Execution Info** — thread_id / run_id / 重试信息
- **Server Info** — assistant_id / graph_id / 认证用户（仅 LangGraph Server）
- **Config** — `RunnableConfig`
- **Tool Call ID**

```python
from langchain.tools import tool, ToolRuntime

@tool
def get_last_user_message(runtime: ToolRuntime) -> str:
    """Get the most recent message from the user."""
    messages = runtime.state["messages"]
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.content
    return "No user messages found"
```

**迁移说明**：旧的 `InjectedState` / `InjectedStore` / `get_runtime()` / `InjectedToolCallId` 统一被 `ToolRuntime` 取代。

`runtime.execution_info` 需要 `deepagents>=0.5.0` 或 `langgraph>=1.1.5`。

### 2.5 用 `Command` 改状态

```python
from langchain.agents import AgentState
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command

class CustomState(AgentState):
    user_name: str

@tool
def set_user_name(new_name: str, runtime: ToolRuntime[None, CustomState]) -> Command:
    """Set the user's name in the conversation state."""
    return Command(
        update={
            "user_name": new_name,
            "messages": [
                ToolMessage(content=f"User name set to {new_name}.",
                            tool_call_id=runtime.tool_call_id)
            ],
        }
    )
```

并行工具调用可能同时改同一字段时，需要用 reducer。

### 2.6 长期记忆 Store

```python
from langgraph.store.memory import InMemoryStore

@tool
def get_user_info(user_id: str, runtime: ToolRuntime) -> str:
    """Look up user info."""
    user_info = runtime.store.get(("users",), user_id)
    return str(user_info.value) if user_info else "Unknown user"

@tool
def save_user_info(user_id: str, user_info: dict, runtime: ToolRuntime) -> str:
    """Save user info."""
    runtime.store.put(("users",), user_id, user_info)
    return "Successfully saved."
```

生产环境用 `PostgresStore` / `MongoDBStore` / `RedisStore` 替代 `InMemoryStore`。

### 2.7 返回值形态

| 形态 | 说明 |
|------|------|
| `str` | 纯文本，模型自行判断下一步 |
| `dict` | 结构化数据，模型可对具体字段推理 |
| `list[dict]` | 多模态 content blocks（`{"type": "image", "url": ...}`），需模型支持 |
| `Command` | 更新 graph state |
| `@tool(return_direct=True)` | **短路 agent 循环**，直接把工具输出当最终答案返回 |

`return_direct` 限制：一轮里调了多个工具时，**只有全部工具都是 `return_direct=True` 才生效**。

### 2.8 流式进度

```python
@tool
def get_weather(city: str, runtime: ToolRuntime) -> str:
    """Get weather for a given city."""
    writer = runtime.stream_writer
    writer(f"Looking up data for city: {city}")
    writer(f"Acquired data for city: {city}")
    return f"It's always sunny in {city}!"
```

必须在 LangGraph 执行上下文里才能用 `stream_writer`。

### 2.9 工具错误处理（靠 middleware）

```python
from collections.abc import Callable
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest

@wrap_tool_call
def handle_tool_errors(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage],
) -> ToolMessage:
    """Convert tool exceptions into ToolMessages the model can handle."""
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"Tool error: Please check your input. ({e})",
            tool_call_id=request.tool_call["id"],
        )

agent = create_agent(model="gpt-5.5", tools=[], middleware=[handle_tool_errors])
```

### 2.10 动态工具选择

按状态过滤已注册工具：

```python
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse

@wrap_model_call
def state_based_tools(request: ModelRequest,
                      handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """Filter tools based on conversation state."""
    if not request.state.get("authenticated", False):
        tools = [t for t in request.tools if t.name.startswith("public_")]
        request = request.override(tools=tools)
    return handler(request)
```

运行时注册新工具（**必须同时实现 `wrap_model_call` 和 `wrap_tool_call`**，否则 agent 不知道怎么执行动态加进来的工具）：

```python
class DynamicToolMiddleware(AgentMiddleware):
    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(request.override(tools=[*request.tools, calculate_tip]))

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        if request.tool_call["name"] == "calculate_tip":
            return handler(request.override(tool=calculate_tip))
        return handler(request)
```

### 2.11 Headless tools（服务端声明 schema、客户端执行）

```python
geolocation_tool = tool(
    name="get_geolocation",
    description="Get the user's current geolocation",
    args_schema={
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["coordinates", "address"],
                       "description": "Return format for location data"}
        },
        "required": ["format"]
    }
)
```

模型调用时 graph 以 interrupt 暂停，客户端本地执行后 resume。适用场景：浏览器 API（地理位置、IndexedDB、剪贴板）、隐私敏感操作、低延迟本地操作。

---

## 3. Middleware

### 3.1 `AgentMiddleware` 基类

```python
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime
from typing import Any

class AgentMiddleware:
    state_schema = None   # Optional: extend agent state
    tools = None          # Optional: register additional tools
    transformers = None   # Optional: tuple of stream transformer factories
```

### 3.2 Node 型 hooks（顺序执行）

```python
def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None: ...
def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None: ...
def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None: ...
def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None: ...
```

异步变体加 `a` 前缀：`abefore_agent()` / `abefore_model()` / …

### 3.3 Wrap 型 hooks（控制流）

```python
def wrap_model_call(
    self,
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse: ...

def wrap_tool_call(
    self,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command: ...
```

### 3.4 装饰器形式（单 hook，免继承）

```python
from langchain.agents.middleware import (
    before_agent, before_model, after_model, after_agent,
    wrap_model_call, wrap_tool_call
)

@before_agent
def my_hook(state: AgentState, runtime: Runtime) -> dict[str, Any] | None: ...

@wrap_model_call
def my_wrap(request, handler) -> ModelResponse: ...
```

### 3.5 执行顺序（洋葱模型）

`middleware=[m1, m2, m3]` 时：

- `before_*`：**m1 → m2 → m3**
- `wrap_*` 嵌套：**m1 wraps m2 wraps m3 wraps model**
- `after_*`：**m3 → m2 → m1**

> ⚠️ 与 NexAU 的 hook 命名和顺序语义**几乎完全一致**（见对比文档 §3）。

### 3.6 提前退出 / 跳转

```python
return {
    "messages": [AIMessage("...")],
    "jump_to": "end"     # 也可以是 "model" / "tools"
}
```

用 `@hook_config(can_jump_to=["end"])` 声明可跳转目标。

### 3.7 内置 middleware 清单

#### 上下文管理

| 类 | import | 关键参数 |
|----|--------|---------|
| `SummarizationMiddleware` | `langchain.agents.middleware` | `model`、`trigger`、`keep`、`token_counter`、`summary_prompt`、`trim_tokens_to_summarize` |
| `ContextEditingMiddleware` | 同上 | `edits`（如 `ClearToolUsesEdit(trigger=100000, keep=3)`）、`token_count_method` |

```python
from langchain.agents.middleware import SummarizationMiddleware

agent = create_agent(
    model="gpt-5.5",
    tools=[your_weather_tool],
    middleware=[
        SummarizationMiddleware(
            model="gpt-5.4-mini",
            trigger=("tokens", 4000),
            keep=("messages", 20),
        ),
    ],
)
```

#### 执行控制

| 类 | 关键参数 |
|----|---------|
| `ModelCallLimitMiddleware` | `thread_limit`、`run_limit`、`exit_behavior` |
| `ToolCallLimitMiddleware` | `tool_name`、`thread_limit`、`run_limit`、`exit_behavior` |

```python
agent = create_agent(
    model="gpt-5.5",
    tools=[search_tool, database_tool],
    middleware=[
        ToolCallLimitMiddleware(thread_limit=20, run_limit=10),
        ToolCallLimitMiddleware(tool_name="search", thread_limit=5, run_limit=3),
    ],
)
```

#### 容错与重试

| 类 | 关键参数 |
|----|---------|
| `ModelRetryMiddleware` | `max_retries`、`retry_on`、`on_failure`、`backoff_factor`、`initial_delay`、`max_delay`、`jitter` |
| `ToolRetryMiddleware` | 同上 + `tools` |
| `ModelFallbackMiddleware` | `first_model`、`*additional_models` |
| `ToolErrorMiddleware` | `on_error`、`aon_error`、`tools` |

```python
from langchain.agents.middleware import ModelFallbackMiddleware

agent = create_agent(
    model="gpt-5.5", tools=[],
    middleware=[ModelFallbackMiddleware("gpt-5.4-mini", "claude-3-5-sonnet-20241022")],
)
```

#### 安全与合规

| 类 | 关键参数 | 备注 |
|----|---------|------|
| `HumanInTheLoopMiddleware` | `interrupt_on` | **需要 checkpointer** |
| `PIIMiddleware` | `pii_type`、`strategy`、`detector`、`apply_to_input`、`apply_to_output`、`apply_to_tool_results` | 支持 email / credit_card / IP 等 |

```python
agent = create_agent(
    model="gpt-5.5",
    tools=[your_read_email_tool, your_send_email_tool],
    checkpointer=InMemorySaver(),
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "your_send_email_tool": {"allowed_decisions": ["approve", "edit", "reject"]},
                "your_read_email_tool": False,
            }
        ),
    ],
)
```

#### 工具管理

| 类 | 关键参数 | 备注 |
|----|---------|------|
| `LLMToolSelectorMiddleware` | `model`、`system_prompt`、`max_tools`、`always_include` | 用小模型先筛工具 |
| `LLMToolEmulator` | `tools`、`model` | **用 LLM 模拟工具执行，测试用** |
| `ProviderToolSearchMiddleware` | `searchable_tools` | 需 Anthropic Claude 4+/Opus 4+ 或 OpenAI gpt-5.5+ |

> `LLMToolEmulator` 对 benchmark 很有用：可以在不打真实 API 的前提下跑循环、测编排开销。

#### 规划与系统访问

| 类 | 关键参数 |
|----|---------|
| `TodoListMiddleware` | `system_prompt`、`tool_description` |
| `ShellToolMiddleware` | `workspace_root`、`startup_commands`、`shutdown_commands`、`execution_policy`、`redaction_rules`、`tool_description`、`shell_command`、`env` |
| `FilesystemFileSearchMiddleware` | `root_path`、`use_ripgrep`、`max_file_size_mb` |

#### Deep Agents 侧（独立包 `deepagents`）

| 类 | import | 关键参数 |
|----|--------|---------|
| `FilesystemMiddleware` | `deepagents.middleware.filesystem` | `backend`、`system_prompt`、`custom_tool_descriptions`、`tools` |
| `SubAgentMiddleware` | `deepagents.middleware.subagents` | `default_model`、`default_tools`、`subagents` |
| `RubricMiddleware` | `deepagents.middleware.rubric` | `model`、`max_iterations`；需 `deepagents>=0.6.5` + checkpointer |
| `MemoryMiddleware` / `SkillsMiddleware` | `deepagents.middleware` | `backend`、`sources` |

```python
from deepagents.middleware.subagents import SubAgentMiddleware

agent = create_agent(
    model="claude-sonnet-4-6",
    middleware=[
        SubAgentMiddleware(
            default_model="claude-sonnet-4-6",
            default_tools=[],
            subagents=[{
                "name": "weather",
                "description": "Get weather information",
                "system_prompt": "Use tools to fetch weather",
                "tools": [get_weather],
            }],
        )
    ],
)
```

上下文工程组合（summarization + memory + skills）：

```python
from deepagents.backends import StateBackend
from deepagents.middleware import SummarizationMiddleware, MemoryMiddleware, SkillsMiddleware

backend = StateBackend()
agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search],
    middleware=[
        SummarizationMiddleware(model="anthropic:claude-sonnet-4-6", backend=backend),
        MemoryMiddleware(backend=backend, sources=["./AGENTS.md"]),
        SkillsMiddleware(backend=backend, sources=["./skills/"]),
    ],
)
```

`FilesystemMiddleware` 的 backend 可组合路由：

```python
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

FilesystemMiddleware(
    backend=CompositeBackend(default=StateBackend(), routes={"/memories/": StoreBackend()}),
)
```

---

## 4. Models 层

```python
from langchain.chat_models import init_chat_model

model = init_chat_model("openai:gpt-5.5")
# 或 init_chat_model("anthropic:claude-sonnet-4-6")

model = init_chat_model("claude-sonnet-4-6", temperature=0.7, max_tokens=1000, max_retries=10)
```

参数：`temperature`、`max_tokens`、`timeout`、`max_retries`（**默认 6**）。

Provider：OpenAI、Anthropic、Azure、Google Gemini、AWS Bedrock、HuggingFace、OpenRouter、Fireworks、Baseten、Ollama 等。官方说明："New model names work immediately — no LangChain update required"（provider 包直接透传模型名）。

调用：

```python
response = model.invoke("Why do parrots talk?")

for chunk in model.stream("Your question"):
    print(chunk.text, end="")

responses = model.batch([prompt1, prompt2, prompt3])
```

工具绑定与结构化输出：

```python
model_with_tools = model.bind_tools([get_weather])
response = model_with_tools.invoke("Weather in Boston?")
response.tool_calls

model_structured = model.with_structured_output(Movie)   # Pydantic / TypedDict / JSON Schema
```

**Prompt caching**：多数 provider 自动，部分需显式配置（Anthropic `cache_control`、Bedrock `cachePoint`）；通常有最小输入 token 门槛才生效。

---

## 5. 流式输出

| stream_mode | 含义 |
|-------------|------|
| `values` | 每步后的完整 state |
| `updates` | 每步后的 state 增量 |
| `messages` | `(token, metadata)` 元组，来自任何调 LLM 的节点 |
| `custom` | 节点内用 stream writer 发的自定义数据 |
| `debug` | 调试（LangGraph 高级选项） |

可传列表组合：`stream_mode=["updates", "custom"]`。

```python
agent = create_agent(model="openai:gpt-5.5", tools=[tool_function])

for chunk in agent.stream(
    {"messages": [{"role": "user", "content": "query"}]},
    stream_mode="updates",
    version="v2",
):
    print(chunk["type"])   # 模式名
    print(chunk["data"])   # 载荷
```

- `version="v2"` 让所有 chunk 统一成 `{"type", "ns", "data"}` 结构
- `subgraphs=True` 可流式输出嵌套 agent
- 单个 model 设 `streaming=False` 可选择性关闭 token 流

`stream_events()` 是更高层 API，支持 v3 格式与 `.interleave()`：

```python
stream = agent.stream_events(
    {"messages": [{"role": "user", "content": "Search for AI news and summarize"}]},
    version="v3",
)
for snapshot in stream.values:
    latest_message = snapshot["messages"][-1]
    if isinstance(latest_message, AIMessage):
        print(f"Agent: {latest_message.content}")
```

---

## 6. LangGraph（底层编排）

官方定位："a low-level orchestration framework and runtime for building, managing, and deploying long-running, stateful agents."

**与 `create_agent` 的关系**：`create_agent` 是高层封装，给"常见 LLM + tool-calling 循环"提供预制架构；LangGraph 是不抽象 prompt 与架构的底层编排。

### StateGraph

```python
from langgraph.graph import StateGraph, MessagesState, START, END

graph = StateGraph(MessagesState)
graph.add_node(mock_llm)
graph.add_edge(START, "mock_llm")
graph.add_edge("mock_llm", END)
graph = graph.compile()
```

### 核心能力

- **混合确定性与 agentic 步骤**："mix deterministic, hand-coded steps with LLM-driven agentic steps in the same graph"
- **持久化 / 耐久执行**：agent "persist through failures and can run for extended periods, resuming from where they left off"
- **Human-in-the-loop**：任意点检视并修改 agent state
- **Streaming**

### 持久化：Checkpointer vs Store

| | Checkpointer | Store |
|---|---|---|
| 粒度 | 单 thread 内的 graph state 快照 | 跨 thread 的应用数据 |
| 用途 | 短期记忆、HITL、time travel、故障恢复 | 长期记忆：用户偏好、事实、共享知识 |

后端：`InMemorySaver`（进程重启即丢）、`SqliteSaver`（本地文件，开发用）、`PostgresSaver` / `AsyncPostgresSaver`（生产）、Redis。

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

checkpointer = InMemorySaver()
store = InMemoryStore()
graph = builder.compile(checkpointer=checkpointer, store=store)

result = graph.invoke(
    {"messages": [{"role": "user", "content": "Hi, my name is Bob."}]},
    {"configurable": {"thread_id": "thread-1"}},
)
```

⚠️ 用 `PostgresSaver` 时 `thread_id` 长度须 **< 255 字符**。

### `ToolNode`

LangGraph Graph API 里由 `ToolNode` 负责工具执行，并提供对当前 graph state 与 run 级 context 的访问。详见 `/oss/python/langgraph/workflows-agents#toolnode`。

---

## 7. 爬取时未覆盖、benchmark 前建议补爬的页面

- `/oss/python/langchain/human-in-the-loop` — HITL 完整流程
- `/oss/python/langchain/guardrails` — 护栏
- `/oss/python/langchain/context-engineering` — 上下文工程
- `/oss/python/deepagents/*` — Deep Agents（subagent / filesystem / customization）
- `/oss/python/langgraph/workflows-agents` — Graph API 与 `ToolNode`
- `/oss/python/langgraph/durable-execution`、`/time-travel`、`/streaming`
- `/oss/python/releases/langchain-v1`、`/oss/python/migrate/langchain-v1` — v1 变更与迁移（**对确定 benchmark 基线版本很关键**）
- `/oss/python/integrations/middleware` — 第三方 middleware
- API reference：<https://reference.langchain.com/python/langchain/middleware/>
- 文档索引：<https://docs.langchain.com/llms.txt>
