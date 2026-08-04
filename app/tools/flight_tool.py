"""
flight_tool.py - 机票查询工具（使用 SerpAPI Google Flights）

输入格式:
{
  "departure_city": "Kuala Lumpur",
  "arrival_city": "Bangkok",
  "departure_date": "2026-03-26",
  "passengers": 2,
  "budget": {
    "min": 1000,
    "max": 5000,
    "currency": "MYR"
  }
}

输出格式:
{
  "flights": [
    {
      "name": "Malaysia Airlines",
      "code": "MH782",
      "airline_company": "MAS",
      "departure_airport": "KUL",
      "arrival_airport": "BKK",
      "departure_date": "2026-03-26T14:00:00",
      "arrival_date": "2026-03-26T15:10:00",
      "price": 450.00,
      "luggage_limitation": "20kg"
    }
  ]
}

环境变量 (.env):
SERPAPI_API_KEY=your_key
LANGCHAIN_API_KEY=your_langsmith_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=flight-agent
"""

import os
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from langchain_core.tools import tool
from langsmith import traceable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.tools.currency import convert_to_myr

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "false")
os.environ["LANGCHAIN_PROJECT"]    = os.getenv("LANGCHAIN_PROJECT", "flight-agent")

# ── SerpAPI（可选，没有就用模拟数据）─────────────────────────────────────────
SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")
USE_REAL_API = bool(SERPAPI_KEY)

try:
    from serpapi import GoogleSearch
except ImportError:
    USE_REAL_API = False

# ── 城市名 → IATA 代码 ────────────────────────────────────────────────────────
CITY_TO_IATA = {
    "kuala lumpur": "KUL", "kl": "KUL",
    "tokyo": "TYO", "osaka": "KIX",
    "singapore": "SIN", "bangkok": "BKK",
    "london": "LHR", "paris": "CDG",
    "new york": "JFK", "sydney": "SYD",
    "seoul": "ICN", "beijing": "PEK",
    "shanghai": "PVG", "hong kong": "HKG",
    "dubai": "DXB", "amsterdam": "AMS",
    "frankfurt": "FRA", "taipei": "TPE",
    "jakarta": "CGK", "manila": "MNL",
}

def city_to_iata(city: str) -> str:
    return CITY_TO_IATA.get(city.lower().strip(), city.upper()[:3])

# ── 航司代码 → 全名/简称 ──────────────────────────────────────────────────────
AIRLINE_INFO = {
    "AK": {"name": "AirAsia",             "company": "AirAsia"},
    "MH": {"name": "Malaysia Airlines",   "company": "MAS"},
    "JL": {"name": "Japan Airlines",      "company": "JAL"},
    "NH": {"name": "ANA",                 "company": "ANA"},
    "TR": {"name": "Scoot",               "company": "Scoot"},
    "D7": {"name": "AirAsia X",           "company": "AirAsia X"},
    "OD": {"name": "Batik Air",           "company": "Batik Air"},
    "PR": {"name": "Philippine Airlines", "company": "PAL"},
    "SQ": {"name": "Singapore Airlines",  "company": "SIA"},
    "TG": {"name": "Thai Airways",        "company": "THAI"},
}

# ══════════════════════════════════════════════════════════════════════════════
# 模拟数据生成（SerpAPI 未配置时使用）
# ══════════════════════════════════════════════════════════════════════════════
def _generate_mock_flights(origin, destination, departure_date, passengers):
    airlines = [
        ("AK", False, True,  "20kg", 150, 600),
        ("MH", True,  True,  "20kg", 400, 900),
        ("D7", False, True,  "20kg", 200, 700),
        ("TR", False, False, None,   180, 650),
        ("OD", False, True,  "20kg", 170, 580),
        ("SQ", True,  True,  "30kg", 600, 1200),
        ("TG", True,  True,  "20kg", 500, 1000),
        ("PR", True,  True,  "20kg", 350, 850),
    ]
    random.seed(hash(departure_date + origin + destination) % 10000)
    flights = []
    for code, is_direct, has_bag, bag_kg, min_p, max_p in airlines:
        for _ in range(3):
            dep_h = random.randint(6, 22)
            dep_m = random.choice([0, 15, 30, 45])
            fly_h = random.randint(1, 3) if is_direct else random.randint(5, 10)
            fly_m = random.choice([0, 15, 30, 45])
            dep_dt = datetime.strptime(departure_date, "%Y-%m-%d").replace(
                hour=dep_h, minute=dep_m)
            arr_dt = dep_dt + timedelta(hours=fly_h, minutes=fly_m)
            price = round(random.uniform(min_p, max_p), 2)
            info = AIRLINE_INFO.get(code, {"name": code, "company": code})
            flights.append({
                "name":              info["name"],
                "code":              f"{code}{random.randint(100,999)}",
                "airline_company":   info["company"],
                "departure_airport": origin,
                "arrival_airport":   destination,
                "departure_date":    dep_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "arrival_date":      arr_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "price":             price,
                "total_price":       round(price * passengers, 2),
                "luggage_limitation": bag_kg if has_bag else "No checked bag",
                "is_direct":         is_direct,
            })
    return flights


