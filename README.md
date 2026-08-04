# AI Travel Assistant

This is a full-stack AI travel assistant project.

The backend is built with Python and FastAPI. It parses a natural-language travel request with Gemini, calls flight, hotel, and attraction tools, schedules attractions onto actual trip days, and returns one normalized itinerary payload. The frontend is built with Vue 3 and Vite. It lets users enter a travel request and displays the AI summary plus flight/hotel/attraction cards.

## 1. What This Project Does

- Accepts a natural language travel request (e.g. "我从吉隆坡去首尔玩，一个人，2026.08.10到2026.08.12")
- Correctly identifies departure vs. destination city, even for the LLM-fallback (regex-based) parser
- Generates flight, hotel, and attraction results (real data via SerpAPI, or deterministic mock data if no key is configured), returning up to 3 candidate flights/hotels each rather than a single pick
- Accepts an explicit `budget` and a list of `must_visit_attractions`, which get prioritized in the schedule
- Schedules attractions onto specific trip days with timezone-aware `arrival_time` / `departure_time`, capped to a sane per-day/per-trip limit; default dates (when the user gives none) are computed relative to today, not a fixed literal
- Returns a natural language summary from the AI, in the same language as the user's input, sanitized against script injection both server-side and client-side
- Renders a real scroll/drag timeline of the trip in the frontend, in addition to detail cards
- Returns real HTTP status codes (400/401/429/500/502) instead of always returning 200
- Gates the main endpoint with an optional shared-secret API key, per-IP rate limiting, and a locked-down CORS origin list
- Supports running the attraction tool and the main agent as standalone scripts, independent of the API server

## 2. Project Structure

```text
ai-travel-assistant-with-frontend/
├── app/
│   ├── agents/               # Orchestration: main_agent, flight/hotel/attraction agents
│   ├── tools/                # Flight, hotel, attraction, and currency tools
│   ├── tests/                # pytest tests + a few manual/interactive scripts
│   ├── data/                 # Seed attraction lists + on-disk attraction cache
│   ├── config.py
│   └── server.py             # FastAPI backend entry
├── frontend/                 # Vue + Vite frontend
├── requirements.txt          # Python dependencies
├── CLAUDE.md                 # Architecture notes + known issues (for contributors)
├── Install_venv_and_dependencies.md
└── README.md
```

## 3. Environment Requirements

### Backend

- Python 3.11 is recommended
- The current project docs were also written with Python 3.11 in mind
- If your machine uses a newer Python (3.13+), you can try it first; if dependency issues appear, switch to Python 3.11. `requirements.txt` does not pin exact versions, so results can vary between machines.

### Frontend

- Node.js 22.x LTS is recommended

## 4. Required Environment Variables

