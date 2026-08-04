# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Full-stack AI travel assistant. The backend (Python/FastAPI) parses a natural-language travel request with a Google Gemini LLM, dispatches to flight/hotel/attraction agents (which call SerpAPI or fall back to mock/seed data), normalizes everything into one JSON contract, and generates a natural-language summary. The frontend (Vue 3/Vite) submits the request and renders the result as an AI summary plus a timeline.

## Commands

### Backend setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Python 3.11 is recommended (3.13 may hit dependency issues).

### Run backend

```powershell
uvicorn app.server:app --host 127.0.0.1 --port 8000
```

Health check: `GET http://127.0.0.1:8000/health`. Main endpoint: `POST /api/v1/agent/generate_itinerary` with body `{"input": "<natural language travel request>"}`.

### Run frontend

```powershell
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build
npm run preview
```

Set `frontend/.env.local` with `VITE_API_BASE_URL=http://127.0.0.1:8000`. Set `VITE_USE_MOCK=1` to run the frontend against built-in mock data with no backend/keys required; `VITE_DEMO_SEED=1` preloads that mock data on startup.

### Run a single backend script/test

Most files under `app/tests/` and `app/agents/*_demo.py` are runnable scripts (no pytest suite/config exists for most of them), invoked directly:

```powershell
python app/agents/main_agent.py          # runs the full dispatch pipeline against a hardcoded query
python app/agents/attraction_demo.py     # minimal get_attraction_info demo
python app/tests/test_main_agent.py
```

`app/tests/test_attraction_tool.py` is the exception — it's actual pytest (`monkeypatch`-based unit tests):

```powershell
pytest app/tests/test_attraction_tool.py
pytest app/tests/test_attraction_tool.py::test_get_attraction_info_extracts_core_fields
```

### Environment variables

Required in a root `.env` (see `app/config.py`, loaded via `python-dotenv`):

```env
SERPAPI_API_KEY=your_serpapi_key      # flight, hotel, and attraction search
GOOGLE_API_KEY=your_google_api_key    # main agent + attraction workflow LLM calls
GOOGLE_LLM_MODEL=gemini-2.5-flash     # optional, this is the default
```

Optional: `ATTRACTION_TOOL_DEBUG=1` (verbose stderr logging in `attraction_tool.py`), `LANGCHAIN_TRACING_V2` (defaults to `false`; only set `true` if you have a `LANGCHAIN_API_KEY`), `LANGCHAIN_PROJECT` (LangSmith tracing, used in `flight_tool.py`).

Also optional, `server.py`-specific:

```env
APP_API_KEY=...                # X-API-Key shared secret; unset = auth disabled (see below)
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173   # comma-separated
APP_RATE_LIMIT=20/hour         # slowapi rate-limit string, per client IP
```

`frontend/.env.local` needs a matching `VITE_APP_API_KEY` if `APP_API_KEY` is set, or every request from the frontend gets a 401.

Without `SERPAPI_API_KEY`, `flight_tool.py` transparently falls back to deterministic mock flight generation; without it, `hotel_tool.py` returns an `{"error": ...}` entry instead of hotels.

## Architecture

### Request flow (`app/server.py` → `app/agents/main_agent.py`)

