"""Notification dispatcher — Discord Rich Embed construction and delivery.

Responsibilities:
  - Build structured Discord embed payloads from FlightItinerary domain objects
  - Execute POST to Discord webhook with resilient httpx client
  - Iterate multiple qualifying flights with a 1-second inter-alert delay
    to avoid Discord per-webhook rate limits (30 messages/minute)

Non-goals:
  - Idempotency logic (handled by StateStore)
  - Filtering logic (handled by RulesEngine)
"""

from __future__ import annotations

import asyncio

import httpx

from src.config.settings import Settings
from src.domain.models import FlightItinerary
from src.utils.logger import get_logger

log = get_logger(__name__)

INTER_ALERT_DELAY_SECONDS = 1.0
DISCORD_EMBED_COLOR_NEW = 0x2ECC71       # Green — new low price
DISCORD_EMBED_COLOR_DROP = 0x27AE60      # Darker green — price drop on known route
DISCORD_EMBED_COLOR_ERROR = 0xE74C3C     # Red — system error


class DispatchError(Exception):
    """Non-retryable dispatch failure."""


class Dispatcher:
    """Formats and delivers Discord embed alerts for qualifying itineraries."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _build_embed(
        self,
        itinerary: FlightItinerary,
        is_price_drop: bool,
        previous_price: float | None = None,
    ) -> dict:
        """Construct a Discord Rich Embed from a FlightItinerary.

        Fields always present: airline, route summary, price, departure,
        arrival, layovers, and a direct booking CTA.
        """
        dep = itinerary.departure_time
        arr = itinerary.arrival_time
        dep_str = dep.strftime("%a %d %b %Y, %H:%M")
        arr_str = arr.strftime("%a %d %b %Y, %H:%M")

        first_segment = itinerary.segments[0] if itinerary.segments else None
        last_segment = itinerary.segments[-1] if itinerary.segments else None
        origin = first_segment.origin if first_segment else "?"
        dest = last_segment.destination if last_segment else "?"

        layover_label = (
            "Direct" if itinerary.layover_count == 0
            else f"{itinerary.layover_count} stop(s)"
        )

        price_display = f"{itinerary.currency} ${itinerary.total_price:,.0f}"
        if is_price_drop and previous_price is not None:
            drop = previous_price - itinerary.total_price
            title = f"⬇️ Price Drop — {origin} → {dest}"
            description = (
                f"**{price_display}** — down from {itinerary.currency} ${previous_price:,.0f} "
                f"(saved **${drop:,.0f}**)"
            )
            color = DISCORD_EMBED_COLOR_DROP
        else:
            title = f"✨ New Deal Found — {origin} → {dest}"
            description = f"**{price_display}** — first-time alert for this route"
            color = DISCORD_EMBED_COLOR_NEW

        embed: dict = {
            "title": title,
            "description": description,
            "color": color,
            "url": str(itinerary.booking_link) if itinerary.booking_link else None,
            "fields": [
                {"name": "✈ Airline", "value": itinerary.airline, "inline": True},
                {"name": "🗺 Route", "value": f"{origin} → {dest}", "inline": True},
                {"name": "💺 Stops", "value": layover_label, "inline": True},
                {"name": "🗓 Departs", "value": dep_str, "inline": True},
                {"name": "📍 Arrives", "value": arr_str, "inline": True},
                {"name": "💰 Price", "value": price_display, "inline": True},
            ],
            "footer": {"text": "SkySkimmer • Prices are live snapshots and may change"},
            "timestamp": dep.isoformat(),
        }

        if itinerary.booking_link:
            embed["fields"].append(
                {"name": "🔗 Book Now", "value": f"[Open booking link]({str(itinerary.booking_link)})", "inline": False}
            )

        return embed

    async def _post_embed(self, embed: dict, webhook_url: str) -> None:
        """POST a single embed payload to a Discord webhook."""
        payload = {"embeds": [embed]}
        try:
            response = await self._client.post(webhook_url, json=payload)
            response.raise_for_status()
            log.info(
                "Discord alert delivered",
                status_code=response.status_code,
                embed_title=embed.get("title"),
            )
        except httpx.HTTPStatusError as exc:
            log.error(
                "Discord webhook returned an error",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:500],
            )
            raise DispatchError(f"Discord POST failed: HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            log.exception("Discord webhook request failed (network error)")
            raise DispatchError(f"Discord POST network error: {exc}") from exc

    async def send_alert(
        self,
        itinerary: FlightItinerary,
        is_price_drop: bool = False,
        previous_price: float | None = None,
    ) -> None:
        """Send a single itinerary alert to the main Discord webhook."""
        webhook_url = self.settings.discord_webhook_url.get_secret_value()
        embed = self._build_embed(itinerary, is_price_drop=is_price_drop, previous_price=previous_price)
        await self._post_embed(embed, webhook_url)

    async def send_error_alert(self, message: str, detail: str | None = None) -> None:
        """Send a system error embed to the error webhook."""
        webhook_url = self.settings.effective_error_webhook.get_secret_value()
        embed: dict = {
            "title": "⚠️ SkySkimmer System Alert",
            "description": message,
            "color": DISCORD_EMBED_COLOR_ERROR,
            "footer": {"text": "SkySkimmer • System"},
        }
        if detail:
            embed["fields"] = [{"name": "Detail", "value": detail[:1000], "inline": False}]
        await self._post_embed(embed, webhook_url)

    async def dispatch_all(
        self,
        qualifying: list[FlightItinerary],
        price_context: dict[str, float | None],
    ) -> int:
        """Dispatch alerts for all qualifying itineraries with rate-limit protection.

        Args:
            qualifying: Itineraries confirmed by StateStore as needing an alert.
            price_context: Maps each itinerary booking_link to its previous price
                           (None if first-run). Used for drop vs. new-deal framing.

        Returns:
            Count of alerts successfully delivered.
        """
        delivered = 0
        for index, itinerary in enumerate(qualifying):
            prev = price_context.get(str(itinerary.booking_link))
            is_drop = prev is not None and itinerary.total_price < prev
            try:
                await self.send_alert(itinerary, is_price_drop=is_drop, previous_price=prev)
                delivered += 1
            except DispatchError as exc:
                log.error("Failed to dispatch alert for itinerary", error=str(exc), index=index)

            if index < len(qualifying) - 1:
                await asyncio.sleep(INTER_ALERT_DELAY_SECONDS)

        return delivered
