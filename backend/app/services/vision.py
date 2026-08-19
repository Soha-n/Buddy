"""Generate a durable text description of an image.

This is what makes "send an image to a vision model, then switch to a text-only
model" work. The image bytes themselves are useless to a text model, so at the
moment a vision model is available a second, separate generation describes the
image in detail and that description is stored as plain text on the attachment
row. Every later turn - under any model - reads it as ordinary context.

It is deliberately a distinct call from the user's own question. Reusing the
answer to "what's the total in this invoice?" as the description would record
only the total, and a later question about the vendor's address would find
nothing. Describing the image on its own terms captures what the user did not
think to ask about yet.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Long read timeout: this is a full generation on a vision model, which on a
# CPU-only machine is slow. It runs in the background, so waiting is fine -
# hanging forever is not.
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=10.0)

# Asks for the things a later text-only turn is most likely to be asked about:
# visible text verbatim, numbers, and structure. "Describe this image" alone
# tends to produce two sentences of scene-setting that answer nothing.
DESCRIBE_PROMPT = """Describe this image in thorough detail so that someone who cannot see it could answer questions about it.

Include:
- Every piece of visible text, transcribed exactly, including labels, headings and captions.
- All numbers, values, dates and units you can read.
- If it is a chart or graph: the chart type, axis labels, the series shown, and the approximate value of each data point.
- If it is a table, form or document: its structure and the contents of each field.
- If it is a photo or diagram: the objects present, their arrangement, and any notable detail.

Write plain prose and lists. Do not add interpretation or advice."""

# Descriptions are injected into every subsequent prompt, so an unbounded one
# would slowly eat the context window of the small models this app targets.
MAX_DESCRIPTION_CHARS = 4_000


class VisionError(Exception):
    """Description generation failed."""


async def describe_image(model: str, image_base64: str) -> str:
    """Ask a vision model to describe one image. Returns the description text.

    Uses /api/generate rather than /api/chat: there is no conversation involved,
    just one prompt and one image, and generate's flat response is simpler to
    consume than a chat frame.
    """
    payload = {
        "model": model,
        "prompt": DESCRIBE_PROMPT,
        "images": [image_base64],
        "stream": False,
        # Low temperature: this is transcription, not creative writing, and a
        # hallucinated number here would silently poison every later answer.
        "options": {"temperature": 0.1},
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{settings.ollama_base}/api/generate", json=payload
            )
    except httpx.ConnectError as exc:
        raise VisionError("Ollama is not running.") from exc
    except httpx.HTTPError as exc:
        raise VisionError(f"Could not describe the image: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text.strip()
        try:
            detail = response.json().get("error", detail)
        except ValueError:
            pass
        raise VisionError(detail or f"HTTP {response.status_code}")

    try:
        description = (response.json().get("response") or "").strip()
    except ValueError as exc:
        raise VisionError("Ollama returned an unreadable response.") from exc

    if not description:
        raise VisionError("The model returned an empty description.")
    return description[:MAX_DESCRIPTION_CHARS]
