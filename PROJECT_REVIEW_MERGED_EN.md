# Consolidated Issue List & Remediation Plan

> This document merges `PROJECT_REVIEW.md` (a static code audit, severity-tagged P0–P3,
> with line-level pointers) and `PROJECT_IMPROVEMENT_REQUIREMENTS.md` (the target data
> contract and phased implementation plan), removes duplicate content, and re-verifies
> every claim against the **current state of the code** rather than reproducing the
> original reports' conclusions verbatim.
>
> Verification method: `git status` showed uncommitted changes to `hotel_agent.py`,
> `main_agent.py`, and the test suite, which meant several issues both source documents
> flagged might already be fixed. Every item below was re-checked by reading the current
> code (`grep`/direct inspection), not by trusting the older write-ups. Items are marked
> **Fixed / Partially fixed / Open**, and only the non-fixed ones carry a remediation plan.
>
> This is a review pass only — no source code was changed while producing it.

---

## 0. How the two source documents relate

| Document | Purpose |
| --- | --- |
| `PROJECT_REVIEW.md` | "What's currently wrong" — code-level audit, P0–P3 severity, line numbers and fixes |
| `PROJECT_IMPROVEMENT_REQUIREMENTS.md` | "What the target should look like" — unified data contract, phased rollout, acceptance criteria |

They describe the same underlying problems from different angles (duplicate `hotel_agent.py`,
attractions missing time fields, hardcoded departure city, redundant `view_result` parsing,
inconsistent currency tables, etc.). The rest of this document reorganizes by fix status
instead of by source document.

---

## 1. Already fixed (verified against current code — no action needed)

| # | Issue | Original reference | Verification |
| --- | --- | --- | --- |
| 1 | `hotel_agent.py` was a complete copy-pasted duplicate (class, `run_hotel_agent()`, and CLI entrypoint each defined twice) | REVIEW P2-1 / REQUIREMENTS 2.1 | File is now 67 lines, a single implementation, imports via `from app.tools.hotel_tool import search_hotels` — a clean package-absolute import, no `sys.path` hack |
| 2 | Attractions had no `arrival_time`/`departure_time`, so the "itinerary" was really an unordered attraction list | REVIEW P0-1 / REQUIREMENTS 2.2 | `main_agent.py` now has `_schedule_attractions()` + `_attraction_timezone()`, which allocate attractions across trip days (`_MAX_ATTRACTIONS_PER_DAY = 4`) and emit timezone-aware ISO-8601 timestamps; covered by `app/tests/test_main_agent_regressions.py::test_standard_payload_schedules_seed_attractions_with_timezone` |
| 3 | `_build_standard_payload()` had four `elif` branches with the identical condition (`isinstance(view_result, dict)`), making three of them dead code, plus two bare `except:` blocks | REVIEW P2-2 / REQUIREMENTS 2.4 (part) | Rewritten as a single `_extract_attraction_items()` helper; no bare `except`, no unreachable branches |
| 4 | The LLM-fallback parser picked "the first city mentioned in the text" as the destination, so `"from Kuala Lumpur to Seoul"` could resolve backwards | REVIEW P1-1 (directional part) | New `_extract_departure_city()` uses directional patterns (`从X到Y` / `from X to Y`); `_apply_user_route_constraints()` forces both the LLM path and the fallback path to honor the user's explicit departure city; covered by `test_fallback_dispatch_preserves_user_departure_city` |
| 5 | Default departure city was applied silently | REQUIREMENTS 2.3 ("only use a default when explicitly allowed, and disclose it") | `_DEFAULT_DEPARTURE_CITY` ("Shenzhen") is now used only when no departure city can be found in the user's text, and a message is appended to the response's `warnings` array when it happens |

---

## 2. New finding: a decision that neither original document could have flagged

**The backend can now emit `arrival_time`/`departure_time`, but the frontend never wired it up.**

`timelineItems` / `timelineByDate` (Pinia getters defined in
`frontend/src/stores/itinerary.js`, implemented in `frontend/src/lib/transformItinerary.js`)
are referenced only by the store itself — grepping `frontend/src` for any `.vue` component
that consumes them turns up nothing. `TimelinePanel.vue` was already rewritten to render
flat cards instead.

