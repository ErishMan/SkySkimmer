# SkySkimmer ✈️

> A lightweight, cloud-native flight pricing engine for long-running, scheduled evaluation of highly specific travel itineraries.

SkySkimmer monitors custom flight routes — specific stopovers, alliance routing, points-to-cash valuation — and fires **Discord Rich Embed alerts** only when a meaningful price drop is detected. It runs as a persistent container on Railway (or AWS Lambda) and never duplicates notifications.

---

## How It Works

Every poll cycle runs a five-stage pipeline:

```
Fetch (Tequila API)
  ↓ Validated JSON envelope
Adapt (Anti-corruption mapper)
  ↓ list[FlightItinerary] — clean domain objects
Evaluate (Pure rules engine + ScraperConfig)
  ↓ Filtered & sorted qualifying flights
State Check (IdempotencyCache)
  ↓ Only new flights or price-drop flights
Dispatch (Discord Rich Embed webhook)
```

---

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system blueprint including provider selection rationale, component topology diagram, and the 5-phase implementation roadmap.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| HTTP client | `httpx` (async, timeout-enforced) |
| Retry logic | `tenacity` (exponential backoff) |
| Schema validation | `pydantic` v2 + `pydantic-settings` |
| Structured logging | `loguru` |
| Scheduler | `APScheduler` 3.x (AsyncIO) |
| Flight data | Tequila by Kiwi API (`/v2/search`) |
| State persistence | Local JSON file (dev) / Supabase (prod) |
| Notifications | Discord Webhooks (Rich Embeds) |
| Containerisation | Docker |
| Deployment target | Railway (primary) / AWS Lambda (alt) |

---

## Prerequisites

