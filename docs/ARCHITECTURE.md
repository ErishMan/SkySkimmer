# SkySkimmer — Architectural Blueprint

> **Prompt 1 of 5 — Design Phase. No source code generated.**
> Principal Systems Architect + Senior Developer Relations Engineer analysis.

---

## 1. Technical Stack & Provider Selection

### Recommended: Python 3.11+ on Railway (primary) / AWS Lambda (secondary)

**Language: Python**

Python wins decisively for this use case for three reasons:

1. **Ecosystem dominance for HTTP/data tasks**: `httpx` (async HTTP with full proxy/header control), `pydantic` (strict schema validation to catch API drift early), `apscheduler` or native cron — all are battle-tested and have minimal cold-start overhead when containerized.
2. **Data processing libraries**: `pandas` and `polars` are available for price-history diffing with minimal code. Node.js equivalents require more boilerplate.
3. **Lambda cold-start parity**: For a lightweight Python service with minimal dependencies (no Playwright, no Chromium), a Lambda cold-start is under 300ms — negligible for a scheduled job that runs every 15–60 minutes, not a real-time API.

**Runtime & Host**

| Host | Cold Start | Cron Native | Free Tier | Notes |
|---|---|---|---|---|
| **Railway** ✅ | ~0ms (always-on container) | Via `schedule` lib or `APScheduler` | 500 hrs/mo free | Best for long-running scheduled service; persistent container means no cold starts, easy env vars, built-in logging |
| AWS Lambda + EventBridge | 200–400ms | Native EventBridge cron rules | 1M requests/mo free | Best if cost is paramount; cold start irrelevant for 15min+ intervals |
| Render (free tier) | Spins down after 15min inactivity | Via `APScheduler` in-process | Free (with spin-down) | Acceptable; spin-down adds ~30s first-run latency per cycle |
| Vercel Functions | 50–200ms | Vercel Cron Jobs (Pro) | Limited on free | Not recommended — 10s function timeout too restrictive for multi-leg flight queries |

**Verdict**: Deploy as a **Railway containerized service** (Python 3.11, Dockerfile). Always-on, no cold-start, native env var management, $5/mo after free tier. Use `APScheduler` for in-process cron. If cost is zero-tolerance, fall back to AWS Lambda + EventBridge with no code changes.

---

## 2. Data Provider Matrix

| API Name | Free/Low-Tier Cost | Anti-Bot Evasion Capability | Key Trade-off |
|---|---|---|---|
| **Tequila by Kiwi** ⭐ PRIMARY | Free tier: up to 100 req/hr; $0 with API key registration | **Low** (not needed — direct B2B API, no bot detection) | Best flexible routing (stopover duration, alliance filters, nomad mode); coverage can miss some LCC carriers; requires Kiwi account approval |
| Amadeus Self-Service | Free sandbox; production ~$0.004/search after 1k/mo | **Low** (direct REST API, no evasion needed) | IATA-grade data quality; rigid fare class schema makes custom stopover logic harder; sandbox data is synthetic |
| Scrapfly | $29/mo starter (1M scrape credits) | **High** (Akamai, Cloudflare, DataDome bypass; residential proxy pool built-in) | Scraping engine, not structured data — requires HTML parsing + schema drift handling; most expensive; use only if direct APIs fail to cover a route |

### Primary Selection: Tequila by Kiwi

