"""SkySkimmer — application entry point.

Startup sequence:
  1. Load & validate environment (fail-fast on misconfiguration)
  2. Configure structured logger
  3. Initialise application components (fetcher, state store, dispatcher)
  4. Register signal handlers for graceful shutdown
  5. Confirm health of all subsystems
  6. Initialise APScheduler with overlap protection
  7. Execute one immediate pipeline run at startup
  8. Block main thread; scheduler fires on configured interval

Pipeline per tick:
  Fetch → Adapt → Evaluate → State Check → Dispatch
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import Settings, load_settings
from src.domain.models import ScraperConfig
from src.services.dispatcher import Dispatcher
from src.services.flight_adapter import map_tequila_response_to_itineraries
from src.services.flight_evaluator import evaluate_itineraries
from src.services.flight_fetcher import (
    ClientRequestError,
    FlightFetcherService,
    ResponseValidationError,
    RetryableUpstreamError,
)
from src.services.state_store import JsonFileStateStore, StateStore
from src.utils.logger import configure_logger, get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pipeline configuration
# These values will be driven from routes.yaml + env in Phase 5.
# For now they are wired here to complete the full observable pipeline.
# ---------------------------------------------------------------------------
STARTUP_ROUTE = dict(
    origin="SYD",
    destination="LHR",
    date_from="15/11/2026",
    date_to="20/11/2026",
    currency="AUD",
    limit=10,
)
STARTUP_CONFIG = ScraperConfig(
    max_price=1800.0,
    max_layovers=1,
    allowed_airlines=["QF", "BA", "CX", "JL"],
    sort_order="price_asc",
    currency="AUD",
)


class SkySkimmer:
    """Application container managing lifecycle, scheduling, and pipeline execution."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._shutdown_event = asyncio.Event()

        # Services
        self.flight_fetcher = FlightFetcherService(settings)
        self.state_store: StateStore = JsonFileStateStore(Path("data/alerts_cache.json"))
        self.dispatcher = Dispatcher(settings)

        # Overlap protection: one boolean lock per cron job
        self._pipeline_running = False

        # Scheduler (AsyncIO-native; lives in the same event loop as httpx)
        self.scheduler = AsyncIOScheduler()

    # -----------------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Async entry point: boot, schedule, run, block."""
        self._log_startup_banner()
        self._health_check()
        self._register_signal_handlers()
        self._configure_scheduler()

        # Fire one immediate pipeline run at startup for immediate feedback
        log.info("Running immediate startup pipeline tick")
        await self._run_pipeline()

        # Start the recurring scheduler
        self.scheduler.start()
        log.info(
            "Scheduler started — pipeline will repeat on interval",
            poll_interval_minutes=self.settings.poll_interval_minutes,
        )

        # Block until shutdown signal
        await self._shutdown_event.wait()
        await self._shutdown()

    def _log_startup_banner(self) -> None:
        log.info("━" * 40)
        log.info("✈  SkySkimmer starting up")
        log.info(
            "Environment loaded",
            app_env=self.settings.app_env.value,
            log_level=self.settings.log_level.value,
            state_store="supabase" if self.settings.use_supabase else "local_json",
            poll_interval_minutes=self.settings.poll_interval_minutes,
        )
        log.info("━" * 40)

    def _health_check(self) -> None:
        log.info(
            "[health] Tequila API key: present",
            key_prefix=self.settings.tequila_api_key.get_secret_value()[:6] + "...",
        )
        log.info("[health] Discord webhook: configured")
        if self.settings.discord_error_webhook_url:
            log.info("[health] Discord error webhook: configured")
        else:
            log.warning("[health] DISCORD_ERROR_WEBHOOK_URL not set — errors go to main webhook")
        if self.settings.use_supabase:
            log.info("[health] State store: Supabase (remote)")
        else:
            log.warning("[health] State store: local JSON file (not suitable for multi-replica)")
        log.info("[health] All checks passed — application is healthy")

    # -----------------------------------------------------------------------
    # Scheduler
    # -----------------------------------------------------------------------

    def _configure_scheduler(self) -> None:
        self.scheduler.add_job(
            self._guarded_pipeline_tick,
            trigger="interval",
            minutes=self.settings.poll_interval_minutes,
            id="pipeline_tick",
            name="SkySkimmer pipeline tick",
            max_instances=1,        # APScheduler-level hard cap
            coalesce=True,          # merge missed ticks instead of stacking them
            misfire_grace_time=120, # skip if more than 120s late
        )
        self.scheduler.add_listener(
            self._on_scheduler_event,
            EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )
        log.debug("Scheduler configured", job_id="pipeline_tick")

    def _on_scheduler_event(self, event) -> None:
        if event.code == EVENT_JOB_MISSED:
            log.warning("Scheduler tick missed (previous run still active?)", job_id=event.job_id)
        if event.code == EVENT_JOB_ERROR:
            log.error(
                "Scheduler job raised an unhandled exception",
                job_id=event.job_id,
                exception=str(event.exception),
            )

    # -----------------------------------------------------------------------
    # Overlap protection wrapper
    # -----------------------------------------------------------------------

    async def _guarded_pipeline_tick(self) -> None:
        """Singleton lock: abort gracefully if the previous tick is still running."""
        if self._pipeline_running:
            log.warning(
                "Pipeline overlap detected — skipping this tick",
                reason="previous execution still in progress",
            )
            return
        self._pipeline_running = True
        try:
            await self._run_pipeline()
        finally:
            self._pipeline_running = False

    # -----------------------------------------------------------------------
    # Core pipeline
    # -----------------------------------------------------------------------

    async def _run_pipeline(self) -> None:
        """Full pipeline: Fetch → Adapt → Evaluate → State Check → Dispatch."""
        log.info("Pipeline tick started")
        try:
            # --- STEP 1: Fetch ---
            payload = await self.flight_fetcher.fetch_flight_data(**STARTUP_ROUTE)

            # --- STEP 2: Adapt ---
            itineraries = map_tequila_response_to_itineraries(payload)
            if not itineraries:
                log.info("Pipeline tick: no itineraries after adaptation; skipping dispatch")
                return

            # --- STEP 3: Evaluate ---
            qualifying = evaluate_itineraries(itineraries, STARTUP_CONFIG)
            log.info(
                "Pipeline tick: evaluation complete",
                total=len(itineraries),
                qualifying=len(qualifying),
            )
            if not qualifying:
                log.info("Pipeline tick: no qualifying itineraries; skipping dispatch")
                return

            # --- STEP 4: State check ---
            # For each qualifying itinerary, ask the state store if we should alert.
            # We also capture previous prices here for drop-vs-new-deal framing.
            to_dispatch: list = []
            price_context: dict[str, float | None] = {}

            for itinerary in qualifying:
                flight_key = _make_flight_key(itinerary)
                prev_price = await self.state_store.get_last_alerted_price(flight_key)
                price_context[itinerary.booking_link] = prev_price

                if await self.state_store.should_send_alert(flight_key, itinerary.total_price):
                    to_dispatch.append((itinerary, flight_key))

            log.info(
                "Pipeline tick: state check complete",
                qualifying=len(qualifying),
                to_dispatch=len(to_dispatch),
            )

            if not to_dispatch:
                log.info("Pipeline tick: all qualifying flights already alerted at current price")
                return

            # --- STEP 5: Dispatch ---
            dispatched = await self.dispatcher.dispatch_all(
                [item[0] for item in to_dispatch],
                price_context,
            )
            log.info("Pipeline tick: dispatch complete", alerts_sent=dispatched)

            # Persist updated prices only for successfully dispatched alerts
            for itinerary, flight_key in to_dispatch:
                await self.state_store.set_last_alerted_price(flight_key, itinerary.total_price)

            log.info(
                "Pipeline tick finished",
                alerts_sent=dispatched,
                state_updated=len(to_dispatch),
            )

        except ClientRequestError as exc:
            log.error(
                "Pipeline tick: non-retryable Tequila client error",
                status_code=exc.status_code,
            )
            await self._send_error_alert(
                f"SkySkimmer could not fetch flight data (HTTP {exc.status_code}).",
                detail=str(exc),
            )
        except RetryableUpstreamError as exc:
            log.error(
                "Pipeline tick: Tequila exhausted retries",
                status_code=exc.status_code,
            )
            await self._send_error_alert(
                "Tequila API is unreachable after retries.",
                detail=str(exc),
            )
        except ResponseValidationError as exc:
            log.error("Pipeline tick: Tequila response schema drift detected")
            await self._send_error_alert(
                "API response schema drift detected — check drift log.",
                detail=str(exc),
            )
        except Exception as exc:
            log.exception("Pipeline tick: unexpected error")
            await self._send_error_alert("Unexpected error during pipeline tick.", detail=str(exc))

    async def _send_error_alert(self, message: str, detail: str | None = None) -> None:
        try:
            await self.dispatcher.send_error_alert(message, detail)
        except Exception:
            log.exception("Failed to deliver error alert to Discord")

    # -----------------------------------------------------------------------
    # Graceful shutdown
    # -----------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown_signal)
        log.debug("Signal handlers registered (SIGINT, SIGTERM)")

    def _handle_shutdown_signal(self) -> None:
        log.warning("Shutdown signal received — initiating graceful shutdown")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        log.info("Shutting down SkySkimmer")

        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            log.info("Scheduler stopped")

        # Wait for in-flight pipeline to complete (max 30s)
        wait_seconds = 0
        while self._pipeline_running and wait_seconds < 30:
            log.info("Waiting for active pipeline tick to finish", waited_seconds=wait_seconds)
            await asyncio.sleep(1)
            wait_seconds += 1

        await self.flight_fetcher.close()
        await self.state_store.close()
        await self.dispatcher.close()

        log.info("Shutdown complete. Goodbye. ✈")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flight_key(itinerary) -> str:
    """Derive a stable idempotency key for an itinerary.

    Key is built from route + departure date (not booking_link deep URL,
    which can change across API calls for the same physical flight).
    """
    dep_date = itinerary.departure_time.strftime("%Y-%m-%d")
    return f"{itinerary.airline}::{dep_date}::{itinerary.layover_count}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    settings = load_settings()
    configure_logger(settings)
    app = SkySkimmer(settings)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
