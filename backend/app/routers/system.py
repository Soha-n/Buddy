"""Health, specs and recommendation endpoints."""

from __future__ import annotations

import asyncio
import sys

from fastapi import APIRouter, Query

from app.config import settings
from app.models.catalog import load_catalog
from app.models.schemas import (
    HealthResponse,
    RecommendationsResponse,
    SystemSpecs,
    TieredRecommendation,
    LocationResponse,
    SetLocationRequest,
    TiersResponse,
    WebSearchStatus,
)
from app.services import ollama, searxng_manager, usercontext, websearch
from app.services.scoring import rank_all, rank_models
from app.services.specs import detect_specs

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report whether Ollama is installed and reachable."""
    status = await ollama.get_status()
    return HealthResponse(
        status="ok" if status.running else "degraded",
        ollama=status,
    )


def _location_response(location: usercontext.UserLocation) -> LocationResponse:
    clock = usercontext.local_time_context()
    return LocationResponse(
        city=location.city,
        region=location.region,
        country=location.country,
        timezone=location.timezone or clock["timezone"],
        label=location.label,
        source=location.source,  # type: ignore[arg-type]
        local_date=clock["date"],
        local_time=clock["time"],
        # Coordinates come from a Windows-only API, so offering "use precise
        # location" anywhere else would be a button that cannot work.
        precise_available=sys.platform == "win32",
    )


@router.get("/context/location", response_model=LocationResponse)
async def get_location(
    detect: bool = Query(default=False),
    precise: bool = Query(default=False),
) -> LocationResponse:
    """The location Buddy would use for "here" questions.

    Both flags default to false so opening a settings panel neither probes the OS
    nor triggers a permission prompt. `precise` asks the Windows Location Service
    for exact coordinates, which surfaces an OS consent dialog - so it only ever
    happens when the user clicks for it.
    """
    location = usercontext.cached_location()
    if detect or precise:
        location = await usercontext.get_location(
            force_refresh=precise, allow_precise=precise
        )
    return _location_response(location or usercontext.UserLocation(source="unavailable"))


@router.post("/context/location", response_model=LocationResponse)
async def set_location(request: SetLocationRequest) -> LocationResponse:
    """Override the detected location, for when the OS guess is wrong."""
    # No geocoding step: the weather provider takes a place name directly, so a
    # typed city is usable as-is. That also removes the round trip this endpoint
    # used to make before it could answer.
    location = usercontext.set_manual_location(
        request.city, request.region, request.country
    )
    return _location_response(location)


@router.delete("/context/location", response_model=LocationResponse)
async def clear_location() -> LocationResponse:
    """Drop a manual override and fall back to the OS-reported location."""
    usercontext.clear_manual_location()
    location = await usercontext.get_location(force_refresh=True)
    return _location_response(location)


@router.get("/websearch/status", response_model=WebSearchStatus)
async def websearch_status(refresh: bool = Query(default=False)) -> WebSearchStatus:
    """Which provider serves web search, and whether it is ready right now.

    Never reports search as unavailable: Buddy runs its own SearXNG and keeps a
    fallback behind it, so the honest answer is always "yes, via X" or "yes, but
    the good one is still starting".
    """
    if await websearch.searxng_available(force_refresh=refresh):
        return WebSearchStatus(
            available=True,
            provider="searxng",
            searxng_detected=True,
            detail="Using Buddy's built-in private search. Unlimited, nothing shared.",
        )

    configured = settings.search_provider.strip().lower()
    if configured and settings.search_api_key.strip():
        return WebSearchStatus(
            available=True,
            provider=configured,
            searxng_detected=False,
            detail=f"Using your {configured.title()} API key.",
        )

    # SearXNG is not up yet. Say which stage it is at, because "installing" is a
    # wait and "failed" is not.
    manager = searxng_manager.state()
    if settings.searxng_managed and (manager.installing or manager.starting):
        stage = "installing" if manager.installing else "starting"
        return WebSearchStatus(
            available=settings.allow_scraping_fallback,
            provider="duckduckgo" if settings.allow_scraping_fallback else "none",
            searxng_detected=False,
            detail=(
                f"Built-in private search is {stage} (first run only). "
                "Using public search until it is ready."
            ),
        )

    if settings.allow_scraping_fallback:
        return WebSearchStatus(
            available=True,
            provider="duckduckgo",
            searxng_detected=False,
            detail=(
                "Using public search. Buddy's built-in private search will take "
                "over once it is ready."
                if settings.searxng_managed
                else "Using public search."
            ),
        )

    return WebSearchStatus(
        available=False,
        provider="none",
        searxng_detected=False,
        detail=(
            manager.error
            or "No search provider is enabled. Add a Brave or Tavily API key, or "
            "re-enable the built-in search."
        ),
    )


@router.post("/websearch/start")
async def websearch_start() -> dict:
    """Install and start the built-in search on demand.

    Exposed so the UI can offer a retry after a failed first attempt without the
    user restarting Buddy.
    """
    ok, err = await searxng_manager.start()
    return {"ready": ok, "error": err or None, "url": searxng_manager.local_url()}


@router.get("/specs", response_model=SystemSpecs)
async def specs(refresh: bool = Query(default=False)) -> SystemSpecs:
    """Detect hardware. Detection shells out, so it runs off the event loop."""
    return await asyncio.to_thread(detect_specs, refresh)


@router.get("/recommendations", response_model=RecommendationsResponse)
async def recommendations(
    limit: int = Query(default=3, ge=1, le=16),
    refresh: bool = Query(default=False),
) -> RecommendationsResponse:
    """Rank the catalog against this machine and return the best matches."""
    system_specs = await asyncio.to_thread(detect_specs, refresh)
    picks, excluded = rank_models(system_specs, limit=limit)
    return RecommendationsResponse(
        specs=system_specs,
        recommendations=picks,
        excluded=excluded,
        catalog_size=len(load_catalog()),
    )


@router.get("/tiers", response_model=TiersResponse)
async def tiers(refresh: bool = Query(default=False)) -> TiersResponse:
    """Bucket the whole catalog into Best / Better / Good for this machine."""
    system_specs = await asyncio.to_thread(detect_specs, refresh)
    buckets, excluded = await asyncio.to_thread(rank_all, system_specs)

    def _tagged(recs: list, tier: str) -> list[TieredRecommendation]:
        return [TieredRecommendation(**rec.model_dump(), tier=tier) for rec in recs]

    return TiersResponse(
        specs=system_specs,
        best=_tagged(buckets["best"], "best"),
        better=_tagged(buckets["better"], "better"),
        good=_tagged(buckets["good"], "good"),
        excluded=excluded,
        catalog_size=len(load_catalog()),
    )
