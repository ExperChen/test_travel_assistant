"""
Attraction info provider (SerpAPI + Google).

This module exposes a single importable function:
    fetch_attraction_details(attraction_name: str, location: str | None = None) -> dict

It focuses on 4 fields only:
- image_url
- opening_hours
- visit_duration
- ticket_price

Design goals:
- best-effort extraction from SerpAPI google/google_images responses
- no hard failure on partial/missing data
- always return fixed response shape for downstream teammates
"""

from __future__ import annotations

import os
import re
from typing import Any

from serpapi import GoogleSearch


_PRICE_REGEX = re.compile(
    r"(?:(?:RM|MYR|USD|SGD|EUR|GBP|AUD|CNY|RMB|JPY|THB|IDR|PHP|VND|INR|HKD|TWD|KRW)\s*\d+(?:[\.,]\d{1,2})?"
    r"|(?:US\$|S\$|€|£|¥|\$)\s*\d+(?:[\.,]\d{1,2})?"
    r"|\d+(?:[\.,]\d{1,2})?\s*(?:RM|MYR|USD|SGD|EUR|GBP|AUD|CNY|RMB|JPY|THB|IDR|PHP|VND|INR|HKD|TWD|KRW))",
    re.IGNORECASE,
)

_HOURS_REGEXES = [
    re.compile(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[^\n]{0,80}(?:\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm)?)[^\n]{0,80}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm)?\s*(?:-|–|to|~|至)\s*\d{1,2}(?::\d{2})?\s?(?:AM|PM|am|pm)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:opening\s*hours|open\s*hours|营业时间|开放时间)[:：]?\s*([^\n\.;]{4,80})", re.IGNORECASE),
]

_DURATION_REGEXES = [
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:-|–|to)?\s*\d*(?:\.\d+)?\s*(?:hours?|hrs?|小时)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*(?:minutes?|mins?|分钟)\b", re.IGNORECASE),
    re.compile(r"(?:how\s*long\s*to\s*spend|visit\s*duration|recommended\s*time)[:：]?\s*([^\n\.;]{2,80})", re.IGNORECASE),
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _flatten_text(payload: Any) -> str:
    """Convert nested dict/list into searchable plain text."""
    if payload is None:
        return ""
    if isinstance(payload, dict):
        return " ".join(_flatten_text(v) for v in payload.values())
    if isinstance(payload, list):
        return " ".join(_flatten_text(v) for v in payload)
    return _safe_text(payload)


def _serp_google(query: str, api_key: str) -> dict[str, Any]:
    params = {
        "engine": "google",
        "q": query,
        "hl": "en",
        "num": 10,
        "api_key": api_key,
    }
    return GoogleSearch(params).get_dict()


def _serp_google_images(query: str, api_key: str) -> dict[str, Any]:
    params = {
        "engine": "google_images",
        "q": query,
        "hl": "en",
        "num": 10,
        "api_key": api_key,
    }
    return GoogleSearch(params).get_dict()


def _extract_opening_hours(text: str) -> str:
    for reg in _HOURS_REGEXES:
        m = reg.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _extract_ticket_price(text: str) -> str:
    # Prefer contexts with pricing keywords first, then fallback to first currency-like match.
    lines = [line.strip() for line in re.split(r"[\n\|]", text) if line.strip()]
    keyword_lines = [
        ln
        for ln in lines
        if re.search(r"ticket|admission|entry|price|fee|门票|票价", ln, re.IGNORECASE)
    ]

    for ln in keyword_lines:
        m = _PRICE_REGEX.search(ln)
        if m:
            return m.group(0).strip()

    m_all = _PRICE_REGEX.search(text)
    return m_all.group(0).strip() if m_all else ""


def _extract_visit_duration(text: str) -> str:
    for reg in _DURATION_REGEXES:
        m = reg.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _estimate_duration(name: str) -> str:
    lowered = name.lower()
    if any(k in lowered for k in ["museum", "gallery", "博物馆", "美术馆"]):
        return "2–3 hours"
    if any(k in lowered for k in ["park", "garden", "公园", "植物园"]):
        return "2–4 hours"
    if any(k in lowered for k in ["tower", "observation", "塔", "观景"]):
        return "1–2 hours"
    return "1–2 hours"


