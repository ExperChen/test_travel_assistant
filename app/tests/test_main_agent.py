import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.flight_agent import run_flight_agent
from app.agents.hotel_agent import run_hotel_agent
from app.agents.main_agent import parse_natural_language_to_hotel_json
from app.tests.test_attraction_agent import run_tool_flow


def _build_flight_input(hotel_request_json: str) -> str:
    hotel_request = json.loads(hotel_request_json)
    flight_request = {
        "departure_city": "Kuala Lumpur",
        "arrival_city": hotel_request["city"],
        "departure_date": hotel_request["check_in"],
        "passengers": 2,
        "budget": {
            "min": 1000,
            "max": 5000,
            "currency": "MYR",
        },
    }
    return json.dumps(flight_request, ensure_ascii=False)


def _safe_parse_json(raw):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _clean_text(value):
    if value is None:
        return ""
    return str(value).replace("`", "").strip()


def _build_view_input(hotel_request_json: str) -> str:
    hotel_request = json.loads(hotel_request_json)
    return json.dumps(
        {
            "location": hotel_request["city"],
            "type": "tourist_attraction",
        },
        ensure_ascii=False,
    )


def _build_standard_payload(hotel_request_json: str, flight_result, hotel_result, view_result) -> dict:
    hotel_request = json.loads(hotel_request_json)
    flight_item = flight_result.get("flight") if isinstance(flight_result, dict) else None
    flights = [flight_item] if flight_item else []

    hotels = []
    if isinstance(hotel_result, list):
        hotels = hotel_result
    elif isinstance(hotel_result, dict) and isinstance(hotel_result.get("hotels"), list):
        hotels = hotel_result.get("hotels", [])
    normalized_hotels = []
    for item in hotels:
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            normalized_hotels.append(
                {
                    "name": "",
                    "location": "",
                    "arrive_date": _clean_text(hotel_request.get("check_in")),
                    "leave_date": _clean_text(hotel_request.get("check_out")),
                    "price": 0.0,
                    "rating": 0.0,
                    "map_source": "",
                    "hotel_source": _clean_text(item.get("error")),
                }
            )
            continue
        normalized_hotels.append(
            {
                "name": _clean_text(item.get("name")),
                "location": _clean_text(item.get("location")),
                "arrive_date": _clean_text(item.get("arrive_date")),
                "leave_date": _clean_text(item.get("leave_date")),
                "price": float(item.get("price", 0) or 0),
                "rating": float(item.get("rating", 0) or 0),
                "map_source": _clean_text(item.get("map_source")),
                "hotel_source": _clean_text(item.get("hotel_source")),
            }
        )

    attractions = view_result.get("results", []) if isinstance(view_result, dict) else []
    normalized_views = []
    for index, item in enumerate(attractions):
        if not isinstance(item, dict):
            continue
        arrival_hour = 9 + index
        departure_hour = 12 + index
        normalized_views.append(
            {
                "name": _clean_text(item.get("attraction_name")),
                "location": _clean_text(item.get("attraction_location")),
                "information": "",
                "price": float(item.get("attraction_price", 0) or 0),
                "open_time": _clean_text(item.get("attraction_open_time")),
                "arrival_time": f'{hotel_request["check_in"]}T{arrival_hour:02d}:00:00',
                "departure_time": f'{hotel_request["check_in"]}T{departure_hour:02d}:00:00',
                "visit_duration": _clean_text(item.get("attraction_estimated_visit_time")),
                "image": "",
            }
        )

    return {
        "code": 200,
        "message": "success",
        "data": {
            "flights": flights,
            "hotels": normalized_hotels,
            "views": normalized_views,
        },
    }


def run_test_main_agent_flow(user_text: str) -> dict:
    hotel_request_json = parse_natural_language_to_hotel_json(user_text)
    print("=== 自然语言转 JSON ===")
    print(hotel_request_json)

    flight_request_json = _build_flight_input(hotel_request_json)
    view_request_json = _build_view_input(hotel_request_json)

    hotel_result = _safe_parse_json(run_hotel_agent(hotel_request_json))
    flight_result = _safe_parse_json(run_flight_agent(flight_request_json))
    view_result = _safe_parse_json(run_tool_flow(view_request_json))
    stored_payload = _build_standard_payload(hotel_request_json, flight_result, hotel_result, view_result)

    return {
        "dispatch_payload": {
            "hotel_request": json.loads(hotel_request_json),
            "flight_request": json.loads(flight_request_json),
            "view_request": json.loads(view_request_json),
        },
        "stored_payload": stored_payload,
    }


def main() -> None:
    user_text = input("请输入旅行需求（自然语言）: ").strip() or "我想去曼谷，2026-03-26入住，2026-03-28退房"
    output = run_test_main_agent_flow(user_text)
    print("=== 分发请求 ===")
    print(json.dumps(output["dispatch_payload"], ensure_ascii=False, indent=2))
    print("=== 存储结果 ===")
    print(json.dumps(output["stored_payload"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
