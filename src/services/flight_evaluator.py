"""Pure rules engine for evaluating normalized flight itineraries.

This module is deterministic and side-effect free:
  - no network calls
  - no global state
  - no I/O
It accepts domain models + config and returns filtered/sorted results.
"""

from __future__ import annotations

from src.domain.models import FlightItinerary, ScraperConfig


def _airline_allowed(itinerary: FlightItinerary, config: ScraperConfig) -> bool:
    """Return True if airline passes allowlist, or if no allowlist is defined."""
    if not config.allowed_airlines:
        return True
    allowed = {code.upper() for code in config.allowed_airlines}
    return itinerary.airline.upper() in allowed


def qualifies_itinerary(itinerary: FlightItinerary, config: ScraperConfig) -> bool:
    """Apply all configured constraints to a single itinerary."""
    if itinerary.total_price > config.max_price:
        return False
    if itinerary.layover_count > config.max_layovers:
        return False
    if itinerary.currency.upper() != config.currency.upper():
        return False
    if not _airline_allowed(itinerary, config):
        return False
    return True


def evaluate_itineraries(
    itineraries: list[FlightItinerary],
    config: ScraperConfig,
) -> list[FlightItinerary]:
    """Filter and sort itineraries using only the supplied config.

    Sorting is configurable and stable:
      - price_asc  => cheapest first
      - price_desc => most expensive first
    """
    filtered = [itinerary for itinerary in itineraries if qualifies_itinerary(itinerary, config)]

    reverse = config.sort_order == "price_desc"
    return sorted(filtered, key=lambda itinerary: itinerary.total_price, reverse=reverse)
