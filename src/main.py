"""SkySkimmer — application entry point.

Startup sequence (strict order):
  1. Load & validate environment (fail-fast on misconfiguration)
  2. Configure structured logger
  3. Log startup diagnostics
  4. Register SIGINT/SIGTERM handlers for graceful shutdown
  5. (Future) Start APScheduler with configured cron jobs
  6. Block until termination signal received
"""

import signal
import sys

from src.config.settings import Settings, load_settings
from src.utils.logger import configure_logger, get_logger

# Module-level logger — populated after configure_logger() is called
log = get_logger(__name__)


class SkySkimmer:
    """Application container managing lifecycle and shutdown coordination."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._is_shutting_down = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Boot the application and block until a shutdown signal."""
        self._log_startup_banner()
        self._register_signal_handlers()
        self._health_check()

        log.info(
            "Scheduler ready — waiting for first poll cycle",
            poll_interval_minutes=self.settings.poll_interval_minutes,
        )

        # TODO (Phase 3): scheduler.start() will be called here.
        # For now, we block the main thread to keep the container alive
        # and confirm the harness is operational.
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
        """Validate that all subsystem prerequisites are reachable.

        At this scaffold stage, we confirm required secrets are present
        (already guaranteed by Settings validation) and log their status
        without ever printing secret values.
        """
        log.info(
            "[health] Tequila API key: present",
            key_prefix=self.settings.tequila_api_key.get_secret_value()[:6] + "...",
        )
        log.info("[health] Discord webhook: configured")
        if self.settings.discord_error_webhook_url:
            log.info("[health] Discord error webhook: configured")
        else:
            log.warning(
                "[health] DISCORD_ERROR_WEBHOOK_URL not set — "
                "system errors will go to the main webhook"
            )
        if self.settings.use_supabase:
            log.info("[health] State store: Supabase (remote)")
        else:
            log.warning(
                "[health] State store: local JSON file (not suitable for production)"
            )
        log.info("[health] All checks passed — application is healthy")

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Register OS signal handlers for clean shutdown.

        SIGINT  — Ctrl+C (interactive terminal)
        SIGTERM — Container orchestrator stop (Railway, Kubernetes, Docker)
        """
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        log.debug("Signal handlers registered (SIGINT, SIGTERM)")

    def _handle_shutdown_signal(self, signum: int, frame: object) -> None:
        sig_name = signal.Signals(signum).name
        log.warning(f"Received {sig_name} — initiating graceful shutdown")
        self._is_shutting_down = True

    def _shutdown(self) -> None:
        log.info("Shutting down SkySkimmer")
        # TODO (Phase 3): scheduler.shutdown(wait=True) will be called here
        # TODO (Phase 4): state store connection cleanup will go here
        log.info("Shutdown complete. Goodbye. ✈")

    def _block_until_shutdown(self) -> None:
        """Block the main thread, yielding to signals, until shutdown is requested."""
        import time

        try:
            while not self._is_shutting_down:
                time.sleep(1)
        finally:
            self._shutdown()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    # Step 1: Fail-fast environment validation
    # If any required variable is missing, this exits the process immediately.
    settings = load_settings()

    # Step 2: Configure structured logger (must happen before any log calls)
    configure_logger(settings)

    # Step 3: Boot the application
    app = SkySkimmer(settings)
    app.start()


if __name__ == "__main__":
    main()