Create a `.env` file in the project root (it's git-ignored — never commit it):

```env
SERPAPI_API_KEY=your_serpapi_key
GOOGLE_API_KEY=your_google_api_key
GOOGLE_LLM_MODEL=gemini-2.5-flash
```

Optional variables:

```env
ATTRACTION_TOOL_DEBUG=0
LANGCHAIN_TRACING_V2=false
LANGCHAIN_PROJECT=flight-agent
APP_API_KEY=some-random-secret
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
APP_RATE_LIMIT=20/hour
```

Recommended frontend variables in `frontend/.env.local` (also git-ignored):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_APP_API_KEY=some-random-secret
VITE_USE_MOCK=0
VITE_DEMO_SEED=0
```

Notes:

- `SERPAPI_API_KEY` is required for real flight, hotel, and attraction search. Without it, flights fall back to deterministic mock data and hotels return an `error` entry — the app still runs, just with degraded data.
- `GOOGLE_API_KEY` is required for request parsing and the AI summary. Without it, the app falls back to a regex-based parser for parsing (still works for common "from X to Y" phrasing) but the natural-language summary/output will fail.
- `GOOGLE_LLM_MODEL` defaults to `gemini-2.5-flash` if unset.
- `LANGCHAIN_TRACING_V2` defaults to `false`. Only set it to `true` if you have a `LANGCHAIN_API_KEY` and actually want LangSmith tracing — otherwise it just generates failed upload attempts in the logs.
- `APP_API_KEY` gates `POST /api/v1/agent/generate_itinerary` behind an `X-API-Key` header. **Left unset, the endpoint has no auth** (matches this project's "missing key → degrade, don't hard-fail" convention) — fine for pure local dev, not fine for anything internet-reachable. If you set it, `frontend/.env.local`'s `VITE_APP_API_KEY` must match, or every frontend request gets a 401. Generate one with e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `CORS_ALLOWED_ORIGINS` defaults to the local Vite dev server ports if unset.
- `APP_RATE_LIMIT` defaults to `20/hour` per client IP (via `slowapi`); exceeding it returns HTTP 429.
- `VITE_API_BASE_URL` is the backend API base URL used by the frontend. Without it, the frontend's requests silently 404 against the Vite dev server instead of reaching the backend — set this file even for local dev.
- `VITE_USE_MOCK=1` makes the frontend use mock data instead of calling the backend.
- `VITE_DEMO_SEED=1` preloads demo data in the frontend on startup.

Important:

- Never commit real API keys to GitHub.
- `.env`, `.env.*` (except `.env.example`), and `frontend/.env.local` are all git-ignored — verify with `git check-ignore -v .env` if unsure.

## 5. Install Backend Dependencies

Run the following commands in the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you already have a virtual environment, activate it and install the dependencies there.

## 6. Start the Backend

Run this command in the project root:

```powershell
uvicorn app.server:app --host 127.0.0.1 --port 8000
```

After the server starts, you can open:

- Health check: `http://127.0.0.1:8000/health`

Main backend endpoint:

- `POST /api/v1/agent/generate_itinerary`

Request example (`budget` and `must_visit_attractions` are both optional):

```json
{
  "input": "I am traveling from Kuala Lumpur to Seoul alone from 2026-08-10 to 2026-08-12",
  "budget": { "min": 500, "max": 2000, "currency": "MYR" },
  "must_visit_attractions": ["Gwangjang Market"]
}
```

If `APP_API_KEY` is set on the backend, requests also need a matching header: `X-API-Key: <APP_API_KEY>`.

Successful response (HTTP 200):

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input": "original user input",
    "flights": [
      { "leg": "outbound", "departure_airport": "KUL", "arrival_airport": "ICN", "price": 604.0, "...": "..." },
      { "leg": "outbound", "departure_airport": "KUL", "arrival_airport": "ICN", "price": 672.0, "...": "..." },
      { "leg": "inbound", "departure_airport": "ICN", "arrival_airport": "KUL", "price": 591.0, "...": "..." }
    ],
    "hotels": [ { "name": "Hotel S Seoul", "price": 169.0, "...": "..." } ],
    "views": [
      {
        "name": "Gwangjang Market",
        "arrival_time": "2026-08-10T09:00:00+09:00",
        "departure_time": "2026-08-10T11:00:00+09:00",
        "...": "..."
      }
    ],
    "warnings": ["景点数量超过本次旅行可安排的上限，已仅安排前 8 个景点。"],
    "output": "natural language summary generated by the AI"
  }
}
```

Failure responses use real HTTP status codes, not always 200:

- `400` — the request was rejected (e.g. missing/empty `input`, or `input` too long)
- `401` — missing or wrong `X-API-Key` (only applies if `APP_API_KEY` is configured)
- `429` — per-IP rate limit exceeded (`APP_RATE_LIMIT`, default 20/hour)
- `500` — the backend failed internally (full traceback is logged server-side; the client only gets a generic message)
- `502` — an upstream dependency (LLM/SerpAPI) failed in a way `main_agent` reported as non-200

Field summary:

- `flights` / `hotels`: up to 3 candidates each (price-sorted, cheapest first), not just a single pick. Flight items carry a `leg` field (`outbound`/`inbound`) so you can tell the two directions apart once there are several of each.
- `budget`: overrides the dispatch plan's default `0-10000 MYR` on both flight legs when provided; flights outside the range are filtered out entirely (an unreasonably tight budget can legitimately return zero flights).
- `must_visit_attractions`: enriched the same way seed attractions are, deduplicated against them by name, and scheduled first (so they land in the earliest available slot).
- `views`: attraction list, each item scheduled onto a specific day with timezone-aware `arrival_time` / `departure_time`. If more attractions were found than fit the trip, the excess are dropped and noted in `warnings`.
- `warnings`: array of human-readable notices about degraded/defaulted/truncated data (e.g. "no departure city detected, defaulted to Shenzhen").
- `output`: natural language itinerary summary shown in the frontend's AI panel, matching the user's input language.

## 7. Start the Frontend

Run these commands inside the frontend directory:

```powershell
cd frontend
npm install
npm run dev
```

After startup, the terminal usually prints a local URL such as:

- `http://localhost:5173`

