"""自主规划 Agent：把全部工具交给 LLM，由它自己决定调什么、调几次。

**这是项目里唯一的规划路径。** 原来还有一条固定 DAG（节点顺序、每步调哪个
工具、结果怎么用全写死在代码里），已经删除——保留两条并行编排意味着每个
需求都要实现两遍，而它们的保证本来就不一样，测试也没法互相替代。

## 为什么用原生 tool calling 而不是文本 ReAct

`react_intake` 那 3 个工具用 `Thought/Action/Action Input` 文本格式够用。
但这里有十几个工具、参数结构复杂（嵌套数组、枚举、可选字段），
让模型手写 JSON 出错率高得多。原生 tool calling 由 provider 保证结构合法。

## 三条护栏

1. **步数上限**（`AGENT_MAX_STEPS`）。模型可能陷入"查了又查"的循环。
2. **相同参数不查第二次**。始终生效——重复查询是纯浪费，零信息增益。
3. **SerpAPI 次数上限**（`AGENT_SERPAPI_BUDGET`，**默认 0 = 不限制**）。
   免费额度只有 250 次/月，需要保险时设成正整数。

## 酒店必须可核实

`finish_plan` 强制要求酒店的 **name / address / lng / lat** 四项，
且必须逐字来自工具返回——用户要拿它去订房和导航，编一个名字比不给还糟。
`SERPAPI_MOCK=true` 时 `hotels_search` 返回的是合成店名（模型分辨不出来），
所以 `_hotel_hint()` 会明确告诉它改用高德的住宿 POI。

## 交给模型之后失去了什么（如实记录）

原来的固定管线里这些是**代码保证**的，现在全部变成"模型自觉"：

- 景点营业时间与时间窗的约束求解
- 返程航班倒推的末日截止时刻
- 分天聚类与天内路线排序

所以排出的行程**可能违反营业时间或赶不上返程航班**，没有任何代码会拦下来。
换来的是灵活性：能处理"先去看熊猫再决定住哪"这类固定 DAG 表达不了的需求。
只有坐标系转换仍是硬保证——它由 `planner_tools` 的封装层负责，
因为 GCJ-02 / WGS-84 搞错是**静默**的错误，模型自觉不了。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.config import settings
from app.core.exceptions import AppError, QuotaExceeded
from app.core.logging import get_logger
from app.models.intake import ReactStep
from app.tools.registry import all_specs

log = get_logger(__name__)

__all__ = ["PlannerAgent", "AgentRun", "SYSTEM_PROMPT", "llm_tool_specs"]


def llm_tool_specs() -> list:
    """所有对模型开放的工具。

    导入 `planner_tools` 是为了触发它的 `@tool` 注册——那 3 个封装把原本
    收 `GeoPoint`、模型用不了的路径工具补了进来。
    """
    from app.agents import planner_tools  # noqa: F401 —— 仅为触发注册

    return [spec for spec in all_specs() if spec.llm_facing]


SYSTEM_PROMPT = """你是旅行规划助手。用给你的工具查真实数据，排出一份逐日行程。

## 工作方式
1. 先用 district_lookup 拿到目的地的 citycode 和中心坐标——**后面公交查询要用**。
2. 用 poi_keyword / poi_around 找景点，用 poi_detail 补入口坐标
   （大景区中心点常在山里湖里，算路线要用入口）。
3. 找住处：{hotel_hint}
   **选定后必须能说出它的全名、门牌号地址和经纬度**——这三样都要原样复制
   工具返回的字段，不能自己拼凑。地址没有就用 address_of 反查坐标补上。
4. 用 distance_many 给候选按通勤远近排序（一次算多个，省额度）。
5. 用 route_between 算每一段位移的真实时长——**不要自己估**。
6. 想清楚了就调 finish_plan 交付。

## 铁律
- **所有距离和时长必须来自工具**，绝不自己编。编出来的数字看着合理但对不上，
  是最难发现的错误。
- 坐标一律用工具返回的原值，不要自己改写或换算。
- 日期格式 YYYY-MM-DD。
- **额度有限**：机票和酒店查询很贵，同样的参数不要查第二次。
- 排行程时留出通勤时间，并注意景点营业时间（poi_detail 会返回）。
- 末日行程必须在返程航班起飞前结束，留出去机场和值机的时间。

## 当前任务
{task}

