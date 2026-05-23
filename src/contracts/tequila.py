"""Data contracts for Tequila API ingestion.

We intentionally validate only the outer response envelope plus the
small subset of fields needed to inspect payload shape safely at this
phase. Downstream business logic and deeper field mapping will come later.
"""

from typing import Any

from pydantic import BaseModel, Field


class TequilaRoute(BaseModel):
    """Minimal route segment contract."""

    id: str | None = None
    cityFrom: str | None = None
    cityTo: str | None = None
    flyFrom: str | None = None
    flyTo: str | None = None
    airline: str | None = None
    local_departure: str | None = None
    local_arrival: str | None = None


class TequilaFlightOffer(BaseModel):
    """Minimal flight offer contract for shape validation.

    We keep this intentionally small for Phase 2: enough to verify the
    payload and log raw data, but not yet our full business model.
    """

    id: str | None = None
    price: float | int | None = None
    currency: str | None = None
    deep_link: str | None = None
    route: list[TequilaRoute] = Field(default_factory=list)


class TequilaSearchResponse(BaseModel):
    """Validated outer envelope returned by Tequila /v2/search."""

    search_id: str | None = None
    currency: str | None = None
    data: list[TequilaFlightOffer] = Field(default_factory=list)
    _results: int | None = None


class RawTequilaResponse(BaseModel):
    """Permissive wrapper for safe logging of validated raw payloads."""

    payload: dict[str, Any]
