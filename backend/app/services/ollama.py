"""Async client for the Ollama REST API.

We talk HTTP rather than shelling out to ollama.exe: /api/pull already emits
structured NDJSON with exact byte counts, whereas the CLI prints ANSI progress
bars that would have to be screen-scraped. HTTP also gives clean cancellation -
dropping the connection tells Ollama to stop.

The one thing the CLI is used for is locating the binary, which lets us tell
"Ollama isn't installed" apart from "Ollama is installed but not running".
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.models.schemas import InstalledModel, OllamaStatus

logger = logging.getLogger(__name__)

# Pull requests can idle for a while between progress frames on a slow network,
# so there is no total timeout - only a connect timeout.
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=None, pool=None)
_QUICK_TIMEOUT = httpx.Timeout(5.0)

_KNOWN_BINARY_PATHS = [
    Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    Path(r"C:\Program Files\Ollama\ollama.exe"),
]


class OllamaError(Exception):
    """Ollama returned an error we should surface to the user."""


class OllamaUnavailable(OllamaError):
    """Ollama is not reachable at all."""


def find_binary() -> str | None:
    """Locate ollama.exe on PATH or in its usual install locations."""
    on_path = shutil.which("ollama")
    if on_path:
        return on_path
    for candidate in _KNOWN_BINARY_PATHS:
        if candidate.exists():
            return str(candidate)
    return None


async def get_status() -> OllamaStatus:
    """Distinguish not-installed from installed-but-not-running.

    These need different fixes from the user, so they must not collapse into one
    generic error.
    """
    binary = find_binary()

    try:
        async with httpx.AsyncClient(timeout=_QUICK_TIMEOUT) as client:
            response = await client.get(f"{settings.ollama_base}/api/version")
            response.raise_for_status()
            version = response.json().get("version")
        return OllamaStatus(
            installed=True,
            running=True,
            version=version,
            binary_path=binary,
        )
    except httpx.ConnectError:
        return OllamaStatus(
            installed=binary is not None,
            running=False,
            binary_path=binary,
            error=(
                "Ollama is installed but not responding. Start it by running "
                "'ollama serve' or launching the Ollama app."
                if binary
                else "Ollama was not found. Install it from https://ollama.com/download"
            ),
        )
    except (httpx.HTTPError, ValueError) as exc:
        return OllamaStatus(
            installed=binary is not None,
            running=False,
            binary_path=binary,
            error=f"Could not query Ollama: {exc}",
        )


async def list_installed() -> list[InstalledModel]:
    """List locally available models.

    Ollama also reports cloud-hosted pseudo-models (tagged ':cloud'); those are
    not on disk, so they are filtered out of an "installed" listing.
    """
    try:
        async with httpx.AsyncClient(timeout=_QUICK_TIMEOUT) as client:
            response = await client.get(f"{settings.ollama_base}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except httpx.ConnectError as exc:
        raise OllamaUnavailable("Ollama is not running.") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Failed to list models: {exc}") from exc

    models: list[InstalledModel] = []
    for entry in payload.get("models", []):
        name = entry.get("name", "")
        if not name or name.endswith(":cloud") or entry.get("remote_model"):
            continue
        models.append(
            InstalledModel(
                name=name,
                size_bytes=entry.get("size", 0),
                modified_at=entry.get("modified_at"),
            )
        )
    return models


async def delete_model(name: str) -> None:
    """Remove a local model, e.g. to retry after a corrupted pull."""
    try:
        async with httpx.AsyncClient(timeout=_QUICK_TIMEOUT) as client:
            response = await client.request(
                "DELETE",
                f"{settings.ollama_base}/api/delete",
                json={"model": name},
            )
            response.raise_for_status()
    except httpx.ConnectError as exc:
        raise OllamaUnavailable("Ollama is not running.") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Failed to delete {name}: {exc}") from exc


async def _stream_ndjson(
    method: str, path: str, payload: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Stream an Ollama NDJSON endpoint, yielding one dict per line.

    Ollama reports mid-stream failures inside a 200 response body rather than via
    an HTTP status, so every frame is checked for an "error" key.
    """
    try:
        async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client:
            async with client.stream(
                method, f"{settings.ollama_base}{path}", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    try:
                        detail = json.loads(detail).get("error", detail)
                    except (ValueError, AttributeError):
                        pass
                    raise OllamaError(detail or f"HTTP {response.status_code}")

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        logger.debug("skipping non-JSON stream line: %r", line[:200])
                        continue

                    if isinstance(frame, dict) and frame.get("error"):
                        raise OllamaError(str(frame["error"]))
                    yield frame
    except httpx.ConnectError as exc:
        raise OllamaUnavailable(
            "Lost connection to Ollama. Is it still running?"
        ) from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Stream failed: {exc}") from exc


async def stream_pull(model: str) -> AsyncIterator[dict[str, Any]]:
    """Download a model, yielding raw progress frames.

    Early frames carry only a status string (no byte counts), so callers must
    treat 'total'/'completed' as optional.
    """
    async for frame in _stream_ndjson(
        "POST", "/api/pull", {"model": model, "stream": True}
    ):
        yield frame


async def stream_chat(
    model: str,
    messages: list[dict[str, str]],
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion, yielding raw frames."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload["options"] = options

    async for frame in _stream_ndjson("POST", "/api/chat", payload):
        yield frame


# How long a preloaded model stays resident in memory after being switched to.
# Long enough to cover a normal chat session without re-loading between turns.
_PRELOAD_KEEP_ALIVE = "30m"

# Loading a model (reading it from disk into RAM/VRAM) can take a while for
# larger models, so this gets its own generous timeout rather than the quick
# one used for simple metadata calls.
_PRELOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=10.0)


async def preload_model(model: str) -> None:
    """Load a model into memory ahead of the first message.

    Ollama's own LRU eviction handles unloading whatever was previously
    resident once VRAM/RAM is needed for the new one - there is nothing extra
    to do on this end to "disconnect" the old model.
    """
    try:
        async with httpx.AsyncClient(timeout=_PRELOAD_TIMEOUT) as client:
            response = await client.post(
                f"{settings.ollama_base}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": _PRELOAD_KEEP_ALIVE},
            )
            if response.status_code >= 400:
                detail = response.text.strip()
                try:
                    detail = response.json().get("error", detail)
                except ValueError:
                    pass
                raise OllamaError(detail or f"HTTP {response.status_code}")
    except httpx.ConnectError as exc:
        raise OllamaUnavailable("Ollama is not running.") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Failed to load {model}: {exc}") from exc