def _pick_image_url(organic_results: list[dict[str, Any]], image_payload: dict[str, Any]) -> str:
    for item in organic_results:
        for key in ("image", "thumbnail"):
            val = _safe_text(item.get(key))
            if val.startswith("http"):
                return val

    for item in image_payload.get("images_results", []) or []:
        original = _safe_text(item.get("original"))
        thumb = _safe_text(item.get("thumbnail"))
        if original.startswith("http"):
            return original
        if thumb.startswith("http"):
            return thumb
    return ""


def _collect_sources(all_organic: list[dict[str, Any]], image_payload: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in all_organic:
        title = _safe_text(item.get("title"))
        link = _safe_text(item.get("link"))
        snippet = _safe_text(item.get("snippet"))
        key = f"{title}|{link}"
        if not (title or link or snippet) or key in seen:
            continue
        seen.add(key)
        sources.append({"title": title, "link": link, "snippet": snippet})
        if len(sources) >= 6:
            break

    if len(sources) < 3:
        for item in image_payload.get("images_results", []) or []:
            title = _safe_text(item.get("title"))
            link = _safe_text(item.get("link") or item.get("original"))
            snippet = _safe_text(item.get("source"))
            key = f"{title}|{link}"
            if not (title or link) or key in seen:
                continue
            seen.add(key)
            sources.append({"title": title, "link": link, "snippet": snippet})
            if len(sources) >= 3:
                break

    return sources


def fetch_attraction_details(attraction_name: str, location: str | None = None) -> dict[str, Any]:
    """
    Fetch attraction details from SerpAPI Google search.

    Args:
        attraction_name: Attraction name.
        location: Optional location context.

    Returns:
        {
          "name": "<attraction_name>",
          "image_url": "",
          "opening_hours": "",
          "visit_duration": "",
          "ticket_price": "",
          "estimated": {
              "opening_hours": bool,
              "visit_duration": bool,
              "ticket_price": bool
          },
          "sources": [
              {"title": "", "link": "", "snippet": ""}
          ]
        }
    """
    name = _safe_text(attraction_name)
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()

    result: dict[str, Any] = {
        "name": name,
        "image_url": "",
        "opening_hours": "",
        "visit_duration": "",
        "ticket_price": "",
        "estimated": {
            "opening_hours": True,
            "visit_duration": True,
            "ticket_price": True,
        },
        "sources": [],
    }

    if not name or not api_key:
        # No crash path: return best-effort defaults.
        if name:
            result["visit_duration"] = _estimate_duration(name)
        return result

    suffix = f" {location.strip()}" if location else ""

    queries = [
        f"{name}{suffix} opening hours",
        f"{name}{suffix} ticket price",
        f"{name}{suffix} admission fee",
        f"{name}{suffix} how long to spend",
        f"{name}{suffix} visit duration",
        f"{name}{suffix} official website",
    ]

    all_organic: list[dict[str, Any]] = []
    text_pool: list[str] = []

    for q in queries:
        try:
            payload = _serp_google(q, api_key)
        except Exception:
            continue

        kg = payload.get("knowledge_graph", {})
        ab = payload.get("answer_box", {})
        organic = payload.get("organic_results", []) or []

        text_pool.append(_flatten_text(kg))
        text_pool.append(_flatten_text(ab))
        text_pool.append(_flatten_text(payload.get("local_results")))

        for item in organic:
            text_pool.append(_flatten_text(item.get("snippet")))

        all_organic.extend(organic)

    image_payload: dict[str, Any] = {}
    try:
        image_payload = _serp_google_images(f"{name}{suffix}", api_key)
    except Exception:
        image_payload = {}

    merged_text = "\n".join(t for t in text_pool if t)

    # opening_hours
    opening = _extract_opening_hours(merged_text)
    if opening:
        result["opening_hours"] = opening
        result["estimated"]["opening_hours"] = False

    # ticket_price
    ticket = _extract_ticket_price(merged_text)
    if ticket:
        result["ticket_price"] = ticket
        result["estimated"]["ticket_price"] = False

    # visit_duration
    duration = _extract_visit_duration(merged_text)
    if duration:
        result["visit_duration"] = duration
        result["estimated"]["visit_duration"] = False
    else:
        result["visit_duration"] = _estimate_duration(name)
        result["estimated"]["visit_duration"] = True

    # image_url
    result["image_url"] = _pick_image_url(all_organic, image_payload)

    # sources (at least 3 best-effort)
    result["sources"] = _collect_sources(all_organic, image_payload)

    return result


if __name__ == "__main__":
    # Simple self-check (best effort; will return defaults when key is missing)
    sample = fetch_attraction_details("Petronas Twin Towers", "Kuala Lumpur")
    print(sample)