- **Python 3.11+** — check with `python --version`
- **Poetry** — dependency and virtual environment manager
- **A Tequila API key** — register at [tequila.kiwi.com](https://tequila.kiwi.com/portal)
- **A Discord Webhook URL** — create one under _Server Settings → Integrations → Webhooks_

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/ErishMan/SkySkimmer.git
cd SkySkimmer
```

### 2. Install Poetry (if not already installed)

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

### 3. Install dependencies

```bash
poetry install
```

This installs all production **and** development dependencies (pytest, ruff, mypy) from `pyproject.toml`.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Then open `.env` and fill in your values:

```dotenv
# Required
TEQUILA_API_KEY=your_tequila_api_key_here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR/WEBHOOK

# Optional — falls back to DISCORD_WEBHOOK_URL if omitted
DISCORD_ERROR_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR/ERROR_WEBHOOK

# Optional — omit both to use local JSON state store
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_supabase_anon_or_service_key

# Scheduler — minimum 5, maximum 1440 (minutes)
POLL_INTERVAL_MINUTES=30

# application
APP_ENV=development
LOG_LEVEL=INFO
```

> **Security:** `.env` is gitignored and must never be committed. All secrets are loaded at runtime via `pydantic-settings`. If any required variable is absent, the application exits immediately with a clear error before doing any work.

### 5. Configure your itineraries

Edit `config/routes.yaml`. A commented example is pre-populated:

```yaml
routes:
  - route_id: "SYD-LHR-economy-oneworld"
    label: "Sydney to London — Economy, Oneworld carriers"
    origin: "SYD"
    destination: "LHR"
    date_from: "2026-11-01"
    date_to:   "2026-11-30"
    max_price: 1800
    currency: "AUD"
    max_stopovers: 1
    stopover_min_hrs: 2
    stopover_max_hrs: 8
    allowed_airlines:
      - "QF"   # Qantas
      - "BA"   # British Airways
      - "CX"   # Cathay Pacific
      - "JL"   # Japan Airlines
    cabin_class: "ECONOMY"
    cpp_valuation: 1.5        # cents-per-point for award vs cash comparison
    alert_threshold_abs: 50   # AUD — minimum drop to re-alert
    alert_threshold_pct: 5.0  # % — minimum drop to re-alert
```

| Field | Description |
|---|---|
| `route_id` | Unique stable identifier (used as idempotency key) |
| `origin` / `destination` | IATA airport or city codes |
| `date_from` / `date_to` | ISO date range to search within |
| `max_price` | Hard price ceiling in `currency` |
| `max_stopovers` | Maximum number of stops (0 = direct only) |
| `stopover_min_hrs` / `stopover_max_hrs` | Acceptable layover window |
| `allowed_airlines` | IATA codes — empty list means no restriction |
| `cabin_class` | `ECONOMY` \| `PREMIUM_ECONOMY` \| `BUSINESS` \| `FIRST` |
| `cpp_valuation` | Cents-per-point multiplier for award fare comparison |
| `alert_threshold_abs` | Absolute price drop (in currency) to trigger re-alert |
| `alert_threshold_pct` | Percentage price drop to trigger re-alert |

### 6. Run the application

```bash
poetry run python -m src.main
```

On startup you will see:

1. **Environment banner** — confirms all required variables are present
2. **Health check** — logs secret presence (never values), state store mode, webhook status
3. **Immediate pipeline tick** — fires a live Tequila request, maps results, evaluates rules, checks idempotency cache, dispatches Discord embeds for any qualifying flights
4. **Scheduler active** — repeats the pipeline every `POLL_INTERVAL_MINUTES` minutes

To stop: `Ctrl+C` — the application drains the in-flight pipeline tick (up to 30 seconds), flushes state to disk, and exits cleanly.

---

## Running Tests

```bash
poetry run pytest tests/ -v
```

The test suite covers environment variable validation (required fields, Supabase credential pairing, `POLL_INTERVAL_MINUTES` bounds, effective error webhook fallback).

---

## Linting & Type Checking

```bash
# Lint and auto-fix
poetry run ruff check src/ --fix

# Static type checking
poetry run mypy src/
```

---

## Project Structure

```
SkySkimmer/
├── src/
│   ├── main.py                    ← Entry point, AsyncIOScheduler, full pipeline
│   ├── config/
│   │   └── settings.py            ← Pydantic Settings — fail-fast env validation
│   ├── contracts/
│   │   └── tequila.py             ← External API DTO schemas (Pydantic)
│   ├── domain/
│   │   └── models.py              ← FlightItinerary, FlightSegment, ScraperConfig
│   ├── services/
│   │   ├── flight_fetcher.py      ← httpx async client, tenacity retry/backoff
│   │   ├── flight_adapter.py      ← Anti-corruption mapper (DTO → domain model)
│   │   ├── flight_evaluator.py    ← Pure rules engine (filter + sort)
│   │   ├── state_store.py         ← Idempotency cache (JSON file / Supabase)
│   │   └── dispatcher.py          ← Discord Rich Embed builder + delivery
│   └── utils/
│       └── logger.py              ← Loguru structured logger (JSON prod / pretty dev)
├── config/
│   └── routes.yaml                ← User-editable itinerary definitions
├── docs/
│   └── ARCHITECTURE.md            ← Full system blueprint
├── tests/
│   └── test_settings.py           ← Environment validator unit tests
├── data/
│   └── alerts_cache.json          ← Runtime idempotency state (gitignored)
├── logs/
│   └── error.log                  ← Rotating error log (gitignored)
├── .env.example                   ← Credential template
├── .gitignore
├── Dockerfile
└── pyproject.toml
```

---

## Discord Alerts

SkySkimmer sends **Discord Rich Embeds** — never plain text.

| Scenario | Embed colour | Title |
|---|---|---|
| First alert for a route | 🟢 Green `#2ECC71` | `✨ New Deal Found — SYD → LHR` |
| Price dropped below last alert | 🟢 Dark green `#27AE60` | `⬇️ Price Drop — SYD → LHR` |
| System error | 🔴 Red `#E74C3C` | `⚠️ SkySkimmer System Alert` |

Every price alert embed includes: **Airline**, **Route**, **Stops**, **Departure**, **Arrival**, **Price**, and a **Book Now** deep link directly into Kiwi.com.

Alerts are rate-limited with a 1-second delay between each embed to stay within Discord's 30 messages/minute webhook limit.

---

## State & Idempotency

SkySkimmer will **not** alert you about the same flight at the same price twice. The idempotency key is:

```
{airline}::{departure_date}::{layover_count}
```

An alert fires only when:
- The key has never been seen before (first run), **or**
- The current price is **strictly less than** the previously recorded price

State is persisted to `data/alerts_cache.json` on every write and flushed on shutdown, so restarts do not reset history.

---

## Deployment: Railway

Railway is the recommended host — always-on container, no cold starts, native env var management.

### 1. Push to GitHub

The repo is already at `https://github.com/ErishMan/SkySkimmer`.

### 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and sign in
2. **New Project → Deploy from GitHub repo → ErishMan/SkySkimmer**
3. Railway auto-detects the `Dockerfile` and builds on every push to `main`

### 3. Add environment variables

In the Railway project dashboard go to **Variables** and add all keys from `.env.example`:

```
TEQUILA_API_KEY
DISCORD_WEBHOOK_URL
DISCORD_ERROR_WEBHOOK_URL   (optional)
SUPABASE_URL                (optional)
SUPABASE_KEY                (optional)
POLL_INTERVAL_MINUTES       (default: 30)
APP_ENV                     production
LOG_LEVEL                   INFO
```

### 4. Add a persistent volume (for local JSON state)

If not using Supabase, mount a Railway Volume at `/app/data` so `alerts_cache.json` survives redeploys:

1. Railway project → **New → Volume**
2. Mount path: `/app/data`

### 5. Deploy

Railway deploys automatically on every push to `main`. Logs are available in the Railway dashboard in real time.

---

## Deployment: AWS Lambda (alternative)

For zero-cost, event-driven operation replace `APScheduler` with an **EventBridge rule** (cron expression) targeting the Lambda function. The application code itself requires no changes — only the entry point changes from `asyncio.run(app.start())` to a Lambda handler calling `asyncio.run(app._run_pipeline())`.

---

## Error Handling

| Error type | Behaviour |
|---|---|
| Missing required env var | Fatal — process exits immediately with a clear message |
| Tequila 429 rate limit | Reads `Retry-After` header, sleeps, retries up to 3× |
| Tequila 5xx server error | Exponential backoff (2s / 4s / 8s), retries up to 3× |
| Tequila 4xx client error | No retry — logs `ERROR`, sends Discord error embed |
| Response schema drift | Logs raw JSON to `logs/error.log`, sends Discord error embed |
| Pipeline overlap | Second tick skips gracefully — no memory leak |
| Discord webhook failure | Logs `ERROR`, does **not** update state (alert will retry next tick) |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `TEQUILA_API_KEY` | ✅ Yes | — | Tequila by Kiwi API key |
| `DISCORD_WEBHOOK_URL` | ✅ Yes | — | Main Discord channel webhook |
| `DISCORD_ERROR_WEBHOOK_URL` | No | Falls back to main | Separate channel for system errors |
| `SUPABASE_URL` | No | — | Supabase project URL (must pair with KEY) |
| `SUPABASE_KEY` | No | — | Supabase anon or service role key |
| `POLL_INTERVAL_MINUTES` | No | `30` | Scheduler interval (5–1440 minutes) |
| `APP_ENV` | No | `development` | `development` or `production` |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Run the test suite: `poetry run pytest tests/ -v`
4. Run the linter: `poetry run ruff check src/ --fix`
5. Open a pull request against `main`

---

## License

MIT
