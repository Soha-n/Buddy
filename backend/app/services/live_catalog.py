"""Live search against ollama.com's model library.

There is no official JSON API for this (confirmed against open, long-standing
feature requests in the Ollama repo), so this scrapes the server-rendered HTML
search page with a stdlib parser. Treated as inherently fragile: a hard
timeout, an in-process TTL cache, and a fallback ladder (fresh cache -> stale
cache -> local catalog only) mean a markup change degrades the "All" tab
gracefully instead of breaking it.

Anything found here is a plain (name, size hint, description) tuple, never fed
into scoring - RAM/VRAM/quality_tier aren't recoverable from a scrape reliably
enough to trust for a fit calculation.
"""

from __future__ import annotations

import logging
import time
from html.parser import HTMLParser

import httpx

from app.config import settings
from app.models.schemas import LibraryEntry

logger = logging.getLogger(__name__)

_LIBRARY_SEARCH_URL = "https://ollama.com/search"

_cache: dict[str, tuple[float, list[LibraryEntry]]] = {}
_last_known_good: list[LibraryEntry] | None = None
_last_known_good_at: float = 0.0


class _LibrarySearchParser(HTMLParser):
    """Extracts model name/description from ollama.com's search results markup.

    Deliberately narrow rather than trying to fully understand the page layout:
    each result card is an <a href="/library/{name}"> wrapping an <h2><span> for
    the name and exactly one <p class="...break-words..."> for the description,
    followed by a run of <span> size/capability tags (e.g. "8b", "tools") this
    parser ignores. If ollama.com's markup changes enough that none of this
    matches, parse() returns an empty list and the caller treats that as a
    structural failure (see _parse_library_html's None-vs-empty contract).
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[LibraryEntry] = []
        self._current_name: str | None = None
        self._in_target_anchor = False
        self._in_description_p = False
        self._description_parts: list[str] = []
        self._description_captured = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "a":
            href = attr_map.get("href")
            if href and href.startswith("/library/") and href.count("/") == 2:
                self._current_name = href.removeprefix("/library/")
                self._in_target_anchor = True
                self._description_parts = []
                self._description_captured = False
            return

        if (
            tag == "p"
            and self._in_target_anchor
            and not self._description_captured
            and "break-words" in (attr_map.get("class") or "")
        ):
            self._in_description_p = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_description_p:
            self._in_description_p = False
            self._description_captured = True
        elif tag == "a" and self._in_target_anchor and self._current_name:
            description = " ".join("".join(self._description_parts).split())
            self._entries.append(
                LibraryEntry(name=self._current_name, description=description or None)
            )
            self._in_target_anchor = False
            self._current_name = None

    def handle_data(self, data: str) -> None:
        if self._in_description_p:
            self._description_parts.append(data)

    def entries(self) -> list[LibraryEntry]:
        # De-duplicate while preserving order; the anchor can appear more than
        # once per card (name link + wrapping card link).
        seen: set[str] = set()
        unique: list[LibraryEntry] = []
        for entry in self._entries:
            if entry.name in seen:
                continue
            seen.add(entry.name)
            unique.append(entry)
        return unique


def _parse_library_html(html: str) -> list[LibraryEntry] | None:
    """Return parsed entries, or None if the markup didn't match at all.

    None vs [] matters: None means "couldn't parse this, markup likely
    changed" (triggers the stale-cache/catalog-only fallback); [] means "parsed
    fine, genuinely no results for this query."
    """
    parser = _LibrarySearchParser()
    try:
        parser.feed(html)
    except Exception:
        logger.exception("failed to parse ollama.com library HTML")
        return None

    entries = parser.entries()
    # A totally empty parse on a non-trivial page is the concrete signal that
    # the markup shape changed rather than that there were no matches.
    if not entries and len(html) > 2000:
        return None
    return entries


async def _fetch_live(query: str) -> list[LibraryEntry] | None:
    timeout = httpx.Timeout(
        connect=2.0, read=settings.live_catalog_timeout_s, write=5.0, pool=5.0
    )
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(_LIBRARY_SEARCH_URL, params={"q": query})
            if response.status_code != 200:
                logger.warning("ollama.com library search returned %s", response.status_code)
                return None
            return _parse_library_html(response.text)
    except httpx.HTTPError as exc:
        logger.warning("ollama.com library search failed: %s", exc)
        return None


async def search_library(query: str) -> tuple[list[LibraryEntry], str, bool]:
    """Search ollama.com's library, returning (entries, source, stale).

    source is one of "live", "stale_cache", "catalog_only" (the last meaning
    the caller should fall back to the local catalog search entirely).
    """
    global _last_known_good, _last_known_good_at

    normalized = query.strip().lower()
    now = time.monotonic()

    cached = _cache.get(normalized)
    if cached and now - cached[0] < settings.live_catalog_cache_ttl_s:
        return cached[1], "live", False

    entries = await _fetch_live(normalized)

    if entries is not None:
        _cache[normalized] = (now, entries)
        if entries:
            _last_known_good = entries
            _last_known_good_at = now
        return entries, "live", False

    if _last_known_good is not None:
        return _last_known_good, "stale_cache", True

    return [], "catalog_only", False
