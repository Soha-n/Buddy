"""Web search and page extraction, for questions about the present.

Two providers, tried in order:

1. **SearXNG**, if one is reachable. It is a self-hosted meta-search front end
   with a real JSON API, so results are stable and nothing is scraped. Buddy
   does not install it - it is used when present and skipped when not.
2. **DuckDuckGo's HTML endpoint** otherwise. No API key, no signup, works on a
   bare machine. It is a scrape, so the markup can change under us; that is why
   the parser tolerates missing pieces instead of assuming a shape.

Search alone returns one-line snippets, which are enough for a price and too
thin for anything that needs explaining. So the top few results are then fetched
and reduced to readable text. Each fetch is independently allowed to fail -
plenty of sites refuse an unattended request (Wikipedia answers 403 to a plain
GET, for instance), and one refusal must not take the answer down with it.

Nothing here runs unless the user turns the web toggle on for a message. That is
deliberate: local models do not judge their own knowledge cutoff reliably (in
testing, one refused a question about 2026 rather than searching, while another
searched for "17 x 23"), so the decision is the user's rather than the model's.
"""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Search itself must feel responsive; page fetches are bounded tighter still
# because several run at once and the slowest one sets the floor.
_SEARCH_TIMEOUT = httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0)
_FETCH_TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0)
_PROBE_TIMEOUT = httpx.Timeout(2.0)

# A browser-ish UA. Several search front ends and news sites return an error or
# a stub page to obviously automated clients.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_RESULTS = 6
# How many result pages get fetched in full. Three is the point where extra
# fetches stop adding information and start adding seconds.
PAGES_TO_FETCH = 3
# Per-page text budget. Enough for the substance of an article, small enough
# that three of them plus the conversation still fit a small model's context.
MAX_PAGE_CHARS = 3_000
MAX_SNIPPET_CHARS = 400
# Refuse to read a response body beyond this; a PDF or video served at a result
# URL would otherwise be pulled into memory in full.
MAX_DOWNLOAD_BYTES = 2_000_000


class SearchError(Exception):
    """Search failed in a way worth telling the user about."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    #: Extracted page text, when the page was fetched and readable.
    content: str | None = None


@dataclass
class SearchOutcome:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    #: "searxng" or "duckduckgo", so the UI can say where answers came from.
    provider: str = ""
    fetched_pages: int = 0


# --------------------------------------------------------------------------- #
# Provider detection
# --------------------------------------------------------------------------- #

# Cached because it is consulted on every search and a SearXNG instance does not
# appear or vanish mid-session in practice. None means "not probed yet".
_searxng_available: bool | None = None
_probe_lock = asyncio.Lock()


def searxng_base_url() -> str:
    """The SearXNG to talk to: Buddy's own managed instance, or a configured one."""
    if settings.searxng_managed:
        from app.services import searxng_manager

        return searxng_manager.local_url()
    return settings.searxng_url.rstrip("/")


async def _probe_searxng() -> bool:
    """Whether a SearXNG instance answers JSON at the configured URL.

    Asking for format=json specifically: an instance that serves HTML but has
    the JSON format disabled would pass a naive reachability check and then fail
    every real query.
    """
    base = searxng_base_url()
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.get(
                f"{base}/search",
                params={"q": "test", "format": "json"},
                headers={"User-Agent": _USER_AGENT},
            )
            if response.status_code != 200:
                return False
            payload = response.json()
            return isinstance(payload, dict) and "results" in payload
    except Exception:
        return False