In other words, `PROJECT_REVIEW.md`'s P2-6 ("pick one: restore the timeline once time
fields exist, or delete the dead code — don't leave it in limbo") now has to be decided:
the data side is ready, only the frontend wiring is missing. **This is currently the
highest-leverage next step** — the backend work is already paid for.

Separately, `PROJECT_IMPROVEMENT_REQUIREMENTS.md` §3.2 proposes a fuller, versioned
`days[]` contract (folding flights / hotel check-in / check-out / attractions / transport
into one time-ordered `items` array per day, with `data_source`, `request_id`, and up to
3 candidate flights/hotels each). **That has not been built** — what landed is only the
minimal fix `PROJECT_REVIEW.md` suggested for P0-1 (timestamps added to `views[]`).
Whether to invest in the fuller contract is a product decision — see §5.

---

## 3. Partially fixed (secondary items still open)

### 3.1 Destination-city parsing is still two independent code paths
`parse_natural_language_to_hotel_json` still picks the destination as
`cities[-1] if cities else "Seoul"` (the **last** city mentioned in the text), while
`_extract_departure_city` picks the departure city via a directional regex or "the first
of the first two cities mentioned." Both work for common phrasings ("from A to B"), but
they're not unified into one shared "which city is which" resolver. Non-blocking; worth
merging into a single function later.

### 3.2 Default dates disagree between the LLM path and the fallback path
`main_agent.py:159` (the LLM prompt) defaults missing dates to `2026-03-26`/`2026-03-28`,
while `parse_natural_language_to_hotel_json` (the fallback path) defaults to
`2026-05-01`/`2026-05-04`. The two paths behave differently, and neither appends a
`warnings` entry when a default date or the default destination city (`Seoul`) is used —
so `PROJECT_IMPROVEMENT_REQUIREMENTS.md` §3.1's "defaults must be explicitly disclosed"
requirement isn't fully met for city/date, only for departure city (§1, item 5).

**Suggested fix**: hoist both defaults into shared module-level constants
(e.g. `_DEFAULT_CHECK_IN` / `_DEFAULT_CHECK_OUT`) used by both code paths; append a
`warnings` entry whenever a default city or date is used, not just a default departure city.

### 3.3 A whole block of computed-but-unused variables in `run_test_main_agent_flow`
`main_agent.py:587-609` fully computes `attraction_names`, `attraction_durations`, and
`hotel_name` (comments say "for route planning") but nothing downstream ever reads them.
In the same function, `transport_result` is always `None`, and `run_travel_agent` is
imported (`main_agent.py:21`) but never called anywhere in the module.

**Suggested fix**: this is the unfinished remainder of a "route planning" feature. Either
wire `transportation_agent` in for real, or delete the whole block (recoverable from git
history if needed) — leaving it in place misleads future readers into thinking the
feature is live.

---

## 4. Open issues (merged and de-duplicated, by severity)

### P0 — recommended to fix first

**P0-A — XSS: prompt injection → LLM output → unsanitized `v-html`**
Confirmed: `ItineraryForm.vue:64` is still
`<div v-if="parsedAiOutput" v-html="parsedAiOutput"></div>` with no `dompurify` in the
dependency tree; `main_agent.py`'s `_generate_natural_language_output` still splices the
raw user string into the prompt with no delimiter
(`"你是一个旅行助手。用户输入了：{user_text}"`). Full chain: user supplies a prompt-injection
payload → the LLM is coaxed into echoing `<img onerror="...">` → `marked` v17 (which
dropped its built-in sanitizer back in v5) passes raw HTML through untouched →
`v-html` executes it in the browser.
**Fix**: (1) add `dompurify` on the frontend —
`DOMPurify.sanitize(marked.parse(text))` before `v-html`; (2) wrap user input in an
explicit delimited block in the prompt and instruct the model to treat its contents as
data, never as instructions; (3) strip `<script>`/`on*=`/`javascript:` patterns from the
backend's output as a defense-in-depth measure before it ever leaves the API.

