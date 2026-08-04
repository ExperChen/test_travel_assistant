"""最小示例：子 Agent 与工具之间使用 JsonOutputParser 进行严格 JSON 输入输出。"""

import json
from pathlib import Path
import os
import re
import sys

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.attraction_tool import get_attraction_info


class AttractionToolInput(BaseModel):
    location: str = Field(..., description="城市或区域，例如 Penang")
    type: str = Field(default="tourist_attraction", description="地点类型")


class AttractionResultItem(BaseModel):
    attraction_name: str
    attraction_location: str
    attraction_open_time: str
    attraction_estimated_visit_time: str
    attraction_price: float


class AttractionToolOutput(BaseModel):
    results: list[AttractionResultItem]


INPUT_PARSER = JsonOutputParser(pydantic_object=AttractionToolInput)
OUTPUT_PARSER = JsonOutputParser(pydantic_object=AttractionToolOutput)


def _candidate_attractions(location: str) -> list[str]:
    mapping = {
        "penang": ["George Town", "Penang Hill", "ESCAPE Penang"],
        "kuala lumpur": ["KL Bird Park", "Petronas Twin Towers", "Batu Caves"],
        "george town": ["Penang Street Art", "Khoo Kongsi", "Chew Jetty"],
        "seoul": ["Gyeongbokgung Palace", "Myeongdong", "N Seoul Tower"],
        "bangkok": ["The Grand Palace", "Wat Arun", "Chatuchak Market"],
    }
    normalized = location.strip().lower()
    return mapping.get(normalized, [f"{location} Old Town", f"{location} City Park"])


def _extract_price_value(ticket_price_text: str) -> float:
    if not ticket_price_text:
        return 0.0
    match = re.search(r"\d+(?:\.\d+)?", ticket_price_text)
    return float(match.group(0)) if match else 0.0


@tool(args_schema=AttractionToolInput)
def attraction_search_tool(location: str, type: str) -> dict:
    """按 location/type 入参查询景点并返回标准化 JSON 结果。"""
    names = _candidate_attractions(location)
    raw_results = []
    for index, attraction_name in enumerate(names):
        info = get_attraction_info(attraction_name=attraction_name, location=location)
        raw_results.append(
            {
                "attraction_name": info.get("name") or attraction_name,
                "attraction_location": location,
                "attraction_open_time": info.get("opening_hours", ""),
                "attraction_estimated_visit_time": info.get("visit_duration") or "2 hours",
                "attraction_price": _extract_price_value(info.get("ticket_price", "")),
            }
        )
    standardized = {"results": raw_results}
    return OUTPUT_PARSER.parse(json.dumps(standardized, ensure_ascii=False))


def _build_llm():
    company_api_key = (os.getenv("COMPANY_API_KEY") or "").strip()
    return ChatOpenAI(
        model=os.getenv("COMPANY_LLM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("COMPANY_BASE_URL"),
        api_key=company_api_key,
    )


def build_attraction_agent():
    llm = _build_llm()
    prompt = (
        "你是景点子 Agent。"
        "你必须调用 attraction_search_tool。"
        "调用工具时仅允许传入 JSON 参数：location、type。"
        "最终只输出 JSON，格式必须是 {\"results\":[{\"attraction_name\":\"...\",\"attraction_location\":\"...\","
        "\"attraction_open_time\":\"...\",\"attraction_estimated_visit_time\":\"...\",\"attraction_price\":0.0}]}。"
    )
    return create_agent(llm, tools=[attraction_search_tool], system_prompt=prompt)


def run_tool_flow(raw_input: str) -> dict:
    parsed_tool_input = INPUT_PARSER.parse(raw_input)
    tool_result = attraction_search_tool.invoke(parsed_tool_input)
    return OUTPUT_PARSER.parse(json.dumps(tool_result, ensure_ascii=False))


def main() -> None:
    raw_input = json.dumps(
        {
            "location": "Kuala Lumpur",
            "type": "tourist_attraction",
        },
        ensure_ascii=False,
    )
    output = run_tool_flow(raw_input)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