Make sure `frontend/.env.local` exists with `VITE_API_BASE_URL` pointing at your running backend (see §4) — there is no dev-server proxy fallback, so without it requests will silently fail against the Vite dev server itself. If the backend has `APP_API_KEY` set, `frontend/.env.local` also needs a matching `VITE_APP_API_KEY`, or every request gets a 401.

## 8. Simplest Usage Flow

Recommended order:

### Option 1: Full End-to-End Run

1. Create and configure `.env` in the project root (§4)
2. Start the backend: `uvicorn app.server:app --host 127.0.0.1 --port 8000`
3. Create `frontend/.env.local` with `VITE_API_BASE_URL=http://127.0.0.1:8000`
4. `cd frontend && npm install`
5. Start the frontend: `npm run dev`
6. Open the printed local URL, enter a travel request, click **Generate Itinerary**

Example input:

```text
I am traveling from Kuala Lumpur to Seoul alone from 2026.08.10 to 2026.08.12
```

A real request takes roughly 30–90 seconds (it makes several sequential LLM and SerpAPI calls) — this is a known limitation, not a hang; see `PROJECT_REVIEW_MERGED_EN.md` (P1-D) if you want to speed it up.

### Option 2: View Only the Frontend

If you do not want to configure backend keys yet, set this in `frontend/.env.local`:

```env
VITE_USE_MOCK=1
```

Then run:

```powershell
cd frontend
npm install
npm run dev
```

The frontend will use mock data directly, which is useful if you only want to preview the UI.

## 9. Run Demo Scripts Separately

### Main Agent Demo

Run in the project root:

```powershell
python app/agents/main_agent.py
```

This script will:

- Use a default travel query
- Run the main agent workflow
- Print the dispatch request
- Print the normalized result, including scheduled `arrival_time`/`departure_time` per attraction

### Attraction Tool Minimal Demo

Run in the project root:

```powershell
python app/agents/attraction_demo.py
```

This script shows:

- How to call `get_attraction_info`
- How to insert attraction data into a single-day itinerary

## 10. Execution Flow

After the user enters a natural language request, the overall flow is:

1. The frontend sends `{"input": "...", "budget": {...}, "must_visit_attractions": [...]}` to the backend API, with an `X-API-Key` header if one is configured.
2. `app/server.py` checks the API key (if configured) and rate limit, then validates the payload (rejects empty/oversized input with HTTP 400).
3. `app/agents/main_agent.py` parses the input via Gemini into a structured dispatch plan (hotel request, outbound/inbound flight requests, attraction task); if the LLM call fails, a regex-based fallback parser takes over, with any substitutions noted in `warnings`. Default trip dates, when the user gives none, are computed relative to today rather than a fixed date. Any `budget`/`pax` from the request then overrides the plan's defaults.
4. The main workflow calls the hotel agent (up to 3 candidates), the flight agent (twice — outbound and inbound legs, up to 3 candidates each), and the attraction seed agent, in sequence. `must_visit_attractions`, if given, are merged into the attraction list ahead of the seed results.
5. Retrieved attractions are scheduled onto trip days with timezone-aware start/end times.
6. The backend normalizes everything into one standard `{flights, hotels, views, warnings}` structure.
7. The backend makes a second Gemini call to generate a natural language summary into `output`, then strips any obvious script-injection patterns as a backstop.
8. The frontend renders `output` in the AI Answer panel (sanitized again client-side with DOMPurify before being injected as HTML), a scroll/drag timeline strip, and `flights`/`hotels`/`views` as detail cards.

Note: `transportation_agent` / `multi_route_tool` (multi-stop route optimization) exist in the codebase but are **not** called by this flow today — they're imported in one place but never invoked. Don't assume route optimization is live.

## 11. Key Files

- `app/server.py`: backend API entry, request validation, HTTP status codes, logging
- `app/agents/main_agent.py`: main orchestration workflow, dispatch parsing, attraction scheduling
- `app/agents/attraction_demo.py`: minimal attraction demo
- `app/tools/attraction_tool.py`: core attraction search tool
- `app/tools/flight_tool.py`: flight tool
- `app/tools/hotel_tool.py`: hotel tool
- `app/tools/currency.py`: single shared MYR exchange-rate table, used by all three tools above
- `frontend/src/components/ItineraryForm.vue`: frontend input form + AI answer panel
- `frontend/src/stores/itinerary.js`: frontend request logic and state management

## 12. Testing and Troubleshooting

Only two files under `app/tests/` are genuine automated `pytest` tests:

```powershell
pytest app/tests/test_attraction_tool.py
pytest app/tests/test_main_agent_regressions.py
```

