import json
import os
from pathlib import Path
import re
import sys

from dotenv import load_dotenv
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_attraction_agent import run_tool_flow


class MainAgentPreferences(BaseModel):
    type: list[str] = Field(default_factory=list)
    budget_level: str = Field(default="medium")


class MainAgentInput(BaseModel):
    task: str = Field(..., description="结构化任务类型，例如 search_attractions")
    destination: str = Field(..., description="目的地城市，例如 Penang")
    preferences: MainAgentPreferences = Field(default_factory=MainAgentPreferences)


class AttractionResultItem(BaseModel):
    attraction_name: str
    attraction_location: str
    attraction_open_time: str
    attraction_estimated_visit_time: str
    attraction_price: float


class MainAgentOutput(BaseModel):
    agent: str
    data: list[AttractionResultItem]


MAIN_INPUT_PARSER = JsonOutputParser(pydantic_object=MainAgentInput)
MAIN_OUTPUT_PARSER = JsonOutputParser(pydantic_object=MainAgentOutput)


def _build_company_llm() -> ChatOpenAI:
    """创建 COMPANY 模型实例（ChatOpenAI），用于把自然语言解析为结构化任务。"""
    load_dotenv()
    return ChatOpenAI(
        model=os.getenv("COMPANY_LLM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("COMPANY_BASE_URL"),
        api_key=(os.getenv("COMPANY_API_KEY") or "").strip(),
        temperature=0,
    )


def _heuristic_parse(user_text: str) -> dict:
    """当 LLM 不可用时，用最简单的规则把自然语言转为结构化任务。"""
    destination = ""
    if re.search(r"(吉隆坡|kuala\s*lumpur)", user_text, re.IGNORECASE):
        destination = "Kuala Lumpur"
    if re.search(r"(槟城|penang)", user_text, re.IGNORECASE):
        destination = "Penang"
    if not destination:
        destination = "Kuala Lumpur"
    payload = {"task": "search_attractions", "destination": destination, "preferences": {}}
    return MAIN_INPUT_PARSER.parse(json.dumps(payload, ensure_ascii=False))


def _extract_first_json_object(text: str) -> str:
    """从模型输出中提取第一个 JSON 对象字符串（容忍前后夹杂自然语言）。"""
    start = text.find("{")
    if start == -1:
        return ""

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def parse_user_text_to_task(user_text: str) -> dict:
    """使用 COMPANY 模型把自然语言解析成 MainAgentInput 结构化 JSON。

    这里按你的要求改用 StrOutputParser：先拿到原始字符串，再自行 json.loads + schema 校验。
    """
    prompt = PromptTemplate(
        template=(
            "你是主 Agent 的结构化解析器。\n"
            "把用户的旅行需求解析成结构化任务，禁止输出自然语言。\n"
            "规则：\n"
            "1) task 固定为 \"search_attractions\"。\n"
            "2) destination 必须是城市英文名（例如 Kuala Lumpur / Penang）。\n"
            "3) preferences 可留空对象。\n"
            "{format_instructions}\n"
            "用户输入：{query}\n"
        ),
        input_variables=["query"],
        partial_variables={"format_instructions": MAIN_INPUT_PARSER.get_format_instructions()},
    )

    try:
        llm = _build_company_llm()
        chain = prompt | llm | StrOutputParser()
        raw_text = chain.invoke({"query": user_text})

        json_text = _extract_first_json_object(raw_text) or raw_text
        obj = json.loads(json_text)
        return MAIN_INPUT_PARSER.parse(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return _heuristic_parse(user_text)


def _call_attraction_agent(destination: str) -> dict:
    """把主 Agent 的 destination 转成子 Agent 的 tool 入参，并调用子 Agent。"""
    sub_agent_input = json.dumps(
        {
            "location": destination,
            "type": "tourist_attraction",
        },
        ensure_ascii=False,
    )
    return run_tool_flow(sub_agent_input)


def run_main_agent_flow(user_text: str) -> dict:
    """主 Agent：接收自然语言 -> 解析任务 -> 自动调用 attraction 子 Agent -> 输出结构化 JSON。"""
    parsed_input = parse_user_text_to_task(user_text)
    sub_result = _call_attraction_agent(destination=parsed_input["destination"])
    payload = {
        "agent": "attraction_agent",
        "data": sub_result.get("results", []),
    }
    return MAIN_OUTPUT_PARSER.parse(json.dumps(payload, ensure_ascii=False))


def format_text_output(main_agent_output: dict) -> str:
    agent_name = main_agent_output.get("agent", "")
    items = main_agent_output.get("data", []) or []

    lines: list[str] = []
    lines.append(f"子 Agent：{agent_name}")
    lines.append(f"景点数量：{len(items)}")
    lines.append("")
    lines.append("推荐景点：")

    for index, item in enumerate(items, start=1):
        name = item.get("attraction_name", "")
        location = item.get("attraction_location", "")
        open_time = item.get("attraction_open_time", "")
        visit_time = item.get("attraction_estimated_visit_time", "")
        price = item.get("attraction_price", 0.0)

        lines.append(f"{index}. {name}（{location}）")
        lines.append(f"   开放时间：{open_time or '暂无'}")
        lines.append(f"   建议游玩：{visit_time or '暂无'}")
        lines.append(f"   价格：{price}")

    return "\n".join(lines).strip()


def main() -> None:
    """示例：输入自然语言，自动调用子 Agent 并输出结构化 JSON。"""
    user_text = input("旅行请求: ").strip() or "我想去吉隆坡玩"
    output = run_main_agent_flow(user_text)

    print("=== JSON Output ===")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("")
    print("=== 文本 Output ===")
    print(format_text_output(output))


if __name__ == "__main__":
    main()
