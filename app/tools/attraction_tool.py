import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from filelock import FileLock
from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from serpapi import GoogleSearch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.tools.currency import EXCHANGE_TO_MYR as _FIXED_EXCHANGE_RATES

_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "attraction_cache.json"
_CITY_ATTRACTION_SEEDS_PATH = Path(__file__).resolve().parents[1] / "data" / "city_attraction_seeds.json"

# `_CACHE_LOCK` only serializes threads within this process; `_CACHE_FILE_LOCK` is the
# real guard against concurrent `uvicorn --workers N` processes (or a script run
# alongside the server) reading, mutating, and writing back a stale full copy of the
# cache and losing each other's updates.
_CACHE_LOCK = threading.Lock()
_CACHE_FILE_LOCK = FileLock(str(_CACHE_PATH) + ".lock", timeout=10)
_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # attraction hours/prices go stale; 30 days

_EXACT_PRICE_PATTERNS = [
    r"(?:RM|MYR)\s+\d+(?:[\.,]\d{1,2})?",
    r"USD\s+\d+(?:[\.,]\d{1,2})?",
    r"\$\s?\d+(?:[\.,]\d{1,2})?",
    r"(?:THB|฿)\s?\d+(?:[\.,]\d{1,2})?",
    r"(?:CNY|RMB)\s+\d+(?:[\.,]\d{1,2})?",
    r"(?:CNY|RMB)\s*\d+(?:[\.,]\d{1,2})?\s*元?",
    r"[¥]\s?\d+(?:[\.,]\d{1,2})?",
    r"\d+(?:[\.,]\d{1,2})?\s*元",
]
_FROM_PRICE_PATTERNS = [
    r"(?:from|starting\s+from|adult\s+ticket)\s*(?:at\s*)?(?:RM|MYR|USD|THB|฿|CNY|RMB|¥)\s?\d+(?:[\.,]\d{1,2})?",
    r"(?:成人票|起价|起)\s*(?:RM|MYR|USD|THB|฿|CNY|RMB|¥)?\s?\d+(?:[\.,]\d{1,2})?",
]
_RANGE_PRICE_PATTERNS = [
    r"(?:RM|MYR|USD|THB|฿|CNY|RMB|¥)\s?\d+(?:[\.,]\d{1,2})?\s?(?:-|–|to|~|～)\s?(?:RM|MYR|USD|THB|฿|CNY|RMB|¥)?\s?\d+(?:[\.,]\d{1,2})?",
    r"\d+(?:[\.,]\d{1,2})?\s?(?:-|–|to|~|～)\s?\d+(?:[\.,]\d{1,2})?\s?(?:元|RMB|CNY|¥|RM|MYR|USD|THB|฿)",
]
_FREE_PRICE_PATTERNS = [r"\bfree\b", r"free\s+entry", r"possibly\s+free", r"免票", r"免费"]

_PLATFORM_PRIORITIES = {
    "official": 100,
    "wikipedia": 90,
    "tripadvisor": 85,
    "trip": 80,
    "klook": 78,
    "travel_guide": 72,
    "google_maps": 70,
    "kkday": 65,
    "ctrip": 65,
    "other": 20,
}

