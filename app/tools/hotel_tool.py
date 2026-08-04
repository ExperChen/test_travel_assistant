import os
import re
from serpapi import GoogleSearch
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

@tool
def search_hotels(location: str, check_in_date: str, check_out_date: str, max_results: int = 3) -> list:
    """
    Search for hotels and return structured data including source links.
    Returns up to `max_results` candidates, cheapest data first as returned by SerpAPI.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return [{"error": "SERPAPI_API_KEY not found"}] 

    params = {
        "engine": "google_hotels",
        "q": location,
        "gl": "my",           
        "hl": "en",        
        "currency": "MYR",    
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "api_key": api_key
    } 

    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        properties = results.get("properties", []) 
        
        hotel_list = []
        for hotel in properties[:max(1, max_results)]:
            # Data Cleaning for Price 
            raw_price = str(hotel.get("rate_per_night", {}).get("lowest", "0"))
            clean_num = re.sub(r'[^\d.]', '', raw_price)
            numeric_price = float(clean_num) if clean_num else 0.0

            hotel_list.append({
                "name": hotel.get("name"), 
                "location": hotel.get("address", "Exact address via Maps..."),
                "arrive_date": check_in_date,
                "leave_date": check_out_date,
                "price": numeric_price,
                "rating": hotel.get("overall_rating", 0.0), 
                "map_source": hotel.get("gps_coordinates", {}).get("link", "https://maps.google.com"),
                "hotel_source": hotel.get("link", "https://www.google.com/hotels")
            })
        return hotel_list
    except Exception as e:
        return [{"error": str(e)}] 
    
    # --- TOOL TESTING BLOCK ---
if __name__ == "__main__":
   
    test_result = search_hotels.invoke({
        "location": "Bangkok",
        "check_in_date": "2026-03-26",
        "check_out_date": "2026-03-28"
    })
    
   
    import json
    print(json.dumps(test_result, indent=2))