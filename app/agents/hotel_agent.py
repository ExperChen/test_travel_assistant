import json
from datetime import datetime

from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

from app.tools.hotel_tool import search_hotels


class HotelAgentInput(BaseModel):
    city: str = Field(..., description="酒店城市英文名")
    check_in: str = Field(..., description="入住日期 YYYY-MM-DD")
    check_out: str = Field(..., description="退房日期 YYYY-MM-DD")


HOTEL_INPUT_PARSER = JsonOutputParser(pydantic_object=HotelAgentInput)


def run_hotel_agent(json_input: str) -> str:
    """Validate a hotel request and return the hotel-tool result as JSON."""
    data = HOTEL_INPUT_PARSER.parse(json_input)
    hotels = search_hotels.invoke(
        {
            "location": data["city"],
            "check_in_date": data["check_in"],
            "check_out_date": data["check_out"],
        }
    )
    return json.dumps(hotels, ensure_ascii=False, indent=2)


def _read_valid_dates() -> tuple[str, str]:
    while True:
        check_in = input("Enter check-in date (YYYY-MM-DD): ").strip()
        check_out = input("Enter check-out date (YYYY-MM-DD): ").strip()
        try:
            check_in_date = datetime.strptime(check_in, "%Y-%m-%d").date()
            check_out_date = datetime.strptime(check_out, "%Y-%m-%d").date()
        except ValueError:
            print("❌ Invalid date format. Please use YYYY-MM-DD.")
            continue

        if check_in_date < datetime.now().date():
            print("❌ Check-in date must be today or later.")
        elif check_out_date <= check_in_date:
            print("❌ Check-out date must be after the check-in date.")
        else:
            return check_in, check_out


if __name__ == "__main__":
    print("--- 🏨 AI Hotel Booking Assistant ---")
    city = input("Enter destination city (e.g., Penang): ").strip()
    while not city:
        print("❌ City name cannot be empty. Please try again.")
        city = input("Enter destination city (e.g., Penang): ").strip()

    check_in, check_out = _read_valid_dates()
    print(f"\n🚀 Processing request for {city} from {check_in} to {check_out}...")
    print(
        run_hotel_agent(
            json.dumps(
                {"city": city, "check_in": check_in, "check_out": check_out},
                ensure_ascii=False,
            )
        )
    )