async def searxng_available(
    force_refresh: bool = False, autostart: bool = False
) -> bool:
    """Whether SearXNG will answer, optionally starting Buddy's own instance.

    autostart is what makes search work with no setup: the first query that needs
    the web brings the managed instance up. It is off for plain status checks so
    that opening a settings panel never triggers an install.
    """
    global _searxng_available
    if _searxng_available is not None and not force_refresh:
        return _searxng_available
    async with _probe_lock:
        if _searxng_available is not None and not force_refresh:
            return _searxng_available

        _searxng_available = await _probe_searxng()

        if not _searxng_available and autostart and settings.searxng_managed:
            from app.services import searxng_manager

            ok, err = await searxng_manager.start()
            if ok:
                _searxng_available = True
            else:
                logger.info("managed SearXNG unavailable (%s); using fallback", err)

        logger.info(
            "SearXNG at %s: %s",
            searxng_base_url(),
            "available" if _searxng_available else "not reachable, falling back",
        )
        return _searxng_available


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


async def _search_searxng(query: str) -> list[SearchResult]:
    base = searxng_base_url()
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        response = await client.get(
            f"{base}/search",
            params={"q": query, "format": "json", "safesearch": 1},
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()

    results: list[SearchResult] = []
    for entry in payload.get("results", [])[:MAX_RESULTS]:  # trimmed again by the caller's budget
        url = (entry.get("url") or "").strip()
        title = _clean_text(entry.get("title") or "")
        if not url or not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=_clean_text(entry.get("content") or "")[:MAX_SNIPPET_CHARS],
            )
        )
    return results


# DuckDuckGo's HTML page, parsed with regex rather than an HTML parser: the two
# classes below are the only structure needed, and adding a parser dependency to
# read two attributes is not worth it. Both patterns tolerate absence - a markup
# change degrades to "no results", never to an exception.
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.IGNORECASE | re.DOTALL
)


async def _search_duckduckgo(query: str) -> list[SearchResult]:
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT, follow_redirects=True) as client:
        response = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _USER_AGENT},
        )
        body = response.text

    if _looks_blocked(response.status_code, body):
        # Rate-limited or challenged. Raising lets the caller try another
        # provider instead of reporting "no results found", which would wrongly
        # suggest the query itself was bad.
        raise SearchError("DuckDuckGo is rate-limiting automated requests.")
    response.raise_for_status()

    titles = list(_DDG_RESULT_RE.finditer(body))
    snippets = [_clean_text(m.group("snippet")) for m in _DDG_SNIPPET_RE.finditer(body)]

    results: list[SearchResult] = []
    for index, match in enumerate(titles[:MAX_RESULTS]):
        url = _normalize_ddg_url(html_module.unescape(match.group("url")))
        title = _clean_text(match.group("title"))
        if not url or not title:
            continue
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=(snippets[index] if index < len(snippets) else "")[
                    :MAX_SNIPPET_CHARS
                ],
            )
        )
    return results


async def _search_brave(query: str, count: int) -> list[SearchResult]:
    """Brave Search API. Documented, stable, and its terms permit commercial use.

    The key belongs to the user, so the agreement is between them and Brave -
    which is what makes this shippable in a paid product, unlike scraping.
    """
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(count, 20)},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": settings.search_api_key.strip(),
            },
        )
    if response.status_code in (401, 422):
        raise SearchError(
            "The Brave Search API key was rejected. Check it in settings."
        )
    if response.status_code == 429:
        raise SearchError("The Brave Search API rate limit was reached.")
    response.raise_for_status()

    results: list[SearchResult] = []
    for entry in (response.json().get("web") or {}).get("results", []):
        results.append(
            SearchResult(
                title=_clean_text(entry.get("title") or ""),
                url=entry.get("url") or "",
                snippet=_clean_text(entry.get("description") or "")[:MAX_SNIPPET_CHARS],
            )
        )
    return [r for r in results if r.url and r.title]


