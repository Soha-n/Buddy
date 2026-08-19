"""Conversation history CRUD. Message content itself is written from chat.py,
as a side effect of streaming, not through these endpoints.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.models import conversation_store as store
from app.models.schemas import (
    ConversationDetail,
    ConversationSummary,
    ConversationsListResponse,
    RenameConversationRequest,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=ConversationsListResponse)
async def list_conversations() -> ConversationsListResponse:
    conversations = await asyncio.to_thread(store.list_conversations)
    return ConversationsListResponse(conversations=conversations)


@router.post("", response_model=ConversationSummary, status_code=201)
async def create_conversation() -> ConversationSummary:
    """Create an empty conversation shell, for the sidebar's 'New chat' button.

    Sending the first message does not need this: POST /api/chat creates one
    implicitly when conversation_id is omitted.
    """
    return await asyncio.to_thread(store.create_conversation)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str) -> ConversationDetail:
    detail = await asyncio.to_thread(store.get_conversation, conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: str, request: RenameConversationRequest
) -> ConversationSummary:
    summary = await asyncio.to_thread(
        store.rename_conversation, conversation_id, request.title
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return summary


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict:
    deleted = await asyncio.to_thread(store.delete_conversation, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True, "id": conversation_id}