def _debug_enabled() -> bool:
    return os.getenv("ATTRACTION_TOOL_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _debug_log(message: str) -> None:
    if _debug_enabled():
        print(f"[attraction_tool][debug] {message}", file=sys.stderr)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


_ATTRACTION_ALIAS_OVERRIDES: dict[str, list[str]] = {
    "forbidden city": ["the palace museum", "palace museum", "故宫", "故宫博物院", "紫禁城"],
    "the palace museum": ["forbidden city", "故宫", "故宫博物院", "紫禁城"],
    "petronas twin towers": [
        "petronas towers",
        "klcc twin towers",
        "menara berkembar petronas",
        "双子塔",
        "雙子塔",
        "国油双峰塔",
        "國油雙峰塔",
        "吉隆坡双子塔",
        "吉隆坡雙子塔",
    ],
    "双子塔": [
        "petronas twin towers",
        "petronas towers",
        "klcc twin towers",
        "menara berkembar petronas",
    ],
    "雙子塔": [
        "petronas twin towers",
        "petronas towers",
        "klcc twin towers",
        "menara berkembar petronas",
    ],
    "temple of heaven": ["temple of heaven park", "天坛", "天坛公园"],
    "summer palace": ["颐和园"],
    "mutianyu great wall": ["长城", "慕田峪长城", "mutianyu"],
    "badaling great wall": ["长城", "八达岭长城", "badaling"],
    "beijing ancient observatory": ["北京古观象台", "古观象台", "ancient observatory"],
    "北京古观象台": ["beijing ancient observatory", "古观象台", "ancient observatory"],
    "the sanctuary of truth": ["sanctuary of truth"],
    "sanctuary of truth": ["the sanctuary of truth"],
}

def _load_city_attraction_seeds() -> dict[str, list[str]]:
    try:
        payload = json.loads(_CITY_ATTRACTION_SEEDS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    normalized: dict[str, list[str]] = {}
    for city, attractions in payload.items():
        city_key = _normalize_text(city).lower()
        if not city_key or not isinstance(attractions, list):
            continue
        cleaned_attractions = [_normalize_text(item) for item in attractions if _normalize_text(item)]
        if cleaned_attractions:
            normalized[city_key] = cleaned_attractions
    return normalized


CITY_ATTRACTION_SEEDS: dict[str, list[str]] = _load_city_attraction_seeds()

_CITY_ICONIC_ATTRACTIONS: dict[str, list[str]] = {}

_CITY_PLACE_ALIASES: dict[str, str] = {
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


def _normalize_match_text(value: Any) -> str:
    text = _normalize_text(value).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _canonicalize_place_name(place: str) -> str:
    text = _normalize_text(place)
    if not text:
        return ""
    return _CITY_PLACE_ALIASES.get(text.lower(), _CITY_PLACE_ALIASES.get(text, text))


def _build_attraction_aliases(attraction_name: str, aliases: list[str] | None = None) -> list[str]:
    raw_values = [attraction_name, *(aliases or [])]
    seed = _normalize_match_text(attraction_name)
    if seed in _ATTRACTION_ALIAS_OVERRIDES:
        raw_values.extend(_ATTRACTION_ALIAS_OVERRIDES[seed])

    expanded: list[str] = []
    for value in raw_values:
        normalized = _normalize_match_text(value)
        if not normalized:
            continue
        expanded.append(normalized)
        if " " in normalized:
            expanded.append(normalized.split(" (")[0].strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in expanded:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


_CITY_ICONIC_ATTRACTIONS = {
    city: sorted(
        {
            alias
            for seed in seed_names
            for alias in _build_attraction_aliases(seed)
        }
    )
    for city, seed_names in CITY_ATTRACTION_SEEDS.items()
}


def _preferred_lookup_name(attraction_name: str, aliases: list[str] | None = None) -> str:
    raw_aliases = [attraction_name, *(aliases or [])]
    seed = _normalize_match_text(attraction_name)
    if seed in _ATTRACTION_ALIAS_OVERRIDES:
        raw_aliases.extend(_ATTRACTION_ALIAS_OVERRIDES[seed])

    for value in raw_aliases:
        text = _normalize_text(value)
        if re.search(r"[A-Za-z]", text):
            return text
    return _normalize_text(attraction_name)


def is_source_relevant_to_attraction(
    attraction_name: str,
    source_title: str,
    source_snippet: str,
    page_text: str | None = None,
    aliases: list[str] | None = None,
) -> bool:
    alias_values = _build_attraction_aliases(attraction_name, aliases=aliases)
    if not alias_values:
        return False

    title_text = _normalize_match_text(source_title)
    snippet_text = _normalize_match_text(source_snippet)
    page_match_text = _normalize_match_text(page_text) if page_text else ""
    combined_text = " ".join(part for part in [title_text, snippet_text, page_match_text] if part).strip()

    def _contains_alias(text: str) -> bool:
        if not text:
            return False
        for alias in alias_values:
            if alias in text:
                return True
        return False

    if _contains_alias(title_text) or _contains_alias(snippet_text):
        return True

    if page_match_text:
        compact_aliases = [alias for alias in alias_values if len(alias) >= 4]
        for alias in compact_aliases:
            if page_match_text.count(alias) >= 1:
                return True

    other_aliases: set[str] = set()
    target_alias_set = set(alias_values)
    for base_name, alias_list in _ATTRACTION_ALIAS_OVERRIDES.items():
        normalized_base = _normalize_match_text(base_name)
        if normalized_base not in target_alias_set:
            other_aliases.add(normalized_base)
        for alias in alias_list:
            normalized_alias = _normalize_match_text(alias)
            if normalized_alias and normalized_alias not in target_alias_set:
                other_aliases.add(normalized_alias)

    if combined_text and any(alias in combined_text for alias in other_aliases if len(alias) >= 4):
        return False

    return True


def _load_cache() -> dict[str, dict[str, Any]]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file in the same directory, then swap it into
    # place with os.replace() (atomic on both POSIX and Windows). Writing directly to
    # _CACHE_PATH risks a truncated, unparseable file if the process is killed
    # mid-write -- which _load_cache()'s broad except would then silently treat as an
    # empty cache, discarding every previously cached entry.
    tmp_path = _CACHE_PATH.with_name(f"{_CACHE_PATH.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, _CACHE_PATH)


def _cache_entry_fresh(entry: dict[str, Any]) -> bool:
    cached_at = entry.get("_cached_at")
    if not isinstance(cached_at, (int, float)):
        # Entries written before TTL support existed have no timestamp; treat them as
        # stale so they get refreshed once rather than trusted as valid forever.
        return False
    return (time.time() - cached_at) < _CACHE_TTL_SECONDS


def _contains_price_or_ticket_tokens(text: str) -> bool:
    value = _normalize_text(text)
    if not value:
        return False
    return bool(
        re.search(
            r"\bRM\b|\bMYR\b|\$\s?\d|\bAdult\b|\bChild\b|\bTicket\b|\bAdmission\b|per\s+adult|\bprice\b|\bpackage\b|门票|票价|收费",
            value,
            re.IGNORECASE,
        )
    )


def _contains_non_business_hours_tokens(text: str) -> bool:
    value = _normalize_text(text)
    if not value:
        return False
    return bool(
        re.search(
            r"address|street|jalan|road|avenue|lot\s*\d+|program|show|feeding|otter|exhibit|session|performance|schedule|itinerary|\|",
            value,
            re.IGNORECASE,
        )
    )


def _has_business_hours_label(text: str) -> bool:
    value = _normalize_text(text)
    if not value:
        return False
    return bool(
        re.search(
            r"opening\s*hours|operating\s*hours|business\s*hours|open\s*daily|daily\s*:",
            value,
            re.IGNORECASE,
        )
    )


def _has_strict_time_range(text: str) -> bool:
    value = _normalize_text(text)
    if not value:
        return False
    twelve_hour = r"(?:0?[1-9]|1[0-2])(?::[0-5]\d)?\s?(?:AM|PM|am|pm)"
    twenty_four = r"(?:[01]?\d|2[0-3]):[0-5]\d"
    pattern = rf"(?:{twelve_hour}|{twenty_four})\s*(?:-|–|to|至|~)\s*(?:{twelve_hour}|{twenty_four})"
    return bool(re.search(pattern, value))


def _extract_first_time_range(text: str) -> str:
    value = _normalize_text(text)
    if not value:
        return ""
    twelve_hour = r"(?:0?[1-9]|1[0-2])(?::[0-5]\d)?\s?(?:AM|PM|am|pm)"
    twenty_four = r"(?:[01]?\d|2[0-3]):[0-5]\d"
    pattern = rf"(?:{twelve_hour}|{twenty_four})\s*(?:-|–|to|至|~)\s*(?:{twelve_hour}|{twenty_four})"
    match = re.search(pattern, value)
    return _normalize_text(match.group(0)) if match else ""


def is_valid_opening_hours(text: str) -> bool:
    if not text:
        return False

    value = text.strip()
    if len(value) < 4 or len(value) > 120:
        return False

    blocked_patterns = [r"\?q=", r"&sa=", r"&ved=", r"http", r"https", r"<[^>]+>"]
    if any(re.search(pat, value, re.IGNORECASE) for pat in blocked_patterns):
        return False

    if any(token in value for token in ['{"', '"}', '":', '",']):
        return False

    if _contains_price_or_ticket_tokens(value):
        return False

    if re.search(r"[A-Za-z0-9_-]{30,}", value):
        return False

    has_time = bool(re.search(r"\b\d{1,2}:\d{2}\b|\b(?:0?[1-9]|1[0-2])\s?(?:AM|PM|am|pm)\b", value, re.IGNORECASE))
    has_time_range = _has_strict_time_range(value)
    has_day_or_month = bool(
        re.search(
            r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun|January|February|March|April|May|June|July|August|September|October|November|December",
            value,
            re.IGNORECASE,
        )
    )
    has_hours_keyword = bool(re.search(r"opening\s*hours|operating\s*hours|business\s*hours|open\s*daily|daily\s*:|营业时间|开放时间", value, re.IGNORECASE))

    if re.search(r"\b\d+\s*(?:to|-)\s*\d+\s*hours?\b", value, re.IGNORECASE):
        return False
    if _contains_non_business_hours_tokens(value):
        return False
    if re.search(r"(?:^|\s)00(?::00)?\s*am\b", value, re.IGNORECASE):
        return False

    if has_time_range and not _contains_non_business_hours_tokens(value):
        return True
    if has_hours_keyword and has_time_range:
        return True
    if has_time and has_hours_keyword:
        return True
    if has_day_or_month and has_time_range:
        return True
    return False


def clean_opening_hours(text: str) -> str:
    if not text:
        return ""

    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"[?&](?:q|ved|sa|usg|ei|oq|aqs)=[^\s]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)

    chunks = re.split(r"[\n;。]+", cleaned)
    for chunk in chunks:
        candidate = " ".join(chunk.split()).strip(" -|,。\t")
        if _has_business_hours_label(candidate):
            range_only = _extract_first_time_range(candidate)
            if range_only and is_valid_opening_hours(range_only):
                return range_only
        if is_valid_opening_hours(candidate):
            return candidate
    return ""


def _extract_hours_from_sources(sources: list[dict[str, str]]) -> str:
    for src in sources:
        snippet = _normalize_text(src.get("snippet"))
        title = _normalize_text(src.get("title"))
        merged = f"{title}. {snippet}"
        if _contains_price_or_ticket_tokens(merged):
            continue
        source_type, score = _classify_source_type(title=title, link=_normalize_text(src.get("link")), snippet=snippet)
        if source_type not in {"official_ticket_page", "official_visitor_info", "official_faq", "official_homepage"}:
            continue
        if score > 35:
            continue
        if not _has_business_hours_label(merged) and not re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", merged, re.IGNORECASE):
            continue
        candidate = clean_opening_hours(merged)
        if candidate:
            return candidate
    return ""


def _extract_hours(text: str) -> str:
    if not text:
        return ""

    patterns = [
        r"(?:opening\s*hours|operating\s*hours|business\s*hours|open\s*daily|daily\s*:|营业时间|开放时间)[:：]?\s*[^\n\.;]{4,120}",
        r"(?:\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b[^\n]{0,120})(?:[01]?\d:[0-5]\d|(?:0?[1-9]|1[0-2])(?::[0-5]\d)?\s?(?:AM|PM|am|pm))\s?(?:-|–|to|至|~)\s?(?:[01]?\d:[0-5]\d|(?:0?[1-9]|1[0-2])(?::[0-5]\d)?\s?(?:AM|PM|am|pm))",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = clean_opening_hours(match.group(0))
            if candidate:
                return candidate
    return ""


def _extract_high_confidence_opening_hours_from_sources(sources: list[dict[str, str]]) -> str:
    for src in sources:
        title = _normalize_text(src.get("title"))
        link = _normalize_text(src.get("link"))
        snippet = _normalize_text(src.get("snippet"))
        source_type, score = _classify_source_type(title=title, link=link, snippet=snippet)
        if source_type not in {"official_ticket_page", "official_visitor_info", "official_faq", "official_homepage", "official_visitor_guide_pdf"}:
            continue
        if score > 35:
            continue

        page_text = _fetch_url_text(link)
        _debug_log(f"fetched_page_text_length url={link} length={len(page_text)}")
        if len(page_text) < 100:
            continue

        candidate = _extract_hours(page_text)
        if candidate:
            return candidate

    return ""


def _detect_attraction_type(name: str, text: str = "") -> str:
    corpus = f"{name} {text}".lower()
    rules = [
        ("theme_park", ["theme park", "amusement", "游乐园", "主题公园"]),
        ("museum", ["museum", "gallery", "博物馆", "美术馆"]),
        ("zoo_aquarium", ["zoo", "aquarium", "海洋馆", "动物园"]),
        ("shopping_old_town", ["old town", "shopping street", "古镇", "步行街", "老街"]),
        ("tower", ["tower", "observation", "塔", "观景"]),
        ("temple", ["temple", "church", "mosque", "寺", "教堂", "清真寺"]),
        ("park", ["park", "garden", "植物园", "公园"]),
        ("palace_historic", ["palace", "historic", "fort", "古迹", "宫殿", "遗址"]),
        ("monument", ["monument", "memorial", "statue", "纪念碑", "雕像"]),
    ]
    for attraction_type, keywords in rules:
        if any(keyword in corpus for keyword in keywords):
            return attraction_type
    return "generic"


def estimate_ticket_price(attraction_name: str, context_text: str = "") -> str | None:
    _ = attraction_name
    _ = context_text
    return None


def estimate_visit_duration(attraction_name: str, context_text: str = "") -> str:
    attraction_type = _detect_attraction_type(attraction_name, context_text)
    estimates = {
        "theme_park": "4-6 hours (estimated)",
        "museum": "2-3 hours (estimated)",
        "park": "2-4 hours (estimated)",
        "tower": "1-2 hours (estimated)",
        "temple": "1-2 hours (estimated)",
        "monument": "1 hour (estimated)",
        "zoo_aquarium": "2-4 hours (estimated)",
        "shopping_old_town": "2-3 hours (estimated)",
        "generic": "2 hours (estimated)",
    }
    return estimates.get(attraction_type, estimates["generic"])


def _is_valid_visit_duration_output(value: Any) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    if len(text) > 80:
        return False
    if re.search(r"https?://|[?&](?:q|ved|sa)=|<[^>]+>|[{}[\]\"]", text, re.IGNORECASE):
        return False
    if re.search(r"\b20\d{2}\b", text) and not re.search(r"\b20\d{2}\s*minutes?\b", text, re.IGNORECASE):
        return False

    normalized = " ".join(text.split())
    if "(estimated)" in normalized.lower():
        estimated_base = normalized[: normalized.lower().find("(estimated)")].strip(" -–~")
        return _is_valid_visit_duration_output(estimated_base)

    patterns = [
        r"^\d+(?:\.\d+)?\s*(?:-|–|to|~)\s*\d+(?:\.\d+)?\s*(?:hours?|hrs?|小时)$",
        r"^\d+(?:\.\d+)?\s*(?:hours?|hrs?|小时)$",
        r"^\d+\s*(?:-|–|to|~)\s*\d+\s*(?:minutes?|mins?|分钟)$",
        r"^\d+\s*(?:minutes?|mins?|分钟)$",
    ]
    return any(re.fullmatch(pattern, normalized, re.IGNORECASE) for pattern in patterns)


def _format_duration_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def normalize_visit_duration(value: Any, attraction_name: str = "", context_text: str = "") -> str:
    raw = _normalize_text(value)
    fallback = estimate_visit_duration(attraction_name, context_text)
    if not raw:
        return fallback

    cleaned = re.sub(r"https?://\S+", " ", raw)
    cleaned = re.sub(r"[?&](?:q|ved|sa|usg|ei|oq|aqs)=[^\s]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("\n", " ")
    cleaned = re.sub(
        r"(recommended\s*time|how\s*long\s*to\s*spend|visit\s*duration|suggested\s*duration|建议游玩时长|建議遊玩時長)[:：-]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip(" -|,.;。")
    estimated_suffix = " (estimated)" if "estimated" in raw.lower() else ""

    range_patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:-|–|to|~)\s*(\d+(?:\.\d+)?)\s*(hours?|hrs?|小时)", "hours"),
        (r"(\d+)\s*(?:-|–|to|~)\s*(\d+)\s*(minutes?|mins?|分钟)", "minutes"),
    ]
    for pattern, unit in range_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        start = float(match.group(1))
        end = float(match.group(2))
        normalized = f"{_format_duration_number(start)}-{_format_duration_number(end)} {unit}"
        if _is_valid_visit_duration_output(normalized):
            return f"{normalized}{estimated_suffix}".strip()

    single_patterns = [
        (r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|小时)", "hour", "hours"),
        (r"(\d+)\s*(minutes?|mins?|分钟)", "minute", "minutes"),
    ]
    for pattern, singular, plural in single_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        amount = float(match.group(1))
        unit = singular if amount == 1 else plural
        normalized = f"{_format_duration_number(amount)} {unit}"
        if _is_valid_visit_duration_output(normalized):
            return f"{normalized}{estimated_suffix}".strip()

    if _is_valid_visit_duration_output(cleaned):
        return cleaned
    return fallback


def _extract_numbers(value: str) -> list[float]:
    nums = re.findall(r"\d+(?:[\.,]\d+)?", value)
    return [float(n.replace(",", "")) for n in nums]


def convert_to_myr(amount: float, currency: str) -> float | None:
    normalized_currency = currency.upper().strip()
    rate = _FIXED_EXCHANGE_RATES.get(normalized_currency)
    if rate is None:
        return None
    return round(float(amount) * rate, 2)


def _format_myr_amount(amount: float) -> str:
    return f"RM {amount:.2f}"


def _format_rm_clean(amount: float) -> str:
    rounded = round(float(amount), 2)
    if float(rounded).is_integer():
        return f"RM {int(rounded)}"
    return f"RM {rounded:.2f}"


def _normalize_currency(value: str) -> str:
    upper = value.upper()
    if "MYR" in upper or re.search(r"\bRM\b", upper):
        return "MYR"
    if "$" in value:
        return "USD"
    if "USD" in upper:
        return "USD"
    if "THB" in upper or "฿" in value:
        return "THB"
    if "CNY" in upper or "RMB" in upper:
        return "CNY"
    if "元" in value:
        return "CNY"
    if "EUR" in upper:
        return "EUR"
    if "SGD" in upper:
        return "SGD"
    if "JPY" in upper:
        return "JPY"
    if "GBP" in upper:
        return "GBP"
    if "¥" in value:
        return "JPY"
    return ""


def _is_reliable_source_type(source_type: str) -> bool:
    lowered = _normalize_text(source_type).lower()
    return lowered == "government" or lowered.startswith("official")


_TICKET_PRICE_JUDGE_PROMPT = """
You are a strict attraction ticket-price judge.
Use ONLY the provided candidate pool and source snippets.
Do NOT browse the web.
Do NOT invent facts.
Do NOT choose a number just because it is the only number.

Your task:
1. Review all ticket-price candidates.
2. Prefer the candidate that most likely represents the MAIN attraction admission price.
3. Penalize package prices, tours, bundles, transport fares, parking fees, rentals, guide fees, dining prices, or sub-attraction prices.
4. Prefer candidates whose context mentions words like ticket, admission, entry, adult, visitor, standard, general admission.
5. Prefer official/government sources over OTA/platform sources, and platform sources over weak/blog sources.
6. If multiple candidates agree, treat that as stronger evidence.
7. Preserve the ORIGINAL currency if you select a non-free price.
8. If uncertain, return an empty ticket_price.

Return JSON only in this exact shape:
{"ticket_price":"","price_type":"official|platform|weak|free|range|unknown","price_note":"","selected_candidate_index":-1,"reason":""}
""".strip()


def resolve_ticket_price(price_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    converted: list[dict[str, Any]] = []
    for candidate in price_candidates:
        amount = candidate.get("value")
        currency = _normalize_currency(_normalize_text(candidate.get("currency")))
        source_type = _normalize_text(candidate.get("source_type")).lower() or "third_party"
        if _platform_bucket(source_type) == "official":
            source_type = "official"
        elif _platform_bucket(source_type) == "platform":
            source_type = "third_party"
        else:
            source_type = "third_party"
        url = _normalize_text(candidate.get("url"))
        if amount is None or not currency:
            continue
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError):
            continue

        myr_value = convert_to_myr(numeric_amount, currency)
        if myr_value is None:
            continue

        converted.append(
            {
                "value_myr": round(myr_value, 2),
                "source_type": source_type,
                "url": url,
            }
        )

    reliable_values = sorted(
        item["value_myr"] for item in converted if _is_reliable_source_type(item["source_type"])
    )
    if reliable_values:
        unique_values = sorted({round(v, 2) for v in reliable_values})
        if len(unique_values) == 1:
            return {
                "ticket_price": _format_rm_clean(unique_values[0]),
                "price_type": "exact",
                "price_note": "Official or government source",
            }
        return {
            "ticket_price": f"{_format_rm_clean(unique_values[0])}–{_format_rm_clean(unique_values[-1])}",
            "price_type": "range",
            "price_note": "Conflicting official/government prices",
        }

    third_party_values = sorted(item["value_myr"] for item in converted if item["source_type"] == "third_party")
    unique_third_party = sorted({round(v, 2) for v in third_party_values})
    if len(unique_third_party) > 1:
        return {
            "ticket_price": f"{_format_rm_clean(unique_third_party[0])}–{_format_rm_clean(unique_third_party[-1])}",
            "price_type": "range",
            "price_note": "Range derived from multiple non-official sources",
        }
    if len(unique_third_party) == 1:
        return {
            "ticket_price": _format_rm_clean(unique_third_party[0]),
            "price_type": "exact",
            "price_note": "Single non-official source",
        }

    return {
        "ticket_price": None,
        "price_type": "unknown",
        "price_note": "Official price not found.",
    }


def _extract_price_context(text: str, start: int, end: int, window: int = 80) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return snippet[:220]


def _platform_bucket(source_type: str) -> str:
    lowered = _normalize_text(source_type).lower()
    if lowered.startswith("official") or lowered in {"official", "government"}:
        return "official"
    if any(token in lowered for token in ["ota", "klook", "trip", "kkday", "platform"]):
        return "platform"
    return "weak"


def _score_ticket_candidate(candidate: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    context = _normalize_text(candidate.get("context")).lower()
    source_type = _normalize_text(candidate.get("source_type"))
    bucket = _platform_bucket(source_type)

    if bucket == "official":
        score += 50
        reasons.append("official/government source")
    elif bucket == "platform":
        score += 15
        reasons.append("platform source")
    else:
        score -= 10
        reasons.append("weak/non-official source")

    if any(token in context for token in ["ticket", "admission", "entry", "visitor", "general admission", "standard"]):
        score += 20
        reasons.append("ticket/admission wording")
    if any(token in context for token in ["adult", "成人"]):
        score += 10
        reasons.append("adult ticket wording")
    if any(token in context for token in ["child", "children", "学生", "senior"]):
        score += 3
        reasons.append("structured pricing wording")

    if any(token in context for token in ["parking", "car park", "locker", "rental", "guide fee"]):
        score -= 30
        reasons.append("non-admission fee wording")
    if any(token in context for token in ["tour", "package", "combo", "bundle", "transfer", "pickup", "add-on", "addon"]):
        score -= 35
        reasons.append("package/tour wording")
    if any(token in context for token in ["start from", "starting from", "prices vary", "vary by package", "from rm", "from usd", "from thb"]):
        score -= 35
        reasons.append("uncertain starting price wording")
    if any(token in context for token in ["blog", "itinerary", "travel guide"]):
        score -= 20
        reasons.append("weak editorial context")

    return score, reasons


def _extract_price_candidates(
    text: str,
    source_type: str,
    url: str,
    title: str = "",
    source_label: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    patterns = [*_EXACT_PRICE_PATTERNS, *_FROM_PRICE_PATTERNS, *_RANGE_PRICE_PATTERNS]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = match.group(0).strip()
            currency = _normalize_currency(value)
            numbers = _extract_numbers(value)
            if not currency or not numbers:
                continue
            for number in numbers[:2]:
                context = _extract_price_context(text, match.start(), match.end())
                score, reasons = _score_ticket_candidate(
                    {
                        "context": context,
                        "source_type": source_type,
                    }
                )
                candidates.append(
                    {
                        "value": number,
                        "currency": currency,
                        "raw_price_text": value,
                        "context": context,
                        "title": title,
                        "source": source_label or source_type,
                        "source_type": source_type,
                        "source_bucket": _platform_bucket(source_type),
                        "score": score,
                        "score_reasons": reasons,
                        "url": url,
                    }
                )
    return candidates


def _dedupe_ticket_price_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in sorted(candidates, key=lambda item: int(item.get("score", 0)), reverse=True):
        key = (
            _normalize_text(candidate.get("raw_price_text")).lower(),
            _normalize_text(candidate.get("url")).lower(),
            _normalize_text(candidate.get("context")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def build_ticket_price_candidate_pool(
    sources: list[dict[str, str]],
    attraction_name: str = "",
    aliases: list[str] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source in sources[:8]:
        title = _normalize_text(source.get("title"))
        link = _normalize_text(source.get("link"))
        snippet = _normalize_text(source.get("snippet"))
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue

        source_type, _ = _classify_source_type(title=title, link=link, snippet=snippet)
        combined = f"{title}\n{snippet}".strip()
        if combined:
            candidates.extend(
                _extract_price_candidates(
                    combined,
                    source_type=source_type,
                    url=link,
                    title=title,
                    source_label=source_type,
                )
            )

        page_text = _fetch_url_text(link)
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            page_text=page_text,
            aliases=aliases,
        ):
            continue
        if page_text:
            candidates.extend(
                _extract_price_candidates(
                    page_text,
                    source_type=source_type,
                    url=link,
                    title=title,
                    source_label=source_type,
                )
            )

    return _dedupe_ticket_price_candidates(candidates)


def _infer_source_type(link: str) -> str:
    lowered = link.lower()
    if ".gov" in lowered or "tourism" in lowered and ".gov" in lowered:
        return "government"
    if any(token in lowered for token in ["official", "visit", "tickets", "admission"]):
        return "official"
    return "third_party"


def extract_ticket_price(text: str, attraction_name: str = "") -> dict[str, Any]:
    _ = attraction_name
    normalized = re.sub(r"https?://\S+", " ", text)
    normalized = re.sub(r"[?&](?:q|ved|sa|usg|ei|oq|aqs)=[^\s]+", " ", normalized, flags=re.IGNORECASE)
    candidates = _extract_price_candidates(normalized, source_type="third_party", url="")
    return resolve_ticket_price(candidates)


def convert_price_to_myr(text: str) -> str:
    candidates = _extract_price_candidates(text, source_type="third_party", url="")
    resolved = resolve_ticket_price(candidates)
    return _normalize_text(resolved.get("ticket_price"))


def normalize_ticket_price(text: str) -> str:
    return convert_price_to_myr(text)


def _is_valid_price_text(value: str) -> bool:
    if not value:
        return False
    value = value.strip()
    if len(value) < 6 or len(value) > 80:
        return False
    if re.search(r"[?&](?:q|ved|sa)=", value, re.IGNORECASE):
        return False
    if not re.search(r"\d", value):
        return False

    has_currency = re.search(r"(?:\bRM\b|\bMYR\b|\bUSD\b|\bTHB\b|\bCNY\b|\bRMB\b|\bEUR\b|\bSGD\b|\bJPY\b|\bGBP\b|¥|฿)", value, re.IGNORECASE)
    return bool(has_currency)


def extract_visit_duration(text: str, attraction_name: str = "") -> str:
    patterns = [
        r"\b\d+(?:\.\d+)?\s?(?:-|–|to|~)?\s?\d*(?:\.\d+)?\s?(?:hours?|hrs?|小时)\b",
        r"\b\d+\s?(?:minutes?|mins?|分钟)\b",
        r"(?:recommended\s*time|how\s*long\s*to\s*spend|visit\s*duration|建议游玩时长)[:：]?\s*[^\n\.;]{2,60}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_visit_duration(match.group(0), attraction_name=attraction_name, context_text=text)
    return normalize_visit_duration("", attraction_name=attraction_name, context_text=text)


def _seed_city_key(place: str) -> str:
    canonical_place = _canonicalize_place_name(place)
    lowered = _normalize_text(canonical_place).lower()
    if "george town" in lowered:
        return "george town"
    return lowered.split(",", 1)[0].strip()


def _get_city_seed_names(place: str) -> list[str]:
    city_key = _seed_city_key(place)
    seed_names = list(CITY_ATTRACTION_SEEDS.get(city_key, []))
    if city_key == "george town":
        seed_names.extend(CITY_ATTRACTION_SEEDS.get("penang", []))

    deduped: list[str] = []
    seen: set[str] = set()
    for seed in seed_names:
        normalized = _normalize_text(seed)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _build_seed_candidate(place: str, attraction_name: str) -> dict[str, Any]:
    description = ""
    image = ""
    ticket_price = ""

    try:
        from app.tools.tools import TRAVEL_ATTRACTION_CATALOG
    except Exception:
        TRAVEL_ATTRACTION_CATALOG = {}

    if isinstance(TRAVEL_ATTRACTION_CATALOG, dict):
        city_keys = [_seed_city_key(place)]
        if city_keys[0] == "george town":
            city_keys.append("penang")
        for city_key in city_keys:
            catalog_rows = TRAVEL_ATTRACTION_CATALOG.get(city_key, [])
            for row in catalog_rows if isinstance(catalog_rows, list) else []:
                if not isinstance(row, dict):
                    continue
                row_name = _normalize_text(row.get("name"))
                if _normalize_match_text(row_name) != _normalize_match_text(attraction_name):
                    continue
                description = _normalize_text(row.get("information"))
                image = _normalize_text(row.get("image"))
                price_value = row.get("price")
                currency = _normalize_text(row.get("currency"))
                if isinstance(price_value, (int, float)):
                    if float(price_value) <= 0:
                        ticket_price = "Free"
                    elif currency:
                        rendered_price = int(price_value) if float(price_value).is_integer() else round(float(price_value), 2)
                        ticket_price = f"{currency} {rendered_price}".strip()
                break
            if description or image or ticket_price:
                break

    return {
        "name": attraction_name,
        "description": description,
        "image": image,
        "ticket_price": ticket_price,
        "sources": [],
        "source_type": "offline_seed",
        "score": 0,
    }


def _recommendation_needs_seed_fallback(candidates: list[dict[str, Any]], place: str) -> bool:
    valid_candidates = [candidate for candidate in candidates if _is_valid_recommendation_entity(candidate, place)]
    if len(valid_candidates) < 5:
        return True

    ranked = sorted(valid_candidates, key=lambda item: int(item.get("score", 0)), reverse=True)
    top_ranked = ranked[:5]
    strong_candidates = sum(1 for item in top_ranked if int(item.get("score", 0)) >= 25)
    iconic_candidates = sum(1 for item in top_ranked if _has_city_iconic_match(_normalize_text(item.get("name")), place))
    descriptive_candidates = sum(
        1
        for item in top_ranked
        if _description_is_usable_for_recommendation(_normalize_text(item.get("description")))
    )
    return strong_candidates < 3 or iconic_candidates == 0 or descriptive_candidates < 2


def _inject_seed_recommendation_candidates(
    place: str,
    candidates: list[dict[str, Any]],
    seen_names: set[str],
) -> list[dict[str, Any]]:
    if not _get_city_seed_names(place):
        return candidates
    if not _recommendation_needs_seed_fallback(candidates, place):
        return candidates

    injected = list(candidates)
    existing_aliases = {
        alias
        for candidate in injected
        for alias in _build_attraction_aliases(_normalize_text(candidate.get("name")))
    }
    for seed_name in _get_city_seed_names(place):
        seed_key = seed_name.lower()
        seed_aliases = set(_build_attraction_aliases(seed_name))
        if seed_key in seen_names or (seed_aliases and seed_aliases & existing_aliases):
            continue
        seen_names.add(seed_key)
        seed_candidate = _build_seed_candidate(place=place, attraction_name=seed_name)
        seed_candidate["score"] = _recommendation_quality_score(seed_candidate, place)
        injected.append(seed_candidate)
        existing_aliases.update(seed_aliases)
    return injected


def _pick_image_url(results: list[dict[str, Any]], image_results: dict[str, Any]) -> str:
    for result in results:
        for key in ("thumbnail", "image", "favicon"):
            value = _normalize_text(result.get(key))
            if value.startswith("http"):
                return value

    for image in image_results.get("images_results", []):
        original = _normalize_text(image.get("original"))
        thumbnail = _normalize_text(image.get("thumbnail"))
        if original.startswith("http"):
            return original
        if thumbnail.startswith("http"):
            return thumbnail
    return ""


def _search_google(query: str, api_key: str, num: int = 10) -> dict[str, Any]:
    params = {"engine": "google", "q": query, "hl": "en", "num": num, "api_key": api_key}
    return GoogleSearch(params).get_dict()


def _search_google_images(query: str, api_key: str, num: int = 10) -> dict[str, Any]:
    params = {"engine": "google_images", "q": query, "hl": "en", "num": num, "api_key": api_key}
    return GoogleSearch(params).get_dict()


def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 8) -> dict[str, Any] | list[Any] | None:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return None

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def fetch_wikipedia_summary(attraction_name: str, location: str | None = None) -> dict[str, str]:
    query = attraction_name.strip()
    if location:
        query = f"{query}, {location.strip()}"

    title = urllib.parse.quote(query.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    data = _http_get_json(url, headers={"Accept": "application/json", "User-Agent": "ai-travel-assistant/1.0"})

    if not isinstance(data, dict) and location:
        fallback_title = urllib.parse.quote(attraction_name.strip().replace(" ", "_"))
        fallback_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{fallback_title}"
        data = _http_get_json(
            fallback_url,
            headers={"Accept": "application/json", "User-Agent": "ai-travel-assistant/1.0"},
        )

    if not isinstance(data, dict):
        return {"description": "", "image_url": "", "source_url": ""}

    page_url = _normalize_text(data.get("content_urls", {}).get("desktop", {}).get("page"))
    if not page_url:
        page_url = _normalize_text(data.get("content_urls", {}).get("mobile", {}).get("page"))

    return {
        "description": _normalize_text(data.get("extract")),
        "image_url": _normalize_text(data.get("thumbnail", {}).get("source")),
        "source_url": page_url,
    }


def fetch_nominatim_place(attraction_name: str, location: str | None = None) -> dict[str, Any]:
    query = f"{attraction_name} {location}".strip() if location else attraction_name.strip()
    if not query:
        return {}

    encoded_query = urllib.parse.quote(query)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&limit=1"
    data = _http_get_json(url, headers={"User-Agent": "ai-travel-assistant/1.0"})
    if not isinstance(data, list) or not data:
        return {}
    first = data[0] if isinstance(data[0], dict) else {}
    return {
        "display_name": _normalize_text(first.get("display_name")),
        "osm_url": f"https://www.openstreetmap.org/{_normalize_text(first.get('osm_type'))}/{_normalize_text(first.get('osm_id'))}"
        if first.get("osm_type") and first.get("osm_id")
        else "",
    }


def _collect_price_candidates_from_sources(
    sources: list[dict[str, str]],
    attraction_name: str = "",
    aliases: list[str] | None = None,
) -> list[dict[str, Any]]:
    return build_ticket_price_candidate_pool(sources=sources, attraction_name=attraction_name, aliases=aliases)


def _extract_domain(url: str) -> str:
    value = _normalize_text(url).lower()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    return parsed.netloc or ""


def _looks_like_ticket_source(title: str, link: str, snippet: str) -> bool:
    corpus = f"{title} {link} {snippet}".lower()
    wanted = [
        "ticket", "admission", "price", "pricing", "rate", "rates", "faq", "visitor", "entry fee", "门票", "票价", "收费", "guide", "facilities", ".pdf",
    ]
    return any(token in corpus for token in wanted)


def _classify_source_type(title: str, link: str, snippet: str) -> tuple[str, int]:
    corpus = f"{title} {link} {snippet}".lower()
    domain = _extract_domain(link)

    official_like = any(token in domain for token in [".gov", ".edu", "official", "tourism", "penanghill", "hillrailway", "mypenang"]) or "official" in corpus
    is_pdf = ".pdf" in corpus
    has_ticket_intent = any(token in corpus for token in ["ticket", "admission", "pricing", "rates", "entry fee", "fare"])
    has_hours_intent = any(token in corpus for token in ["opening hours", "operating hours", "business hours", "open daily"])
    has_faq_intent = "faq" in corpus
    has_visitor_intent = any(token in corpus for token in ["visitor information", "visitor info", "visit info", "facilities"])
    is_review = any(token in corpus for token in ["review", "reviews", "tripadvisor.com/attraction_review", "rating"])
    is_ota = any(token in corpus for token in ["klook", "trip.com", "kkday", "booking", "traveloka", "viator", "getyourguide"])

    if official_like and has_ticket_intent:
        return "official_ticket_page", 10
    if official_like and is_pdf:
        return "official_visitor_guide_pdf", 12
    if official_like and has_faq_intent:
        return "official_faq", 20
    if official_like and has_visitor_intent:
        return "official_visitor_info", 24
    if official_like and has_hours_intent:
        return "official_visitor_info", 24
    if official_like:
        return "official_homepage", 30
    if has_ticket_intent and not is_review:
        return "ota_product_page", 42
    if is_ota and has_ticket_intent:
        return "ota_product_page", 42
    if is_review:
        return "review_page", 55
    return "generic_attraction_page", 50


def _ticket_source_priority(title: str, link: str, snippet: str) -> int:
    _, score = _classify_source_type(title=title, link=link, snippet=snippet)
    return score


def _is_strong_ticket_source_type(source_type: str) -> bool:
    return source_type in {
        "official_ticket_page",
        "official_visitor_guide_pdf",
        "ota_product_page",
    }


def _has_strong_ticket_source_evidence(
    sources: list[dict[str, str]],
    attraction_name: str = "",
    aliases: list[str] | None = None,
) -> bool:
    for src in sources:
        title = _normalize_text(src.get("title"))
        link = _normalize_text(src.get("link"))
        snippet = _normalize_text(src.get("snippet"))
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue
        source_type, _ = _classify_source_type(title=title, link=link, snippet=snippet)
        if _is_strong_ticket_source_type(source_type):
            return True
    return False


def _extract_text_from_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _fetch_url_text(url: str, timeout: int = 10) -> str:
    if not url.startswith("http"):
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "ai-travel-assistant/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = _normalize_text(resp.headers.get("Content-Type")).lower()
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return ""

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        try:
            decoded = raw.decode("latin-1", errors="ignore")
        except Exception:
            return ""
        return " ".join(decoded.split())

    try:
        decoded = raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return _extract_text_from_html(decoded)


def _extract_strong_ticket_values(text: str, attraction_name: str = "") -> list[dict[str, Any]]:
    if not text:
        return []

    values: list[dict[str, Any]] = []
    lowered_text = text.lower()

    for m in re.finditer(r"(?:RM|MYR)\s*([0-9]+(?:\.[0-9]{1,2})?)", text, re.IGNORECASE):
        full = m.group(0)
        context = lowered_text[max(0, m.start() - 100) : m.end() + 100]

        if any(token in context for token in ["from", "starting", "start at", "package", "vary", "contact us", "from rm", "starting at"]):
            continue

        positive_score = 0
        if any(token in context for token in ["adult", "general", "admission", "entry", "standard", "normal"]):
            positive_score += 2
        if any(token in context for token in ["ticket", "price", "rate", "fare"]):
            positive_score += 1

        negative_score = 0
        if any(token in context for token in ["child", "children", "kid", "senior", "student", "foreigner", "non-malaysian"]):
            negative_score += 1
        if any(token in context for token in ["package", "combo", "add-on", "addon", "express lane", "bundle", "combo deal"]):
            negative_score += 2
        if any(token in context for token in ["the habitat", "habitat", "canopy walk", "funicular package", "sub-attraction", "exhibit"]):
            negative_score += 2

        if attraction_name and attraction_name.lower() not in lowered_text and "general admission" not in lowered_text:
            negative_score += 1

        if positive_score - negative_score <= 0:
            continue

        try:
            amount = float(m.group(1))
        except (TypeError, ValueError):
            continue
        values.append({"kind": "exact", "amount": amount, "raw": full, "score": positive_score - negative_score})

    for m in re.finditer(r"(?:RM|MYR)\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(?:-|–|to|~|～)\s*(?:RM|MYR)?\s*([0-9]+(?:\.[0-9]{1,2})?)", text, re.IGNORECASE):
        context = lowered_text[max(0, m.start() - 100) : m.end() + 100]
        if any(token in context for token in ["from", "starting", "package", "vary", "contact us", "starting at", "from rm"]):
            continue
        if any(token in context for token in ["the habitat", "habitat", "canopy walk", "add-on", "addon", "combo", "bundle"]):
            continue

        positive_score = 0
        if any(token in context for token in ["adult", "general", "admission", "entry", "standard", "normal lane"]):
            positive_score += 2
        if any(token in context for token in ["ticket", "price", "rate", "fare"]):
            positive_score += 1

        try:
            low = float(m.group(1))
            high = float(m.group(2))
        except (TypeError, ValueError):
            continue
        if low > high:
            low, high = high, low
        values.append({"kind": "range", "low": low, "high": high, "raw": m.group(0), "score": positive_score})

    for m in re.finditer(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)(?:\s*(?:-|–|to|~|～)\s*\$?\s*([0-9]+(?:\.[0-9]{1,2})?))?", text):
        context = lowered_text[max(0, m.start() - 100) : m.end() + 100]
        if any(token in context for token in ["from", "starting", "package", "vary", "contact us", "starting at", "from $"]):
            continue
        if any(token in context for token in ["the habitat", "habitat", "canopy walk", "add-on", "addon", "review", "rating"]):
            continue

        positive_score = 0
        if any(token in context for token in ["adult", "general", "admission", "entry", "standard", "ticket", "price", "rate", "fare"]):
            positive_score += 2
        if any(token in context for token in ["child", "children", "kid", "student", "senior"]):
            positive_score -= 1
        if positive_score < 1:
            continue

        try:
            low_usd = float(m.group(1))
            high_usd = float(m.group(2)) if m.group(2) else None
        except (TypeError, ValueError):
            continue

        low_myr = convert_to_myr(low_usd, "USD")
        if low_myr is None:
            continue
        if high_usd is None:
            values.append({"kind": "exact", "amount": low_myr, "raw": m.group(0), "score": positive_score})
        else:
            high_myr = convert_to_myr(high_usd, "USD")
            if high_myr is None:
                continue
            low, high = (low_myr, high_myr) if low_myr <= high_myr else (high_myr, low_myr)
            values.append({"kind": "range", "low": low, "high": high, "raw": m.group(0), "score": positive_score})

    if re.search(r"\bfree\b|免费|免票", text, re.IGNORECASE):
        values.append({"kind": "free", "raw": "Free", "score": 1})

    return values


def _pick_ticket_price_from_values(values: list[dict[str, Any]]) -> str:
    if not values:
        return ""

    reliable = [v for v in values if int(v.get("score", 0)) >= 1 or v.get("kind") == "free"]
    if not reliable:
        return ""

    if any(v.get("kind") == "free" for v in reliable):
        return "Free"

    exact_amounts = sorted({round(float(v["amount"]), 2) for v in reliable if v.get("kind") == "exact"})
    ranges = [v for v in reliable if v.get("kind") == "range"]

    if exact_amounts:
        if len(exact_amounts) == 1:
            return _format_rm_clean(exact_amounts[0])
        return f"{_format_rm_clean(exact_amounts[0])}–{_format_rm_clean(exact_amounts[-1])}"

    if ranges:
        low = min(float(v["low"]) for v in ranges)
        high = max(float(v["high"]) for v in ranges)
        return f"{_format_rm_clean(low)}–{_format_rm_clean(high)}"

    return ""


def resolve_ticket_price_from_sources(
    sources: list[dict[str, str]],
    attraction_name: str = "",
    aliases: list[str] | None = None,
) -> str:
    candidate_pool = build_ticket_price_candidate_pool(sources=sources, attraction_name=attraction_name, aliases=aliases)
    if candidate_pool:
        if max(int(candidate.get("score", 0)) for candidate in candidate_pool) <= 20:
            return ""
        resolved = resolve_ticket_price(candidate_pool)
        picked = _normalize_text(resolved.get("ticket_price"))
        if picked:
            return picked

    ranked = []
    for src in sources:
        title = _normalize_text(src.get("title"))
        link = _normalize_text(src.get("link"))
        snippet = _normalize_text(src.get("snippet"))
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue
        if not _looks_like_ticket_source(title, link, snippet):
            continue
        source_type, score = _classify_source_type(title=title, link=link, snippet=snippet)
        if source_type in {"review_page", "generic_attraction_page"}:
            continue
        ranked.append((score, source_type, title, link, snippet))

    ranked.sort(key=lambda x: x[0])
    all_values: list[dict[str, Any]] = []
    for _, source_type, title, link, snippet in ranked[:8]:
        combined = f"{title} {snippet}".strip()
        if source_type != "review_page":
            all_values.extend(_extract_strong_ticket_values(combined, attraction_name=attraction_name))

        page_text = _fetch_url_text(link)
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            page_text=page_text,
            aliases=aliases,
        ):
            continue
        if page_text and len(page_text) >= 20:
            all_values.extend(_extract_strong_ticket_values(page_text, attraction_name=attraction_name))
        elif source_type == "ota_product_page" and combined:
            all_values.extend(_extract_strong_ticket_values(combined, attraction_name=attraction_name))

        picked = _pick_ticket_price_from_values(all_values)
        if picked:
            return picked

    return _pick_ticket_price_from_values(all_values)


def _resolve_gemini_api_key() -> str:
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    return gemini_api_key or google_api_key


def _parse_gemini_ticket_payload(raw_text: str) -> dict[str, str]:
    text = _normalize_text(raw_text)
    if not text:
        return {"ticket_price": "", "price_type": "unknown", "price_note": ""}

    cleaned = text
    if "```" in cleaned:
        cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

    ticket_price = _normalize_text(payload.get("ticket_price"))
    price_type = _normalize_text(payload.get("price_type")).lower() or "unknown"
    price_note = _normalize_text(payload.get("price_note"))
    if price_type == "platform":
        price_type = "third_party"
    elif price_type == "weak":
        price_type = "third_party"

    if ticket_price and ticket_price != "Free":
        ticket_price = ticket_price.replace("-", "–")
        normalized_ticket_price = normalize_ticket_price(ticket_price)
        if normalized_ticket_price:
            ticket_price = normalized_ticket_price
        elif not _is_valid_ticket_price_output(ticket_price):
            ticket_price = ""

    if ticket_price == "Free":
        price_type = "free"

    if price_type not in {"official", "third_party", "free", "range", "unknown"}:
        price_type = "unknown"

    return {
        "ticket_price": ticket_price,
        "price_type": price_type,
        "price_note": price_note,
    }


def _parse_reasonableness_gemini_payload(raw_text: str) -> dict[str, str]:
    text = _normalize_text(raw_text)
    if not text:
        return {"opening_hours": "", "ticket_price": "", "ticket_status": "unknown", "price_note": ""}

    cleaned = text
    if "```" in cleaned:
        cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

    opening_hours = _normalize_opening_hours_value(_normalize_text(payload.get("opening_hours")))
    ticket_price = _normalize_text(payload.get("ticket_price"))
    ticket_status = _normalize_text(payload.get("ticket_status")).lower() or "unknown"
    price_note = _normalize_text(payload.get("price_note"))

    if ticket_price:
        normalized_ticket_price = normalize_ticket_price(ticket_price)
        if normalized_ticket_price:
            ticket_price = normalized_ticket_price
        elif ticket_price != "Free":
            ticket_price = ""

    if ticket_price == "Free":
        ticket_status = "free"

    if ticket_status not in {"free", "paid", "partially_paid", "unknown"}:
        ticket_status = "unknown"

    return {
        "opening_hours": opening_hours,
        "ticket_price": ticket_price,
        "ticket_status": ticket_status,
        "price_note": price_note,
    }


def resolve_ticket_price_with_gemini(
    attraction_name: str,
    location: str | None,
    sources: list[dict[str, str]],
    rule_based_price: str = "",
    aliases: list[str] | None = None,
) -> dict[str, str]:
    api_key = _resolve_gemini_api_key()
    _debug_log(f"gemini_api_key_found={bool(api_key)}")
    if not api_key or not sources:
        return {"ticket_price": "", "price_type": "unknown", "price_note": ""}

    candidate_pool = build_ticket_price_candidate_pool(sources=sources, attraction_name=attraction_name, aliases=aliases)
    ranked_sources: list[tuple[int, dict[str, str], str]] = []
    for source in sources[:8]:
        title = _normalize_text(source.get("title"))
        link = _normalize_text(source.get("link"))
        snippet = _normalize_text(source.get("snippet"))
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue
        source_type, source_score = _classify_source_type(title=title, link=link, snippet=snippet)
        if not _looks_like_ticket_source(title, link, snippet) and source_type not in {
            "official_homepage",
            "official_visitor_info",
            "official_faq",
            "official_ticket_page",
            "official_visitor_guide_pdf",
        }:
            continue
        if source_type in {"review_page", "generic_attraction_page"}:
            continue
        _debug_log(f"ticket_source_selected url={link} source_type={source_type} score={source_score}")
        page_text = _fetch_url_text(link)
        _debug_log(f"fetched_page_text_length url={link} length={len(page_text)}")
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            page_text=page_text,
            aliases=aliases,
        ):
            continue
        if len(page_text) < 20 and source_type not in {"ota_product_page", "official_homepage", "official_visitor_info", "official_faq"}:
            continue
        if len(page_text) < 40 and not snippet:
            continue
        ranked_sources.append((source_score, {**source, "_source_type": source_type}, page_text))

    if not ranked_sources:
        return {"ticket_price": "", "price_type": "unknown", "price_note": ""}

    ranked_sources.sort(key=lambda x: x[0])
    _debug_log("gemini_resolver_called=True")
    source_entries: list[dict[str, str]] = []
    for priority, source, page_text in ranked_sources[:4]:
        title = _normalize_text(source.get("title"))
        link = _normalize_text(source.get("link"))
        snippet = _normalize_text(source.get("snippet"))
        source_type = _normalize_text(source.get("_source_type")) or str(priority)
        source_entries.append(
            {
                "source_url": link,
                "source_type": source_type,
                "title": title,
                "snippet": snippet,
                "content": page_text[:2200],
                "rule_candidates": _extract_strong_ticket_values(f"{title}\n{snippet}\n{page_text}", attraction_name=attraction_name),
            }
        )
        _debug_log(
            f"gemini_source_payload url={link} priority={priority} rule_candidates={json.dumps(source_entries[-1]['rule_candidates'], ensure_ascii=False)}"
        )

    payload = {
        "attraction_name": attraction_name,
        "location": location or "",
        "rule_based_price": rule_based_price,
        "candidate_pool": [
            {
                "value": candidate.get("value"),
                "currency": candidate.get("currency"),
                "raw_price_text": candidate.get("raw_price_text"),
                "context": candidate.get("context"),
                "source_type": candidate.get("source_type"),
                "source_bucket": candidate.get("source_bucket"),
                "score": candidate.get("score"),
                "score_reasons": candidate.get("score_reasons"),
                "url": candidate.get("url"),
                "title": candidate.get("title"),
            }
            for candidate in candidate_pool[:20]
        ],
        "sources": source_entries,
    }

    try:
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key, temperature=0)
        response = llm.invoke(f"{_TICKET_PRICE_JUDGE_PROMPT}\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        _debug_log("gemini_call_failed=True")
        return {"ticket_price": "", "price_type": "unknown", "price_note": ""}

    content = _normalize_text(getattr(response, "content", ""))
    _debug_log(f"gemini_raw_response={content[:500]}")
    parsed = _parse_gemini_ticket_payload(content)
    return parsed


def analyze_visit_reasonableness_with_gemini(
    attraction_name: str,
    location: str | None,
    sources: list[dict[str, str]],
    current_opening_hours: str = "",
    current_ticket_price: str = "",
    aliases: list[str] | None = None,
) -> dict[str, str]:
    api_key = _resolve_gemini_api_key()
    if not api_key or not sources:
        return {"opening_hours": "", "ticket_price": "", "ticket_status": "unknown", "price_note": ""}

    source_entries: list[dict[str, str]] = []
    for source in sources[:6]:
        title = _normalize_text(source.get("title"))
        link = _normalize_text(source.get("link"))
        snippet = _normalize_text(source.get("snippet"))
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue
        page_text = _fetch_url_text(link)
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            page_text=page_text,
            aliases=aliases,
        ):
            continue
        source_entries.append(
            {
                "title": title,
                "link": link,
                "snippet": snippet,
                "content": page_text[:2200],
            }
        )

    if not source_entries:
        return {"opening_hours": "", "ticket_price": "", "ticket_status": "unknown", "price_note": ""}

    payload = {
        "attraction_name": attraction_name,
        "location": location or "",
        "current_opening_hours": current_opening_hours,
        "current_ticket_price": current_ticket_price,
        "sources": source_entries,
    }
    prompt = (
        "You analyze attraction visit practicality from provided sources only. "
        "Do not browse. Do not invent facts. "
        "Infer whether the main attraction is free, paid, partially_paid, or unknown. "
        "Use partially_paid when the landmark itself is free but a specific deck/exhibit/sub-attraction appears paid. "
        "For landmark-style towers/buildings/plazas, prefer free or partially_paid instead of paid when no explicit admission amount is present. "
        "If official or source snippets clearly state opening hours, return them. "
        "If no price is explicit but sources strongly imply free access, return ticket_status=free and ticket_price='Free'. "
        "If uncertain, keep ticket_price empty and ticket_status=unknown. "
        "Return JSON only with shape: "
        '{"opening_hours":"","ticket_price":"","ticket_status":"free|paid|partially_paid|unknown","price_note":""}.'
    )

    try:
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key, temperature=0)
        response = llm.invoke(f"{prompt}\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        _debug_log("reasonableness_gemini_call_failed=True")
        return {"opening_hours": "", "ticket_price": "", "ticket_status": "unknown", "price_note": ""}

    raw = _normalize_text(getattr(response, "content", ""))
    _debug_log(f"reasonableness_gemini_raw={raw[:500]}")
    return _parse_reasonableness_gemini_payload(raw)


def _classify_platform(link: str, title: str = "") -> str:
    haystack = f"{link} {title}".lower()
    if any(x in haystack for x in ["official", ".gov", ".edu"]):
        return "official"
    if "wikipedia.org" in haystack:
        return "wikipedia"
    if "tripadvisor" in haystack:
        return "tripadvisor"
    if "trip.com" in haystack:
        return "trip"
    if "klook" in haystack:
        return "klook"
    if any(x in haystack for x in ["travel guide", "lonely planet", "wikivoyage", "guide"]):
        return "travel_guide"
    if "google.com/maps" in haystack or "maps.google" in haystack:
        return "google_maps"
    if "kkday" in haystack:
        return "kkday"
    if "ctrip" in haystack or "携程" in haystack:
        return "ctrip"
    return "other"


def collect_preferred_sources(
    results: list[dict[str, Any]],
    min_count: int = 3,
    attraction_name: str = "",
    aliases: list[str] | None = None,
) -> list[dict[str, str]]:
    ranked: list[tuple[int, dict[str, str]]] = []
    seen_links: set[str] = set()

    for item in results:
        title = _normalize_text(item.get("title"))
        link = _normalize_text(item.get("link"))
        snippet = _normalize_text(item.get("snippet") or item.get("snippet_highlighted_words"))
        if not (title or link or snippet):
            continue
        if attraction_name and not is_source_relevant_to_attraction(
            attraction_name=attraction_name,
            source_title=title,
            source_snippet=snippet,
            aliases=aliases,
        ):
            continue

        dedup_key = link or title.lower()
        if dedup_key in seen_links:
            continue
        seen_links.add(dedup_key)

        platform = _classify_platform(link, title)
        score = _PLATFORM_PRIORITIES.get(platform, _PLATFORM_PRIORITIES["other"])
        ranked.append((score, {"title": title, "link": link, "snippet": snippet}))

    ranked.sort(key=lambda x: x[0], reverse=True)
    sources = [item for _, item in ranked[:6]]
    return sources[: max(3, min_count)] if len(sources) >= 3 else sources



_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_GEOCODER_USER_AGENT = "ai-travel-assistant/1.0"


def _resolve_place_geometry(place: str) -> dict[str, Any]:
    query = _normalize_text(place)
    if not query:
        return {}

    encoded_query = urllib.parse.quote(query)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&limit=1"
    data = _http_get_json(url, headers={"User-Agent": _GEOCODER_USER_AGENT})
    if not isinstance(data, list) or not data:
        return {}

    first = data[0] if isinstance(data[0], dict) else {}
    bbox = first.get("boundingbox") if isinstance(first.get("boundingbox"), list) else []
    if len(bbox) == 4:
        try:
            south, north, west, east = map(float, bbox)
        except (TypeError, ValueError):
            south = north = west = east = None
    else:
        south = north = west = east = None

    try:
        lat = float(first.get("lat"))
        lon = float(first.get("lon"))
    except (TypeError, ValueError):
        return {}

    return {
        "lat": lat,
        "lon": lon,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "display_name": _normalize_text(first.get("display_name")),
    }


def _run_overpass_query(query: str, timeout: int = 20) -> dict[str, Any]:
    payload = query.encode("utf-8")
    request = urllib.request.Request(
        _OVERPASS_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "User-Agent": _GEOCODER_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_valid_entity_name(name: str) -> bool:
    text = _normalize_text(name)
    if len(text) < 3:
        return False
    lowered = text.lower()
    blocked_tokens = [
        "top ", "attractions", "things to do", "travel guide", "guide", "攻略", "景点玩乐", "必做事项", "washington georgetown",
    ]
    if any(token in lowered for token in blocked_tokens):
        return False
    if re.search(r"\b\d+\s*(?:best|top|大景点)\b", lowered):
        return False
    return True


def _normalize_opening_hours_value(value: str) -> str:
    cleaned = clean_opening_hours(value)
    return cleaned if cleaned and is_valid_opening_hours(cleaned) else ""


def _extract_ticket_price_from_tags(tags: dict[str, Any]) -> str:
    fee = _normalize_text(tags.get("fee") or tags.get("charge") or tags.get("price"))
    if not fee:
        return ""
    low = fee.lower()
    if low in {"no", "none", "unknown"}:
        return ""
    if low in {"yes", "paid"}:
        return ""
    if "free" in low or "免费" in fee:
        return "Free"

    normalized = convert_price_to_myr(fee)
    if normalized and normalized.startswith("RM"):
        return normalized
    if re.search(r"\bRM\b", fee, re.IGNORECASE):
        return fee.replace("MYR", "RM").strip()
    return ""


def _fetch_wikidata_entity(entity_id: str) -> dict[str, Any]:
    entity = _normalize_text(entity_id)
    if not entity:
        return {}
    url = (
        "https://www.wikidata.org/w/api.php?action=wbgetentities&ids="
        f"{urllib.parse.quote(entity)}&format=json&props=claims|descriptions|sitelinks"
    )
    data = _http_get_json(url, headers={"User-Agent": _GEOCODER_USER_AGENT})
    if not isinstance(data, dict):
        return {}
    entities = data.get("entities")
    if not isinstance(entities, dict):
        return {}
    payload = entities.get(entity)
    return payload if isinstance(payload, dict) else {}


def _extract_wikidata_image_url(entity_payload: dict[str, Any]) -> str:
    claims = entity_payload.get("claims") if isinstance(entity_payload.get("claims"), dict) else {}
    image_claims = claims.get("P18") if isinstance(claims.get("P18"), list) else []
    if not image_claims:
        return ""
    first = image_claims[0] if isinstance(image_claims[0], dict) else {}
    mainsnak = first.get("mainsnak") if isinstance(first.get("mainsnak"), dict) else {}
    datavalue = mainsnak.get("datavalue") if isinstance(mainsnak.get("datavalue"), dict) else {}
    filename = _normalize_text(datavalue.get("value"))
    if not filename:
        return ""
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(filename)}"


def _extract_wikidata_description(entity_payload: dict[str, Any]) -> str:
    descriptions = entity_payload.get("descriptions") if isinstance(entity_payload.get("descriptions"), dict) else {}
    for key in ("en", "zh", "zh-cn", "ms"):
        val = descriptions.get(key)
        if isinstance(val, dict):
            text = _normalize_text(val.get("value"))
            if text:
                return text
    return ""


def _normalize_wikipedia_title(value: str) -> str:
    text = _normalize_text(value)
    if text.startswith("en:"):
        return text[3:]
    return text


_OSM_ALLOWED_TOURISM_VALUES = {
    "attraction",
    "museum",
    "viewpoint",
    "theme_park",
    "gallery",
    "zoo",
    "aquarium",
    "artwork",
}
_OSM_ALLOWED_LEISURE_VALUES = {"park", "garden", "nature_reserve", "water_park", "theme_park"}
_OSM_ALLOWED_HISTORIC_VALUES = {"castle", "fort", "ruins", "archaeological_site", "city_gate"}
_OSM_BLOCKED_TAG_VALUES = {
    ("historic", "aircraft"),
    ("historic", "milestone"),
    ("historic", "memorial"),
    ("historic", "monument"),
    ("amenity", "clock"),
    ("amenity", "parking"),
}
_ARTICLE_OR_PRODUCT_PATTERNS = [
    r"things to do",
    r"best attractions",
    r"top attractions",
    r"tourist attractions",
    r"must-see attractions",
    r"must visit attractions",
    r"sights\s*(?:&|and)\s*attractions",
    r"discover the",
    r"travel guide",
    r"official site",
    r"places to visit",
    r"historical landmarks",
    r"historical sites",
    r"hidden gems",
    r"itinerary",
    r"top recommendations",
    r"landmark tours",
    r"tour package",
    r"自由行",
    r"必去",
    r"必玩",
    r"景點推薦",
    r"景点推荐",
    r"熱門旅遊景點",
    r"热门旅游景点",
    r"一日遊行程",
    r"一日游行程",
    r"親子好去處",
    r"亲子好去处",
    r"親子遊景點推薦",
    r"亲子游景点推荐",
    r"旅遊攻略",
    r"旅游攻略",
    r"門票",
    r"门票",
    r"交通.*美食",
    r"景點美食",
    r"景点美食",
]
_GENERIC_OBJECT_PATTERNS = [
    r"^airplane$",
    r"\bmilestone\b",
    r"\bmonument\b",
    r"\bmemorial\b",
    r"\bstation\b",
    r"\bterminal\b",
    r"\bpier\b",
    r"\bclock\b",
    r"\broundabout\b",
    r"\bstatue\b",
]
_ATTRACTION_NAME_HINTS = [
    "museum", "temple", "palace", "park", "garden", "tower", "hill", "wall", "lake", "street",
    "market", "jetty", "mosque", "church", "cathedral", "fort", "village", "zoo", "aquarium",
    "sanctuary", "beach", "walk", "walking street", "viewpoint", "dolphinarium", "island",
    "博物院", "博物馆", "公园", "寺", "寺庙", "故宫", "长城", "胡同", "广场", "乐园", "古城", "山", "湖", "海滩",
]
_WEAK_DESCRIPTION_PATTERNS = [
    r"^an airplane is\b",
    r"^overview\.",
    r"full-day tour",
    r"hotel pickup",
    r"熱門景點",
    r"热门景点",
    r"含\d+個景點",
    r"top attractions include",
]


def _has_attraction_name_hint(text: str) -> bool:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    return any(token in lowered or token in normalized for token in _ATTRACTION_NAME_HINTS)


def _looks_like_article_or_product_text(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _ARTICLE_OR_PRODUCT_PATTERNS):
        return True
    if re.search(r"【\s*20\d{2}.*】", normalized):
        return True
    if re.search(r"\b(?:youtube|reddit|facebook|instagram)\b", normalized):
        return True
    return bool(re.search(r"\btours?\b", normalized))


def _looks_like_generic_object_name(name: str) -> bool:
    normalized = _normalize_text(name).strip().lower()
    if not normalized:
        return True
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _GENERIC_OBJECT_PATTERNS)


def _has_city_iconic_match(name: str, city: str) -> bool:
    normalized_name = _normalize_match_text(name)
    normalized_city = _normalize_text(city).lower()
    for city_key, iconic_terms in _CITY_ICONIC_ATTRACTIONS.items():
        if city_key in normalized_city and any(term in normalized_name for term in iconic_terms):
            return True
    return False


def _description_is_usable_for_recommendation(text: str) -> bool:
    value = _normalize_text(text)
    if _has_placeholder_description(value):
        return False
    lowered = value.lower()
    return not any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _WEAK_DESCRIPTION_PATTERNS)


def _infer_recommendation_source_bucket(candidate: dict[str, Any]) -> str:
    source_type = _normalize_text(candidate.get("source_type")).lower()
    link = ""
    sources = candidate.get("sources", []) if isinstance(candidate.get("sources"), list) else []
    if sources:
        link = _normalize_text(sources[0])
    title = _normalize_text(candidate.get("source_title") or candidate.get("name"))
    snippet = _normalize_text(candidate.get("source_snippet") or candidate.get("description"))
    haystack = f"{link} {title} {snippet}".lower()
    platform = _classify_platform(link, title)

    if source_type in {"osm", "osm_poi"}:
        return "osm_poi"
    if source_type in {"wikipedia"} or platform == "wikipedia":
        return "wiki_entity"
    if source_type == "search_entity":
        return "search_entity"
    if platform == "official":
        return "official_attraction_page" if _has_attraction_name_hint(title) else "official_destination_page"
    if platform in {"trip", "klook", "kkday", "ctrip"}:
        return "ota_product_page" if _looks_like_article_or_product_text(haystack) else "ota_destination_page"
    if any(token in haystack for token in ["youtube", "youtu.be", "episode", "ep5", "ep6"]):
        return "video_page"
    if any(token in haystack for token in ["reddit", "forum", "quora"]):
        return "ugc_discussion"
    if _looks_like_article_or_product_text(haystack) or platform in {"travel_guide", "tripadvisor"}:
        return "travel_blog_article"
    if source_type == "offline_catalog":
        return "offline_seed"
    if source_type == "serpapi":
        return "search_entity"
    return source_type or "other"


def _is_supported_osm_candidate(tags: dict[str, Any], name: str) -> bool:
    tourism = _normalize_text(tags.get("tourism")).lower()
    leisure = _normalize_text(tags.get("leisure")).lower()
    historic = _normalize_text(tags.get("historic")).lower()
    amenity = _normalize_text(tags.get("amenity")).lower()
    railway = _normalize_text(tags.get("railway")).lower()
    man_made = _normalize_text(tags.get("man_made")).lower()

    if any((key, value) in _OSM_BLOCKED_TAG_VALUES for key, value in [("historic", historic), ("amenity", amenity)]):
        return False
    if railway or man_made in {"tower", "mast", "water_tower"}:
        return False
    if _looks_like_generic_object_name(name):
        return False

    allowed = (
        tourism in _OSM_ALLOWED_TOURISM_VALUES
        or leisure in _OSM_ALLOWED_LEISURE_VALUES
        or historic in _OSM_ALLOWED_HISTORIC_VALUES
    )
    if not allowed:
        return False

    evidence = any(
        _normalize_text(tags.get(field))
        for field in ["description", "short_description", "image", "wikimedia_commons", "wikipedia", "wikidata", "website", "url"]
    )
    strong_type = tourism in {"attraction", "museum", "theme_park", "zoo", "aquarium"} or historic in {"castle", "fort"}
    return strong_type or evidence or _has_attraction_name_hint(name)


def _extract_poi_from_element(element: dict[str, Any]) -> dict[str, Any]:
    tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
    name = _normalize_text(tags.get("name"))
    if not _is_valid_entity_name(name):
        return {}
    if not _is_supported_osm_candidate(tags, name):
        return {}

    poi: dict[str, Any] = {
        "name": name,
        "description": _normalize_text(tags.get("description") or tags.get("short_description")),
        "image": _normalize_text(tags.get("image")),
        "opening_hours": _normalize_opening_hours_value(_normalize_text(tags.get("opening_hours"))),
        "ticket_price": _extract_ticket_price_from_tags(tags),
        "wikipedia": _normalize_wikipedia_title(_normalize_text(tags.get("wikipedia"))),
        "wikidata": _normalize_text(tags.get("wikidata")),
        "website": _normalize_text(tags.get("website") or tags.get("url")),
        "osm_type": _normalize_text(element.get("type")),
        "osm_id": _normalize_text(element.get("id")),
        "tourism": _normalize_text(tags.get("tourism")),
        "historic": _normalize_text(tags.get("historic")),
        "amenity": _normalize_text(tags.get("amenity")),
        "leisure": _normalize_text(tags.get("leisure")),
    }
    if not poi["image"]:
        commons = _normalize_text(tags.get("wikimedia_commons"))
        if commons:
            poi["image"] = f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(commons)}"
    return poi


def _get_osm_city_pois(place: str, limit: int = 12) -> list[dict[str, Any]]:
    geo = _resolve_place_geometry(place)
    if not geo:
        return []

    lat = geo.get("lat")
    lon = geo.get("lon")
    if lat is None or lon is None:
        return []

    radius = 12000
    if isinstance(geo.get("south"), float) and isinstance(geo.get("north"), float):
        span_km = abs(float(geo["north"]) - float(geo["south"])) * 111
        if span_km > 0:
            radius = int(max(6000, min(22000, span_km * 700)))

    query = f"""
    [out:json][timeout:25];
    (
      nwr(around:{radius},{lat},{lon})[tourism=attraction];
      nwr(around:{radius},{lat},{lon})[tourism=museum];
      nwr(around:{radius},{lat},{lon})[tourism=viewpoint];
      nwr(around:{radius},{lat},{lon})[tourism=theme_park];
      nwr(around:{radius},{lat},{lon})[tourism=gallery];
      nwr(around:{radius},{lat},{lon})[tourism=zoo];
      nwr(around:{radius},{lat},{lon})[tourism=aquarium];
      nwr(around:{radius},{lat},{lon})[historic=castle];
      nwr(around:{radius},{lat},{lon})[historic=fort];
      nwr(around:{radius},{lat},{lon})[historic=ruins];
      nwr(around:{radius},{lat},{lon})[leisure=park];
      nwr(around:{radius},{lat},{lon})[leisure=garden];
    );
    out tags center 120;
    """

    data = _run_overpass_query(query)
    elements = data.get("elements") if isinstance(data.get("elements"), list) else []

    pois: list[dict[str, Any]] = []
    seen: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        poi = _extract_poi_from_element(element)
        if not poi:
            continue
        key = poi["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        pois.append(poi)
        if len(pois) >= limit:
            break
    return pois


def _search_osm_poi_by_name(name: str, location: str | None = None) -> dict[str, Any]:
    query = f"{name} {location}".strip() if location else name.strip()
    if not query:
        return {}

    encoded_query = urllib.parse.quote(query)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&limit=1"
    data = _http_get_json(url, headers={"User-Agent": _GEOCODER_USER_AGENT})
    if not isinstance(data, list) or not data:
        return {}

    first = data[0] if isinstance(data[0], dict) else {}
    osm_type = _normalize_text(first.get("osm_type"))
    osm_id = _normalize_text(first.get("osm_id"))
    if not osm_type or not osm_id:
        return {}

    overpass_type = {"node": "node", "way": "way", "relation": "relation"}.get(osm_type, "")
    if not overpass_type:
        return {}

    query_text = f"""
    [out:json][timeout:20];
    {overpass_type}({osm_id});
    out tags center 1;
    """
    data = _run_overpass_query(query_text)
    elements = data.get("elements") if isinstance(data.get("elements"), list) else []
    for element in elements:
        if isinstance(element, dict):
            poi = _extract_poi_from_element(element)
            if poi:
                return poi
    return {}


def _enrich_poi_with_knowledge(poi: dict[str, Any], location: str | None = None) -> dict[str, Any]:
    enriched = dict(poi)
    wiki_title = _normalize_text(enriched.get("wikipedia"))
    if not wiki_title:
        wiki_title = enriched.get("name", "")

    wiki = fetch_wikipedia_summary(wiki_title, location)
    if wiki.get("description") and not enriched.get("description"):
        enriched["description"] = wiki["description"]
    if wiki.get("image_url") and not enriched.get("image"):
        enriched["image"] = wiki["image_url"]

    wikidata_id = _normalize_text(enriched.get("wikidata"))
    if wikidata_id:
        entity = _fetch_wikidata_entity(wikidata_id)
        wd_desc = _extract_wikidata_description(entity)
        wd_image = _extract_wikidata_image_url(entity)
        if wd_desc and not enriched.get("description"):
            enriched["description"] = wd_desc
        if wd_image and not enriched.get("image"):
            enriched["image"] = wd_image

    source_urls = []
    osm_type = _normalize_text(enriched.get("osm_type"))
    osm_id = _normalize_text(enriched.get("osm_id"))
    if osm_type and osm_id:
        source_urls.append(f"https://www.openstreetmap.org/{osm_type}/{osm_id}")
    if wiki.get("source_url"):
        source_urls.append(wiki["source_url"])
    if enriched.get("website"):
        source_urls.append(_normalize_text(enriched.get("website")))

    deduped = []
    seen = set()
    for url in source_urls:
        if url and url not in seen:
            seen.add(url)
            deduped.append(url)
    enriched["sources"] = deduped[:5]
    return enriched

def _is_plausible_attraction_name(name: str) -> bool:
    text = re.sub(r"\s+", " ", name).strip(" -|,:;。.!?[](){}")
    if len(text) < 3:
        return False

    lowered = text.lower()
    if _looks_like_article_or_product_text(text):
        return False
    if _looks_like_generic_object_name(text):
        return False

    generic_patterns = [
        r"^top\s+\d+",
        r"^the\s+\d+\s+best",
        r"^\d+\s+(best|top|beautiful|must-see|must visit)",
        r"most beautiful",
        r"beautiful sights",
        r"what to do",
        r"where to go",
        r"places to visit in ",
        r"best places to visit",
        r"top places to visit",
        r"historical sites",
        r"自由行",
        r"景點推薦|景点推荐",
        r"親子好去處|亲子好去处",
        r"一日遊行程|一日游行程",
        r"熱門旅遊景點|热门旅游景点",
        r"visit .* in ",
        r"attractions? in ",
        r"guide to",
        r"nearby",
        r"\btours?\b$",
        r"^ep\d+\b",
    ]
    if any(re.search(pattern, lowered) for pattern in generic_patterns):
        return False

    if re.search(r"\b\d{4}\b", text):
        return False
    if re.fullmatch(r"[A-Za-z]{1,4}\d*[-–:]?", text):
        return False

    if not _has_attraction_name_hint(text) and len(text.split()) > 8:
        return False

    return True


def _clean_recommendation_candidate_name(name: str) -> str:
    text = _normalize_text(name)
    if not text:
        return ""

    text = re.split(r"\s[-|–:]\s", text)[0].strip()
    text = re.sub(r"^(nearby|附近)[:：\s]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d+\s*", "", text)
    if re.search(r"[\u4e00-\u9fff]", text) and re.search(r"[A-Za-z]", text):
        latin_segments = [seg.strip() for seg in re.findall(r"[A-Za-z][A-Za-z0-9'&\-\s]{2,}", text) if seg.strip()]
        plausible_latin = [seg for seg in latin_segments if _is_plausible_attraction_name(seg)]
        if plausible_latin:
            text = max(plausible_latin, key=len)
    if not _is_plausible_attraction_name(text):
        return ""
    return text


def _truncate_recommendation_page_text(text: str, max_chars: int = 2400) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) <= max_chars:
        return normalized
    cutoff = normalized.rfind(". ", 0, max_chars)
    if cutoff >= max_chars // 2:
        return normalized[: cutoff + 1].strip()
    return normalized[:max_chars].rstrip()


def _pick_recommendation_description(name: str, snippet: str, page_text: str) -> str:
    normalized_snippet = _normalize_text(snippet)
    excerpt = _truncate_recommendation_page_text(page_text, max_chars=3200)
    if not excerpt:
        return normalized_snippet

    sentence_candidates = re.split(r"(?<=[。！？.!?])\s+", excerpt)
    alias_values = _build_attraction_aliases(name)
    for sentence in sentence_candidates:
        cleaned = re.sub(r"\s+", " ", sentence).strip(" -|,:;。.!?[](){}")
        if len(cleaned) < 24:
            continue
        if len(cleaned) > 240:
            cleaned = cleaned[:240].rstrip()
        if any(_normalize_match_text(alias) in _normalize_match_text(cleaned) for alias in alias_values if _normalize_match_text(alias)):
            return cleaned

    first_sentence = ""
    for sentence in sentence_candidates:
        cleaned = re.sub(r"\s+", " ", sentence).strip(" -|,:;。.!?[](){}")
        if len(cleaned) >= 24:
            first_sentence = cleaned[:240].rstrip()
            break
    return first_sentence or normalized_snippet


def _candidate_name_grounded_in_source(name: str, source_title: str, source_snippet: str, page_text: str = "") -> bool:
    normalized_source = _normalize_match_text(f"{source_title} {source_snippet} {page_text}")
    if not normalized_source:
        return False

    for alias in _build_attraction_aliases(name):
        normalized_alias = _normalize_match_text(alias)
        if not normalized_alias:
            continue
        if normalized_alias in normalized_source:
            return True
        alias_tokens = [token for token in normalized_alias.split() if len(token) >= 4]
        if alias_tokens and all(token in normalized_source for token in alias_tokens):
            return True
    return False


def _looks_like_generic_destination_candidate(name: str, city: str) -> bool:
    normalized_name = _normalize_text(name).strip().lower()
    normalized_city = _normalize_text(city).split(",", 1)[0].strip().lower()
    if not normalized_name:
        return True
    if normalized_city and normalized_name == normalized_city:
        return True
    if _looks_like_article_or_product_text(normalized_name):
        return True
    if _looks_like_generic_object_name(normalized_name):
        return True
    if normalized_city and normalized_city in normalized_name and not _has_attraction_name_hint(normalized_name) and not _has_city_iconic_match(normalized_name, city):
        return True
    return False


def _is_valid_recommendation_entity(candidate: dict[str, Any], city: str) -> bool:
    name = _normalize_text(candidate.get("name"))
    if not _is_plausible_attraction_name(name):
        return False
    if _looks_like_generic_destination_candidate(name, city):
        return False

    description = _normalize_text(candidate.get("description") or candidate.get("source_snippet"))
    source_title = _normalize_text(candidate.get("source_title"))
    source_bucket = _infer_recommendation_source_bucket(candidate)
    if source_bucket == "osm_poi":
        return True
    if source_bucket in {"official_destination_page", "ota_product_page", "ota_destination_page", "travel_blog_article", "ugc_discussion", "video_page"}:
        return False

    if _looks_like_article_or_product_text(source_title) and not _has_city_iconic_match(name, city):
        return False
    if _looks_like_generic_object_name(name):
        return False

    sources = candidate.get("sources", []) if isinstance(candidate.get("sources"), list) else []
    has_authority = any(
        token
        for token in [
            _normalize_text(candidate.get("wikipedia")),
            _normalize_text(candidate.get("wikidata")),
            _normalize_text(candidate.get("website")),
        ]
    ) or any(
        _classify_platform(_normalize_text(source), source_title) in {"official", "wikipedia"}
        for source in sources
    )
    has_evidence = has_authority or _description_is_usable_for_recommendation(description) or _has_attraction_name_hint(name) or _has_city_iconic_match(name, city)
    return has_evidence


def _has_placeholder_description(text: str) -> bool:
    value = _normalize_text(text).lower()
    if not value:
        return True
    placeholders = {
        "popular attraction in this destination.",
        "popular attraction in this destination",
        "top attraction",
        "must-visit attraction",
        "a popular attraction.",
        "a popular temple attraction.",
        "a popular street attraction.",
        "a popular shopping center.",
        "a popular water park.",
        "a recommended beach attraction.",
        "a recommended night market attraction.",
    }
    if value in placeholders:
        return True
    return bool(re.fullmatch(r"a\s+(popular|recommended)\s+.+attraction\.", value))


def _recommendation_quality_score(candidate: dict[str, Any], city: str) -> int:
    name = _normalize_text(candidate.get("name"))
    desc = _normalize_text(candidate.get("description"))
    ticket = _normalize_text(candidate.get("ticket_price"))
    image = _normalize_text(candidate.get("image"))
    sources = candidate.get("sources", []) if isinstance(candidate.get("sources"), list) else []
    source_count = len([s for s in sources if _normalize_text(s)])
    source_bucket = _infer_recommendation_source_bucket(candidate)

    score = 0
    if _has_city_iconic_match(name, city):
        score += 40
    if _description_is_usable_for_recommendation(desc):
        score += 8
    if image.startswith("http"):
        score += 5
    if ticket and _is_valid_ticket_price_output(ticket):
        score += 3
    score += min(4, source_count)
    if city.lower() in name.lower():
        score += 2
    if _has_attraction_name_hint(name):
        score += 10

    source_bucket_scores = {
        "official_attraction_page": 25,
        "wiki_entity": 20,
        "osm_poi": 15,
        "offline_seed": 6,
        "search_entity": 28,
        "official_destination_page": -40,
        "ota_product_page": -25,
        "ota_destination_page": -25,
        "travel_blog_article": -35,
        "ugc_discussion": -35,
        "video_page": -35,
    }
    score += source_bucket_scores.get(source_bucket, 0)
    if _looks_like_generic_object_name(name):
        score -= 60
    if not _description_is_usable_for_recommendation(desc) and not _has_city_iconic_match(name, city):
        score -= 20
    return score


def _parse_recommendation_gemini_payload(raw_text: str) -> list[dict[str, str]]:
    text = _normalize_text(raw_text)
    if not text:
        return []
    if "```" in text:
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

    attractions = payload.get("attractions", []) if isinstance(payload.get("attractions"), list) else []
    normalized: list[dict[str, str]] = []
    for item in attractions:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": _normalize_text(item.get("name")),
                "description": _normalize_text(item.get("description")),
                "image": _normalize_text(item.get("image")),
                "ticket_price": _normalize_text(item.get("ticket_price")),
            }
        )
    return normalized


def _parse_search_candidate_gemini_payload(raw_text: str) -> list[dict[str, Any]]:
    text = _normalize_text(raw_text)
    if not text:
        return []
    if "```" in text:
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

    attractions = payload.get("attractions", []) if isinstance(payload.get("attractions"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in attractions:
        if not isinstance(item, dict):
            continue
        source_index = item.get("source_index")
        normalized.append(
            {
                "name": _normalize_text(item.get("name")),
                "description": _normalize_text(item.get("description")),
                "source_index": source_index if isinstance(source_index, int) else -1,
            }
        )
    return normalized


def _extract_search_candidates_with_gemini(
    place: str,
    query: str,
    organic_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    api_key = _resolve_gemini_api_key()
    if not api_key or not organic_results:
        return []

    search_result_contexts: list[dict[str, str]] = []
    for item in organic_results[:8]:
        if not isinstance(item, dict):
            continue
        link = _normalize_text(item.get("link"))
        page_text = _truncate_recommendation_page_text(_fetch_url_text(link)) if link else ""
        search_result_contexts.append(
            {
                "title": _normalize_text(item.get("title")),
                "snippet": _normalize_text(item.get("snippet")),
                "link": link,
                "page_text": page_text,
            }
        )

    payload = {
        "city": place,
        "query": _normalize_text(query),
        "results": [
            {
                "source_index": index,
                "title": context["title"],
                "snippet": context["snippet"],
                "link": context["link"],
                "page_text": context["page_text"],
            }
            for index, context in enumerate(search_result_contexts)
        ],
    }
    if not payload["results"]:
        return []

    prompt = (
        "You extract actual attraction entities from search results for a travel recommendation system. "
        "Use ONLY the provided titles/snippets/links/page_text. Never invent attraction names. "
        "If a result is a list article, extract the specific attraction names explicitly mentioned in that result's snippet or page_text. "
        "Ignore generic article titles that do not clearly mention a real attraction. "
        "Prefer grounding entities in page_text when available. "
        "Descriptions must be short summaries based only on the source snippet/page_text. "
        "Return JSON only in shape: "
        '{"attractions":[{"name":"","description":"","source_index":0}]}.'
    )

    try:
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key, temperature=0)
        response = llm.invoke(f"{prompt}\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        _debug_log("search_candidate_gemini_call_failed=True")
        return []

    raw = _normalize_text(getattr(response, "content", ""))
    _debug_log(f"search_candidate_gemini_raw={raw[:500]}")
    parsed = _parse_search_candidate_gemini_payload(raw)

    extracted: list[dict[str, Any]] = []
    for item in parsed:
        name = _clean_recommendation_candidate_name(item.get("name", ""))
        if not name or not _is_plausible_attraction_name(name):
            continue
        source_index = item.get("source_index", -1)
        source_context = search_result_contexts[source_index] if isinstance(source_index, int) and 0 <= source_index < len(search_result_contexts) else {}
        title = _normalize_text(source_context.get("title")) if isinstance(source_context, dict) else ""
        snippet = _normalize_text(source_context.get("snippet")) if isinstance(source_context, dict) else ""
        page_text = _normalize_text(source_context.get("page_text")) if isinstance(source_context, dict) else ""
        if not _candidate_name_grounded_in_source(name, title, snippet, page_text=page_text):
            continue
        link = _normalize_text(source_context.get("link")) if isinstance(source_context, dict) else ""
        candidate = {
            "name": name,
            "description": "" if _has_placeholder_description(_normalize_text(item.get("description"))) else _normalize_text(item.get("description")),
            "image": "",
            "ticket_price": "",
            "sources": [link] if link else [],
            "source_type": "search_entity",
            "source_title": title,
            "source_snippet": snippet,
            "page_text": page_text,
        }
        if not _description_is_usable_for_recommendation(_normalize_text(candidate.get("description"))):
            candidate["description"] = _pick_recommendation_description(name, snippet, page_text)
        if not _is_valid_recommendation_entity(candidate, place):
            continue
        extracted.append(
            candidate
        )
    return extracted


def normalize_recommendations_with_gemini(
    user_query: str,
    city: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    api_key = _resolve_gemini_api_key()
    _debug_log(f"recommendation_gemini_api_key_found={bool(api_key)}")
    if not api_key or not candidates:
        return candidates

    payload = {
        "user_query": _normalize_text(user_query),
        "city": city,
        "candidates": [
            {
                "name": _normalize_text(c.get("name")),
                "description": _normalize_text(c.get("description")),
                "image": _normalize_text(c.get("image")),
                "ticket_price": _normalize_text(c.get("ticket_price")),
                "sources": c.get("sources", []) if isinstance(c.get("sources"), list) else [],
            }
            for c in candidates
        ],
    }

    prompt = (
        "You are a strict attraction recommendation normalizer and reranker. "
        "Use ONLY provided candidate data. Do not browse web. Do not invent facts. "
        "Keep only relevant attraction entities for the target city. "
        "Remove weak/generic low-value candidates when needed. "
        "Descriptions must be short and clean. "
        "Never invent image URLs or ticket prices. Keep unsupported image/ticket_price as ''. "
        "Return JSON only in shape: {\"attractions\":[{\"name\":\"\",\"description\":\"\",\"image\":\"\",\"ticket_price\":\"\"}]}."
    )

    try:
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_key=api_key, temperature=0)
        response = llm.invoke(f"{prompt}\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:
        _debug_log("recommendation_gemini_call_failed=True")
        return candidates

    raw = _normalize_text(getattr(response, "content", ""))
    _debug_log(f"recommendation_gemini_raw={raw[:500]}")
    parsed = _parse_recommendation_gemini_payload(raw)
    if not parsed:
        return candidates

    by_name = {(_normalize_text(c.get("name")).lower()): c for c in candidates if _normalize_text(c.get("name"))}
    normalized: list[dict[str, Any]] = []
    for item in parsed:
        name = _normalize_text(item.get("name"))
        base = by_name.get(name.lower())
        if not name or not base or not _is_plausible_attraction_name(name):
            continue

        final_image = _normalize_text(item.get("image"))
        if final_image and final_image != _normalize_text(base.get("image")):
            final_image = _normalize_text(base.get("image"))

        final_ticket = _normalize_text(item.get("ticket_price"))
        if final_ticket and final_ticket != _normalize_text(base.get("ticket_price")):
            final_ticket = _normalize_text(base.get("ticket_price"))
        if final_ticket and not _is_valid_ticket_price_output(final_ticket):
            final_ticket = ""

        final_description = _normalize_text(item.get("description")) or _normalize_text(base.get("description"))
        if _has_placeholder_description(final_description):
            base_description = _normalize_text(base.get("description"))
            final_description = "" if _has_placeholder_description(base_description) else base_description

        normalized.append(
            {
                "name": name,
                "description": final_description,
                "image": final_image,
                "ticket_price": final_ticket,
                "sources": base.get("sources", []) if isinstance(base.get("sources"), list) else [],
                "score": _recommendation_quality_score(base, city),
            }
        )

    return normalized or candidates


def _build_place_search_queries(place: str) -> list[str]:
    return [
        f"{place} tourist attractions",
        f"{place} best attractions",
        f"{place} things to do",
        f"{place} landmarks",
        f"{place} 景点",
    ]


def _collect_search_recommendation_candidates(
    place: str,
    api_key: str,
    seen_names: set[str],
    query_hint: str = "",
    limit: int = 14,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    search_queries = _build_place_search_queries(place)
    if query_hint:
        search_queries.insert(0, query_hint)

    for query in search_queries:
        try:
            payload = _search_google(query, api_key)
        except Exception:
            continue

        organic_results = payload.get("organic_results", [])[:10]
        gemini_candidates = _extract_search_candidates_with_gemini(place=place, query=query, organic_results=organic_results)
        for candidate in gemini_candidates:
            name_key = _normalize_text(candidate.get("name")).lower()
            if not name_key or name_key in seen_names or not _is_valid_recommendation_entity(candidate, place):
                continue
            seen_names.add(name_key)
            candidate["score"] = _recommendation_quality_score(candidate, place)
            candidates.append(candidate)
            if len(candidates) >= limit:
                return candidates
        if gemini_candidates:
            continue

        for item in organic_results:
            title = _normalize_text(item.get("title"))
            link = _normalize_text(item.get("link"))
            snippet = _normalize_text(item.get("snippet"))
            page_text = _truncate_recommendation_page_text(_fetch_url_text(link)) if link else ""
            name = _clean_recommendation_candidate_name(re.split(r"\s[-|–]\s", title)[0].strip() if title else "")
            if not _is_plausible_attraction_name(name) or _looks_like_generic_destination_candidate(name, place):
                continue

            name_key = name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            candidate = {
                "name": name,
                "description": _pick_recommendation_description(name, snippet, page_text),
                "image": "",
                "ticket_price": "",
                "sources": [link] if link else [],
                "source_type": "serpapi",
                "source_title": title,
                "source_snippet": snippet,
                "page_text": page_text,
            }
            if not _is_valid_recommendation_entity(candidate, place):
                continue
            candidate["score"] = _recommendation_quality_score(candidate, place)
            candidates.append(candidate)
            if len(candidates) >= limit:
                return candidates
    return candidates


def _search_wikipedia_titles(query: str, limit: int = 8) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "format": "json",
            "utf8": 1,
            "srsearch": query,
            "srlimit": max(1, min(limit, 10)),
        }
    )
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    data = _http_get_json(url, headers={"Accept": "application/json", "User-Agent": "ai-travel-assistant/1.0"})
    if not isinstance(data, dict):
        return []
    query_payload = data.get("query", {})
    results = query_payload.get("search", []) if isinstance(query_payload, dict) else []
    return [item for item in results if isinstance(item, dict)]


def _collect_wikipedia_recommendation_candidates(
    place: str,
    seen_names: set[str],
    query_hint: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    search_queries = []
    if query_hint:
        search_queries.append(query_hint)
    search_queries.extend(
        [
            f"{place} landmarks",
            f"{place} attractions",
            f"{place} tourism",
            f"{place} museums",
            f"{place} historic sites",
        ]
    )

    seen_titles: set[str] = set()
    for query in search_queries:
        for item in _search_wikipedia_titles(query, limit=8):
            title = _normalize_text(item.get("title"))
            title_key = title.lower()
            if not title or title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            name = _clean_recommendation_candidate_name(title)
            if not _is_plausible_attraction_name(name):
                continue
            if _looks_like_generic_destination_candidate(name, place):
                continue

            lowered_name = name.lower()
            if any(token in lowered_name for token in ["tourism in ", "list of ", "history of ", "transport in "]):
                continue
            if lowered_name in seen_names:
                continue

            summary = fetch_wikipedia_summary(name, location=place)
            description = _normalize_text(summary.get("description"))
            if _has_placeholder_description(description):
                description = ""

            candidate = {
                "name": name,
                "description": description,
                "image": _normalize_text(summary.get("image_url")),
                "ticket_price": "",
                "sources": [_normalize_text(summary.get("source_url"))] if _normalize_text(summary.get("source_url")) else [],
                "source_type": "wikipedia",
            }
            candidate["score"] = _recommendation_quality_score(candidate, place)
            seen_names.add(lowered_name)
            candidates.append(candidate)
            if len(candidates) >= limit:
                return candidates
    return candidates


def _collect_offline_catalog_recommendation_candidates(
    place: str,
    seen_names: set[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    try:
        from app.tools.tools import TRAVEL_ATTRACTION_CATALOG
    except Exception:
        return []

    place_key = _normalize_text(place).split(",", 1)[0].strip().lower()
    catalog = TRAVEL_ATTRACTION_CATALOG.get(place_key, [])
    if not isinstance(catalog, list):
        return []

    candidates: list[dict[str, Any]] = []
    for row in catalog:
        if not isinstance(row, dict):
            continue
        name = _normalize_text(row.get("name"))
        if not _is_plausible_attraction_name(name):
            continue
        if _looks_like_generic_destination_candidate(name, place):
            continue
        lowered_name = name.lower()
        if lowered_name in seen_names:
            continue
        seen_names.add(lowered_name)

        price_value = row.get("price", 0)
        currency = _normalize_text(row.get("currency"))
        ticket_price = "Free"
        if isinstance(price_value, (int, float)) and float(price_value) > 0:
            formatted_price = int(price_value) if float(price_value).is_integer() else round(float(price_value), 2)
            ticket_price = f"{currency} {formatted_price}".strip()

        candidate = {
            "name": name,
            "description": _normalize_text(row.get("information")),
            "image": _normalize_text(row.get("image")),
            "ticket_price": ticket_price,
            "sources": [],
            "source_type": "offline_catalog",
        }
        candidate["score"] = _recommendation_quality_score(candidate, place)
        candidates.append(candidate)
        if len(candidates) >= limit:
            return candidates
    return candidates


def get_attractions_by_place(place: str, query_type: str | None = None) -> list[dict[str, str]]:
    query_hint = _normalize_text(query_type)
    place = _canonicalize_place_name(place)
    if not place:
        return []

    candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if api_key:
        candidates.extend(
            _collect_search_recommendation_candidates(
                place=place,
                api_key=api_key,
                seen_names=seen_names,
                query_hint=query_hint,
                limit=14,
            )
        )

    osm_limit = 8 if api_key else 14
    osm_pois = _get_osm_city_pois(place, limit=osm_limit)
    for poi in osm_pois:
        enriched = _enrich_poi_with_knowledge(poi, place)
        name = _normalize_text(enriched.get("name"))
        if not _is_plausible_attraction_name(name):
            continue
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        desc = _normalize_text(enriched.get("description"))
        sources = enriched.get("sources", []) if isinstance(enriched.get("sources"), list) else []
        candidate = {
            "name": name,
            "description": desc,
            "image": _normalize_text(enriched.get("image")),
            "ticket_price": _normalize_text(enriched.get("ticket_price")),
            "sources": [_normalize_text(s) for s in sources if _normalize_text(s)],
            "source_type": "osm",
            "wikipedia": _normalize_text(enriched.get("wikipedia")),
            "wikidata": _normalize_text(enriched.get("wikidata")),
            "website": _normalize_text(enriched.get("website")),
        }
        if not _is_valid_recommendation_entity(candidate, place):
            continue
        candidate["score"] = _recommendation_quality_score(candidate, place)
        candidates.append(candidate)
        if len(candidates) >= 14:
            break

    if len(candidates) < 4:
        candidates.extend(
            _collect_wikipedia_recommendation_candidates(
                place=place,
                seen_names=seen_names,
                query_hint=query_hint,
                limit=max(4, 12 - len(candidates)),
            )
        )
    if len(candidates) < 4:
        candidates.extend(
            _collect_offline_catalog_recommendation_candidates(
                place=place,
                seen_names=seen_names,
                limit=max(4, 12 - len(candidates)),
            )
        )

    candidates = _inject_seed_recommendation_candidates(place=place, candidates=candidates, seen_names=seen_names)

    candidates = [
        c
        for c in candidates
        if _is_valid_recommendation_entity(c, place)
    ]
    candidates.sort(key=lambda c: int(c.get("score", 0)), reverse=True)
    normalized = normalize_recommendations_with_gemini(user_query=query_hint or place, city=place, candidates=candidates[:14])
    if not normalized:
        normalized = candidates[:12]

    final_items: list[dict[str, str]] = []
    base_candidates_by_name = {
        _normalize_text(candidate.get("name")).lower(): candidate
        for candidate in candidates
        if _normalize_text(candidate.get("name"))
    }
    for item in normalized[:12]:
        name = _normalize_text(item.get("name"))
        base_item = base_candidates_by_name.get(name.lower(), item)
        if not _is_valid_recommendation_entity(base_item, place):
            continue
        desc = _normalize_text(item.get("description"))
        if _has_placeholder_description(desc):
            desc = ""
        sources = item.get("sources", []) if isinstance(item.get("sources"), list) else []
        source_link = _normalize_text(sources[0]) if sources else ""
        final_items.append(
            {
                "name": name,
                "brief_description": desc,
                "image": _normalize_text(item.get("image")),
                "ticket_price": _normalize_text(item.get("ticket_price")),
                "source_link": source_link,
            }
        )
    final_seen_names = {item["name"].lower() for item in final_items if _normalize_text(item.get("name"))}
    if len(final_items) < 8:
        for candidate in sorted(candidates, key=lambda item: int(item.get("score", 0)), reverse=True):
            name = _normalize_text(candidate.get("name"))
            if not name or name.lower() in final_seen_names:
                continue
            final_seen_names.add(name.lower())
            sources = candidate.get("sources", []) if isinstance(candidate.get("sources"), list) else []
            desc = _normalize_text(candidate.get("description"))
            final_items.append(
                {
                    "name": name,
                    "brief_description": "" if _has_placeholder_description(desc) else desc,
                    "image": _normalize_text(candidate.get("image")),
                    "ticket_price": _normalize_text(candidate.get("ticket_price")),
                    "source_link": _normalize_text(sources[0]) if sources else "",
                }
            )
            if len(final_items) >= 12:
                break
    return final_items


def _is_valid_ticket_price_output(value: Any) -> bool:
    if value is None:
        return True
    text = _normalize_text(value)
    if not text:
        return False
    if text == "Free":
        return True
    return bool(re.fullmatch(r"RM\s\d+(?:\.\d{1,2})?(?:–RM\s\d+(?:\.\d{1,2})?)?", text))


def _is_cache_entry_usable(entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    opening_hours = _normalize_text(entry.get("opening_hours"))
    ticket_price = _normalize_text(entry.get("ticket_price"))
    visit_duration = _normalize_text(entry.get("visit_duration"))
    name = _normalize_text(entry.get("name"))
    description = _normalize_text(entry.get("description"))

    if opening_hours and not is_valid_opening_hours(opening_hours):
        return False
    if ticket_price and not _is_valid_ticket_price_output(ticket_price):
        return False
    if visit_duration and not _is_valid_visit_duration_output(
        normalize_visit_duration(visit_duration, attraction_name=name, context_text=description)
    ):
        return False
    return True



def get_attraction_info(
    attraction_name: str,
    location: str | None = None,
    enrichment_mode: str = "detail",
) -> dict[str, Any]:
    attraction_name = attraction_name.strip()
    attraction_aliases = _build_attraction_aliases(attraction_name)
    lookup_name = _preferred_lookup_name(attraction_name, aliases=attraction_aliases)
    result: dict[str, Any] = {
        "query_type": "attraction_info",
        "name": attraction_name,
        "description": "",
        "image_url": "",
        "opening_hours": "",
        "visit_duration": "",
        "ticket_price": "",
        "ticket_status": "unknown",
        "price_type": "unknown",
        "price_note": "Official price not found.",
        "ticket_price_candidates": [],
        "sources": [],
    }

    if not attraction_name:
        result["name"] = ""
        result["visit_duration"] = estimate_visit_duration(attraction_name)
        return result

    cache_key = f"{attraction_name.lower()}::{(location or '').strip().lower()}"
    with _CACHE_LOCK, _CACHE_FILE_LOCK:
        cache = _load_cache()
        cached = cache.get(cache_key)
        if cached and _is_cache_entry_usable(cached) and _cache_entry_fresh(cached):
            return cached

    # OSM/Wikipedia/Wikidata first
    poi = _search_osm_poi_by_name(lookup_name, location)
    if poi:
        poi = _enrich_poi_with_knowledge(poi, location)
        result["name"] = _normalize_text(poi.get("name")) or attraction_name
        result["description"] = _normalize_text(poi.get("description"))
        result["image_url"] = _normalize_text(poi.get("image"))
        result["opening_hours"] = _normalize_opening_hours_value(_normalize_text(poi.get("opening_hours")))
        result["ticket_price"] = _normalize_text(poi.get("ticket_price"))
        result["sources"] = poi.get("sources", []) if isinstance(poi.get("sources"), list) else []

    if not result["description"] or not result["image_url"]:
        wiki_queries = [lookup_name, attraction_name, *[alias for alias in attraction_aliases if re.search(r"[A-Za-z]", alias)]]
        seen_wiki_queries: set[str] = set()
        for wiki_query in wiki_queries:
            normalized_query = _normalize_text(wiki_query)
            if not normalized_query or normalized_query.lower() in seen_wiki_queries:
                continue
            seen_wiki_queries.add(normalized_query.lower())
            wiki_summary = fetch_wikipedia_summary(attraction_name=normalized_query, location=location)
            if wiki_summary.get("description") and not result["description"]:
                result["description"] = wiki_summary.get("description", "")
            if wiki_summary.get("image_url") and not result["image_url"]:
                result["image_url"] = wiki_summary.get("image_url", "")
            if wiki_summary.get("source_url"):
                result["sources"].append(wiki_summary["source_url"])
            if result["description"] and result["image_url"]:
                break

    nominatim_result = fetch_nominatim_place(attraction_name=lookup_name, location=location)
    if nominatim_result.get("osm_url"):
        result["sources"].append(nominatim_result["osm_url"])

    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    _debug_log(f"serpapi_api_key_found={bool(api_key)}")
    preferred_sources: list[dict[str, str]] = []
    all_organic: list[dict[str, Any]] = []

    if api_key:
        location_suffix = f" {location}" if location else ""
        queries = [
            f"{lookup_name}{location_suffix} official ticket",
            f"{lookup_name}{location_suffix} admission fee",
            f"{lookup_name}{location_suffix} opening hours",
            f"{lookup_name}{location_suffix} official website",
        ]

        text_blobs: list[str] = []
        for query in queries:
            try:
                data = _search_google(query, api_key)
            except Exception:
                continue

            organic = data.get("organic_results", [])
            all_organic.extend(organic)
            text_blobs.extend([
                _normalize_text(data.get("knowledge_graph")),
                _normalize_text(data.get("answer_box")),
            ])
            for item in organic[:8]:
                text_blobs.append(_normalize_text(item.get("title")))
                text_blobs.append(_normalize_text(item.get("snippet")))

        preferred_sources = collect_preferred_sources(
            all_organic,
            min_count=3,
            attraction_name=attraction_name,
            aliases=attraction_aliases,
        )
        result["ticket_price_candidates"] = build_ticket_price_candidate_pool(
            sources=preferred_sources,
            attraction_name=lookup_name,
            aliases=attraction_aliases,
        )[:20]
        opening_hour_text_blobs: list[str] = []
        for src in preferred_sources:
            title = _normalize_text(src.get("title"))
            link = _normalize_text(src.get("link"))
            snippet = _normalize_text(src.get("snippet"))
            source_type, source_score = _classify_source_type(title=title, link=link, snippet=snippet)
            _debug_log(
                f"preferred_ticket_source url={link} source_type={source_type} score={source_score}"
            )
            if source_type in {"official_ticket_page", "official_visitor_info", "official_faq", "official_homepage", "official_visitor_guide_pdf"}:
                merged = f"{title}. {snippet}".strip()
                if merged and not _contains_non_business_hours_tokens(merged):
                    opening_hour_text_blobs.append(merged)
        merged_text = "\n".join(t for t in text_blobs if t)
        opening_merged_text = "\n".join(opening_hour_text_blobs)

        if not result["visit_duration"]:
            result["visit_duration"] = extract_visit_duration(merged_text, attraction_name=lookup_name)

        if not result["opening_hours"]:
            result["opening_hours"] = (
                _extract_hours_from_sources(preferred_sources)
                or _extract_high_confidence_opening_hours_from_sources(preferred_sources)
                or _extract_hours(opening_merged_text)
            )
            _debug_log(f"opening_hours_selected={result['opening_hours']}")
        if not result["ticket_price"]:
            has_strong_ticket_sources = _has_strong_ticket_source_evidence(
                preferred_sources,
                attraction_name=attraction_name,
                aliases=attraction_aliases,
            )
            if not preferred_sources:
                result["ticket_price"] = ""
                result["price_type"] = "unknown"
                result["price_note"] = "No attraction-specific ticket source found."
            elif enrichment_mode == "recommendation" and not has_strong_ticket_sources:
                _debug_log("ticket_price_enrichment=skipped_due_to_weak_sources")
                result["ticket_price"] = ""
                result["price_type"] = "unknown"
                result["price_note"] = "Skipped in recommendation mode due to weak ticket sources."
            else:
                _debug_log("ticket_price_enrichment=attempted_due_to_strong_ticket_sources")
                strong_price = resolve_ticket_price_from_sources(
                    preferred_sources,
                    attraction_name=attraction_name,
                    aliases=attraction_aliases,
                )
                _debug_log(f"rule_based_price={strong_price}")
                gemini_price = resolve_ticket_price_with_gemini(
                    attraction_name=lookup_name,
                    location=location,
                    sources=preferred_sources,
                    rule_based_price=strong_price,
                    aliases=attraction_aliases,
                )
                gemini_ticket_price = _normalize_text(gemini_price.get("ticket_price"))
                if gemini_ticket_price:
                    result["ticket_price"] = gemini_ticket_price
                    result["ticket_status"] = "free" if gemini_ticket_price == "Free" else "paid"
                    result["price_type"] = _normalize_text(gemini_price.get("price_type")) or ("range" if "–" in gemini_ticket_price else "official")
                    result["price_note"] = _normalize_text(gemini_price.get("price_note")) or "Gemini-assisted ticket price resolution"
                    _debug_log("ticket_price_path=gemini")
                elif strong_price:
                    result["ticket_price"] = strong_price
                    result["ticket_status"] = "free" if strong_price == "Free" else "paid"
                    result["price_type"] = "exact" if "–" not in strong_price else "range"
                    candidate_count = len(result.get("ticket_price_candidates") or [])
                    result["price_note"] = f"Parsed from ticket-related source content ({candidate_count} candidates reviewed)"
                    _debug_log("ticket_price_path=rule_based_fallback")
                else:
                    price_candidates = result.get("ticket_price_candidates") or _collect_price_candidates_from_sources(
                        preferred_sources,
                        attraction_name=attraction_name,
                        aliases=attraction_aliases,
                    )
                    _debug_log(f"fallback_price_candidates={json.dumps(price_candidates, ensure_ascii=False)}")
                    price_resolution = resolve_ticket_price(price_candidates)
                    result["ticket_price"] = _normalize_text(price_resolution.get("ticket_price"))
                    if result["ticket_price"]:
                        result["ticket_status"] = "free" if result["ticket_price"] == "Free" else "paid"
                    result["price_type"] = _normalize_text(price_resolution.get("price_type")) or "unknown"
                    result["price_note"] = _normalize_text(price_resolution.get("price_note")) or "Official price not found."
                    _debug_log("ticket_price_path=legacy_fallback")

        if preferred_sources and (not result["opening_hours"] or not result["ticket_price"]):
            reasoned = analyze_visit_reasonableness_with_gemini(
                attraction_name=lookup_name,
                location=location,
                sources=preferred_sources,
                current_opening_hours=_normalize_text(result.get("opening_hours")),
                current_ticket_price=_normalize_text(result.get("ticket_price")),
                aliases=attraction_aliases,
            )
            if not result["opening_hours"] and _normalize_text(reasoned.get("opening_hours")):
                result["opening_hours"] = _normalize_text(reasoned.get("opening_hours"))
            if not result["ticket_price"] and _normalize_text(reasoned.get("ticket_price")):
                result["ticket_price"] = _normalize_text(reasoned.get("ticket_price"))
            reasoned_status = _normalize_text(reasoned.get("ticket_status")).lower()
            if reasoned_status in {"free", "paid", "partially_paid"}:
                result["ticket_status"] = reasoned_status
            if _normalize_text(reasoned.get("price_note")):
                result["price_note"] = _normalize_text(reasoned.get("price_note"))

        if not result["image_url"]:
            try:
                image_data = _search_google_images(f"{lookup_name}{location_suffix}", api_key)
            except Exception:
                image_data = {}
            result["image_url"] = _pick_image_url(all_organic, image_data)

        for source in preferred_sources:
            link = _normalize_text(source.get("link"))
            if link:
                result["sources"].append(link)
    else:
        _debug_log("ticket_price_search_enrichment_skipped_no_serpapi_key=True")

    if not result["visit_duration"]:
        result["visit_duration"] = estimate_visit_duration(attraction_name, result.get("description", ""))
    result["visit_duration"] = normalize_visit_duration(
        result.get("visit_duration"),
        attraction_name=result.get("name") or attraction_name,
        context_text=result.get("description", ""),
    )

    result["opening_hours"] = _normalize_opening_hours_value(result.get("opening_hours", ""))
    if not _is_valid_ticket_price_output(result.get("ticket_price")):
        if _normalize_text(result.get("ticket_price")) == "Free":
            pass
        else:
            result["ticket_price"] = ""
    if result["ticket_price"] == "Free":
        result["ticket_status"] = "free"
    elif not result["ticket_price"] and result.get("ticket_status") == "paid":
        result["ticket_status"] = "unknown"
    if result["name"] == attraction_name and lookup_name != attraction_name and (result["description"] or result["sources"]):
        result["name"] = lookup_name

    deduped_sources: list[str] = []
    seen: set[str] = set()
    for url in result.get("sources", []):
        text = _normalize_text(url)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped_sources.append(text)
    result["sources"] = deduped_sources[:8]

    with _CACHE_LOCK, _CACHE_FILE_LOCK:
        cache = _load_cache()
        cache[cache_key] = {**result, "_cached_at": time.time()}
        _save_cache(cache)

    return result


@tool
def attraction_information_tool(attraction_name: str, location: str | None = None) -> dict[str, Any]:
    """
    景点信息工具：返回图片、营业时间、建议游玩时长、门票价格和可追溯来源。

    Args:
        attraction_name: 景点名称，例如 "Petronas Twin Towers"
        location: 可选地点，例如 "Kuala Lumpur"
    """

    return get_attraction_info(attraction_name=attraction_name, location=location)