**Rationale**: Tequila's `/v2/flights/search` endpoint supports `max_stopovers`, `stopover_from`/`stopover_to` duration parameters, `select_airlines` (alliance filtering via IATA codes), `curr` (currency), and `fly_from`/`fly_to` with airport or city codes. This maps directly to the project objective of *hyper-custom itinerary evaluation*. No anti-bot infrastructure needed — it's a REST JSON API with a Bearer token. Register at [tequila.kiwi.com](https://tequila.kiwi.com).

---

## 3. Component Architecture Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                        SCHEDULER TRIGGER                        │
│              APScheduler cron: every N minutes                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    1. INGESTION LAYER                           │
│  • Reads itinerary config from /config/routes.yaml              │
│  • Each itinerary defines: origin, dest, date_range, stopover   │
│    constraints, airline/alliance allowlist, max_price_usd       │
│  • Constructs typed Pydantic request models per itinerary       │
│  • Hands request batch to Evasion & Extraction layer            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              2. EVASION & EXTRACTION LAYER                      │
│  • httpx AsyncClient with retry + exponential backoff           │
│  • Injects Authorization: Bearer <TEQUILA_API_KEY> header       │
│  • On 429: honour Retry-After header, sleep, retry ×3           │
│  • On 5xx: log, skip cycle, alert Dispatcher with ERROR payload │
│  • Validates response against Pydantic FlightOffer schema       │
│  • On schema mismatch: logs raw JSON to /logs/drift.log,        │
│    raises StructuralDriftError, notifies Dispatcher             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              3. RULES & PROCESSING ENGINE                       │
│  • Filters results by: max_stopovers, stopover_duration_range,  │
│    allowed_airlines, alliance_codes, cabin_class, baggage       │
│  • Points-to-cash valuation: applies user-defined cpp (cents-   │
│    per-point) multiplier to compare award vs cash fares         │
│  • Calculates "effective price" after valuation                 │
│  • Sorts results by effective_price ASC, picks best N offers    │
│  • Packages result into AlertPayload dataclass                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    4. DISPATCHER                                │
│  IDEMPOTENCY CHECK:                                             │
│  • Reads last_alerted_price from Supabase (or local JSON file)  │
│  • Compares current best_price vs stored value                  │
│  • Rule: only fire notification if Δprice >= threshold (e.g.    │
│    5% drop or AUD $20 absolute) OR if first-run for this route  │
│  • On fire: POST to Discord/Slack Webhook with rich embed       │
│  • Updates last_alerted_price in state store                    │
│  • On no-fire: silent log only                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

```
routes.yaml → PydanticRequest → httpx(Tequila API) → JSON
    → PydanticFlightOffer[] → RulesEngine → AlertPayload
    → IdempotencyCheck(Supabase) → [FIRE | SKIP]
    → Discord/Slack Webhook POST
```

---

## 4. Security & Error Vector Mapping

### 4.1 Secret Management

| Secret | Storage | Injection Method |
|---|---|---|
| `TEQUILA_API_KEY` | Railway env var / AWS Secrets Manager | `os.environ` at runtime — never hardcoded |
| `DISCORD_WEBHOOK_URL` | Railway env var | Same as above |
| `SUPABASE_URL` + `SUPABASE_KEY` | Railway env var | Same as above |
| `TWILIO_*` (optional) | Railway env var | Same as above |

**Principles**:
- `.env` file committed to `.gitignore` — never pushed to repository.
- `python-dotenv` for local development only; production reads from Railway's env var injection.
- No secrets in `routes.yaml` — config file contains only routing preferences, no credentials.
- Rotate keys via Railway dashboard; service restart picks up new values automatically.

### 4.2 Upstream API Failure Handling

```
Tequila API Failure Vectors:
├── 429 Too Many Requests
│   └── Action: Read Retry-After header → asyncio.sleep(n) → retry ×3
│              If still failing after 3 attempts: skip cycle, log WARNING
├── 5xx Server Error
│   └── Action: Exponential backoff (2s, 4s, 8s) → retry ×3
│              After 3 failures: dispatch ERROR embed to Discord (separate
│              error webhook or #alerts-errors channel), skip price cycle
├── Network Timeout / DNS failure
│   └── Action: httpx timeout=30s → TimeoutException → treat as 5xx
└── 401 Unauthorized
    └── Action: CRITICAL log + immediate Discord alert "API key invalid"
               → halt scheduler to prevent log spam
```

### 4.3 Data Structural Drift

The most insidious failure mode. When Tequila renames a field (e.g. `price` → `fare.total`):

```
Strategy: Pydantic Schema Validation as Canary

1. All API responses are validated via a strict FlightOffer Pydantic model.
2. On ValidationError:
   a. Log full raw JSON to /logs/drift_{timestamp}.json
   b. Capture specific field that failed validation
   c. Dispatch SCHEMA DRIFT alert to Discord:
      "⚠️ API field 'price' missing. Raw response saved to drift log."
   d. Skip notification cycle — do NOT process partial data
3. Developer reviews drift log, updates Pydantic model, redeploys.
```

**Secondary protection**: Pin Tequila API version in the base URL (`/v2/`) and monitor Kiwi changelog via RSS or webhook.

---

## 5. Phased Implementation Roadmap

### Step 1 — Project Scaffold & Config Schema
**Definition of Done (DoD)**:
- Repository initialized with `pyproject.toml` (Poetry or uv), `Dockerfile`, `.env.example`, `.gitignore`.
- `routes.yaml` schema defined and documented (fields: `route_id`, `origin`, `destination`, `date_from`, `date_to`, `max_price`, `max_stopovers`, `stopover_min_hrs`, `stopover_max_hrs`, `allowed_airlines`, `cpp_valuation`).
- Pydantic models defined for `ItineraryConfig` (inbound from YAML) and `FlightOffer` (inbound from Tequila API).
- Unit tests pass for config parsing with both valid and malformed YAML inputs.
- ✅ No external API calls made yet.

### Step 2 — Tequila API Integration & Extraction Layer
**Definition of Done (DoD)**:
- `TequilaClient` class implemented using `httpx.AsyncClient`.
- Retry logic (429 + 5xx) implemented and tested with mocked responses.
- A live test call against the Tequila sandbox returns a valid, Pydantic-validated `FlightOffer` list for at least one test itinerary.
- Structural drift detection working: a deliberately malformed response triggers `StructuralDriftError` and writes to drift log.
- ✅ No scheduler, no notifications yet — pure data retrieval proof-of-concept.

### Step 3 — Rules & Processing Engine
**Definition of Done (DoD)**:
- `RulesEngine` function accepts `List[FlightOffer]` + `ItineraryConfig` and returns filtered, ranked `List[AlertPayload]`.
- All filter dimensions implemented: stopover duration, airline allowlist, cabin class, baggage.
- Points-to-cash effective price calculation tested against known cpp values.
- Unit tests cover: empty result set, all-filtered-out result, tie-breaking sort, cpp=0 edge case.
- ✅ No external calls — pure functional logic, fully testable in isolation.

### Step 4 — State Management & Dispatcher
**Definition of Done (DoD)**:
- State store abstracted behind a `StateStore` interface with two implementations: `SupabaseStateStore` (production) and `JsonFileStateStore` (local dev/fallback).
- Idempotency logic implemented: notification fires only when `Δprice >= threshold` or first-run.
- Discord webhook POST implemented with a rich embed (route, price, Δ from last alert, booking link, timestamp).
- Integration test: run dispatcher twice with same price → second call produces no webhook POST.
- Integration test: run dispatcher with price drop > threshold → webhook fires exactly once.
- ✅ Full pipeline testable end-to-end with mocked Tequila responses.

### Step 5 — Scheduler, Deployment & Hardening
**Definition of Done (DoD)**:
- `APScheduler` integrated; job interval configurable via env var (`POLL_INTERVAL_MINUTES`, default 30).
- `Dockerfile` builds cleanly; Railway deployment succeeds with env vars injected.
- Error webhook alerts (API failure, schema drift, key expiry) verified in production Discord channel.
- `README.md` documents: setup, `routes.yaml` schema, env vars, deployment steps.
- Smoke test: scheduler runs for 2 full cycles in production, at least one real price is fetched and logged, state store is updated, Discord notification fires correctly on first run.
- ✅ Production-ready. Code generation phases can begin.

---

*Blueprint generated for Prompt 1 of 5. Proceed to Prompt 2 for code generation.*
