"""Health, specs and recommendation endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from app.config import settings
from app.models.catalog import load_catalog
from app.models.schemas import (
    HealthResponse,
    RecommendationsResponse,
    SystemSpecs,
    TieredRecommendation,
    TiersResponse,
    WebSearchStatus,
)
from app.services import ollama, websearch
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


@router.get("/websearch/status", response_model=WebSearchStatus)
async def websearch_status(refresh: bool = Query(default=False)) -> WebSearchStatus:
    """Which search provider the web toggle would use, and whether it works.

    Lets the UI explain a toggle that cannot help - "no internet connection" is a
    very different problem from "search is off", and a toggle that silently does
    nothing is worse than one that says why.
    """
    searxng = await websearch.searxng_available(force_refresh=refresh)
    if searxng:
        return WebSearchStatus(
            available=True,
            provider="searxng",
            searxng_detected=True,
            detail=f"Using your SearXNG instance at {settings.searxng_url}.",
        )

    reachable = await websearch.internet_reachable()
    if reachable:
        return WebSearchStatus(
            available=True,
            provider="duckduckgo",
            searxng_detected=False,
            detail="Using DuckDuckGo. Run SearXNG locally for private search.",
        )

    return WebSearchStatus(
        available=False,
        provider="none",
        searxng_detected=False,
        detail="No internet connection was detected, so web search cannot run.",
    )


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
