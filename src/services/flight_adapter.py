"""Adapter layer: map external Tequila DTOs into internal domain models.

This module is the anti-corruption boundary. Raw API responses stop here.
Anything beyond this point consumes only normalized FlightItinerary objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.contracts.tequila import TequilaFlightOffer, TequilaRoute, TequilaSearchResponse
from src.domain.models import FlightItinerary, FlightSegment
from src.utils.logger import get_logger

log = get_logger(__name__)


DEFAULT_BOOKING_LINK = "https://www.kiwi.com"
DEFAULT_AIRLINE = "UNKNOWN"
DEFAULT_AIRPORT = "UNKNOWN"
DEFAULT_CURRENCY = "AUD"


def _safe_float(value: Any) -> float | None:
    """Convert mixed numeric input safely to float.

    Handles cases such as:
      - 600
      - 600.0
      - "600.00"
      - None (returns None)
    Invalid strings return None instead of throwing TypeError/ValueError.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _safe_datetime(value: Any) -> datetime | None:
    """Parse an ISO-ish datetime string safely, else return None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _map_segment(route: TequilaRoute) -> FlightSegment | None:
    """Normalize one raw route segment.

    We defensively skip segments if required fields are missing or malformed,
    rather than allowing partial/null segment data to poison the domain model.
    """
    airline = route.airline or DEFAULT_AIRLINE
    origin = route.flyFrom or route.cityFrom or DEFAULT_AIRPORT
    destination = route.flyTo or route.cityTo or DEFAULT_AIRPORT
    departure_time = _safe_datetime(route.local_departure)
    arrival_time = _safe_datetime(route.local_arrival)

    if departure_time is None or arrival_time is None:
        return None

    return FlightSegment(
        airline=airline,
        origin=origin,
        destination=destination,
        departure_time=departure_time,
        arrival_time=arrival_time,
    )


def map_tequila_response_to_itineraries(payload: TequilaSearchResponse) -> list[FlightItinerary]:
    """Map a validated Tequila response into normalized domain itineraries.

    Handling strategy for nested/missing fields:
      - `price` is parsed via _safe_float() to tolerate strings like "600.00"
      - `route` entries are individually normalized; malformed segments are skipped
      - if all segments are invalid or price is missing, the itinerary is skipped
      - top-level fallbacks are applied for airline/currency/booking link
    """
    itineraries: list[FlightItinerary] = []

    for offer in payload.data:
        total_price = _safe_float(offer.price)
        if total_price is None:
            log.warning("Skipping offer with invalid/missing price", offer_id=offer.id)
            continue

        segments = [segment for route in offer.route if (segment := _map_segment(route)) is not None]
        if not segments:
            log.warning("Skipping offer with no valid route segments", offer_id=offer.id)
            continue

        first_segment = segments[0]
        last_segment = segments[-1]
        primary_airline = first_segment.airline or DEFAULT_AIRLINE
        layover_count = max(len(segments) - 1, 0)

        itinerary = FlightItinerary(
            airline=primary_airline,
            total_price=total_price,
            currency=offer.currency or payload.currency or DEFAULT_CURRENCY,
            departure_time=first_segment.departure_time,
            arrival_time=last_segment.arrival_time,
            layover_count=layover_count,
            booking_link=offer.deep_link or DEFAULT_BOOKING_LINK,
            segment_count=len(segments),
            segments=segments,
        )
        itineraries.append(itinerary)

    log.info(
        "Mapped Tequila payload into normalized itineraries",
        input_count=len(payload.data),
        output_count=len(itineraries),
    )
    return itineraries
