"""Manual interactive attraction-agent demo.

Despite its historical filename, this module is not an automated test. All
provider and agent initialization is deliberately kept inside ``main`` so that
pytest can collect the repository without requiring external credentials.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.attraction_tool import attraction_information_tool


SYSTEM_PROMPT = """
你是一名专业的 AI 旅游规划助手。先确认用户的出发城市、目的地、日期和预算；
信息完整后，使用 attraction_information_tool 查询景点，再返回景点、门票、建议游玩
时长和简单游玩顺序。信息不完整时主动追问，不要编造查询结果。
""".strip()


def _build_llm():
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if provider == "google" or os.getenv("GOOGLE_API_KEY"):
        return ChatGoogleGenerativeAI(
            model=os.getenv("GOOGLE_LLM_MODEL", "gemini-2.5-flash"),
            api_key=os.getenv("GOOGLE_API_KEY"),
        )
    return ChatOpenAI(
        model=os.getenv("COMPANY_LLM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("COMPANY_BASE_URL"),
        api_key=os.getenv("COMPANY_API_KEY"),
    )


def main() -> None:
    agent = create_agent(
        _build_llm(),
        tools=[attraction_information_tool],
        system_prompt=SYSTEM_PROMPT,
    )

    print("AI Travel Assistant 已启动，输入 exit 退出。")
    while True:
        text = input("旅行请求: ").strip()
        if not text:
            continue
        if text.lower() == "exit":
            print("已退出。")
            return

        try:
            result = agent.invoke({"messages": [("user", text)]})
            messages = result.get("messages", [])
            output = messages[-1].content if messages else "无回复"
        except Exception as exc:
            output = f"调用失败: {exc}"

        print("\n=== 旅行规划结果 ===")
        print(output)
        print("====================\n")


if __name__ == "__main__":
    main()
