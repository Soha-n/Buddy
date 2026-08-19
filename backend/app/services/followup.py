"""Resolve follow-up questions against what was already said.

"What's the weather in Tokyo?" ... "what about tomorrow?"

The second message is meaningless on its own. Sent to a search engine verbatim it
returns nothing useful, and classified on its own it looks like idle chat rather
than a weather question. So a short message that leans on the conversation is
rewritten into a standalone one before planning and searching: "what about
tomorrow" becomes "weather in Tokyo tomorrow".

The rewrite is lexical, not model-driven. A model call would add seconds to every
follow-up and, on 3B-class models, frequently returns a rewrite that has quietly
changed the question. Lexical rewriting is instant and, when it cannot help, it
returns the message untouched - which is exactly the current behaviour, so
nothing regresses.

Note the split of responsibilities: the *model* always receives the full message
history and resolves pronouns itself perfectly well. This exists only for the
machinery around the model - the intent classifier and the search query - which
sees one message at a time.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# A follow-up is short. A long message carries its own context even when it opens
# with "and what about" - rewriting it would only add noise.
MAX_FOLLOWUP_WORDS = 12

# Openers that mark a message as continuing the previous one rather than starting
# something new.
_FOLLOWUP_OPENER_RE = re.compile(
    # "what about X", "how about X"
    r"^\s*(and\s+)?(what|how)\s+about\b"
    # A message that simply opens with a conjunction is continuing the last one:
    # "and ethereum", "also in celsius", "but why".
    r"|^\s*(and|also|but|or)\s+\w+"
    r"|^\s*(what if|ok(ay)?)\b"
    # A bare interrogative: "why?", "how?", "since when?"
    r"|^\s*(why|how|when|where|who|which)\s*\??\s*$"
    r"|^\s*(tell me )?more\b"
    r"|^\s*(and\s+)?(the\s+)?(same|others?|rest)\b"
    # Comparative with no stated subject: "which is cheaper", "which one lasts
    # longer". Anchored to end shortly after the comparative, so a real question
    # that happens to start "what is ..." is not swept up - those name their own
    # subject and need no rewriting.
    r"|^\s*(which|who)\s+(one\s+)?(is|was|has|costs?|does|lasts?)\b[\w\s]{0,18}$"
    # A pronoun-subject question: "how old is he", "when did they release it".
    r"|^[\w\s']{0,30}\b(he|she|it|they|them|his|her|its|their)\b[\w\s'?]{0,20}$",
    re.IGNORECASE,
)

# Bare referring expressions with no antecedent in the message itself.
_DANGLING_REFERENCE_RE = re.compile(
    r"\b(it|that|those|these|them|they|this one|the same|there)\b", re.IGNORECASE
)

# Content words worth carrying forward from the previous question. Deliberately
# narrow: copying the whole previous message would bury the new one.
_STOPWORDS = frozenset(
    """a an and are as at be by can could did do does for from get give had has have
    how i if in is it its me my not of on or please should show so tell that the their
    them then there these they this to was were what when where which who why will with
    would you your about more also same""".split()
)

# Time words in the *new* message override any in the old one: the whole point of
# "what about tomorrow" is that tomorrow replaces today.
_TIME_WORD_RE = re.compile(
    r"\b(today|tomorrow|tonight|yesterday|now|this (week|weekend|month|year)|"
    r"next (week|weekend|month|year)|last (week|month|year)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"morning|afternoon|evening|\d{4})\b",
    re.IGNORECASE,
)


def _content_terms(text: str) -> list[str]:
    words = re.findall(r"[\w'-]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def looks_like_followup(message: str) -> bool:
    """Whether this message needs the conversation to make sense."""
    words = message.split()
    if len(words) > MAX_FOLLOWUP_WORDS:
        return False
    if _FOLLOWUP_OPENER_RE.search(message):
        return True
    # A short message whose only nouns are pronouns: "is it open?", "how much is
    # that?" - the referent lives in the previous turn.
    if len(words) <= 8 and _DANGLING_REFERENCE_RE.search(message):
        terms = _content_terms(message)
        # If it has two or more real content words it probably stands alone.
        return len(terms) <= 2
    return False


def previous_user_question(messages: list[dict]) -> str | None:
    """The most recent earlier user message, which is what a follow-up extends."""
    user_messages = [
        m.get("content", "")
        for m in messages
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    # The last entry is the current message; the one before it is the context.
    return user_messages[-2] if len(user_messages) >= 2 else None


def resolve(message: str, messages: list[dict]) -> tuple[str, bool]:
    """Rewrite a follow-up into a standalone question.

    Returns (resolved_text, was_rewritten). The original is returned untouched
    whenever rewriting would not clearly help, so this can never make a
    self-contained question worse.
    """
    if not looks_like_followup(message):
        return message, False

    previous = previous_user_question(messages)
    if not previous:
        return message, False

    previous_terms = _content_terms(previous)
    if not previous_terms:
        return message, False

    new_terms = set(_content_terms(message))
    # Time words in the new message replace the old ones rather than joining
    # them - "weather Tokyo today tomorrow" would confuse both the planner and
    # the search engine.
    new_has_time = bool(_TIME_WORD_RE.search(message))

    carried = [
        term
        for term in previous_terms
        if term not in new_terms
        and not (new_has_time and _TIME_WORD_RE.fullmatch(term))
    ]
    if not carried:
        return message, False

    # Subject first, then the new message's own words: "weather Tokyo" +
    # "tomorrow" reads as a query a search engine handles well.
    resolved = f"{' '.join(carried)} {message.strip()}".strip()
    logger.debug("follow-up %r resolved to %r", message, resolved)
    return resolved, True
