"""State store interface — tracks last-alerted prices per route.

Two implementations will be provided in Phase 4:
  - SupabaseStateStore  (production)
  - JsonFileStateStore  (local dev / fallback)

This module defines the abstract interface only.
Placeholder stubs are included so imports don't break during scaffold.
"""

from abc import ABC, abstractmethod
from decimal import Decimal


class StateStore(ABC):
    """Abstract base for price-history state persistence."""

    @abstractmethod
    async def get_last_alerted_price(self, route_id: str) -> Decimal | None:
        """Return the last price we sent an alert for, or None if first run."""
        ...

    @abstractmethod
    async def set_last_alerted_price(self, route_id: str, price: Decimal) -> None:
        """Persist the price we just alerted on."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any underlying connections."""
        ...
