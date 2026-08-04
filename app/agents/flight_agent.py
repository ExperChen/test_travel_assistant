import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.flight_tool import search_and_filter_flights


def run_flight_agent(json_input: str) -> str:
    tool_output = search_and_filter_flights.invoke({"query_json": json_input})
    parsed = json.loads(tool_output)

    flights = parsed.get("flights", [])

    result = {"flights": flights}
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    test_input = {
        "departure_city": "Kuala Lumpur",
        "arrival_city": "Bangkok",
        "departure_date": "2026-03-26",
        "passengers": 2,
        "budget": {
            "min": 1000,
            "max": 5000,
            "currency": "MYR",
        },
    }
    print(run_flight_agent(json.dumps(test_input, ensure_ascii=False)))
