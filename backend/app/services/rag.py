"""Ingestion and retrieval: files in, prompt context out.

Search is SQLite FTS5 with BM25 ranking. No embedding model, no vector store, no
extra download - FTS5 ships inside SQLite, so indexing a document is a plain
INSERT and searching it is one query. The tradeoff is honest: this matches words,
not meaning, so a question phrased entirely differently from the document ("what
do I get for my desk?" vs "chair stipend") can miss. Two things blunt that:

- Every term is OR-ed rather than AND-ed, so partial overlap still ranks.
- A query that matches nothing falls back to the document's opening chunks,
  because showing the model the document beats showing it nothing.

Two kinds of content bypass search entirely and are always injected:

- Tabular schemas. A spreadsheet's column names are needed to answer almost
  anything about it, and to write code against it.
- Image descriptions. They are the only trace of an image that a text-only model
  can read at all.

Leaving either to a keyword match would make the model intermittently blind to
data it was explicitly given.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.models import attachment_store as store
from app.models.schemas import RetrievedChunk
from app.services.extraction import ExtractionError, extract

logger = logging.getLogger(__name__)

# How many chunks reach the prompt. Five ~300-token chunks is roughly 1.5k
# tokens, which still leaves room for the conversation on small local models.
TOP_K = 5

# Per-chunk truncation in the prompt, so one enormous chunk cannot crowd out the
# other four.
MAX_CHUNK_CHARS_IN_PROMPT = 1_600

# Words carrying no retrieval signal. Dropped so BM25 ranks on the terms that
# actually distinguish one passage from another - without this, "what is the"
# matches every chunk in the document about equally.
_STOPWORDS = frozenset(
    """a an and are as at be by can could do does for from has have how i in is it
    its me my of on or please should show tell that the their there these this to
    was were what when where which who why will with would you your""".split()
)

# FTS5 treats punctuation as query syntax. It is stripped rather than escaped,
# because the goal is to search for the user's words - not to let a question
# accidentally become an FTS5 expression.
_PUNCTUATION_RE = re.compile(r"[^\w\s]+")


def build_match_query(question: str) -> str:
    """Turn a natural-language question into a safe FTS5 MATCH expression.

    Terms are OR-ed: a document rarely contains every word of a question, and
    AND would return nothing for most real phrasings. BM25 still ranks a chunk
    matching several terms above one matching a single term, so OR gives up less
    precision than it appears to.
    """
    cleaned = _PUNCTUATION_RE.sub(" ", question.lower())
    terms = [
        term
        for term in cleaned.split()
        # Single characters are noise; pure stopwords match everything.
        if len(term) > 1 and term not in _STOPWORDS
    ]
    if not terms:
        return ""
    # Each term is double-quoted so an FTS5 keyword (AND / OR / NOT / NEAR)
    # appearing in the question is treated as a word to find, not an operator.
    return " OR ".join(f'"{term}"' for term in terms)


async def ingest(attachment_id: str, filename: str, data: bytes) -> None:
    """Extract and store one file's chunks, marking the attachment ready or error.

    Never raises: this runs detached as a background task, where an exception
    would be logged and lost. Failures are written to the attachment row so the
    UI can show them next to the file.
    """
    try:
        extracted = await asyncio.to_thread(extract, data, filename)
    except ExtractionError as exc:
        await asyncio.to_thread(store.mark_error, attachment_id, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - background task, must not vanish
        logger.exception("extraction crashed for %s", filename)
        await asyncio.to_thread(
            store.mark_error, attachment_id, f"Could not read this file: {exc}"
        )
        return

    if extracted.chunks:
        await asyncio.to_thread(
            store.save_chunks,
            attachment_id,
            [
                (index, chunk.content, chunk.locator)
                for index, chunk in enumerate(extracted.chunks)
            ],
        )

    await asyncio.to_thread(
        store.mark_ready, attachment_id, len(extracted.chunks), extracted.data_summary
    )
    logger.info(
        "indexed %s: %d chunks%s",
        filename,
        len(extracted.chunks),
        " (truncated)" if extracted.truncated else "",
    )


async def retrieve(conversation_id: str, question: str) -> list[RetrievedChunk]:
    """Rank this conversation's chunks against the question.

    An empty list is the normal no-attachments case, not an error.
    """
    match_query = build_match_query(question)

    chunks: list[RetrievedChunk] = []
    if match_query:
        try:
            chunks = await asyncio.to_thread(
                store.search_chunks, conversation_id, match_query, TOP_K
            )
        except Exception:  # noqa: BLE001 - a bad MATCH must not fail the turn
            logger.warning("FTS query failed for %r", match_query, exc_info=True)

    if not chunks:
        # No lexical overlap. The document is still what the user is asking
        # about, so send its opening chunks rather than nothing.
        chunks = await asyncio.to_thread(store.list_chunks, conversation_id, TOP_K)

    for chunk in chunks:
        chunk.content = chunk.content[:MAX_CHUNK_CHARS_IN_PROMPT]
    return chunks


def _format_sources(chunks: list[RetrievedChunk]) -> str:
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        origin = chunk.filename
        if chunk.locator:
            origin = f"{origin}, {chunk.locator}"
        blocks.append(f"[{index}] {origin}\n{chunk.content}")
    return "\n\n".join(blocks)


async def build_context(conversation_id: str, question: str) -> str | None:
    """Assemble the full attachment context block for a turn.

    None when this conversation has no usable attachment content, so the caller
    can leave the prompt exactly as it was rather than injecting an empty section
    that would just confuse a small model.
    """
    has_any = await asyncio.to_thread(store.has_ready_attachments, conversation_id)
    if not has_any:
        return None

    summaries = await asyncio.to_thread(store.list_data_summaries, conversation_id)
    descriptions = await asyncio.to_thread(store.list_descriptions, conversation_id)
    chunks = await retrieve(conversation_id, question)

    if not summaries and not descriptions and not chunks:
        return None

    sections: list[str] = []

    if summaries:
        rendered = "\n\n".join(
            f"File: {filename}\n{summary}" for filename, summary in summaries
        )
        sections.append(
            "DATA FILES AVAILABLE IN THIS CHAT\n"
            "Use these exact column names in any code you write.\n\n" + rendered
        )

    if descriptions:
        rendered = "\n\n".join(
            f"Image: {filename}\n{description}"
            for filename, description in descriptions
        )
        sections.append(
            "IMAGES SHARED EARLIER IN THIS CHAT\n"
            "These descriptions were written by a vision model when the image was "
            "sent. Treat them as an accurate account of the image.\n\n" + rendered
        )

    if chunks:
        sections.append(
            "EXCERPTS FROM THE UPLOADED DOCUMENTS\n"
            "These passages were selected as the most relevant to the question. "
            "Cite the bracketed number when you use one.\n\n" + _format_sources(chunks)
        )

    return "\n\n---\n\n".join(sections)


# The instruction that turns retrieved text into grounded answers.
#
# The opening lines are emphatic for a reason: small models (3B and under) treat
# an uploaded document as something they lack access to and answer "I don't have
# that data" even with the text sitting in their context. Stating plainly that
# the material below IS the user's file, and that refusing is wrong when the
# answer is present, is what fixes that. The escape hatch stays - but it comes
# after the instruction to use what is there, not before it.
SYSTEM_PROMPT = """The user has uploaded files to this chat. Their contents are provided to you below, in the sections marked DATA FILES, IMAGES and EXCERPTS.

That material IS the user's data. You have full access to it. Read it and answer from it directly.

Rules:
- Answer using the provided material. Never say you lack access to the data, cannot see the file, or need the user to paste it - it is already here.
- Values in a data summary (counts, sums, min/max, previews) are real figures from the user's file. Quote them as fact.
- Cite excerpts by their bracketed number, like [1].
- Only if the material genuinely does not contain the answer, say which part is missing.
- When the user asks for a chart, graph or plot, reply with a single ```python code block using pandas and matplotlib. Read data files by their exact filename (for example pd.read_csv("sales.csv")) and use the exact column names shown in the data summary. Do not call plt.show(); the figure is captured automatically.
- Chart code may only import pandas, numpy, matplotlib and standard analysis libraries."""
