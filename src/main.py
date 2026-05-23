"""SkySkimmer — application entry point.

Startup sequence (strict order):
  1. Load & validate environment (fail-fast on misconfiguration)
  2. Configure structured logger
  3. Log startup diagnostics
  4. Register SIGINT/SIGTERM handlers for graceful shutdown
  5. Trigger a one-shot Tequila fetch for payload inspection
  6. Block until termination signal received
"""

import asyncio
import signal

from src.config.settings import Settings, load_settings
from src.services.flight_fetcher import (
    ClientRequestError,
    FlightFetcherService,
    ResponseValidationError,
    RetryableUpstreamError,
)
from src.utils.logger import configure_logger, get_logger

log = get_logger(__name__)


class SkySkimmer:
    """Application container managing lifecycle and shutdown coordination."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._is_shutting_down = False
        self.flight_fetcher = FlightFetcherService(settings)

    def start(self) -> None:
        """Boot the application and block until a shutdown signal."""
        self._log_startup_banner()
        self._register_signal_handlers()
        self._health_check()
        asyncio.run(self._run_startup_fetch())

        log.info(
            "Network ingestion hook completed — application idle",
            poll_interval_minutes=self.settings.poll_interval_minutes,
        )

        self._block_until_shutdown()

    def _log_startup_banner(self) -> None:
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("✈  SkySkimmer starting up")
        log.info(
            "Environment loaded",
            app_env=self.settings.app_env.value,
            log_level=self.settings.log_level.value,
            state_store="supabase" if self.settings.use_supabase else "local_json",
            poll_interval_minutes=self.settings.poll_interval_minutes,
        )
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    def _health_check(self) -> None:
        log.info(
            "[health] Tequila API key: present",
            key_prefix=self.settings.tequila_api_key.get_secret_value()[:6] + "...",
        )
        log.info("[health] Discord webhook: configured")
        if self.settings.discord_error_webhook_url:
            log.info("[health] Discord error webhook: configured")
        else:
            log.warning(
                "[health] DISCORD_ERROR_WEBHOOK_URL not set — system errors will go to the main webhook"
            )
        if self.settings.use_supabase:
            log.info("[health] State store: Supabase (remote)")
        else:
            log.warning("[health] State store: local JSON file (not suitable for production)")
        log.info("[health] All checks passed — application is healthy")

    async def _run_startup_fetch(self) -> None:
        """One-shot startup hook for inspecting raw validated payload shape."""
        try:
            payload = await self.flight_fetcher.fetch_flight_data(
                origin="SYD",
                destination="LHR",
                date_from="15/11/2026",
                date_to="20/11/2026",
                currency="AUD",
                limit=3,
            )
            log.info(
                "Startup fetch succeeded — raw validated payload follows",
                payload=payload.model_dump(mode="json"),
            )
        except ClientRequestError as exc:
            log.exception(
                "Startup fetch failed with non-retryable client error",
                status_code=exc.status_code,
            )
        except RetryableUpstreamError as exc:
            log.exception(
                "Startup fetch exhausted retries on transient upstream failure",
                status_code=exc.status_code,
            )
        except ResponseValidationError:
            log.exception("Startup fetch failed due to response schema drift")
        finally:
            await self.flight_fetcher.close()

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        log.debug("Signal handlers registered (SIGINT, SIGTERM)")

    def _handle_shutdown_signal(self, signum: int, frame: object) -> None:
        sig_name = signal.Signals(signum).name
        log.warning(f"Received {sig_name} — initiating graceful shutdown")
        self._is_shutting_down = True

    def _shutdown(self) -> None:
        log.info("Shutting down SkySkimmer")
        log.info("Shutdown complete. Goodbye. ✈")

    def _block_until_shutdown(self) -> None:
        import time

        try:
            while not self._is_shutting_down:
                time.sleep(1)
        finally:
            self._shutdown()


def main() -> None:
    settings = load_settings()
    configure_logger(settings)
    app = SkySkimmer(settings)
    app.start()


if __name__ == "__main__":
    main()