**P0-B — `node_modules/` committed to git; root dependency is a deprecated stub package**
Confirmed: `git ls-files` still lists 616 files under `node_modules/`; the root
`package.json` still declares only `"three.js": "^0.77.1"` — a deprecated npm forwarding
package whose own metadata says as much, and which pulls in a 2016-era `three@0.77.0`
that has nothing to do with the real `"three": "^0.164.0"` dependency actually used in
`frontend/package.json`. It's an accidental install with no code referencing it.
**Fix**: add `node_modules/` to the root `.gitignore`; run
`git rm -r --cached node_modules`; delete the root `package.json` /
`package-lock.json` / `node_modules/` once confirmed unused.

**P0-C — `.gitignore` still has unresolved merge-conflict markers**
Confirmed: the file still literally contains `<<<<<<< HEAD` / `=======` /
`>>>>>>> dev`. `.env` currently sits between the markers and is therefore still ignored
by luck, not by design — the next edit to this file could silently drop that protection
and expose `SERPAPI_API_KEY`/`GOOGLE_API_KEY` in a future commit.
**Fix**: hand-clean the file into proper sections (Python / env & secrets / Node /
runtime artifacts); add a `.env.example` with keys only, no values; consider adding
`gitleaks` or GitHub Secret Scanning as a backstop.

**P0-D — the core endpoint has no auth, no rate limiting, no timeout; CORS allows any origin**
Confirmed: `CORSMiddleware` in `server.py` is still `allow_origins=["*"]`;
`POST /api/v1/agent/generate_itinerary` has no API-key check, no rate limit, no input
length cap, and its body type is a bare `Dict[str, Any]`. A single request triggers 2
serial Gemini calls plus multiple SerpAPI calls, so an unauthenticated caller can burn
through paid API quota very quickly.
**Fix**: (1) add an `X-API-Key` header check (env-var-backed); (2) add `slowapi` for
per-IP rate limiting; (3) replace the bare dict body with an explicit Pydantic model,
capping `input` length; (4) add timeouts to every external call and to the handler as a
whole (e.g. `asyncio.wait_for(..., timeout=90)`); (5) restrict CORS to an explicit,
env-configured origin list.

### P1 — clear defects, contract violations, or cost problems

**P1-A — flight and hotel results are hard-truncated to a single candidate**
Confirmed: `hotel_tool.py:35` still does `for hotel in properties[:1]`,
`flight_tool.py:297` still does `} for f in filtered[:1]]`, and `main_agent.py:414-415`
truncates the hotel list a second time. All three truncations happen **after** the full
API response has already been fetched — the "reduces API calls" comments are inaccurate;
the only thing saved is a few LLM-summary tokens. The real cost is that `flight_tool`'s
budget filtering/sorting and `hotel_tool`'s full candidate list both go to waste, and
`PROJECT_IMPROVEMENT_REQUIREMENTS.md` §3.3's ask for "return up to ~3 candidates each,
with one flagged as recommended" is unmet.
**Fix**: parameterize the result count (`max_results: int = 3`); delete the second
truncation in `main_agent.py`; if LLM-summary token cost is a concern, trim the payload
fed to `_generate_natural_language_output` specifically, not the data returned to the
frontend.

**P1-B — three independent currency tables, with conflicting CNY rates**
Confirmed: `flight_tool.py:97` has `"CNY": 0.94`, while `tools.py:996` and
`attraction_tool.py:58` both have `"CNY": 0.65` (true market rate is roughly 0.63).
`flight_tool`'s figure over-estimates by ~45%, which materially distorts budget
filtering for anyone specifying a CNY budget. All three tables are hardcoded and will
keep drifting from real rates over time regardless.
**Fix**: short term — extract a single `app/tools/currency.py` with one
`EXCHANGE_TO_MYR` table and one `convert_to_myr()`, have all three call sites import it,
and correct `flight_tool`'s CNY to 0.65. Medium term — back it with a cached (TTL ~24h)
exchange-rate API call, falling back to the constant table when unavailable.

**P1-C — `attraction_tool` gets imported two different ways and becomes two separate modules**
Confirmed: `attraction_seed_agent.py:10`, `transportation_agent.py:11`,
`attraction_agent.py:21`, `flight_agent.py:7`, and several `app/tests/*.py` scripts still
push `app/tools/` or the project root onto `sys.path` and import bare module names, while
other code (e.g. `tools.py`) imports the same file via the `app.tools.attraction_tool`
package path. Python treats these as two unrelated modules — so their module-level
caches, locks, and parsed seed data are duplicated in memory and don't coordinate.
`main_agent.py` itself has already been converted to a pure package-absolute import
(no `sys.path` hack); the rest of the codebase hasn't followed.
**Fix**: standardize on `from app.tools.xxx import ...` everywhere; remove every
`sys.path.insert`/`sys.path.append`; ensure package `__init__.py` markers are in place;
run scripts as `python -m app.agents.xxx` rather than as bare file paths.

