"""LangChain Attraction sub-agent for recommendations and attraction details."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.attraction_tool import (  # noqa: E402
    clean_opening_hours,
    get_attraction_info,
    get_attractions_by_place,
    is_valid_opening_hours,
)


_CITY_RECOMMENDATION_SEEDS: dict[str, list[str]] = {
    "beijing": [
        "Forbidden City",
        "Temple of Heaven",
        "Summer Palace",
        "Mutianyu Great Wall",
    ],
    "北京": [
        "Forbidden City",
        "Temple of Heaven",
        "Summer Palace",
        "Mutianyu Great Wall",
    ],
    "kuala lumpur, malaysia": [
        "Petronas Twin Towers",
        "KL Tower",
        "Batu Caves",
        "Central Market",
    ],
    "pattaya": [
        "The Sanctuary of Truth",
        "Pattaya Floating Market",
        "Big Buddha Temple",
        "Nong Nooch Tropical Garden",
    ],
    "bangkok": [
        "The Grand Palace",
        "Wat Pho",
        "Wat Arun",
        "Chatuchak Weekend Market",
    ],
    "shanghai": [
        "The Bund",
        "Oriental Pearl Tower",
        "Yu Garden",
        "Shanghai Tower",
    ],
    "上海": [
        "The Bund",
        "Oriental Pearl Tower",
        "Yu Garden",
        "Shanghai Tower",
    ],
    "penang, malaysia": [
        "Penang Hill",
        "Chew Jetty",
        "Kek Lok Si Temple",
        "Armenian Street",
    ],
}

_CITY_NAME_ALIASES: dict[str, str] = {
    "北京": "Beijing",
    "beijing": "Beijing",
    "上海": "Shanghai",
    "shanghai": "Shanghai",
    "芭堤雅": "Pattaya",
    "芭提雅": "Pattaya",
    "pattaya": "Pattaya",
    "曼谷": "Bangkok",
    "bangkok": "Bangkok",
    "吉隆坡": "Kuala Lumpur, Malaysia",
    "kuala lumpur": "Kuala Lumpur, Malaysia",
    "槟城": "Penang, Malaysia",
    "檳城": "Penang, Malaysia",
    "penang": "Penang, Malaysia",
    "乔治城": "George Town, Penang, Malaysia",
    "喬治城": "George Town, Penang, Malaysia",
    "george town": "George Town, Penang, Malaysia",
}


@tool
def attraction_recommendation_tool(city: str, query_hint: str = "") -> dict[str, Any]:
    """Return attraction candidates by city."""
    attractions = get_attractions_by_place(place=city, query_type=query_hint or None)
    return {
        "city": city,
        "attractions": attractions,
        "sources": [
            item.get("source_link", "")
            for item in attractions
            if isinstance(item, dict) and item.get("source_link")
        ],
    }


@tool
def attraction_detail_tool(attraction_name: str, location: str = "") -> dict[str, Any]:
    """Return attraction details by attraction name and optional location."""
    return get_attraction_info(attraction_name=attraction_name, location=location or None)


def _canonicalize_city_name(text: str) -> str:
    city = str(text or "").strip()
    if not city:
        return ""
    return _CITY_NAME_ALIASES.get(city.lower(), _CITY_NAME_ALIASES.get(city, city))


def _normalize_city(text: str) -> str:
    query = str(text or "").strip()
    if not query:
        return ""

    normalized = query.lower()
    if "george town" in normalized or "乔治城" in query or "喬治城" in query:
        return "George Town, Penang, Malaysia"

    patterns = [
        r"top attractions in\s+([A-Za-z\s\-,'\.]+)",
        r"attractions in\s+([A-Za-z\s\-,'\.]+)",
        r"([A-Za-z\s\-,'\.]+)\s+attractions",
        r"([\u4e00-\u9fffA-Za-z\s\-,'\.]+?)\s*(?:有什么好玩的景点|有什么值得去的景点|景点推荐|推荐景点)",
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return _canonicalize_city_name(match.group(1).strip(" .,!，。"))

    cleaned = re.sub(
        r"(有什么好玩的景点|有什么值得去的景点|景点推荐|推荐景点|attractions?|things to do|top attractions in)",
        "",
        query,
        flags=re.IGNORECASE,
    )
    return _canonicalize_city_name(cleaned.strip(" .,!，。"))


def _is_recommendation_query(query: str) -> bool:
    text = str(query or "").lower()
    recommendation_tokens = [
        "有什么好玩的景点",
        "有什么值得去的景点",
        "景点推荐",
        "推荐景点",
        "top attractions",
        "things to do",
        "attractions in",
        "attractions",
    ]
    if any(token in text for token in recommendation_tokens):
        return True
    return any(token in query for token in ["景点", "好玩", "值得去"])


def _is_detail_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in ["ticket", "price", "门票", "開放", "开放", "hours", "营业"])


def _extract_detail_target(query: str) -> tuple[str, str]:
    text = str(query or "").strip()
    if not text:
        return "", ""

    location_hint = ""
    lowered = text.lower()
    if "penang" in lowered or "槟城" in text or "檳城" in text:
        location_hint = "Penang, Malaysia"
    elif "beijing" in lowered or "北京" in text:
        location_hint = "Beijing"
    elif "kuala lumpur" in lowered or "吉隆坡" in text:
        location_hint = "Kuala Lumpur, Malaysia"

    cleaned = re.sub(
        r"(门票多少钱|門票多少錢|ticket\s*price|admission\s*fee|how much|开放时间|開放時間|opening\s*hours|营业时间|營業時間|visit duration|游玩时长|建議游玩时长)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[?？,，。!]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if " " in cleaned and any(word in cleaned.lower() for word in ["ticket", "opening", "hours", "price"]):
        cleaned = cleaned.split(" ")[0].strip()

    return cleaned or text, location_hint


def _clean_ticket_price(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    blocked = ["estimated", "unknown", "official price not found", "none", "null", "maybe"]
    if any(token in lowered for token in blocked):
        return ""
    if lowered == "free":
        return "Free"

    if re.search(r"\bRM\b", text, re.IGNORECASE):
        return text.replace("--", "-").replace("MYR", "RM").strip()
    if re.search(r"\b(THB|MYR|USD|SGD|HKD|CNY|JPY|EUR)\b", text, re.IGNORECASE):
        return re.sub(r"\s+", " ", text).strip()
    if re.search(r"[¥฿$€]", text):
        return re.sub(r"\s+", " ", text).strip()
    return ""


def _compact_description(text: Any) -> str:
    content = str(text or "").strip()
    if not content:
        return ""
    content = re.sub(r"\s+", " ", content)
    return content[:220].rstrip()


def _normalize_opening_hours(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = clean_opening_hours(raw)
    if cleaned and is_valid_opening_hours(cleaned):
        return cleaned
    return ""


def _clean_candidate_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""

    text = re.split(r"\s[-|–:]\s", text)[0].strip()
    text = re.sub(r"^(在鄰近地區|在邻近地区|附近|nearby)[:：\s]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d+\s*", "", text)

    blocked_patterns = [
        r"\d+\s*[大个個]?\s*景点",
        r"必做事项",
        r"things to do",
        r"places to visit",
        r"historical landmarks|historical sites",
        r"landmark tours|tour package",
        r"ultimate guide",
        r"终极指南|終極指南",
        r"旅游指南|旅遊指南",
        r"自由行|必去|必玩",
        r"景點推薦|景点推荐",
        r"熱門旅遊景點|热门旅游景点",
        r"一日遊行程|一日游行程",
        r"親子好去處|亲子好去处|親子遊景點推薦|亲子游景点推荐",
        r"旅遊攻略|旅游攻略|攻略",
        r"游览观光|遊覽觀光",
        r"top\s*\d+",
        r"the\s+\d+\s+best",
        r"best attractions",
        r"tourist attractions",
        r"must-see attractions|must see attractions|must visit attractions",
        r"discover the",
        r"beautiful sights|sights\s*&\s*attractions|sights\s+and\s+attractions",
        r"guide to|where to go|what to do|nearby attractions|is there much to do|top recommendations",
        r"\btours?\b$|^ep\d+\b",
        r"景点玩乐|景點玩樂",
        r"washington|華盛頓",
        r"【\s*20\d{2}.*】",
        r"\bairplane\b|\bmilestone\b|\bmonument\b|\bmemorial\b|\bstation\b",
    ]
    lowered = text.lower()
    if any(re.search(pat, lowered, re.IGNORECASE) for pat in blocked_patterns):
        return ""

    if len(text) < 3:
        return ""
    return text


def _seed_recommendation_candidates(city: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seeded = [dict(item) for item in candidates if isinstance(item, dict)]
    existing = {str(item.get("name", "")).strip().lower() for item in seeded if isinstance(item, dict)}

    if len(seeded) < 2 and ("george town" in city.lower() or "penang" in city.lower()):
        for seed in ["Penang Hill", "Chew Jetty", "Kek Lok Si Temple", "Armenian Street"]:
            if seed.lower() not in existing:
                seeded.append({"name": seed, "brief_description": "", "source_link": ""})
                existing.add(seed.lower())

    city_seed_names = _CITY_RECOMMENDATION_SEEDS.get(city.lower(), []) or _CITY_RECOMMENDATION_SEEDS.get(city, [])
    if len(seeded) < 2 and city_seed_names:
        for seed in city_seed_names:
            if seed.lower() not in existing:
                seeded.append({"name": seed, "brief_description": "", "source_link": ""})
                existing.add(seed.lower())

    return seeded


def _needs_recommendation_enrichment(item: dict[str, Any]) -> bool:
    description = _compact_description(item.get("description") or item.get("brief_description"))
    image = str(item.get("image") or item.get("image_url") or "").strip()
    ticket_price = _clean_ticket_price(item.get("ticket_price"))
    return not all([description, image, ticket_price])


def _merge_recommendation_detail(candidate: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged = dict(candidate)
    if not _compact_description(merged.get("description") or merged.get("brief_description")):
        merged["description"] = _compact_description(detail.get("description"))
    if not str(merged.get("image") or merged.get("image_url") or "").strip():
        merged["image"] = str(detail.get("image_url") or detail.get("image") or "").strip()
    if not _clean_ticket_price(merged.get("ticket_price")):
        merged["ticket_price"] = _clean_ticket_price(detail.get("ticket_price"))
    if not merged.get("source_link") and str(detail.get("source_link") or "").strip():
        merged["source_link"] = str(detail.get("source_link") or "").strip()
    detail_sources = detail.get("sources") or []
    if not merged.get("source_link") and isinstance(detail_sources, list):
        for src in detail_sources:
            if isinstance(src, dict):
                link = str(src.get("link", "")).strip()
            else:
                link = str(src).strip()
            if link:
                merged["source_link"] = link
                break
    return merged


def _enrich_recommendation_candidates(city: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    for index, item in enumerate(candidates):
        candidate = dict(item)
        if index < 5 and _needs_recommendation_enrichment(candidate):
            try:
                detail = get_attraction_info(
                    attraction_name=str(candidate.get("name", "")).strip(),
                    location=city or None,
                )
            except Exception:
                detail = {}
            if isinstance(detail, dict):
                candidate = _merge_recommendation_detail(candidate, detail)
        enriched.append(candidate)

    return enriched


def _normalize_recommendation_candidate(item: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    if not isinstance(item, dict):
        return None, ""

    raw_name = item.get("name", "")
    name = _clean_candidate_name(raw_name)
    if not name:
        return None, ""

    description = _compact_description(item.get("description") or item.get("brief_description"))
    image = str(item.get("image") or item.get("image_url") or "").strip()
    ticket_price = _clean_ticket_price(item.get("ticket_price"))
    source_link = str(item.get("source_link") or "").strip()

    return (
        {
            "name": name,
            "description": description,
            "image": image,
            "ticket_price": ticket_price,
        },
        source_link,
    )


def _build_recommendation_from_city(city: str, query: str) -> dict[str, Any]:
    candidates = _seed_recommendation_candidates(
        city=city,
        candidates=get_attractions_by_place(place=city, query_type=query),
    )
    candidates = _enrich_recommendation_candidates(city=city, candidates=candidates)

    attractions: list[dict[str, str]] = []
    sources: list[str] = []
    seen_names: set[str] = set()

    for item in candidates:
        normalized_item, source_link = _normalize_recommendation_candidate(item)
        if not normalized_item:
            continue
        name = normalized_item["name"]
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        attractions.append(normalized_item)
        if source_link:
            sources.append(source_link)
        if len(attractions) >= 8:
            break

    deduped_sources: list[str] = []
    seen_source: set[str] = set()
    for src in sources:
        if src in seen_source:
            continue
        seen_source.add(src)
        deduped_sources.append(src)

    return {
        "query_type": "attraction_recommendation",
        "city": city,
        "attractions": attractions,
        "sources": deduped_sources[:10],
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model text."""
    text = (text or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text") or item.get("output_text") or ""
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks).strip()

    if isinstance(content, dict):
        text = content.get("text") or content.get("output_text") or ""
        if text:
            return str(text).strip()

    return str(content or "").strip()


