"""Model listing, download and deletion."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models.catalog import find_model, load_catalog
from app.models.schemas import (
    CatalogModel,
    InstalledModelsResponse,
    LibrarySearchResponse,
    PreloadRequest,
    PullRequest,
)
from app.services import live_catalog, ollama
from app.sse import MEDIA_TYPE, SSE_HEADERS, format_event, guarded_stream

router = APIRouter(prefix="/api/models", tags=["models"])

# Ignore the very first moments when computing speed, since dividing by a
# near-zero elapsed time produces absurd numbers.
_MIN_ELAPSED_FOR_SPEED = 0.5


@router.get("/installed", response_model=InstalledModelsResponse)
async def installed() -> InstalledModelsResponse:
    """List models already on disk."""
    try:
        models = await ollama.list_installed()
    except ollama.OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return InstalledModelsResponse(models=models)


async def _pull_events(model: str) -> AsyncIterator[str]:
    """Translate Ollama's pull frames into SSE progress events."""
    started = time.monotonic()
    last_completed = 0

    async for frame in ollama.stream_pull(model):
        status = frame.get("status", "")
        # Early frames carry only a status string, so these are optional.
        total = frame.get("total") or 0
        completed = frame.get("completed") or 0

        percent = round(completed / total * 100, 1) if total > 0 else None

        elapsed = time.monotonic() - started
        speed_bps: float | None = None
        eta_s: float | None = None
        if completed > 0 and elapsed > _MIN_ELAPSED_FOR_SPEED:
            speed_bps = completed / elapsed
            if speed_bps > 0 and total > completed:
                eta_s = round((total - completed) / speed_bps, 1)

        if completed >= last_completed:
            last_completed = completed

        yield format_event(
            "progress",
            {
                "status": status,
                "digest": frame.get("digest"),
                "total": total,
                "completed": completed,
                "percent": percent,
                "speed_bps": round(speed_bps, 1) if speed_bps else None,
                "eta_s": eta_s,
            },
        )

        # Ollama signals completion with a final "success" status.
        if status == "success":
            yield format_event("done", {"model": model})
            return

    # Stream ended without an explicit success frame; treat as done so the client
    # is not left waiting forever.
    yield format_event("done", {"model": model})


@router.post("/pull")
async def pull(request: PullRequest) -> StreamingResponse:
    """Download a model, streaming progress as SSE."""
    model = request.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    return StreamingResponse(
        guarded_stream(_pull_events(model), f"pull:{model}"),
        media_type=MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.post("/preload")
async def preload(request: PreloadRequest) -> dict:
    """Load a model into memory ahead of the first message.

    Called when the user switches models in the UI, so the "connecting"
    delay happens right then instead of silently extending the first reply.
    """
    model = request.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    try:
        await ollama.preload_model(model)
    except ollama.OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"model": model, "ready": True}


@router.get("/search", response_model=list[CatalogModel])
async def search_catalog(q: str = Query(default="", max_length=100)) -> list[CatalogModel]:
    """Instant local filter over the curated catalog - no network."""
    needle = q.strip().lower()
    if not needle:
        return load_catalog()
    return [
        model
        for model in load_catalog()
        if needle in model.name.lower()
        or needle in model.family.lower()
        or needle in model.description.lower()
        or any(needle in tag.lower() for tag in model.tags)
    ]


@router.get("/library", response_model=LibrarySearchResponse)
async def search_library(q: str = Query(min_length=1, max_length=100)) -> LibrarySearchResponse:
    """Live search against ollama.com for models beyond the curated catalog."""
    entries, source, stale = await live_catalog.search_library(q)
    return LibrarySearchResponse(query=q, entries=entries, source=source, stale=stale)


@router.get("/catalog/{name:path}")
async def catalog_entry(name: str) -> dict:
    """Look up catalog metadata for a tag, for the chat header."""
    model = find_model(name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"{name} is not in the catalog")
    return model.model_dump()


@router.delete("/{name:path}")
async def delete(name: str) -> dict:
    """Delete a local model."""
    try:
        await ollama.delete_model(name)
    except ollama.OllamaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"deleted": True, "model": name}
