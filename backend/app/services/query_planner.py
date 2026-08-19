"""Decide what a question actually needs before spending time on it.

Every question got the same treatment before this: six results, three pages
fetched, several seconds. That is right for "compare the latest flagship phones"
and absurd for "what time is it" - which needs no search at all - or "who wrote
Dune", where the search snippet already contains the answer.

So a question is classified into an *intent*, and the intent decides the work:

    DIRECT      -> a purpose-built API answers it exactly. No search, no pages.
                   Weather, time, date. Scraping a weather page to learn the
                   temperature is strictly worse than asking a weather API.
    LOOKUP      -> snippets only, no page fetches. A fact with one short answer:
                   a price, a score, a definition, who someone is.
    RESEARCH    -> snippets plus several fetched pages. Comparisons, "how do I",
                   analysis, anything where a one-line snippet cannot carry the
                   answer.
    NONE        -> no external data needed at all. Maths, code, writing,
                   reasoning about text the user already provided.

Classification is rule-based rather than a model call. A model classifier would
add two to four seconds to every message - to a feature whose entire purpose is
spending less time - and small local models classify inconsistently. Rules are
instant, deterministic, and inspectable when they get it wrong.

Ordering matters more than the individual rules: DIRECT is checked before
everything because "what's the temperature" must never fall through to a generic
search, and NONE is checked early so "17 x 23" never reaches the network.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    NONE = "none"
    DIRECT = "direct"
    LOOKUP = "lookup"
    RESEARCH = "research"


class DirectKind(str, Enum):
    WEATHER = "weather"
    TIME = "time"


@dataclass
class QueryPlan:
    intent: Intent
    #: Set only for DIRECT: which purpose-built provider answers this.
    direct_kind: DirectKind | None = None
    #: The place named in the question, if any. A geocoder needs "Tokyo", not
    #: "the weather in Tokyo" - the latter resolves to nothing.
    place: str | None = None
    #: Search terms, cleaned of conversational padding and with the user's
    #: location or the current year folded in where that sharpens the query.
    search_query: str = ""
    results_wanted: int = 0
    pages_to_fetch: int = 0
    #: True when the question referred to "here" without naming a place.
    needs_location: bool = False
    #: Why this plan was chosen, for logging and for the UI's "why" tooltip.
    reason: str = ""
    matched: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #

# Weather has its own API, so it is matched narrowly and confidently.
_WEATHER_RE = re.compile(
    r"\b(weather|temperature|temp|forecast|rains?|raining|rainfall|humidity|"
    r"snows?|snowing|snowfall|sunny|cloudy|wind speed|how (hot|cold|warm)|"
    r"climate today|need an umbrella)\b",
    re.IGNORECASE,
)

_TIME_RE = re.compile(
    r"\b(what('s| is) the (time|date)|current time|time (is it|now)|"
    r"what day is it|today'?s date|what year is it)\b",
    re.IGNORECASE,
)

# "Here" without a named place. If one of these matches and no place name is
# present, the question needs the user's location resolved.
_HERE_RE = re.compile(
    r"\b(here|my area|my city|my location|nearby|near me|around me|local|outside|"
    r"where i (am|live))\b",
    re.IGNORECASE,
)

# Explicitly naming a place means the user's own location is not needed. Covers
# "in Tokyo", "for London", "at Delhi" - capitalisation is the signal, so this
# also requires the _NOT_PLACES filter below to reject "in January".
_IN_PLACE_RE = re.compile(
    r"\b(?:in|for|at|around|near)\s+([A-Z][a-zA-Z.\-]+(?:\s+[A-Z][a-zA-Z.\-]+){0,2})"
)

# Time-sensitivity. Presence pushes toward searching; absence pushes away.
_CURRENT_RE = re.compile(
    r"\b(current(ly)?|now|today|tonight|tomorrow|yesterday|this (week|month|year|morning|"
    r"evening)|latest|newest|recent(ly)?|live|right now|so far|up[- ]to[- ]date|"
    r"as of|breaking|news|price|stock|score|released?|launch(ed|ing)?|announce(d|ment)?|"
    r"trending|available now)\b",
    re.IGNORECASE,
)

# Depth signals: these questions cannot be answered by a one-line snippet.
_RESEARCH_RE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference between|which is better|pros and cons|"
    r"advantages?|disadvantages?|review|in depth|detailed|explain why|analy[sz]e|analysis|"
    r"how (do|does|to|can) i|step[- ]by[- ]step|guide|tutorial|"
    # "best laptop for X", "best phone under Y" - a recommendation question,
    # which needs real pages rather than a snippet.
    r"best\s+\w+(\s+\w+)?\s+(for|to|under|in|of)\b|best (way|practice|option)|"
    r"which .{0,30}(should|would) (i|you)|worth (it|buying)|"
    r"recommend(ations?)?|options for|alternatives? to|summar(y|ise|ize) the)\b",
    re.IGNORECASE,
)

# A short factual question. "who is X", "when did Y", "how much is Z".
_LOOKUP_RE = re.compile(
    r"^\s*(who|what|when|where|which|how (much|many|old|far|long|tall))\b",
    re.IGNORECASE,
)

# Questions answerable from the model's own reasoning, with no external data.
# Checked early so arithmetic and code never touch the network.
# Arithmetic, kept in its own pattern with no trailing \b. A closing \b would
# have to match immediately after the final digit group - inside "23" - where
# there is no word boundary, so folding these into the \b-anchored pattern below
# silently prevents them from ever matching.
_ARITHMETIC_RE = re.compile(
    r"\d+\s*(times|plus|minus|divided\s+by|multiplied\s+by|[x\+\-\*/÷])\s*\d",
    re.IGNORECASE,
)

# Keyword tasks the model can do unaided. Safe to anchor with \b on both sides
# because every alternative here begins and ends on a word character.
_SELF_CONTAINED_RE = re.compile(
    r"\b(calculate|compute|square root|percent of|solve for|derivative|integral|"
    r"write (me )?(a|an|some)? ?(function|code|script|program|poem|story|essay|email|"
    r"letter|summary)|refactor|debug|fix this|rename|translate|rewrite|proofread|"
    r"correct (my|the) (grammar|spelling)|explain (this|the following) code)\b",
    re.IGNORECASE,
)

# Padding stripped from a query. Search engines match on content words; leaving
# "can you please tell me" in the query dilutes every real term.
_PADDING_RE = re.compile(
    r"^\s*(can you|could you|would you|please|hey|hi|hello|ok(ay)?|so|um|well|just|"
    r"i want to know|i'?d like to know|tell me|do you know|let me know|"
    r"i was wondering|any idea|quick question|what'?s)[,:\s]+",
    re.IGNORECASE,
)
_TRAILING_POLITE_RE = re.compile(r"[\s,]*(please|thanks|thank you)\s*[?.!]*\s*$", re.IGNORECASE)


def clean_query(text: str) -> str:
    """Strip conversational padding, keeping the substantive question."""
    cleaned = text.strip()
    # Looped because real messages stack these: "hey, can you please tell me the
    # price of gold" needs three passes before "the price of gold" is left. One
    # pass strips only the outermost phrase.
    for _ in range(5):
        stripped = _PADDING_RE.sub("", cleaned).lstrip(" ,:")
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = _TRAILING_POLITE_RE.sub("", cleaned)
    return cleaned.strip(" ?.!,") or text.strip()


# Words that follow "in" without being places, so "in January" and "in celsius"
# do not get sent to a geocoder.
_NOT_PLACES = frozenset(
    """january february march april may june july august september october november
    december monday tuesday wednesday thursday friday saturday sunday celsius
    fahrenheit kelvin metric imperial detail depth summary general short brief""".split()
)


def extract_place(text: str) -> str | None:
    """Pull just the place name out of a question.

    A geocoder needs "Tokyo"; handed "the weather in Tokyo" it returns nothing.
    Capitalisation after "in"/"at"/"for" is the signal, which is imperfect but
    cheap - and a wrong guess costs only a failed geocode that falls back to the
    user's own location.
    """
    for match in _IN_PLACE_RE.finditer(text):
        candidate = match.group(1).strip(" .,?!")
        if candidate.lower() in _NOT_PLACES:
            continue
        # Trailing filler that capitalisation alone would have swept in.
        words = [w for w in candidate.split() if w.lower() not in _NOT_PLACES]
        if words:
            return " ".join(words)
    return None


def _mentions_named_place(text: str) -> bool:
    """Whether a specific place appears, so "here" resolution can be skipped."""
    return extract_place(text) is not None


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

# Depth budgets per intent. LOOKUP fetches nothing: the snippet is the answer,
# and fetching pages for "what's the score" adds seconds for no information.
_BUDGETS = {
    Intent.LOOKUP: (5, 0),
    Intent.RESEARCH: (6, 3),
}


def plan(question: str, web_enabled: bool, has_attachments: bool = False) -> QueryPlan:
    """Classify a question and decide how much work it justifies.

    web_enabled reflects the user's toggle. With it off, no plan can involve the
    network - but the classification still runs, because the caller needs to know
    whether the question *would* have needed live data in order to say so.
    """
    text = question.strip()
    if not text:
        return QueryPlan(intent=Intent.NONE, reason="empty question")

    matched: list[str] = []

    # 1. Time and date. Answered from the OS clock - never a search, and correct
    #    even with the web toggle off.
    if _TIME_RE.search(text):
        matched.append("time phrase")
        return QueryPlan(
            intent=Intent.DIRECT,
            direct_kind=DirectKind.TIME,
            reason="asks for the current time or date, answered from the system clock",
            matched=matched,
        )

    # 2. Weather. A dedicated API returns exact figures; a scraped page returns
    #    whatever a marketing div happened to contain.
    if _WEATHER_RE.search(text):
        matched.append("weather phrase")
        place = extract_place(text)
        return QueryPlan(
            intent=Intent.DIRECT,
            direct_kind=DirectKind.WEATHER,
            search_query=clean_query(text),
            place=place,
            needs_location=place is None,
            reason="weather question, answered from a weather API"
            + (f" for {place}" if place else " for the user's location"),
            matched=matched,
        )

    # 3. Self-contained work. Checked before any search signal so "calculate
    #    17 x 23" and "write me a function" never reach the network.
    if _ARITHMETIC_RE.search(text) or _SELF_CONTAINED_RE.search(text):
        matched.append("self-contained task")
        return QueryPlan(
            intent=Intent.NONE,
            reason="a task the model can do without external data",
            matched=matched,
        )

    is_current = bool(_CURRENT_RE.search(text))
    is_research = bool(_RESEARCH_RE.search(text))
    is_lookup = bool(_LOOKUP_RE.search(text))
    if is_current:
        matched.append("time-sensitive wording")
    if is_research:
        matched.append("comparative or how-to wording")
    if is_lookup:
        matched.append("short factual question")

    # 4. A question about the user's own attached files, with no current-events
    #    signal, is answered from those files - searching the web for it would
    #    return strangers' documents.
    if has_attachments and not is_current:
        return QueryPlan(
            intent=Intent.NONE,
            reason="answerable from the attached files",
            matched=matched or ["attachment present"],
        )

    # 5. Depth. Research wording wins over lookup wording: "compare the current
    #    prices of X and Y" is both, and the comparison is what sets the work.
    if is_research:
        intent = Intent.RESEARCH
        reason = "needs explanation or comparison, so pages are read in full"
    elif is_current:
        # Time-sensitive but not comparative - a price, a score, a headline.
        intent = Intent.LOOKUP
        reason = "asks for something current with a short answer, so snippets suffice"
    elif is_lookup:
        # A plain factual question with no currency signal ("who wrote Dune").
        # Worth a cheap search when the web is on: snippets settle it, and the
        # model may simply not know.
        intent = Intent.LOOKUP
        reason = "a short factual question, answered from search snippets"
    else:
        return QueryPlan(
            intent=Intent.NONE,
            reason="no live information needed",
            matched=matched,
        )

    if not web_enabled:
        # The classification is still returned so the caller can tell the user
        # this needed the web, but nothing is planned.
        return QueryPlan(
            intent=intent,
            search_query=clean_query(text),
            results_wanted=0,
            pages_to_fetch=0,
            needs_location=bool(_HERE_RE.search(text)) and not _mentions_named_place(text),
            reason=reason + " (web search is off)",
            matched=matched,
        )

    results, pages = _BUDGETS[intent]
    return QueryPlan(
        intent=intent,
        search_query=clean_query(text),
        results_wanted=results,
        pages_to_fetch=pages,
        needs_location=bool(_HERE_RE.search(text)) and not _mentions_named_place(text),
        reason=reason,
        matched=matched,
    )


def localize_query(query: str, location_label: str | None) -> str:
    """Append the user's place to a query that implied it without naming it.

    "restaurants near me" is a useless search string; "restaurants near me
    Nagpur, Maharashtra, India" is a good one.
    """
    if not location_label or location_label == "unknown location":
        return query
    if location_label.split(",")[0].lower() in query.lower():
        return query
    return f"{query} {location_label}"
