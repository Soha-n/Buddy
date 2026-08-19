"""Which models can see images, and which to recommend when one cannot.

Capability comes from Ollama's own /api/show response, which lists a
"capabilities" array containing "vision" for multimodal models. That is
authoritative, unlike guessing from the tag name: "llava", "-vl" and "vision"
substrings catch some models and miss others, and a wrong guess here means
either a silently ignored image or a blocked upload that would have worked.

Results are cached for the process lifetime because a given tag's capabilities
cannot change without the model being re-pulled, and this is consulted on every
keystroke-adjacent UI check.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings
from app.services import ollama

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

# tag -> capability list. Cleared only by a restart, which is also when a
# re-pulled model would be picked up.
_cache: dict[str, list[str]] = {}
_cache_lock = asyncio.Lock()

VISION_CAPABILITY = "vision"


async def _fetch_capabilities(model: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{settings.ollama_base}/api/show", json={"model": model}
            )
            if response.status_code >= 400:
                return []
            payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError):
        logger.debug("could not read capabilities for %s", model, exc_info=True)
        return []

    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        return [str(item) for item in capabilities]

    # Older Ollama builds predate the capabilities array. Fall back to the
    # projector field, which is what actually makes a model multimodal.
    details = payload.get("details") or {}
    if payload.get("projector_info") or details.get("projector_type"):
        return ["completion", VISION_CAPABILITY]
    return ["completion"]


async def get_capabilities(model: str) -> list[str]:
    """Capability list for a tag, cached."""
    if model in _cache:
        return _cache[model]
    async with _cache_lock:
        # Re-check inside the lock: concurrent callers for the same new model
        # would otherwise both issue a request.
        if model in _cache:
            return _cache[model]
        capabilities = await _fetch_capabilities(model)
        _cache[model] = capabilities
        return capabilities


async def supports_vision(model: str) -> bool:
    return VISION_CAPABILITY in await get_capabilities(model)


async def list_vision_models() -> list[str]:
    """Installed models that can accept images.

    Queried concurrently: /api/show is one round trip per model, and doing a
    dozen of them in series is a visible pause in the composer's warning.
    """
    try:
        installed = await ollama.list_installed()
    except ollama.OllamaError:
        return []

    names = [model.name for model in installed]
    results = await asyncio.gather(
        *(supports_vision(name) for name in names), return_exceptions=True
    )
    return [
        name
        for name, capable in zip(names, results)
        if capable is True
    ]


