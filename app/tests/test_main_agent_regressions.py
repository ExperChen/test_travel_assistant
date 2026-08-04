import json
from datetime import datetime

from app.agents import main_agent


def _hotel_request_json() -> str:
    return json.dumps(
        {
            "city": "Seoul",
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
        }
    )


def test_fallback_dispatch_preserves_user_departure_city():
    plan = main_agent._build_fallback_dispatch_plan(
        "我从吉隆坡到首尔旅行，日期是 2026-08-10 到 2026-08-12"
    )

    assert plan["flight_request_outbound"]["departure_city"] == "Kuala Lumpur"
    assert plan["flight_request_inbound"]["arrival_city"] == "Kuala Lumpur"
    assert plan["hotel_request"]["city"] == "Seoul"
    assert plan["warnings"] == []


def test_standard_payload_schedules_seed_attractions_with_timezone():
    result = main_agent._build_standard_payload(
        _hotel_request_json(),
        flight_result={"flights": []},
        hotel_result=[],
        view_result={
            "attractions": [
                {
                    "name": "Gyeongbokgung Palace",
                    "description": "Historic palace",
                    "ticket_price": "KRW 3000",
                    "opening_hours": "09:00 - 18:00",
                    "visit_duration": "2 hours",
                },
                {
                    "name": "N Seoul Tower",
                    "visit_duration": "1.5 hours",
                },
            ]
        },
        user_text="",
        output_text="",
    )

    views = result["data"]["views"]
    assert len(views) == 2
    assert views[0]["arrival_time"] == "2026-08-10T09:00:00+09:00"
    assert views[0]["departure_time"] == "2026-08-10T11:00:00+09:00"
    assert views[1]["arrival_time"] == "2026-08-10T11:00:00+09:00"
    assert datetime.fromisoformat(views[1]["departure_time"]) > datetime.fromisoformat(
        views[1]["arrival_time"]
    )


def test_standard_payload_rejects_unsupported_attraction_shape():
    result = main_agent._build_standard_payload(
        _hotel_request_json(),
        flight_result={"flights": []},
        hotel_result=[],
        view_result={"travel_plan": "not a supported list"},
        user_text="",
        output_text="",
    )

    assert result["data"]["views"] == []
    assert result["data"]["warnings"] == ["景点服务没有返回受支持的景点列表。"]
