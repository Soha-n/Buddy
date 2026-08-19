"""Server-Sent Events helpers.

Named event types (progress / token / done / error) let one endpoint carry
heterogeneous frames without inventing a discriminator field, and the wire
format stays readable in DevTools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Without these, a proxy (including Vite's dev proxy) may buffer the stream so
# progress appears frozen and then arrives all at once.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

MEDIA_TYPE = "text/event-stream"


def format_event(event: str, data: Any) -> str:
    """Encode one SSE frame.

    JSON is dumped without newlines so a payload can never break framing.
    """
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def guarded_stream(
    source: AsyncIterator[str], context: str
) -> AsyncIterator[str]:
    """Convert an unhandled generator exception into a terminal error event.

    Once headers are sent an exception cannot become an HTTP error status, so it
    has to reach the client as a normal SSE frame instead of a silent hang.
    """
    try:
        async for chunk in source:
            yield chunk
    except Exception as exc:  # noqa: BLE001 - last line of defence for the stream
        logger.exception("%s stream failed", context)
        yield format_event("error", {"message": str(exc)})
