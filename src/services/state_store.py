"""State store — tracks previously alerted prices per route/offer.

Two implementations:
  - JsonFileStateStore  (local dev / fallback / Railway persistent disk)
  - SupabaseStateStore  (production)

The abstract interface ensures the rest of the pipeline never cares which
backend is active — swapping is a single constructor change in main.py.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path

from src.utils.logger import get_logger

log = get_logger(__name__)


class StateStore(ABC):
    """Abstract state persistence contract."""

    @abstractmethod
    async def get_last_alerted_price(self, flight_key: str) -> float | None:
        """Return the last price we sent an alert for, or None if first run."""
        ...

    @abstractmethod
    async def set_last_alerted_price(self, flight_key: str, price: float) -> None:
        """Persist the price we just alerted on."""
        ...

    @abstractmethod
    async def should_send_alert(self, flight_key: str, current_price: float) -> bool:
        """Return True ONLY if this is a new flight OR price is strictly lower."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any underlying connections."""
        ...


class JsonFileStateStore(StateStore):
    """Persists alert history to a local JSON file.

    Suitable for Railway containers with a persistent /data volume,
    or local development. Not suitable for multi-replica deployments.
    """

    def __init__(self, path: Path = Path("data/alerts_cache.json")) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, float] = self._load()
        self._lock = asyncio.Lock()
        log.info("JsonFileStateStore initialised", path=str(self._path), cached_keys=len(self._cache))

    def _load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("Could not load state cache — starting fresh", error=str(exc))
        return {}

    def _flush(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._cache, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("Failed to flush state cache to disk", error=str(exc))

    async def get_last_alerted_price(self, flight_key: str) -> float | None:
        async with self._lock:
            return self._cache.get(flight_key)

    async def set_last_alerted_price(self, flight_key: str, price: float) -> None:
        async with self._lock:
            self._cache[flight_key] = price
            self._flush()
        log.debug("State updated", flight_key=flight_key, price=price)

    async def should_send_alert(self, flight_key: str, current_price: float) -> bool:
        last_price = await self.get_last_alerted_price(flight_key)
        if last_price is None:
            log.debug("Alert: new flight key (first run)", flight_key=flight_key, price=current_price)
            return True
        if current_price < last_price:
            log.debug(
                "Alert: price dropped",
                flight_key=flight_key,
                last_price=last_price,
                current_price=current_price,
                drop=round(last_price - current_price, 2),
            )
            return True
        log.debug(
            "Suppressed: price unchanged or higher",
            flight_key=flight_key,
            last_price=last_price,
            current_price=current_price,
        )
        return False

    async def close(self) -> None:
        self._flush()
        log.debug("JsonFileStateStore closed and flushed")