# ══════════════════════════════════════════════════════════════════════════════
# SerpAPI Google Flights 搜索
# ══════════════════════════════════════════════════════════════════════════════
def _search_serpapi(origin, destination, departure_date, passengers, currency):
    params = {
        "engine":       "google_flights",
        "api_key":      SERPAPI_KEY,
        "departure_id": origin,
        "arrival_id":   destination,
        "outbound_date": departure_date,
        "adults":       passengers,
        "currency":     currency,
        "type":         "2",   # 2 = One way
        "hl":           "en",
    }
    results = GoogleSearch(params).get_dict()
    flights = []

    # SerpAPI 返回 best_flights 和 other_flights
    all_flights = results.get("best_flights", []) + results.get("other_flights", [])

    for offer in all_flights:
        segs = offer.get("flights", [])
        if not segs:
            continue
        first = segs[0]
        last  = segs[-1]

        dep_airport = first.get("departure_airport", {})
        arr_airport = last.get("arrival_airport", {})

        # 解析出发/到达时间
        dep_time_str = dep_airport.get("time", "")   # 格式: "2026-03-26 14:00"
        arr_time_str = arr_airport.get("time", "")

        try:
            dep_dt = datetime.strptime(dep_time_str, "%Y-%m-%d %H:%M")
            arr_dt = datetime.strptime(arr_time_str, "%Y-%m-%d %H:%M")
            dep_iso = dep_dt.strftime("%Y-%m-%dT%H:%M:%S")
            arr_iso = arr_dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            dep_iso = dep_time_str
            arr_iso = arr_time_str

        # 行李信息从 extensions 里提取
        extensions = offer.get("extensions", [])
        luggage = "No checked bag"
        for ext in extensions:
            ext_lower = ext.lower()
            if "checked bag" in ext_lower or "baggage" in ext_lower:
                luggage = ext
                break

        airline_name = first.get("airline", "Unknown")
        flight_num   = first.get("flight_number", "")
        price        = float(offer.get("price", 0))

        flights.append({
            "name":              airline_name,
            "code":              flight_num.replace(" ", ""),
            "airline_company":   airline_name,
            "departure_airport": dep_airport.get("id", origin),
            "arrival_airport":   arr_airport.get("id", destination),
            "departure_date":    dep_iso,
            "arrival_date":      arr_iso,
            "price":             round(price / passengers, 2),
            "total_price":       price,
            "luggage_limitation": luggage,
            "is_direct":         len(segs) == 1,
        })

    return flights


# ══════════════════════════════════════════════════════════════════════════════
# 核心工具函数
# ══════════════════════════════════════════════════════════════════════════════
@tool
@traceable(name="search_and_filter_flights")
def search_and_filter_flights(query_json: str) -> str:
    """
    根据输入的 JSON 查询并筛选机票，返回符合条件的航班列表。

    输入 JSON 格式:
    {
      "departure_city": "Kuala Lumpur",
      "arrival_city": "Bangkok",
      "departure_date": "2026-03-26",
      "passengers": 2,
      "budget": {
        "min": 1000,
        "max": 5000,
        "currency": "MYR"
      }
    }
    """
    # ── 解析输入 ──────────────────────────────────────────────────────────────
    try:
        query = json.loads(query_json)
    except Exception as e:
        return json.dumps({"error": f"输入 JSON 解析失败: {e}"}, ensure_ascii=False)

    departure_city = query.get("departure_city", "")
    arrival_city   = query.get("arrival_city", "")
    departure_date = query.get("departure_date", "")
    passengers     = max(1, int(query.get("passengers") or 1))
    max_results    = max(1, int(query.get("max_results") or 3))
    budget         = query.get("budget", {})
    budget_min     = budget.get("min", 0)
    budget_max     = budget.get("max", 99999)
    currency       = budget.get("currency", "MYR")

    # 预算换算为 MYR（per person）
    budget_min_myr = convert_to_myr(budget_min, currency) / passengers
    budget_max_myr = convert_to_myr(budget_max, currency) / passengers

    origin = city_to_iata(departure_city)
    dest   = city_to_iata(arrival_city)

    # ── 搜索航班 ──────────────────────────────────────────────────────────────
    if USE_REAL_API:
        try:
            raw_flights = _search_serpapi(origin, dest, departure_date, passengers, currency)
        except Exception as e:
            return json.dumps({"error": f"SerpAPI 错误: {e}"}, ensure_ascii=False)
    else:
        raw_flights = _generate_mock_flights(origin, dest, departure_date, passengers)

    # ── 预算筛选 ──────────────────────────────────────────────────────────────
    filtered = [
        f for f in raw_flights
        if budget_min_myr <= f["price"] <= budget_max_myr
    ]

    # ── 按价格升序排列 ────────────────────────────────────────────────────────
    filtered.sort(key=lambda x: x["price"])

    # ── 只保留输出字段，最多返回 max_results 个候选（按价格升序，已排序）────────────────
    output_flights = [{
        "name":              f["name"],
        "code":              f["code"],
        "airline_company":   f["airline_company"],
        "departure_airport": f["departure_airport"],
        "arrival_airport":   f["arrival_airport"],
        "departure_date":    f["departure_date"],
        "arrival_date":      f["arrival_date"],
        "price":             f["price"],
        "luggage_limitation": f["luggage_limitation"],
    } for f in filtered[:max_results]]

    return json.dumps({
        "flights": output_flights,
        "meta": {
            "total_found":    len(raw_flights),
            "after_filter":   len(output_flights),
            "currency":       "MYR",
            "passengers":     passengers,
            "budget_per_pax": f"{budget_min_myr:.0f} - {budget_max_myr:.0f} MYR",
            "data_source":    "SerpAPI Google Flights" if USE_REAL_API else "Mock Data",
        }
    }, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# 直接运行测试
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    test_input = {
        "departure_city": "Kuala Lumpur",
        "arrival_city": "Bangkok",
        "departure_date": "2026-03-26",
        "passengers": 2,
        "budget": {
            "min": 1000,
            "max": 5000,
            "currency": "MYR"
        }
    }
    print("输入:")
    print(json.dumps(test_input, ensure_ascii=False, indent=2))
    print("\n输出:")
    result = search_and_filter_flights.invoke({"query_json": json.dumps(test_input)})
    print(result)