今天是 {today}。"""


class AgentRun:
    """一次自主规划的全过程。**轨迹本身就是产物**——出了问题要能看出它在哪步想歪。"""

    def __init__(self) -> None:
        self.steps: list[ReactStep] = []
        self.answer: str = ""
        self.finished = False
        self.stop_reason = ""

    def add(self, action: str, args: dict, observation: str, thought: str = "") -> None:
        self.steps.append(
            ReactStep(
                thought=thought, action=action, action_input=args,
                observation=observation[:2000],  # 单条观测截断，防止上下文爆掉
            )
        )

    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.action not in ("", "finish_plan"))


class PlannerAgent:
    """把全部 llm_facing 工具交给模型，让它自己规划调用序列。"""

    def __init__(
        self,
        *,
        llm=None,
        max_steps: int | None = None,
        serpapi_budget: int | None = None,
    ):
        self._llm = llm
        self.max_steps = max_steps or settings.agent_max_steps
        self.serpapi_budget = (
            serpapi_budget if serpapi_budget is not None else settings.agent_serpapi_budget
        )
        self._serpapi_attempts = 0

    # ------------------------------------------------------------------
    async def run(self, task: str, *, today: date | None = None) -> AgentRun:
        today = today or date.today()
        run = AgentRun()
        self._serpapi_attempts = 0  # 每次规划独立计数
        specs = {s.name: s for s in llm_tool_specs()}

        client = (self._llm or self._default_llm()).bind_tools(
            [_as_openai_tool(s) for s in specs.values()] + [_FINISH_TOOL]
        )
        messages: list[dict[str, Any]] = [
            {"role": "system",
             "content": SYSTEM_PROMPT.format(
                 task=task, today=today.isoformat(), hotel_hint=_hotel_hint())},
            {"role": "user", "content": task},
        ]
        seen: set[str] = set()  # 已经调过的 (工具, 参数)，用来挡住重复调用

        for step in range(self.max_steps):
            try:
                response = await client.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 —— 模型挂了也要把已有轨迹交出去
                # 超时/限流/断网都归到这儿。**不能上抛**：前面几步查到的数据
                # 还有价值，而且用户更想知道"走到哪一步断的"
                log.warning("agent 模型调用失败", extra={"step": step + 1,
                                                        "err": str(exc)[:200]})
                run.stop_reason = f"第 {step + 1} 步模型调用失败：{type(exc).__name__}"
                run.answer = run.answer or ""
                return run

            calls = getattr(response, "tool_calls", None) or []
            thought = (getattr(response, "content", "") or "").strip()

            if not calls:
                # 模型不调工具了，把它这段话当作最终答复
                run.answer = thought
                run.stop_reason = "模型给出了最终答复"
                run.finished = True
                return run

            messages.append(response)
            for call in calls:
                name = call.get("name", "")
                args = call.get("args") or {}

                if name == "finish_plan":
                    run.answer = json.dumps(args, ensure_ascii=False, indent=2)
                    run.add("finish_plan", args, "已交付", thought)
                    run.stop_reason = "模型主动收尾"
                    run.finished = True
                    return run

                observation = await self._call_tool(name, args, specs, seen, run)
                run.add(name, args, observation, thought)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": observation,
                })

            log.info(
                "agent step",
                extra={"step": step + 1, "calls": [c.get("name") for c in calls],
                       "serpapi_attempts": self._serpapi_attempts},
            )

        run.stop_reason = f"步数用尽（{self.max_steps} 步）"
        run.answer = thought
        return run

    # ------------------------------------------------------------------
    async def _call_tool(
        self, name: str, args: dict, specs: dict, seen: set[str], run: AgentRun
    ) -> str:
        """执行一次工具调用。**任何失败都回灌成文本**，让模型自己纠正。"""
        spec = specs.get(name)
        if spec is None:
            return f"错误：没有名为 {name} 的工具。可用的是 {sorted(specs)}"

        # 同样的调用不做第二次——重复查询是自主循环最常见的烧额度方式
        signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if signature in seen:
            return "这次调用和之前完全相同，结果不会变。请用已有结果继续，不要重复查询。"
        seen.add(signature)

        # 参数先校验再调用。**不能等 TypeError**：`@tool` 装饰器会把任何异常
        # 统一转成 UpstreamError，模型看到"上游故障"会去重试而不是改参数
        if bad := _unknown_kwargs(spec, args):
            allowed = sorted((spec.parameters or {}).get("properties", {}))
            return f"参数不对：不认识 {sorted(bad)}。可用参数：{allowed}"

        # SerpAPI 上限。**0 = 不限制**（默认），配置成正整数才启用。
        # ⚠️ 启用时按**尝试次数**计，不按实际消耗的配额——失败的调用不增配额
        #    计数器，若按配额判断，模型反复调一个失败的接口能无限循环下去
        if spec.provider == "serpapi":
            if self.serpapi_budget and self._serpapi_attempts >= self.serpapi_budget:
                return (
                    f"错误：本次规划的机票/酒店查询已达上限（{self.serpapi_budget} 次）。"
                    "请用已经查到的结果完成规划，不要再调用 serpapi 类工具。"
                )
            self._serpapi_attempts += 1

        try:
            result = await spec.fn(**args)
        except QuotaExceeded as exc:
            return f"额度耗尽：{exc.message}。请用已有数据完成规划。"
        except AppError as exc:
            return f"调用失败（{exc.code}）：{exc.message}"
        except Exception as exc:  # noqa: BLE001 —— 工具崩了也要让模型看见并绕过
            log.warning("agent 工具异常", extra={"tool": name, "err": str(exc)})
            return f"工具异常：{type(exc).__name__}: {exc}"

        return _as_text(result)

    def _default_llm(self):
        """自主循环专用的模型客户端。

        超时用 `agent_llm_timeout_s`（默认 180s）而不是全局的 30s——
        带十几个工具 schema 的请求首次调用就可能超过 30 秒（实测直接超时）。
        """
        from app.providers.llm import get_llm

        return get_llm(timeout_s=settings.agent_llm_timeout_s)


# ---------------------------------------------------------------- 工具描述


def _as_openai_tool(spec) -> dict[str, Any]:
    """ToolSpec → OpenAI function-calling 描述。

    `ToolSpec.parameters` 本来就是 JSON Schema（当初为绑给 LLM 而设计，
    只是一直没人用），这里直接搬过来。
    """
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


_FINISH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finish_plan",
        "description": "行程排好了就调它交付。调用前确认每一段的时长都来自 route_between。",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "给用户看的一段说明"},
                "days": {
                    "type": "array",
                    "description": "逐日安排",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "start": {"type": "string", "description": "HH:MM"},
                                        "end": {"type": "string", "description": "HH:MM"},
                                        "commute_min": {
                                            "type": "integer",
                                            "description": "从上一个点过来的分钟数，"
                                                           "必须来自 route_between",
                                        },
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                        "required": ["date", "items"],
                    },
                },
                "hotel": {
                    "type": "object",
                    "description": (
                        "选定的住处。**名称、地址、经纬度必须逐字来自工具返回**，"
                        "不能自己拼凑或改写——用户要拿这个去订房和导航。"
                    ),
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "酒店全名，**原样复制工具返回的 name 字段**",
                        },
                        "address": {
                            "type": "string",
                            "description": "门牌号地址。工具没给就用 address_of 反查坐标补上",
                        },
                        "lng": {"type": "number", "description": "经度，工具返回的原值"},
                        "lat": {"type": "number", "description": "纬度，工具返回的原值"},
                        "price_note": {
                            "type": "string",
                            "description": "房价说明，如「¥390/晚 × 3 晚 ≈ ¥1170」；"
                                           "查不到就写「价格暂无」",
                        },
                        "why": {"type": "string", "description": "为什么选它（位置/价格/评分）"},
                    },
                    "required": ["name", "address", "lng", "lat"],
                },
                "notes": {"type": "string", "description": "取舍说明、没排进去的景点等"},
            },
            "required": ["summary", "days", "hotel"],
        },
    },
}


def _hotel_hint() -> str:
    """告诉模型该用哪个工具找酒店。

    **这一条必须跟着配置走**：`SERPAPI_MOCK=true` 时 `hotels_search` 返回的是
    合成的假店名（「深圳地铁站维也纳国际酒店」这种），模型分辨不出来，
    会把它当真实结果交付给用户。所以假数据开着时就别把那个工具推荐给它。
    """
    serpapi_fake = settings.serpapi_mock
    if settings.hotel_source == "hybrid" or serpapi_fake:
        hint = (
            "用 poi_keyword(region=城市, types=\"100000\") 查住宿服务 POI —— "
            "**这是真实的酒店名称与坐标**"
        )
        if serpapi_fake:
            hint += ("。⚠️ 不要用 hotels_search：本次运行它返回的是合成数据，"
                     "店名是假的")
        return hint
    return "用 hotels_search 查房价，或 poi_keyword(types=\"100000\") 查地图上的住宿 POI"


def _unknown_kwargs(spec, args: dict) -> set[str]:
    """模型传了函数不认识的参数名时，把它们挑出来。

    靠 `inspect` 而不是 JSON Schema：schema 里可能少写了某个可选参数，
    但函数签名才是真正的契约。带 `**kwargs` 的函数一律放行。
    """
    import inspect

    try:
        signature = inspect.signature(spec.fn)
    except (TypeError, ValueError):  # pragma: no cover —— 拿不到签名就别拦
        return set()
    if any(p.kind is p.VAR_KEYWORD for p in signature.parameters.values()):
        return set()
    return set(args) - set(signature.parameters)


def _as_text(result: Any) -> str:
    """工具结果 → 喂回模型的文本。

    Pydantic 模型要先 dump；列表统一截断到 12 条——把 60 个景点全塞回去，
    上下文一轮就爆了，而模型实际只会用到前几条。
    """
    def dump(value: Any) -> Any:
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

    if isinstance(result, list):
        shown = [dump(v) for v in result[:12]]
        payload: Any = {"count": len(result), "items": shown}
        if len(result) > 12:
            payload["note"] = f"共 {len(result)} 条，只显示前 12 条"
    else:
        payload = dump(result)
    return json.dumps(payload, ensure_ascii=False, default=str)
