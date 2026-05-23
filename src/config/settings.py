"""Environment variable validation using Pydantic Settings.

This module is the FIRST thing loaded on startup. If any required
variable is missing or malformed, the application exits immediately
with a clear, actionable error message — fail-fast by design.
"""

from enum import StrEnum

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Validated application settings.

    All fields marked as required (no default) will cause a
    ValidationError — and application exit — if absent from the
    environment at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently discard unknown env vars
    )

    # --- Required ---
    tequila_api_key: SecretStr = Field(
        ...,
        description="Tequila by Kiwi API key",
    )
    discord_webhook_url: SecretStr = Field(
        ...,
        description="Discord webhook URL for price alerts",
    )

    # --- Optional with sensible defaults ---
    discord_error_webhook_url: SecretStr | None = Field(
        default=None,
        description="Separate Discord webhook for system errors (falls back to main webhook)",
    )
    supabase_url: str | None = Field(
        default=None,
        description="Supabase project URL (omit to use local JSON state store)",
    )
    supabase_key: SecretStr | None = Field(
        default=None,
        description="Supabase anon/service role key",
    )
    poll_interval_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        description="Scheduler poll interval in minutes (5–1440)",
    )
    app_env: AppEnv = Field(
        default=AppEnv.DEVELOPMENT,
        description="Runtime environment",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum log level",
    )

    # --- Cross-field validation ---
    @model_validator(mode="after")
    def supabase_credentials_must_be_paired(self) -> "Settings":
        """Ensure both Supabase URL and key are provided together, or neither."""
        has_url = self.supabase_url is not None
        has_key = self.supabase_key is not None
        if has_url ^ has_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must both be set, or both be omitted. "
                "Partial Supabase configuration is not valid."
            )
        return self

    @property
    def use_supabase(self) -> bool:
        """True if Supabase credentials are fully configured."""
        return self.supabase_url is not None and self.supabase_key is not None

    @property
    def effective_error_webhook(self) -> SecretStr:
        """Returns the error webhook, falling back to the main webhook."""
        return self.discord_error_webhook_url or self.discord_webhook_url


def load_settings() -> Settings:
    """Load and validate settings, exiting on any validation failure.

    This function is intentionally not lazy — call it at the very top
    of main() so misconfiguration is caught before any work begins.
    """
    import sys

    try:
        return Settings()
    except Exception as exc:  # pydantic ValidationError
        # Print directly to stderr — logger may not be initialised yet
        print(
            f"\n[FATAL] Environment configuration is invalid.\n"
            f"SkySkimmer cannot start until all required variables are set.\n\n"
            f"Details:\n{exc}\n\n"
            f"See .env.example for the full list of required variables.\n",
            file=sys.stderr,
        )
        sys.exit(1)