1. `POST /api/v1/agent/generate_itinerary` is gated by an optional `X-API-Key` dependency (`_require_api_key`, no-op if `APP_API_KEY` is unset), a per-IP `slowapi` rate limit (`APP_RATE_LIMIT`, default `20/hour`), and CORS restricted to `CORS_ALLOWED_ORIGINS`. The body is a Pydantic `GenerateItineraryRequest` (`input`, or the `departure`/`destination`/`pax`/`time` fallback fields; plus `budget` and `must_visit_attractions` which are accepted alongside either form) and is forwarded to `run_test_main_agent_flow()` in `main_agent.py`. Real HTTP status codes are returned: 400 for a rejected/empty request, 401 for a bad API key, 429 for rate-limit, 500 for an internal exception (full traceback logged server-side, generic message to the client), 502 when `main_agent` itself reports a non-200 payload.
2. `main_agent._dispatch_user_request_by_company()` sends the user's free-text input to Gemini with a prompt that must return a strict JSON "dispatch plan" (hotel request, outbound/inbound flight requests, attraction task), validated by a `JsonOutputParser` bound to Pydantic models (`_DispatchPlanModel` etc.). If the LLM call/parse fails for any reason, `_build_fallback_dispatch_plan()` derives the same structure using a **regex/keyword-based** natural-language parser (`parse_natural_language_to_hotel_json`) that only recognizes a small hardcoded city map (KL, Penang, Bangkok, Singapore, Seoul, Beijing, Shanghai, Pattaya) and `YYYY-MM-DD`/`YYYY.MM.DD` dates, defaulting to Seoul / 2026-05-01–2026-05-04 when nothing matches. Departure city is resolved separately by `_extract_departure_city()` (directional regex on `从X到Y` / `from X to Y`) and applied to both the LLM and fallback paths via `_apply_user_route_constraints()`; only when no departure city can be found does it fall back to `_DEFAULT_DEPARTURE_CITY` ("Shenzhen"), and that fallback is recorded in the response's `warnings` array. Note the LLM prompt's own missing-date default (`2026-03-26`/`2026-03-28`) does not match the fallback parser's (`2026-05-01`/`2026-05-04`) — check which path you're editing.
3. `_apply_user_trip_constraints()` overlays the request's `budget`/`pax` (when provided) onto both flight legs, overriding the LLM/fallback's hardcoded `0-10000 MYR` / `1 passenger` defaults. The dispatch plan then drives three independent calls: `run_hotel_agent` (up to 3 candidates), `run_flight_agent` (called twice — outbound and inbound legs, each up to 3 candidates — merged into one `flights` list tagged with `"leg": "outbound"/"inbound"`), and `run_seed_agent` (attractions, seed-list based). `must_visit_attractions` from the request, if any, are enriched via `_build_must_visit_attraction_items()` (same `get_attraction_info` lookup the seed agent uses) and prepended to the seed results, deduplicated by name, before the combined list is capped to 10.
4. `_build_standard_payload()` normalizes all three heterogeneous results into the frontend's fixed contract (`flights`/`hotels`/`views` arrays). Attraction extraction goes through a single `_extract_attraction_items()` helper that only accepts a list, or a dict exposing `attractions`/`results`/`views` as a list — unknown shapes are dropped with a `warnings` entry rather than guessed at. `_schedule_attractions()` then allocates the extracted attractions across the trip's days (`_MAX_ATTRACTIONS_PER_DAY = 4`, two-hour slots from 09:00 in the destination city's fixed UTC offset) and stamps each with `arrival_time`/`departure_time` before they reach `normalized_views`. Default check-in/check-out dates (used when the user gives none) are computed relative to *today* via `_default_trip_dates()` (today + 30 days, 3-night default), not a hardcoded literal — a hardcoded past date silently returns zero real flights/hotels once "today" moves past it, which is exactly what happened before this was fixed.
5. `_generate_natural_language_output()` makes a second Gemini call to produce the human-readable `output` string shown in the frontend's AI panel (matches user's input language, targets a fixed field-by-field format for flights/hotels/views, emoji encouraged).

### Agents vs. tools split (`app/agents/` vs `app/tools/`)

- **Tools** (`app/tools/`) are LangChain `@tool`-decorated functions that do the actual external I/O (SerpAPI calls) or pure computation, and are meant to be invoked via `.invoke({...})`, not called directly.
  - `flight_tool.search_and_filter_flights`: SerpAPI Google Flights if `SERPAPI_API_KEY` set, else deterministic mock data seeded from `hash(date+origin+dest)`; converts budget to MYR (via `app.tools.currency`), filters, returns up to `max_results` (default 3, read from the query JSON), cheapest first.
  - `hotel_tool.search_hotels`: SerpAPI Google Hotels, returns up to `max_results` (default 3, function parameter).
  - `attraction_tool.py` (~3500 lines): the most complex module — scrapes/aggregates attraction info (description, ticket price, opening hours, visit duration, image) from Google search + Wikipedia + Nominatim, with heavy text-cleaning/scoring heuristics (price pattern regexes, source-platform priority scoring, opening-hours validation) and a JSON on-disk cache (`app/data/attraction_cache.json`) plus a curated seed list (`app/data/city_attraction_seeds.json`) used as fallback/supplement per city. The cache is guarded by both an in-process `threading.Lock` and a cross-process `filelock.FileLock` (`attraction_cache.json.lock`), writes go through a temp-file-plus-`os.replace()` atomic swap, and entries carry a `_cached_at` timestamp checked against a 30-day TTL — a plain `uvicorn --workers N` deployment no longer loses concurrent writes or trusts stale ticket prices/hours forever.
  - `multi_route_tool.optimize_multi_location_route`: brute-force permutation TSP over a small set of locations using SerpAPI Google Maps Directions (auto-switches to walking mode under 1km).
  - `tools.py`: a self-contained hardcoded `TRAVEL_ATTRACTION_CATALOG` (curated attractions per city with fixed prices/hours) plus a `travel_planner` tool that builds a day-by-day `{"views": [...]}` plan directly from dates/cities without calling the LLM — this is a separate, simpler code path from `attraction_seed_agent`/`attraction_tool.py` and the two are not unified.
- **Agents** (`app/agents/`) are the orchestration layer: they call tools, reshape input/output JSON, and (for `main_agent.py`, `attraction_agent.py`, `transportation_agent.py`) make their own direct `ChatGoogleGenerativeAI` calls for NL parsing/translation.
  - `flight_agent.run_flight_agent`, `hotel_agent.run_hotel_agent`: thin adapters around the corresponding tool.
  - `attraction_seed_agent.run_seed_agent`: recall-only — only returns attractions present in the `CITY_ATTRACTION_SEEDS` seed file, enriched via `attraction_tool.get_attraction_info`; this is what `main_agent.py` actually calls today.
  - `attraction_agent.py`: a richer, LLM-driven recommendation/detail agent (city normalization, candidate enrichment/merging) that is more capable than the seed agent but is currently not wired into `main_agent.py`'s main flow — check before assuming it's live.
  - `transportation_agent.run_travel_agent`: takes free-text ("my hotel is X, attractions are Y, Z"), uses Gemini to extract a structured location list, runs `multi_route_tool` for optimal attraction ordering, computes hotel↔first/last-attraction legs separately, then does a final Gemini pass to translate the whole Chinese-keyed result into English JSON. Also currently not wired into `main_agent.py`.