def _extract_payload_from_output(output: Any) -> dict[str, Any]:
    text = _content_to_text(output)
    if not text:
        return {}

    if "```" in text:
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    payload = _extract_json_object(text)
    if payload:
        return payload

    return _extract_json_object(json.dumps(output, ensure_ascii=False))


def _normalize_recommendation(payload: dict[str, Any]) -> dict[str, Any]:
    city = str(payload.get("city", "")).strip()

    raw_attractions = payload.get("attractions", [])
    if not isinstance(raw_attractions, list):
        raw_attractions = []

    normalized_attractions: list[dict[str, str]] = []
    for item in raw_attractions:
        if isinstance(item, dict):
            raw_name = str(item.get("name", "")).strip()
            name = _clean_candidate_name(raw_name) or raw_name
            description = _compact_description(item.get("description"))
            image = str(item.get("image") or item.get("image_url") or "").strip()
            ticket_price = _clean_ticket_price(item.get("ticket_price"))
        else:
            raw_name = str(item).strip()
            name = _clean_candidate_name(raw_name) or raw_name
            description = ""
            image = ""
            ticket_price = ""
        if name:
            normalized_attractions.append(
                {
                    "name": name,
                    "description": description,
                    "image": image,
                    "ticket_price": ticket_price,
                }
            )

    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        raw_sources = []
    sources = [str(src).strip() for src in raw_sources if str(src).strip()]

    return {
        "query_type": "attraction_recommendation",
        "city": city,
        "attractions": normalized_attractions,
        "sources": sources,
    }