async def _search_tavily(query: str, count: int) -> list[SearchResult]:
    """Tavily search API - built for LLM use, returns page content directly."""
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.search_api_key.strip(),
                "query": query,
                "max_results": min(count, 20),
                "search_depth": "basic",
            },
        )
    if response.status_code in (401, 403):
        raise SearchError("The Tavily API key was rejected. Check it in settings.")
    if response.status_code == 429:
        raise SearchError("The Tavily API rate limit was reached.")
    response.raise_for_status()

    results: list[SearchResult] = []
    for entry in response.json().get("results", []):
        # Tavily returns extracted page text, so a fetch step is unnecessary for
        # these results - the content is already here.
        content = (entry.get("content") or "").strip()
        results.append(
            SearchResult(
                title=_clean_text(entry.get("title") or ""),
                url=entry.get("url") or "",
                snippet=_clean_text(content)[:MAX_SNIPPET_CHARS],
                content=content[:MAX_PAGE_CHARS] if len(content) > MAX_SNIPPET_CHARS else None,
            )
        )
    return [r for r in results if r.url and r.title]


# DuckDuckGo answers a burst of automated requests with HTTP 202 (or 429) and an
# anti-bot page instead of results. Detecting that explicitly matters: the body
# is a valid 200-ish HTML document, so without this check it parses to "zero
# results" and looks like a query that genuinely found nothing.
_BLOCK_MARKERS = ("captcha", "unusual traffic", "are you a robot", "challenge-form")


def _looks_blocked(status_code: int, body: str) -> bool:
    if status_code in (202, 429, 403):
        return True
    lowered = body[:4000].lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


