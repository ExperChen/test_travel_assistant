"""最小示例：在行程生成阶段调用景点信息工具。"""

from dotenv import load_dotenv
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.attraction_tool import get_attraction_info


def build_itinerary_with_attraction(attraction_name: str, location: str) -> dict:
    attraction_info = get_attraction_info(attraction_name=attraction_name, location=location)

    itinerary = {
        "day": "Day 1",
        "city": location,
        "attraction": attraction_info["name"],
        "recommended_visit_duration": attraction_info["visit_duration"],
        "opening_hours": attraction_info["opening_hours"],
        "ticket_price": attraction_info["ticket_price"],
        "notes": "根据景点信息自动补齐，可用于后续 Agent 排序与预算估算。",
    }

    return {
        "itinerary": itinerary,
        "attraction_info": attraction_info,
    }


def main() -> None:
    load_dotenv()
    payload = build_itinerary_with_attraction(
        attraction_name="Escape Penang",
        location="Penang",
    )
    print(payload)


if __name__ == "__main__":
    main()
