"""Tequila/Kiwi data ingestion service.

Responsibilities in Phase 2:
  - construct and execute flight search requests
  - enforce timeout ceiling
  - retry transient faults (429 and 5xx) with exponential backoff
  - validate the outer response envelope with Pydantic
  - log every network phase with structured logging

Non-goals in Phase 2:
  - itinerary filtering
  - price evaluation
  - scheduling/cron
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config.settings import Settings
from src.contracts.tequila import TequilaSearchResponse
from src.utils.logger import get_logger

log = get_logger(__name__)

TEQUILA_BASE_URL = "https://api.tequila.kiwi.com"
SEARCH_PATH = "/v2/search"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RETRY_ATTEMPTS = 3


class FlightFetcherError(Exception):
    """Base exception for fetcher failures."""


class RetryableUpstreamError(FlightFetcherError):
    """Transient upstream failure (429/5xx) eligible for retry."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ClientRequestError(FlightFetcherError):
    """Non-retryable client-side/upstream 4xx failure."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ResponseValidationError(FlightFetcherError):
    """Tequila returned data that failed our response contract."""


def _is_retryable_exception(exc: BaseException) -> bool:
    """Return True for transient faults that should be retried."""
    return isinstance(exc, (RetryableUpstreamError, httpx.TimeoutException, httpx.NetworkError))


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Tenacity callback: log each retry with attempt number and wait time."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    next_sleep = retry_state.next_action.sleep if retry_state.next_action else None
    log.warning(
        "Retrying Tequila request after transient failure",
        attempt=retry_state.attempt_number,
        wait_seconds=next_sleep,
        error_type=type(exc).__name__ if exc else None,
        error_message=str(exc) if exc else None,
    )


class FlightFetcherService:
    """Dedicated service for Tequila flight search ingestion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=TEQUILA_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            headers={
                "apikey": self.settings.tequila_api_key.get_secret_value(),
                "Accept": "application/json",
                "User-Agent": "SkySkimmer/0.1.0",
            },
        )

    async def close(self) -> None:
        """Close underlying HTTP connections."""
        await self._client.aclose()

    async def fetch_flight_data(
        self,
        *,
        origin: str,
        destination: str,
        date_from: str,
        date_to: str | None = None,
        currency: str = "AUD",
        limit: int = 5,
    ) -> TequilaSearchResponse:
        """Fetch raw Tequila flight search data and validate its outer envelope.

        Args:
            origin: IATA origin airport/city code (e.g. SYD)
            destination: IATA destination airport/city code (e.g. LHR)
            date_from: Search start date in DD/MM/YYYY format required by Tequila
            date_to: Optional search end date in DD/MM/YYYY format
            currency: Preferred result currency
            limit: Max number of records for inspection (small by design in Phase 2)
        """
        params: dict[str, Any] = {
            "fly_from": origin,
            "fly_to": destination,
            "date_from": date_from,
            "date_to": date_to or date_from,
            "curr": currency,
            "limit": limit,
            "sort": "date",
            "asc": 1,
        }

        log.info(
            "Initiating Tequila flight search request",
            origin=origin,
            destination=destination,
            date_from=date_from,
            date_to=date_to or date_from,
            currency=currency,
            limit=limit,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )

        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable_exception),
            stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
            before_sleep=_log_retry_attempt,
        ):
            with attempt:
                response = await self._execute_request(params)
                payload = response.json()

                try:
                    validated = TequilaSearchResponse.model_validate(payload)
                except Exception as exc:
                    log.exception(
                        "Tequila response failed schema validation",
                        status_code=response.status_code,
                    )
                    raise ResponseValidationError(
                        f"Tequila response contract validation failed: {exc}"
                    ) from exc

                log.info(
                    "Tequila request completed successfully",
                    status_code=response.status_code,
                    result_count=len(validated.data),
                    search_id=validated.search_id,
                )
                return validated

        raise FlightFetcherError("Unexpected fetcher termination: retry loop exited without result")

    async def _execute_request(self, params: dict[str, Any]) -> httpx.Response:
        """Execute a single HTTP request and classify failure modes."""
        try:
            response = await self._client.get(SEARCH_PATH, params=params)
        except httpx.TimeoutException as exc:
            log.exception(
                "Tequila request timed out",
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            )
            raise
        except httpx.NetworkError as exc:
            log.exception("Network error during Tequila request")
            raise

        status_code = response.status_code

        if status_code == 429:
            retry_after = response.headers.get("Retry-After")
            log.warning(
                "Tequila rate limit encountered",
                status_code=status_code,
                retry_after=retry_after,
                response_text=response.text[:500],
            )
            raise RetryableUpstreamError(
                f"Tequila rate limited request (429). Retry-After={retry_after}",
                status_code=status_code,
            )

        if 500 <= status_code <= 599:
            log.error(
                "Tequila upstream server error",
                status_code=status_code,
                response_text=response.text[:500],
            )
            raise RetryableUpstreamError(
                f"Tequila upstream server error: HTTP {status_code}",
                status_code=status_code,
            )

        if 400 <= status_code <= 499:
            log.exception(
                "Tequila client error — request will not be retried",
                status_code=status_code,
                response_text=response.text[:500],
            )
            raise ClientRequestError(
                f"Tequila client error: HTTP {status_code}",
                status_code=status_code,
            )

        return response