def _normalize_info(payload: dict[str, Any]) -> dict[str, Any]:
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        raw_sources = []

    sources: list[str] = []
    for src in raw_sources:
        if isinstance(src, dict):
            link = str(src.get("link", "")).strip()
            if link:
                sources.append(link)
        else:
            text = str(src).strip()
            if text:
                sources.append(text)

    description = _compact_description(payload.get("description"))
    image = str(payload.get("image") or payload.get("image_url") or "").strip()
    opening_hours = _normalize_opening_hours(payload.get("opening_hours"))
    visit_duration = str(payload.get("visit_duration") or "").strip()
    ticket_price = _clean_ticket_price(payload.get("ticket_price"))
    ticket_status = str(payload.get("ticket_status") or "").strip().lower() or ("free" if ticket_price == "Free" else "unknown")
    price_note = str(payload.get("price_note") or "").strip()

    return {
        "query_type": "attraction_info",
        "name": str(payload.get("name", "")).strip(),
        "description": description,
        "image": image,
        "opening_hours": opening_hours,
        "visit_duration": visit_duration,
        "ticket_price": ticket_price,
        "ticket_status": ticket_status,
        "price_note": price_note,
        "sources": sources,
    }


def _is_placeholder_api_key(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return True

    upper_value = normalized.upper()
    return upper_value.startswith("YOUR_") or "PLACEHOLDER" in upper_value


def _resolve_google_config() -> tuple[str, str]:
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    model = os.getenv("GOOGLE_LLM_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"
    if _is_placeholder_api_key(api_key):
        raise ValueError(
            "Missing valid google model config. Set GOOGLE_API_KEY in .env."
        )
    return api_key, model


def _build_executor() -> Any:
    api_key, model = _resolve_google_config()
    llm = ChatGoogleGenerativeAI(
        model=model,
        api_key=api_key,
        temperature=0,
    )

    tools = [attraction_recommendation_tool, attraction_detail_tool]
    system_prompt = (
        "You are the Attraction sub-agent. Classify each query into one of two tasks and use tools.\n"
        "1) attraction_recommendation: recommend attractions for a city.\n"
        "2) attraction_info: fetch details for one attraction.\n"
        "Output must be a strict JSON object only, with no markdown and no extra commentary.\n"
        "Recommendation JSON schema:\n"
        "{\"query_type\":\"attraction_recommendation\",\"city\":\"string\",\"attractions\":[{\"name\":\"string\",\"description\":\"string\",\"image\":\"string\",\"ticket_price\":\"string\"}],\"sources\":[]}\n"
        "Info JSON schema:\n"
        "{\"query_type\":\"attraction_info\",\"name\":\"string\",\"description\":\"string\",\"image\":\"string\",\"opening_hours\":\"string\",\"visit_duration\":\"string\",\"ticket_price\":\"string\",\"ticket_status\":\"string\",\"price_note\":\"string\",\"sources\":[]}\n"
        "Use tools whenever possible and keep all fields present."
    )
    return create_agent(model=llm, tools=tools, system_prompt=system_prompt)


def _build_detail_from_query(query: str) -> dict[str, Any]:
    name, location_hint = _extract_detail_target(query)
    detail = get_attraction_info(attraction_name=name, location=location_hint or None)

    if location_hint and not _compact_description(detail.get("description")) and not str(detail.get("image_url") or "").strip():
        retry = get_attraction_info(attraction_name=name, location=None)
        for key in ("description", "image_url", "ticket_price", "sources", "opening_hours", "visit_duration"):
            if not detail.get(key) and retry.get(key):
                detail[key] = retry.get(key)

    return _normalize_info(detail)


def run_attraction_agent(query: str) -> dict[str, Any]:
    """Attraction sub-agent entrypoint."""
    if _is_recommendation_query(query):
        city = _normalize_city(query)
        if city:
            return _build_recommendation_from_city(city=city, query=query)

    if _is_detail_query(query):
        return _build_detail_from_query(query)

    executor = _build_executor()
    result = executor.invoke({"messages": [("user", query)]})
    messages = result.get("messages", []) if isinstance(result, dict) else []
    output = messages[-1].content if messages else ""
    payload = _extract_payload_from_output(output)

    query_type = str(payload.get("query_type", "")).strip()
    if query_type == "attraction_recommendation":
        city = _normalize_city(payload.get("city") or query)
        normalized = _normalize_recommendation(payload)
        if len(normalized.get("attractions", [])) >= 1:
            return normalized
        if city:
            return _build_recommendation_from_city(city=city, query=query)
        return normalized

    if query_type == "attraction_info":
        normalized_info = _normalize_info(payload)
        if normalized_info.get("name"):
            return normalized_info
        return _build_detail_from_query(query)

    if "attractions" in payload or "city" in payload:
        normalized = _normalize_recommendation(payload)
        if len(normalized.get("attractions", [])) >= 1:
            return normalized
        city = _normalize_city(payload.get("city") or query)
        if city:
            return _build_recommendation_from_city(city=city, query=query)
        return normalized

    return _build_detail_from_query(query)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run attraction sub-agent with a natural language query.")
    parser.add_argument(
        "query",
        type=str,
        nargs="?",
        help="Natural language query, e.g. 'Top attractions in Beijing'",
    )
    return parser


if __name__ == "__main__":
    parser = _build_cli_parser()
    args = parser.parse_args()
    if not args.query:
        parser.error("query is required. Example: python -m app.agents.attraction_agent 'Batu Caves ticket price'")

    response = run_attraction_agent(args.query)
    print(json.dumps(response, ensure_ascii=False, indent=2))