**P1-D — the whole request pipeline is fully serial with no timeout anywhere (re-verified)**
Confirmed by direct code inspection: `run_test_main_agent_flow` calls hotel search →
outbound flight → inbound flight → each attraction lookup, one after another, with no
`ThreadPoolExecutor`, `asyncio`, or `concurrent.futures` usage anywhere in
`main_agent.py` or `server.py`. `generate_itinerary` is a synchronous `def` with no
handler-level timeout. The frontend `fetch()` call in `stores/itinerary.js` has no
`AbortSignal`/timeout option either (`grep` for both terms returns nothing) — a slow or
hung backend call will spin the UI indefinitely with no user-facing failure.
**Fix**: parallelize the three independent search calls (hotel/outbound/inbound) with
`ThreadPoolExecutor`; parallelize the per-attraction lookups inside `run_seed_agent`
(coordinate with the P1-G cache fix below first, since the cache lock is
process-local); add a total handler timeout and per-call timeouts on the LLM/SerpAPI
clients; add `AbortSignal.timeout(...)` to the frontend fetch with a clear timeout
message. Medium term, consider an async task model (`POST` returns a `task_id`
immediately; frontend polls or uses SSE).

**P1-E — the frontend already sends `budget` and `must_visit_attractions`; the backend ignores both**
Confirmed: `frontend/src/stores/itinerary.js` still builds and sends `budget` (defaulting
to `{min:1000, max:5000, currency:'CNY'}`) and `must_visit_attractions`, but
`server.py`'s `generate_itinerary` only reads `payload.get("input")`; the
`_DispatchPlanModel` / `_build_fallback_dispatch_plan` code paths in `main_agent.py`
hardcode `budget` to `{min:0, max:10000, currency:"MYR"}` regardless, and there is no
receiving logic for "must-visit attractions" anywhere. The frontend's default currency
(`CNY`) also disagrees with the system-wide `MYR` baseline, and combined with P1-B's rate
error this compounds any budget-filtering inaccuracy further.
**Fix**: give `server.py` an explicit Pydantic request model that accepts
`budget`/`must_visit_attractions`/`pax` and forwards them into `main_agent`; merge
must-visit attractions into `run_seed_agent`'s results (de-duplicated, scheduled first);
replace the hardcoded budget with the forwarded value; change the frontend's default
currency to `MYR`. If "must-visit attractions" won't be implemented soon, remove the
field from the frontend instead — a wired-looking field that silently does nothing is
worse than no field.

**P1-F — backend errors are always returned as HTTP 200**
Confirmed: `server.py`'s `except Exception as e: return {"code": 500, ...}` is still a
plain `return`, so FastAPI emits HTTP 200 regardless of the `code` field inside the body;
`message: str(e)` still puts the raw exception string directly into the API response.
**Fix**: use `HTTPException` or `JSONResponse(status_code=...)`; distinguish request
errors (400) from third-party failures (502) from everything else (500); return a
generic message externally and log the full stack trace with `logging.exception()`
internally; replace the scattered `print()` calls with `logging`.

**P1-G — the attraction cache's read-modify-write pattern is not concurrency-safe and has no TTL (re-verified)**
Confirmed by direct inspection of `attraction_tool.py`: `_CACHE_LOCK = threading.Lock()`
(line 18) only guards threads within a single process; the two call sites (around lines
3282 and 3512) both do `_load_cache()` → mutate one key → `_save_cache()` on the full
dict, so multiple processes (e.g. `uvicorn --workers N`, or a script run alongside the
server) can race and silently drop each other's writes. `_save_cache` writes the file
directly rather than via a temp-file-plus-`os.replace()` atomic swap, so a process killed
mid-write can leave a truncated, unparseable JSON file — which `_load_cache`'s exception
handler will quietly treat as an empty cache, discarding everything previously stored.
There is no `created_at`/TTL field anywhere in the cache schema, so entries (ticket
prices, opening hours) never expire on their own.
**Fix**: minimal — atomic writes (write to a temp file, then `os.replace()`) plus a
cross-process file lock (`filelock`). Recommended — move to `sqlite3` (stdlib, supports
concurrent writes and per-key transactions), with a `created_at` column enforcing a TTL
(e.g. 30 days).

