"""Internal domain models for SkySkimmer.

These are our clean business-layer types. No raw Tequila DTOs should leak
past the adapter boundary into filtering/evaluation logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

CabinClass = Literal["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]


class FlightSegment(BaseModel):
    """A normalized segment within an itinerary."""

    airline: str = Field(..., min_length=1)
    origin: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    departure_time: datetime
    arrival_time: datetime


class FlightItinerary(BaseModel):
    """Normalized itinerary used by the rules engine."""

    airline: str = Field(..., min_length=1)
    total_price: float = Field(..., ge=0)
    currency: str = Field(default="AUD", min_length=1)
    departure_time: datetime
    arrival_time: datetime
    layover_count: int = Field(..., ge=0)
    booking_link: HttpUrl
    segment_count: int = Field(..., ge=1)
    segments: list[FlightSegment] = Field(default_factory=list)


class ScraperConfig(BaseModel):
    """Rule context supplied to the pure filtering engine.

    All thresholds and constraints live here so they are configurable
    and never hardcoded into filtering conditionals.
    """

    max_price: float = Field(..., ge=0)
    max_layovers: int = Field(..., ge=0)
    allowed_airlines: list[str] = Field(default_factory=list)
    sort_order: Literal["price_asc", "price_desc"] = "price_asc"
    currency: str = Field(default="AUD", min_length=1)
