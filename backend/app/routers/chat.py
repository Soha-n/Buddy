"""Chat streaming endpoint.

The backend still holds no *in-memory* conversation state between requests —
each request carries the full message array, so a restart mid-conversation
loses nothing. SQLite now sits underneath as a durability layer: the user's
turn is persisted before Ollama is even called, and the assistant's turn is
assembled token-by-token inside this generator and persisted once the stream
ends, including on client disconnect, so a browser tab closing mid-reply does
not silently lose whatever text the user already saw arrive.

Attachments add three things to that flow, all of them here rather than in the
client, so the rules cannot be bypassed by a hand-made request:

1. Retrieval. Before generation, the conversation's indexed documents are
   searched with the user's question and the matching passages are injected as
   a system message. Scoped to one conversation by construction.
2. Image gating. Images are only ever forwarded to a model Ollama reports as
   vision-capable; sending them to a text-only model is refused with the list
   of models that would work, because the alternative is an answer that
   silently ignores the picture.
3. Description capture. After a vision turn, the image is described once in the
   background and stored as text, which is what lets the user switch to a
   text-only model afterwards and keep asking about it.
4. Web search. When the user turns the toggle on for a message, the web is
   searched before generation and the results are injected. When it is off, an
   instruction is injected telling the model to name the toggle rather than
   guess at current facts - which is the difference between "I can't reach live
   data, turn on Web and ask again" and a confidently stale answer.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models import attachment_store as attachments
from app.models import conversation_store as store
from app.models.schemas import ChatRequest
from app.services import (
    capabilities,
    direct_answers,
    followup,
    ollama,
    query_planner,
    rag,
    usercontext,
    vision,
    websearch,
)
from app.sse import MEDIA_TYPE, SSE_HEADERS, format_event, guarded_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

_NS_PER_SECOND = 1_000_000_000


async def _chat_events(
    request: ChatRequest,
    conversation_id: str,
    messages: list[dict],
    describe_targets: list[tuple[str, str]],
) -> AsyncIterator[str]:
    """Translate Ollama chat frames into SSE token events, persisting as it goes."""
    full_content = ""

    try:
        async for frame in ollama.stream_chat(request.model, messages, request.options):
            message = frame.get("message") or {}
            content = message.get("content") or ""
            if content:
                full_content += content
                yield format_event("token", {"content": content})

            if frame.get("done"):
                eval_count = frame.get("eval_count") or 0
                eval_duration = frame.get("eval_duration") or 0
                tokens_per_sec: float | None = None
                if eval_count and eval_duration:
                    tokens_per_sec = round(
                        eval_count / (eval_duration / _NS_PER_SECOND), 1
                    )
                yield format_event(
                    "done",
                    {
                        "eval_count": eval_count,
                        "tokens_per_sec": tokens_per_sec,
                        "done_reason": frame.get("done_reason"),
                    },
                )
                return
    finally:
        # Runs on normal completion, on an error surfaced as an SSE event, and
        # on GeneratorExit from a client disconnect — so partial replies the
        # user already saw are never silently dropped.
        await asyncio.to_thread(
            store.append_assistant_message, conversation_id, full_content, request.model
        )
        # Deliberately after the reply is persisted and outside the streamed
        # response: describing an image is a second full generation, and making
        # the user wait for it before seeing their answer would be a visible
        # regression. Fire-and-forget, with its own error handling inside.
        if describe_targets:
            asyncio.create_task(_describe_images(request.model, describe_targets))


async def _describe_images(model: str, targets: list[tuple[str, str]]) -> None:
    """Generate and store descriptions for images sent this turn.

    Sequential rather than concurrent: two simultaneous generations on one local
    model contend for the same VRAM and finish slower than one after the other.
    """
    for attachment_id, image_base64 in targets:
        try:
            description = await vision.describe_image(model, image_base64)
        except vision.VisionError as exc:
            # A missing description degrades a later text-only turn; it does not
            # break this one, so it is logged rather than surfaced.
            logger.warning("could not describe attachment %s: %s", attachment_id, exc)
            continue
        except Exception:  # noqa: BLE001 - detached task, must not vanish
            logger.exception("describing attachment %s crashed", attachment_id)
            continue
        await asyncio.to_thread(attachments.set_description, attachment_id, description)
        logger.info("stored description for attachment %s", attachment_id)


async def _chat_stream_with_meta(
    request: ChatRequest,
    conversation_id: str,
    announce_id: bool,
    messages: list[dict],
    describe_targets: list[tuple[str, str]],
    search_outcome: websearch.SearchOutcome | None,
    direct: direct_answers.DirectAnswer | None = None,
) -> AsyncIterator[str]:
    """Prefix the token stream with meta and citation events."""
    if announce_id:
        yield format_event("meta", {"conversation_id": conversation_id})
    if direct is not None:
        # A direct answer has exactly one source, presented the same way as
        # search citations so the UI needs no second code path.
        yield format_event(
            "sources",
            {
                "query": "",
                "provider": "direct",
                "citations": [
                    {
                        "index": 1,
                        "title": direct.source_label,
                        "url": direct.source_url or "",
                        "fetched": True,
                    }
                ],
            },
        )
    if search_outcome is not None:
        # Emitted before the first token so the UI can show what was consulted
        # while the model is still thinking.
        yield format_event(
            "sources",
            {
                "query": search_outcome.query,
                "provider": search_outcome.provider,
                "citations": [
                    {
                        "index": index,
                        "title": result.title,
                        "url": result.url,
                        "fetched": result.content is not None,
                    }
                    for index, result in enumerate(search_outcome.results, start=1)
                ],
            },
        )
    async for chunk in _chat_events(request, conversation_id, messages, describe_targets):
        yield chunk


async def _load_images(attachment_ids: list[str]) -> list[tuple[str, str, str]]:
    """Read the requested image attachments as (id, filename, base64).

    Only attachments of kind 'image' with bytes still on disk are returned;
    document ids passed here are ignored rather than rejected, since the client
    sends one list for everything it attached to the turn.
    """
    loaded: list[tuple[str, str, str]] = []
    for attachment_id in attachment_ids:
        record = await asyncio.to_thread(attachments.get_attachment, attachment_id)
        if record is None or record.kind != "image":
            continue
        stored_path, _ = await asyncio.to_thread(
            attachments.get_stored_path, attachment_id
        )
        if not stored_path:
            continue
        path = Path(stored_path)
        if not path.exists():
            continue
        raw = await asyncio.to_thread(path.read_bytes)
        loaded.append(
            (attachment_id, record.filename, base64.b64encode(raw).decode("ascii"))
        )
    return loaded


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream a model response token by token, persisting the turn either way."""
    if not request.model.strip():
        raise HTTPException(status_code=400, detail="model is required")
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    last_user_message = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), None
    )
    if last_user_message is None:
        raise HTTPException(status_code=400, detail="messages must include a user turn")

    announce_id = request.conversation_id is None
    if request.conversation_id is None:
        conversation = await asyncio.to_thread(store.create_conversation)
        conversation_id = conversation.id
    else:
        conversation_id = request.conversation_id
        exists = await asyncio.to_thread(store.conversation_exists, conversation_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation not found")

    # --- Image gating ------------------------------------------------------ #
    # Enforced here rather than only in the UI: this is what guarantees an image
    # is never quietly dropped on the floor by a text-only model.
    images = await _load_images(request.attachment_ids)
    describe_targets: list[tuple[str, str]] = []

    if images:
        if not await capabilities.supports_vision(request.model):
            installed = await capabilities.list_vision_models()
            hint = (
                f"Switch to one of these installed models: {', '.join(installed)}."
                if installed
                else "No installed model can read images."
            )
            raise HTTPException(
                status_code=422,
                detail=f"{request.model} cannot read images. {hint}",
            )
        describe_targets = [
            (attachment_id, encoded) for attachment_id, _, encoded in images
        ]

    # Persisted before calling Ollama, so the user's turn survives even if
    # generation fails immediately.
    # Attachment ids are linked to this message so reopening the conversation
    # re-renders the files against the turn they were sent with.
    await asyncio.to_thread(
        store.append_user_message,
        conversation_id,
        last_user_message,
        request.attachment_ids,
    )

    # --- Plan the turn ----------------------------------------------------- #
    # A follow-up is resolved first: "what about tomorrow" tells the classifier
    # nothing on its own, but "weather tokyo tomorrow" routes straight to the
    # weather API. The model still sees the user's real words - only the
    # machinery around it uses the rewrite.
    history = [m.model_dump(exclude_none=True) for m in request.messages]
    resolved_question, was_rewritten = followup.resolve(last_user_message, history)
    if was_rewritten:
        logger.info("follow-up resolved: %r -> %r", last_user_message, resolved_question)

    has_attachments = await asyncio.to_thread(
        attachments.has_ready_attachments, conversation_id
    )
    plan = query_planner.plan(
        resolved_question,
        web_enabled=request.web_search,
        has_attachments=has_attachments,
    )
    logger.info(
        "plan: intent=%s pages=%d results=%d (%s)",
        plan.intent.value,
        plan.pages_to_fetch,
        plan.results_wanted,
        plan.reason,
    )

    # Location is resolved only when the question actually needs a place, and
    # cached after the first time - so an IP lookup never happens for someone who
    # only ever asks about maths.
    location = usercontext.cached_location()
    needs_place = plan.needs_location or plan.direct_kind == query_planner.DirectKind.WEATHER
    if needs_place:
        # Weather needs coordinates, not a country: wttr.in resolves a bare
        # "India" to an arbitrary town inside it. The OS location service is
        # asked for a precise fix when the question is about the user's own
        # surroundings and we do not already have one - Windows shows its consent
        # prompt once, and the answer is cached for the session afterwards.
        wants_precise = plan.needs_location and (
            location is None or not location.has_coordinates
        )
        if location is None or wants_precise:
            location = await usercontext.get_location(
                force_refresh=wants_precise, allow_precise=wants_precise
            )

    # --- Direct answers ---------------------------------------------------- #
    # Weather and time have exact sources. Searching for them and scraping a
    # result page is slower and less accurate than asking the right API.
    direct: direct_answers.DirectAnswer | None = None
    search_outcome = None
    search_error: str | None = None

    if plan.direct_kind == query_planner.DirectKind.TIME:
        direct = direct_answers.time_and_date(location)
    elif plan.direct_kind == query_planner.DirectKind.WEATHER:
        # A named place in the question wins over the user's own location. Only
        # the place name is passed - a geocoder handed the whole sentence
        # resolves nothing.
        try:
            direct = await direct_answers.weather(plan.place, location)
        except direct_answers.DirectAnswerError as exc:
            # Fall through to ordinary search rather than failing: a generic
            # weather search still beats no answer.
            logger.info("weather provider declined (%s); falling back to search", exc)
            if request.web_search:
                # needs_location is carried over so the query still gets the
                # user's city appended below - a bare "current temperature"
                # search is useless without a place.
                plan = query_planner.QueryPlan(
                    intent=query_planner.Intent.LOOKUP,
                    search_query=plan.search_query,
                    place=plan.place,
                    results_wanted=5,
                    pages_to_fetch=0,
                    needs_location=plan.needs_location,
                    reason="no weather provider configured, answering from search",
                )
            else:
                search_error = str(exc)

    # --- Web search -------------------------------------------------------- #
    # Runs before generation rather than as a tool call: local models decide
    # when to search unreliably (one refused a 2026 question outright, another
    # searched for "17 x 23"), and vision-only models report no tool support at
    # all. A user-controlled toggle behaves identically on every model.
    if (
        direct is None
        and request.web_search
        and plan.results_wanted > 0
    ):
        query = plan.search_query or resolved_question
        if plan.needs_location and location is not None:
            query = query_planner.localize_query(query, location.label)
        try:
            search_outcome = await websearch.search(
                query,
                fetch_pages=plan.pages_to_fetch > 0,
                max_results=plan.results_wanted,
                pages_to_fetch=plan.pages_to_fetch,
            )
        except websearch.SearchError as exc:
            # Surfaced as a system note rather than an HTTP error: the model can
            # still answer from what it knows, and failing the whole turn
            # because search was unavailable would be worse.
            search_error = str(exc)
            logger.warning("web search failed: %s", exc)

    # --- Prompt assembly --------------------------------------------------- #
    messages = [msg.model_dump(exclude_none=True) for msg in request.messages]

    system_turns: list[dict] = []

    # Always first, search or no search: a model that knows today's real date
    # stops calling this year's events "upcoming", and one that knows the user's
    # city can resolve "here". Cheap - a few lines of text.
    system_turns.append(
        {"role": "system", "content": usercontext.build_context(location)}
    )

    context = await rag.build_context(conversation_id, last_user_message)
    if context:
        system_turns.append({"role": "system", "content": rag.SYSTEM_PROMPT})
        system_turns.append({"role": "system", "content": context})

    if direct is not None:
        system_turns.append({"role": "system", "content": direct_answers.SYSTEM_PROMPT})
        system_turns.append({"role": "system", "content": direct.content})
    elif search_outcome is not None:
        system_turns.append({"role": "system", "content": websearch.SYSTEM_PROMPT})
        system_turns.append(
            {"role": "system", "content": websearch.build_context(search_outcome)}
        )
    elif search_error is not None:
        system_turns.append(
            {
                "role": "system",
                "content": (
                    "A live lookup was attempted for this question and could not "
                    f"be completed. The exact reason is: {search_error} "
                    "Relay that reason to the user in one or two sentences, using "
                    "its actual wording. Do NOT explain this as a limit of your "
                    "training data or knowledge cutoff - that is not why it "
                    "failed, and saying so would send the user looking for the "
                    "wrong fix. Do not invent a current value."
                ),
            }
        )
    elif not request.web_search and plan.intent in {
        query_planner.Intent.LOOKUP,
        query_planner.Intent.RESEARCH,
    }:
        # The question needed live data and the toggle is off. Only injected in
        # that case, so an ordinary question is never told about a toggle it does
        # not need - which is what previously made a small model demand the web
        # for "17 x 23".
        system_turns.append(
            {"role": "system", "content": websearch.OFFLINE_SYSTEM_PROMPT}
        )

    if system_turns:
        # Prepended as system turns rather than folded into the user's message,
        # so the transcript the user sees stays exactly what they typed.
        messages = [*system_turns, *messages]

    if images:
        # Ollama takes images on the message they belong to, so they attach to
        # the final user turn.
        for message in reversed(messages):
            if message.get("role") == "user":
                message["images"] = [encoded for _, _, encoded in images]
                break

    return StreamingResponse(
        guarded_stream(
            _chat_stream_with_meta(
                request,
                conversation_id,
                announce_id,
                messages,
                describe_targets,
                search_outcome,
                direct,
            ),
            f"chat:{request.model}",
        ),
        media_type=MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