### P2 — maintainability/robustness issues that keep slowing down iteration

- **`optimize_multi_location_route` is a brute-force O(n!) TSP solver** (confirmed:
  `multi_route_tool.py:112` and `:121` both use `itertools.permutations`). 10 locations
  means 90 SerpAPI calls for the distance matrix plus 3.6M permutations to search. This
  path isn't currently wired into the live flow (it needs P1-D-adjacent work first to be
  reachable at all), but it will surface immediately the moment `transportation_agent` is
  connected. Recommended: Held-Karp DP for n ≤ 8, nearest-neighbor + 2-opt for n > 8, plus
  a hard `max_locations` cap as a circuit breaker.
- **`requirements.txt` has zero version pins**, and is missing explicit `pytest` /
  `pydantic` / `langchain-core` declarations (all used directly but currently pulled in
  transitively); `langchain-openai` is only used by test scripts, not the production
  path, but sits in the main requirements file. Recommended: pin versions, split out a
  `requirements-dev.txt`.
- **Most files under `app/tests/` are manual scripts, not pytest suites.** Confirmed:
  `test_attraction_tool.py` and the new `test_main_agent_regressions.py` are genuine
  pytest test files (an improvement over the state the original review documented), but
  `test_main_agent.py`, `test_attraction_agent.py`, `test_call_attraction_agent.py`,
  `test_agent.py`, `test_serpapi.py`, and the `test_google_*.py` files are still
  assertion-free manual scripts. Worse, `test_main_agent.py:12` does
  `from app.tests.test_attraction_agent import run_tool_flow` — one "test" file imports
  another as if it were a library, and `pytest`'s `test_*.py` collection rule means
  simply running the suite triggers real API calls as an import side effect. Recommended:
  move the manual scripts to a `scripts/` directory (dropping the `test_` prefix so
  pytest stops collecting them) and keep `app/tests/` for genuine pytest cases only.
- **`app/tools/tools.py` (1,400+ lines) has exactly one part that's actually used**:
  `TRAVEL_ATTRACTION_CATALOG`. Its `travel_planner`/`_build_view` scheduling
  implementation and its `get_location_info`/`calculate_distance` tools are unreferenced.
  Note: `_build_view`'s scheduling *idea* has effectively already been re-implemented,
  more simply, as `main_agent._schedule_attractions()` (see §1) — so this file's
  scheduling code can be treated as superseded rather than something to migrate and
  reuse. Recommended: extract `TRAVEL_ATTRACTION_CATALOG` into a data file, delete or
  relocate the rest.
- **`_fetch_url_text` fetches arbitrary URLs with no size cap and no domain
  controls** (`attraction_tool.py`). Recommended: cap the read size, validate
  `Content-Type`, and limit redirects.

### P3 — technical debt, safe to batch-clean

- `server.py:24` uses the deprecated `datetime.utcnow()` — switch to
  `datetime.now(timezone.utc)`.
- The default Gemini model disagrees across three places: `main_agent.py:98` defaults to
  the older `gemini-1.5-flash`, `transportation_agent.py` defaults to `gemini-2.5-flash`,
  and the README also says `gemini-2.5-flash`. Recommended: centralize in `config.py` and
  read it once.
- **`flight_tool.py` divides by `passengers` without guarding against zero**
  (confirmed at lines 215, 262-263: `price / passengers`,
  `convert_to_myr(...) / passengers`). `passengers` comes from user-controlled JSON, so a
  value of `0` raises `ZeroDivisionError`. Recommended:
  `passengers = max(1, int(passengers or 1))`.
- **`flight_tool.py` forces LangSmith tracing on by default at import time (re-verified)**:
  lines 51-52 set `os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "true")`.
  Simply importing this module enables tracing; without a configured `LANGCHAIN_API_KEY`,
  langsmith will keep generating failed background upload attempts and log noise.
  Recommended: default to `"false"`, making tracing an explicit opt-in.
