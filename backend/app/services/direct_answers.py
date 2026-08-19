"""Purpose-built answers for questions a search engine handles badly.

Two kinds:

- **Time and date** -> the machine's own clock. No network, no configuration, no
  terms of service. Always available.
- **Weather** -> a weather API, but only if one is configured.

Weather answers come from wttr.in by default. It is the only keyless option that
clears the bar for a commercial product: Apache-2.0, no API key, no
non-commercial clause, and self-hostable - point WEATHER_BASE_URL at your own
instance and no third party is involved at all.

    WEATHER_PROVIDER=wttr.in   (default) keyless, commercial-safe
    WEATHER_PROVIDER=""        fall through to ordinary web search

Falling back to search is the last resort rather than the default, because search
snippets about weather usually say "check current conditions" without ever stating
the temperature - which is exactly the useless answer this module exists to avoid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings
from app.services import usercontext

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0)



class DirectAnswerError(Exception):
    """The direct provider could not answer; the caller should fall back."""


@dataclass
class DirectAnswer:
    #: Rendered text injected into the prompt as authoritative data.
    content: str
    #: Shown to the user as the source of the answer.
    source_label: str
    source_url: str | None = None


# wttr.in's j1 format is a compact JSON forecast: current conditions plus three
# days, with no key and no query-per-field ceremony.
def _wttr_url(place: str) -> str:
    base = settings.weather_base_url.rstrip("/")
    # The place goes in the path, so it must be encoded - "New York" would
    # otherwise break the URL.
    from urllib.parse import quote

    return f"{base}/{quote(place)}?format=j1"


async def _weather_wttr(place: str, label: str) -> DirectAnswer:
    """Current conditions and a short outlook from wttr.in."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.get(
                _wttr_url(place),
                headers={
                    # wttr.in serves an HTML page to browser-like clients and JSON
                    # to console ones, so this must NOT look like a browser.
                    "User-Agent": "curl/8.0",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DirectAnswerError(
                f"The weather service could not be reached: {exc}"
            ) from exc

    current_list = payload.get("current_condition") or []
    if not current_list:
        raise DirectAnswerError(f"No weather data was returned for {label}.")
    current = current_list[0]

    # wttr.in reports the nearest weather *station*, which is often a suburb or a
    # neighbouring town rather than the place asked about - "Tokyo" comes back as
    # "Shikinejima", "Nagpur" as "Dhantoli". The reading is right for the area but
    # the name is confusing, so the caller's label wins when it has one and the
    # station is only used to fill in a missing region or country.
    areas = (payload.get("nearest_area") or [{}])[0]
    station_parts = [
        (areas.get("areaName") or [{}])[0].get("value"),
        (areas.get("region") or [{}])[0].get("value"),
        (areas.get("country") or [{}])[0].get("value"),
    ]
    station = ", ".join(part for part in station_parts if part)

    if label:
        place_label = label
        # Mentioned only when the station is genuinely a different place, so the
        # user can see where the reading came from without it replacing the name
        # they used.
        station_note = (
            station
            if station and station.split(",")[0].lower() not in label.lower()
            else None
        )
    else:
        place_label = station
        station_note = None

    description = ((current.get("weatherDesc") or [{}])[0]).get("value", "unknown")
    lines = [
        f"Live weather for {place_label}",
    ]
    if station_note:
        lines.append(f"Nearest reporting station: {station_note}.")
    lines += [
        f"Observed at {current.get('localObsDateTime', 'now')} local time.",
        f"Conditions: {description}.",
        f"Temperature: {current.get('temp_C')}°C / {current.get('temp_F')}°F "
        f"(feels like {current.get('FeelsLikeC')}°C).",
        f"Humidity: {current.get('humidity')}%.",
        f"Wind: {current.get('windspeedKmph')} km/h "
        f"{current.get('winddir16Point', '')}.".strip(),
        f"Visibility: {current.get('visibility')} km. "
        f"Pressure: {current.get('pressure')} mb.",
    ]

    daily = payload.get("weather") or []
    if daily:
        lines.append("")
        lines.append("Daily outlook:")
        for day in daily[:3]:
            chance = max(
                (int(h.get("chanceofrain") or 0) for h in day.get("hourly") or []),
                default=0,
            )
            lines.append(
                f"  {day.get('date')}: {day.get('mintempC')}-{day.get('maxtempC')}"
                f"°C, up to {chance}% chance of rain"
            )

    return DirectAnswer(
        content="\n".join(lines),
        source_label=f"wttr.in - live weather for {place_label}",
        source_url=f"{settings.weather_base_url.rstrip('/')}/{place}",
    )


def weather_provider_configured() -> bool:
    """Whether a weather API may be called at all.

    False in a default commercial build, so the caller routes weather questions
    to ordinary web search instead of silently using a non-commercial free tier.
    """
    provider = settings.weather_provider.strip().lower()
    if not provider:
        return False
    # wttr.in needs no key, so it is ready as soon as it is selected.
    if provider in {"wttr.in", "wttr"}:
        return True
    # Anything else is key-based; without a key there is nothing to call.
    return bool(settings.weather_api_key.strip())


async def weather(
    place_hint: str | None, location: usercontext.UserLocation | None
) -> DirectAnswer:
    """Current conditions plus a two-day outlook.

    place_hint is a place named in the question; when absent the user's own
    location is used, which is what makes "what's the temperature" answerable
    with no further input.

    Raises DirectAnswerError when no provider is configured, which the caller
    treats as "use web search instead" rather than as a failure.
    """
    provider = settings.weather_provider.strip().lower()
    if not weather_provider_configured():
        raise DirectAnswerError(
            "No weather provider is configured, so this needs a web search."
        )

    if provider in {"wttr.in", "wttr"}:
        # Precision order matters. wttr.in resolves a bare country to an
        # arbitrary town inside it ("India" landed on Tamia), so coordinates are
        # preferred whenever the OS gave us any - "21.15,79.09" is exact where a
        # country name is a guess. A place named in the question still wins,
        # since the user asked about somewhere specific.
        place: str | None = None
        label = ""
        if place_hint:
            place, label = place_hint, place_hint
        elif location is not None and location.has_coordinates:
            place = f"{location.latitude:.4f},{location.longitude:.4f}"
            label = location.label
        elif location is not None and location.city:
            place, label = location.city, location.label

        if not place:
            raise DirectAnswerError(
                "I do not know which place to check. Name a city, or set your "
                "location in Manage models."
            )
        return await _weather_wttr(place, label)

def time_and_date(location: usercontext.UserLocation | None) -> DirectAnswer:
    """The current date and time from the machine's clock. Never networked."""
    clock = usercontext.local_time_context()
    lines = [
        f"Current local date and time: {clock['date']}, {clock['time']}.",
        f"Timezone: {clock['timezone']} ({clock['offset']}).",
        f"ISO 8601: {clock['iso']}.",
    ]
    if location and location.city:
        lines.append(f"The user is in {location.label}.")
    return DirectAnswer(
        content="\n".join(lines),
        source_label="This computer's clock",
    )


SYSTEM_PROMPT = """You have been given exact, live data from a dedicated source, shown below.

Rules:
- These figures were measured moments ago. State them directly as fact.
- Never say you cannot access live data or real-time information - it is right here.
- Include the specific numbers and the place they apply to, so the user can see the answer is about them.
- Answer in the units given. Do not convert unless the user asks.
- Do not pad the answer with caveats about your training data; this data does not come from your training data."""