Or run both at once:

```powershell
pytest app/tests/test_attraction_tool.py app/tests/test_main_agent_regressions.py
```

The rest of `app/tests/` (`test_main_agent.py`, `test_attraction_agent.py`, `test_call_attraction_agent.py`, `test_agent.py`, `test_serpapi.py`, `test_google_*.py`) are manual/interactive scripts, not pytest suites — some require API keys pytest won't provide automatically (e.g. `OPENAI_API_KEY` for the OpenAI-based ones), and running the whole directory under `pytest` is not recommended.

If something fails, check these items first:

- Whether the virtual environment is activated
- Whether `pip install -r requirements.txt` has been run
- Whether `SERPAPI_API_KEY` is configured
- Whether `GOOGLE_API_KEY` is configured
- Whether the backend URL matches `VITE_API_BASE_URL`

## 13. Common Issues

### 1) Clicking the button in the frontend returns no result

Possible reasons:

- The backend is not running
- `frontend/.env.local` doesn't exist or `VITE_API_BASE_URL` is wrong
- API keys are missing, causing backend errors — check the terminal running `uvicorn` for a logged traceback (the client only ever sees a generic error message now, by design)

### 2) The backend returns HTTP 400 / 401 / 429 / 500 / 502

This is expected behavior, not a regression — the API used to always return HTTP 200 even on failure. Check the response body's `message` field, and check the backend's own log output for the full traceback if it's a 500/502. A 401 means `APP_API_KEY` is set on the backend but the request's `X-API-Key` header is missing or wrong (check `frontend/.env.local`'s `VITE_APP_API_KEY` matches). A 429 means you've hit `APP_RATE_LIMIT` (default 20 requests/hour per IP) — wait or raise the limit for local testing.

### 3) Dependency installation fails

Possible reasons:

- The Python version is too new or too old
- The virtual environment is not activated
- Package download failed because of network issues

Python 3.11 and Node.js 22.x are recommended.

### 4) I only want to preview the page without configuring the backend

Add this to `frontend/.env.local`:

```env
VITE_USE_MOCK=1
```

The frontend will then show mock data directly.

### 5) `UnicodeEncodeError` / garbled console output on Windows

If you're running an older checkout, Windows consoles default to the GBK codepage, which can't print the emoji/Chinese debug output this project logs, crashing the request. This is fixed as of the current code (`app/server.py` and `app/agents/main_agent.py` force UTF-8 on stdout/stderr) — if you still see this, make sure you're on the latest code.

## 14. Known Limitations

These are intentionally out of scope for now, not bugs to report:

- Requests run fully sequentially (dispatch LLM → hotel/flight/attraction lookups → summary LLM) with no per-call or handler-level timeout, so a slow upstream call can make a request take 30–100+ seconds. Not a hang, just unoptimized.
- `transportation_agent.py` / `multi_route_tool.py` (multi-stop route optimization) exist in the codebase but are deliberately not wired into the main flow.
- The API key check is a single shared secret, not per-user auth — enough to stop anonymous scripted abuse, not a substitute for real authentication if you ever expose this beyond your own use.
- `requirements.txt` has no version pins; results can vary between machines/times.

See `PROJECT_REVIEW_MERGED_EN.md` for the original full list with severity ranking — most of its P0/P1 items (auth, rate limiting, currency table mismatch, cache race condition, single-candidate results, ignored `budget`/`must_visit_attractions`, dead timeline UI) have since been fixed; treat that document as historical context, not current status.

## 15. Team Collaboration Rules

### Before You Commit

- Do not commit real secrets from `.env`
- Do not push directly to `main`
- Do not commit `venv` or `node_modules`

### Recommended Branch Workflow

```powershell
git checkout main
git pull origin main
git checkout -b feature/feature-name
```

After development:

```powershell
git add .
git commit -m "add xxx feature"
git push origin feature/feature-name
```

Then create a Pull Request on GitHub.

### Naming Rules

- Use snake_case for regular file names
- Put test files under `tests/`
- Use `test_*.py` for test file names

## 16. Additional References

- Architecture notes and gotchas for contributors: `CLAUDE.md`
- Full code-verified issue list with severity and fixes: `PROJECT_REVIEW_MERGED_EN.md` (Chinese: `PROJECT_REVIEW_MERGED.md`)
- Virtual environment setup: `Install_venv_and_dependencies.md`
- Attraction tool explanation: `attraction_tool_explanation.pdf`
- Frontend guide: `frontend/README.md`