- `app/config.py` is a thin, largely-unused stub (just two `os.getenv` lines, plus a
  comment addressed to nobody in particular — "make sure this key name is right"). Every
  other module calls `load_dotenv()` independently instead of using it. Recommended:
  promote it to a `pydantic-settings` `BaseSettings` class with startup-time validation,
  and have every module import from it.
- `app/data/attraction_cache.json` is still tracked by git (and is indeed one of the
  files with pending local changes right now) — every run produces a diff. Should be
  gitignored.
- No CI, no linter/formatter config, no `pyproject.toml`, no `.env.example`. Confirmed:
  `.github/workflows/` doesn't exist. `frontend/vite.config.js` also has no `server.proxy`
  fallback — without a hand-created `frontend/.env.local`, requests silently 404 against
  the Vite dev server instead of the backend.

### Frontend dead code (re-verified precisely)

Confirmed via `Glob` + `grep` that all four files below exist on disk, but **no `.vue`
component or JS module anywhere in `frontend/src` imports any of them**:

| File | Lines | Status |
| --- | --- | --- |
| `frontend/src/components/Timeline/TimelineNode.vue` | 169 | Zero importers |
| `frontend/src/composables/useHorizontalWheelScroll.js` | 131 | Zero importers |
| `frontend/src/components/AiStatusBar.vue` | 19 | Zero importers |
| `frontend/src/components/HelloWorld.vue` | 43 | Vite scaffold leftover, zero importers |

`frontend/src/lib/transformItinerary.js` (222 lines) is not orphaned in the same sense —
it's still called by the store's `timelineItems` getter — but as noted in §2, that
getter itself has no consumer, so the whole chain is currently inert.

---

## 5. Decisions that need a product/engineering call, not more review

1. **Should the frontend timeline UI be revived?** The backend data is ready (§2); this
   is the highest-leverage remaining step and should probably come first.
2. **Should the project invest in the fuller `days[]` contract from**
   `PROJECT_IMPROVEMENT_REQUIREMENTS.md` §3.2, or is the current minimal
   "`views[]` plus timestamps" fix sufficient? The minimal fix already unblocks the
   frontend timeline; the fuller contract is a larger architectural change.
3. **Is `transportation_agent`/`multi_route_tool` being finished or removed?** It's
   currently in a half-built state (§3.3, P2's TSP note). Finishing it means solving the
   O(n!) complexity problem first; abandoning it means deleting the dead import and the
   unused variable block in `main_agent.py`.
4. **Is `must_visit_attractions` being implemented or dropped from the frontend?** Right
   now it's a feature that looks wired up but silently does nothing — leaving it as-is
   just keeps creating confusion for anyone reading the code.

---

## 6. Suggested order of work (merged from both source documents, already-completed items removed)

**Phase 1 — stop the bleeding (lowest risk, do first)**
1. P0-C: clean up the `.gitignore` merge-conflict markers, add `.env.example`
2. P0-B: remove `node_modules/` from version control, delete the root `package.json`/`three.js`
3. P0-A: add `dompurify` on the frontend
4. P1-B: unify the currency tables, correct the CNY rate
5. Decision 1: whether to revive the frontend timeline (if yes, this phase already shows a visible result)

**Phase 2 — cleanup and consolidation**
6. P1-C: standardize on package-absolute imports, remove every `sys.path` injection
7. The remaining §3 items (unify default-date constants, delete dead variables)
8. P2: pin `requirements.txt`, relocate the manual test scripts

**Phase 3 — fix remaining defects**
9. P1-F: return real HTTP status codes, adopt `logging`
10. P0-D: auth + rate limiting + input constraints + CORS lockdown
11. P1-G: atomic cache writes or SQLite, plus TTL
12. P1-A: parameterize flight/hotel result counts
13. P1-E: accept `budget`/`pax`/`must_visit_attractions` (depends on Decision 4)

**Phase 4 — core capability**
14. Decision 2: whether to upgrade to the full `days[]` contract
15. P1-D: parallelize external calls, add end-to-end timeouts
16. Test coverage + CI
17. Decision 3: finish or remove `transportation_agent`; if finishing, resolve the O(n!) TSP first
