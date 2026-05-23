"""Unit tests for environment variable validation.

These tests verify fail-fast behaviour — the application must refuse
to start if required variables are absent or malformed.
"""

import pytest
from pydantic import ValidationError

from src.config.settings import Settings


MINIMAL_VALID_ENV = {
    "TEQUILA_API_KEY": "test-api-key-abc123",
    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/abc",
}


class TestRequiredFields:
    def test_valid_minimal_config_loads(self):
        settings = Settings(**MINIMAL_VALID_ENV)
        assert settings.tequila_api_key.get_secret_value() == "test-api-key-abc123"
        assert settings.poll_interval_minutes == 30  # default

    def test_missing_tequila_key_raises(self):
        with pytest.raises((ValidationError, SystemExit)):
            Settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc")

    def test_missing_discord_webhook_raises(self):
        with pytest.raises((ValidationError, SystemExit)):
            Settings(TEQUILA_API_KEY="test-key")

    def test_both_required_missing_raises(self):
        with pytest.raises((ValidationError, SystemExit)):
            Settings()


class TestOptionalFields:
    def test_defaults_applied(self):
        settings = Settings(**MINIMAL_VALID_ENV)
        assert settings.poll_interval_minutes == 30
        assert settings.app_env.value == "development"
        assert settings.log_level.value == "INFO"
        assert not settings.use_supabase

    def test_poll_interval_too_low_raises(self):
        with pytest.raises(ValidationError):
            Settings(**MINIMAL_VALID_ENV, POLL_INTERVAL_MINUTES=2)

    def test_poll_interval_too_high_raises(self):
        with pytest.raises(ValidationError):
            Settings(**MINIMAL_VALID_ENV, POLL_INTERVAL_MINUTES=9999)


class TestSupabasePairing:
    def test_supabase_url_without_key_raises(self):
        with pytest.raises(ValidationError, match="must both be set"):
            Settings(**MINIMAL_VALID_ENV, SUPABASE_URL="https://xyz.supabase.co")

    def test_supabase_key_without_url_raises(self):
        with pytest.raises(ValidationError, match="must both be set"):
            Settings(**MINIMAL_VALID_ENV, SUPABASE_KEY="super-secret-key")

    def test_both_supabase_fields_enables_store(self):
        settings = Settings(
            **MINIMAL_VALID_ENV,
            SUPABASE_URL="https://xyz.supabase.co",
            SUPABASE_KEY="super-secret-key",
        )
        assert settings.use_supabase is True


class TestEffectiveErrorWebhook:
    def test_falls_back_to_main_webhook_when_error_webhook_absent(self):
        settings = Settings(**MINIMAL_VALID_ENV)
        assert (
            settings.effective_error_webhook.get_secret_value()
            == MINIMAL_VALID_ENV["DISCORD_WEBHOOK_URL"]
        )

    def test_uses_dedicated_error_webhook_when_present(self):
        error_url = "https://discord.com/api/webhooks/999/error"
        settings = Settings(**MINIMAL_VALID_ENV, DISCORD_ERROR_WEBHOOK_URL=error_url)
        assert settings.effective_error_webhook.get_secret_value() == error_url