# Mojeek runs its own index and does not rate-limit nearly as aggressively as the
# big front ends, which makes it a good second opinion when DuckDuckGo throttles.
_MOJEEK_RESULT_RE = re.compile(
    r'<a[^>]+class="ob"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?<p[^>]*class="s"[^>]*>(?P<snippet>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
# Mojeek's markup has shifted before; this is a looser second attempt.
_MOJEEK_FALLBACK_RE = re.compile(
    r'<h2><a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>.*?)</a></h2>',
    re.IGNORECASE | re.DOTALL,
)


async def _search_mojeek(query: str) -> list[SearchResult]:
    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            "https://www.mojeek.com/search",
            params={"q": query},
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
    if _looks_blocked(response.status_code, response.text):
        raise SearchError("Mojeek declined the request.")

    results: list[SearchResult] = []
    for match in _MOJEEK_RESULT_RE.finditer(response.text):
        results.append(
            SearchResult(
                title=_clean_text(match.group("title")),
                url=html_module.unescape(match.group("url")),
                snippet=_clean_text(match.group("snippet"))[:MAX_SNIPPET_CHARS],
            )
        )
        if len(results) >= MAX_RESULTS:
            break

    if not results:
        for match in _MOJEEK_FALLBACK_RE.finditer(response.text):
            results.append(
                SearchResult(
                    title=_clean_text(match.group("title")),
                    url=html_module.unescape(match.group("url")),
                    snippet="",
                )
            )
            if len(results) >= MAX_RESULTS:
                break
    return results


def _normalize_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo's redirect wrapper into the real destination.

    Results arrive as //duckduckgo.com/l/?uddg=<encoded target>. Left as-is the
    URL is unusable for fetching and meaningless when shown as a citation.
    """
    if "uddg=" in href:
        from urllib.parse import parse_qs, unquote, urlparse

        query = urlparse(href if href.startswith("http") else f"https:{href}").query
        target = parse_qs(query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if href.startswith("//"):
        return f"https:{href}"
    return href


# --------------------------------------------------------------------------- #
# Page fetch + text extraction
# --------------------------------------------------------------------------- #

# Chrome-like removals: these elements are navigation and boilerplate, and
# leaving them in fills the model's context with cookie banners and menus.
_STRIP_BLOCKS_RE = re.compile(
    r"(?is)<(script|style|noscript|nav|footer|header|aside|form|svg|iframe)[^>]*>.*?</\1>"
)
_BLOCK_END_RE = re.compile(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")


def extract_text(html: str) -> str:
    """Reduce an HTML page to readable plain text.

    A hand-rolled reduction rather than a readability library: the goal is only
    to give the model sentences to read, and the failure mode of a slightly
    noisier extraction is far cheaper than another dependency.
    """
    without_blocks = _STRIP_BLOCKS_RE.sub(" ", html)
    # Turn block ends into newlines *before* stripping tags, so paragraphs do
    # not run together into one wall of text.
    with_breaks = _BLOCK_END_RE.sub("\n", without_blocks)
    text = html_module.unescape(_TAG_RE.sub(" ", with_breaks))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _fetch_page(client: httpx.AsyncClient, result: SearchResult) -> None:
    """Fetch one result and attach its text. Failures are swallowed by design."""
    try:
        response = await client.get(result.url, headers={"User-Agent": _USER_AGENT})
        if response.status_code >= 400:
            return
        content_type = response.headers.get("content-type", "")
        # Only HTML is worth extracting; a PDF or image would produce garbage.
        if "html" not in content_type.lower():
            return
        if len(response.content) > MAX_DOWNLOAD_BYTES:
            return
        text = extract_text(response.text)
        if len(text) > 200:
            result.content = text[:MAX_PAGE_CHARS]
    except Exception:
        # Blocked, timed out, TLS error, redirect loop - all equally fine to
        # skip. The snippet still carries the result.
        logger.debug("could not fetch %s", result.url, exc_info=True)


async def search(
    query: str,
    fetch_pages: bool = True,
    max_results: int | None = None,
    pages_to_fetch: int | None = None,
) -> SearchOutcome:
    """Search the web and optionally read the top results.

    max_results and pages_to_fetch come from the query planner, so a one-line
    factual question costs one request while a comparison reads several pages.
    They fall back to the module defaults when not supplied.

    Raises SearchError only when no provider produced anything - a partial
    result set is a success.
    """
    result_budget = max_results if max_results is not None else MAX_RESULTS
    page_budget = pages_to_fetch if pages_to_fetch is not None else PAGES_TO_FETCH
    cleaned = query.strip()
    if not cleaned:
        raise SearchError("The search query was empty.")

    global _searxng_available

    provider = ""
    results: list[SearchResult] = []
    failures: list[str] = []

    # 1. SearXNG. Self-hosted on the user's own machine: no third party involved,
    #    no terms of service, no rate limit, unlimited. The preferred option for
    #    a commercial on-device product.
    if await searxng_available(autostart=True):
        try:
            results = await _search_searxng(cleaned)
            provider = "searxng"
        except Exception as exc:
            logger.warning("SearXNG search failed, falling back: %s", exc)
            failures.append(f"SearXNG: {exc}")
            _searxng_available = False

    # 2. The user's own API key. The agreement is between them and the provider,
    #    so this is safe to ship commercially.
    configured = settings.search_provider.strip().lower()
    if not results and configured and settings.search_api_key.strip():
        provider_fn = {"brave": _search_brave, "tavily": _search_tavily}.get(configured)
        if provider_fn is None:
            failures.append(f"unknown search provider '{configured}'")
        else:
            try:
                results = await provider_fn(cleaned, result_budget)
                if results:
                    provider = configured
            except SearchError as exc:
                failures.append(f"{configured}: {exc}")
            except httpx.HTTPError as exc:
                failures.append(f"{configured}: {exc}")

    # 3. Scraped front ends. Against their terms of service and unfit for a
    #    commercial build, so this only runs when someone has deliberately turned
    #    it on for a personal one.
    if not results and settings.allow_scraping_fallback:
        for name, scraper in (
            ("duckduckgo", _search_duckduckgo),
            ("mojeek", _search_mojeek),
        ):
            try:
                results = await scraper(cleaned)
                if results:
                    provider = name
                    break
                failures.append(f"{name}: no results")
            except SearchError as exc:
                logger.info("%s unavailable: %s", name, exc)
                failures.append(f"{name}: {exc}")
            except httpx.HTTPError as exc:
                logger.info("%s request failed: %s", name, exc)
                failures.append(f"{name}: {exc}")

    if not results and not failures:
        # Should be unreachable: SearXNG autostarts and the scraped fallback is on
        # by default, so at least one provider is always attempted. Kept as a
        # guard for a build where every provider has been deliberately disabled.
        raise SearchError(
            "No search provider is enabled. Turn on the built-in search, or add a "
            "Brave or Tavily API key in settings."
        )

    if not results:
        # Distinguish "nobody would answer us" from "the query found nothing" -
        # they need completely different responses from the user.
        if failures:
            raise SearchError(
                "No search provider would answer right now "
                f"({'; '.join(failures[:3])}). "
                "Free search endpoints rate-limit heavy use; try again shortly, or "
                "run a local SearXNG instance for unthrottled search."
            )
        raise SearchError(f"No results were found for '{cleaned}'.")

    results = results[:result_budget]

    fetched = 0
    if fetch_pages and page_budget > 0:
        targets = results[:page_budget]
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            # Concurrent: three sequential fetches would triple the wait for no
            # benefit, and each one already fails independently.
            await asyncio.gather(*(_fetch_page(client, r) for r in targets))
        fetched = sum(1 for r in targets if r.content)

    return SearchOutcome(
        query=cleaned, results=results, provider=provider, fetched_pages=fetched
    )


def _clean_text(fragment: str) -> str:
    """Strip tags and collapse whitespace in a snippet or title."""
    return re.sub(r"\s+", " ", html_module.unescape(_TAG_RE.sub("", fragment))).strip()


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def build_context(outcome: SearchOutcome) -> str:
    """Render results as a citable context block."""
    blocks: list[str] = []
    for index, result in enumerate(outcome.results, start=1):
        parts = [f"[{index}] {result.title}\n{result.url}"]
        if result.snippet:
            parts.append(result.snippet)
        if result.content:
            parts.append(f"Page content:\n{result.content}")
        blocks.append("\n".join(parts))

    return (
        "LIVE WEB SEARCH RESULTS\n"
        f"Search query: {outcome.query}\n"
        "These were fetched from the internet just now, so they are more current "
        "than your training data. Prefer them over what you remember, and cite "
        "sources by their bracketed number.\n\n" + "\n\n".join(blocks)
    )


SYSTEM_PROMPT = """You have been given live web search results for the user's question, fetched from the internet moments ago.

Rules:
- Treat the search results as current and authoritative. They are newer than your training data, so where they disagree with what you remember, the results are right.
- Never say you cannot access the internet or lack current information - the results below are exactly that.
- Never say a future-dated event "has not happened yet" if the results describe it. Your training cutoff is not the present date.
- Cite sources by their bracketed number, like [1] or [2].
- If the results genuinely do not answer the question, say so and state what they do cover."""


# Shown when the user asks something time-sensitive with the toggle off. The
# model is told to refuse *and how the user can fix it*, rather than either
# inventing an answer or claiming a flat inability.
# Injected when the toggle is off. Ordered deliberately: answer-normally comes
# FIRST and is stated as the default, because a small model given a refusal rule
# up front applies it to everything - in testing, one demanded the toggle for
# "what is 17 times 23". The refusal is framed as a narrow exception with
# concrete examples on both sides.
OFFLINE_SYSTEM_PROMPT = """Answer the user's question normally using your own knowledge. Almost every question should be answered this way, and you must not mention web search, the internet, or any toggle when you do.

Examples you answer normally, without mentioning web search: maths and calculations, writing and editing, code, explanations of concepts, definitions, translation, summarising text the user provided, general knowledge, historical facts, advice.

There is one narrow exception. Buddy's "Web" toggle is currently off, so you cannot fetch live data. If - and only if - the question specifically asks for information that changes by the day and that you therefore cannot know (today's price of something, current weather, today's news, a recent sports result, the latest version of a product, an event dated after your training data), then do not guess and do not present remembered figures as current. In that single case, reply briefly that this needs live information you cannot reach with web search off, and tell the user to turn on the "Web" toggle next to the message box and ask again.

In that case do not suggest the user visit a website, check an exchange, or look it up themselves - Buddy can fetch it for them, so the Web toggle is the only alternative to offer."""