Because several agents overlap in responsibility (three different "get attractions for a city" paths: `attraction_seed_agent`, `attraction_agent`, `tools.travel_planner`), always check which one `main_agent.py` is actually invoking before modifying attraction behavior — modifying the unused ones will have no visible effect on the live API.

### Money/units

All prices returned to the frontend are normalized to MYR ("RM"). `app/tools/currency.py` is the single source of truth for the exchange-rate table (`EXCHANGE_TO_MYR`) and `convert_to_myr()`; `flight_tool.py`, `tools.py`, and `attraction_tool.py` all import from it rather than keeping their own copies. Still hardcoded and demo-grade (not live rates) — keep the table in sync manually if you add a currency.

### Frontend (`frontend/src/`)

- `stores/itinerary.js` (Pinia): owns the single source of truth (`raw.flights/hotels/views`) and the `generateItinerary()` action that POSTs to the backend (or serves `MOCK_RAW` when `VITE_USE_MOCK=1`). Sends `X-API-Key: import.meta.env.VITE_APP_API_KEY` on every request when that env var is set. Also drives a fake "agent thinking" log (`ai.lines`) via a `setInterval` spinner purely for UI feedback while the real request is in flight.
- `lib/transformItinerary.js`: pure functions that turn the raw `{flights, hotels, views}` payload into a flat, time-sorted timeline: builds per-item entries (`makeBaseItem`), sorts by timestamp then type (`marker < flight < hotel < view < cluster`), clusters events within 60s of each other into a single `cluster` item, and injects date `marker` items per day group. Consumed by the store's `timelineItems`/`timelineByDate` getters.
- `components/Timeline/TimelineStrip.vue` renders those getters as an actual horizontal, scroll/drag timeline (`useHorizontalWheelScroll`), one `TimelineNode.vue` per item, connected by a line. `components/Timeline/TimelinePanel.vue` renders below it and still reads `raw.flights`/`raw.hotels`/`raw.views` directly for full-detail flat cards (candidate comparison, hotel links, etc.) — the two are complementary, not a replacement of one by the other. `components/AiStatusBar.vue` and `components/HelloWorld.vue` remain genuinely unimported leftovers; don't assume they're wired in.
- `components/ItineraryForm.vue` has a "Must-Visit Attractions" text input (comma-separated) feeding `must_visit_attractions` in the request body, in addition to the free-text `input` textarea.
- Single route (`router/index.js`): everything lives on `ItineraryBuilder.vue` at `/`.

## Team conventions (from README)

- Don't push directly to `main`; branch as `feature/feature-name`, PR into `main`.
- Don't commit `.venv`, `node_modules`, or a `.env` with real secrets.
- snake_case for regular filenames; tests live under `app/tests/` named `test_*.py`.

## Known issues

`PROJECT_REVIEW_MERGED_EN.md` (and its Chinese counterpart `PROJECT_REVIEW_MERGED.md`) has the
original full, code-verified audit, but a large "Phase 3" pass has since closed most of its P0/P1
items — treat that document as historical unless you've re-verified a specific claim against
current code. Still open as of this writing:

- The request pipeline (dispatch LLM call → hotel/flight/attraction lookups → summary LLM call)
  runs fully sequentially with no per-call or handler-level timeout; a slow upstream call just
  makes the request take longer (real end-to-end requests commonly take 30–100s).
- `transportation_agent.py` / `multi_route_tool.py` (multi-stop route optimization, O(n!) TSP)
  are imported by `main_agent.py` but never called — deliberately left as-is, not scheduled for
  removal or completion.
- `app/tests/` still has several manual/interactive scripts collected alongside the two real
  pytest files (`test_attraction_tool.py`, `test_main_agent_regressions.py`); see §"Run a single
  backend script/test" above.
- No CI, no linter/formatter config, `requirements.txt` has no version pins.

Already fixed (do not assume otherwise without re-checking): XSS sanitization on the AI summary
(backend strip + frontend DOMPurify), committed `node_modules`/conflict-marker `.gitignore`,
the CNY exchange-rate mismatch (now a single shared table), the `attraction_tool` split-module
import bug, flight/hotel single-result truncation (now up to 3 candidates each, tagged by leg),
`budget`/`must_visit_attractions` wiring, API-key auth + per-IP rate limiting + CORS lockdown,
the attraction cache's cross-process race (`filelock` + atomic write + TTL), and the frontend
timeline being dead code (now rendered via `TimelineStrip.vue`).
