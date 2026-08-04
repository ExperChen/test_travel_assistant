import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.attraction_tool import (
    CITY_ATTRACTION_SEEDS,
    _canonicalize_place_name,
    _seed_city_key,
    get_attraction_info
)

def run_seed_agent(city: str) -> dict[str, Any]:
    """
    只召回 seed 中的景点
    """
    # 处理城市名称
    canonical_city = _canonicalize_place_name(city)
    city_key = _seed_city_key(canonical_city)
    
    # 从 seed 中获取城市景点列表
    seed_names = list(CITY_ATTRACTION_SEEDS.get(city_key, []))
    
    # 特殊处理 george town，将 penang 的景点也加进去
    if city_key == "george town":
        seed_names.extend(CITY_ATTRACTION_SEEDS.get("penang", []))
        
    # 去重
    deduped_names: list[str] = []
    seen: set[str] = set()
    for seed in seed_names:
        if not seed:
            continue
        key = seed.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_names.append(seed)
        
    attractions: list[dict[str, Any]] = []
    sources: list[str] = []
    
    # 针对每个 seed 中的景点调用 get_attraction_info 获取详情
    for name in deduped_names:
        # 直接调用底层的 get_attraction_info 来获取最全的信息
        detail = get_attraction_info(attraction_name=name, location=canonical_city)
        
        attractions.append({
            "name": name,
            "description": detail.get("description", ""),
            "image": detail.get("image_url", ""),
            "ticket_price": detail.get("ticket_price", ""),
            "opening_hours": detail.get("opening_hours", ""),
            "visit_duration": detail.get("visit_duration", ""),
        })
        
        # 收集来源
        detail_sources = detail.get("sources", [])
        if isinstance(detail_sources, list):
            sources.extend(detail_sources)
            
    # 对来源进行去重
    deduped_sources: list[str] = []
    seen_sources: set[str] = set()
    for src in sources:
        if src and src not in seen_sources:
            seen_sources.add(src)
            deduped_sources.append(src)

    return {
        "query_type": "attraction_recommendation",
        "city": canonical_city or city,
        "attractions": attractions,
        "sources": deduped_sources,
    }

def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run seed attraction agent to recall attractions only from seeds.")
    parser.add_argument(
        "city",
        type=str,
        help="City name to recall attractions from seeds, e.g. 'Beijing' or '北京'",
    )
    return parser

if __name__ == "__main__":
    parser = _build_cli_parser()
    args = parser.parse_args()

    response = run_seed_agent(args.city)
    print(json.dumps(response, ensure_ascii=False, indent=2))
