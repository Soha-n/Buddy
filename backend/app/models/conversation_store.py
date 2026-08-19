"""SQL access for conversations and messages. The only module that runs SQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db import get_connection, with_lock
from app.models.schemas import (
    AttachmentRecord,
    ConversationDetail,
    ConversationSummary,
    MessageRecord,
)

_DEFAULT_TITLE = "New chat"
_AUTOTITLE_MAX_LEN = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_summary(row) -> ConversationSummary:
    return ConversationSummary(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_model=row["last_model"],
    )


def _row_to_message(row) -> MessageRecord:
    return MessageRecord(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        model_used_for_this_turn=row["model_used_for_this_turn"],
    )


@with_lock
def create_conversation() -> ConversationSummary:
    conn = get_connection()
    conversation_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at, last_model) "
        "VALUES (?, ?, ?, ?, NULL)",
        (conversation_id, _DEFAULT_TITLE, now, now),
    )
    conn.commit()
    return ConversationSummary(
        id=conversation_id, title=_DEFAULT_TITLE, created_at=now, updated_at=now, last_model=None
    )


@with_lock
def list_conversations() -> list[ConversationSummary]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, title, created_at, updated_at, last_model "
        "FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    return [_row_to_summary(row) for row in rows]


@with_lock
def get_conversation(conversation_id: str) -> ConversationDetail | None:
    conn = get_connection()
    conv_row = conn.execute(
        "SELECT id, title, created_at, updated_at, last_model FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conv_row is None:
        return None

    message_rows = conn.execute(
        "SELECT id, role, content, created_at, model_used_for_this_turn "
        "FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()

    # One query for the whole conversation's links rather than one per
    # message, so opening a long chat stays a constant number of round trips.
    attachment_rows = conn.execute(
        "SELECT ma.message_id, a.* FROM message_attachments ma "
        "JOIN attachments a ON a.id = ma.attachment_id "
        "JOIN messages m ON m.id = ma.message_id "
        "WHERE m.conversation_id = ?",
        (conversation_id,),
    ).fetchall()

    by_message: dict[int, list[AttachmentRecord]] = {}
    for row in attachment_rows:
        by_message.setdefault(row["message_id"], []).append(
            AttachmentRecord(
                id=row["id"],
                conversation_id=row["conversation_id"],
                filename=row["filename"],
                kind=row["kind"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                created_at=row["created_at"],
                status=row["status"],
                error=row["error"],
                chunk_count=row["chunk_count"],
                has_description=bool(row["description"]),
            )
        )

    messages = []
    for row in message_rows:
        record = _row_to_message(row)
        record.attachments = by_message.get(row["id"], [])
        messages.append(record)

    summary = _row_to_summary(conv_row)
    return ConversationDetail(**summary.model_dump(), messages=messages)


@with_lock
def conversation_exists(conversation_id: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row is not None


def _truncate_title(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _AUTOTITLE_MAX_LEN:
        return collapsed or _DEFAULT_TITLE
    truncated = collapsed[:_AUTOTITLE_MAX_LEN].rsplit(" ", 1)[0]
    return f"{truncated}…" if truncated else f"{collapsed[:_AUTOTITLE_MAX_LEN]}…"


@with_lock
def append_user_message(
    conversation_id: str, content: str, attachment_ids: list[str] | None = None
) -> int:
    """Persist the user's turn, auto-titling the conversation on its first message.

    Returns the new message id, and links any attachments sent with the turn so
    the transcript can re-render them against the right message later.
    """
    conn = get_connection()
    now = _now()
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES (?, 'user', ?, ?)",
        (conversation_id, content, now),
    )
    message_id = int(cursor.lastrowid or 0)

    if attachment_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO message_attachments (message_id, attachment_id) "
            "VALUES (?, ?)",
            [(message_id, attachment_id) for attachment_id in attachment_ids],
        )

    row = conn.execute(
        "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is not None and row["title"] == _DEFAULT_TITLE:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (_truncate_title(content), now, conversation_id),
        )
    else:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )
    conn.commit()
    return message_id


@with_lock
def append_assistant_message(conversation_id: str, content: str, model: str) -> None:
    """Persist the assistant's turn, even if content is partial (aborted stream)."""
    if not content:
        return
    conn = get_connection()
    now = _now()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at, model_used_for_this_turn) "
        "VALUES (?, 'assistant', ?, ?, ?)",
        (conversation_id, content, now, model),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ?, last_model = ? WHERE id = ?",
        (now, model, conversation_id),
    )
    conn.commit()


@with_lock
def rename_conversation(conversation_id: str, title: str) -> ConversationSummary | None:
    conn = get_connection()
    now = _now()
    cursor = conn.execute(
        "UPDATE conversations SET title = ?, updated_at = updated_at WHERE id = ?",
        (title.strip() or _DEFAULT_TITLE, conversation_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT id, title, created_at, updated_at, last_model FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    return _row_to_summary(row) if row else None


@with_lock
def delete_conversation(conversation_id: str) -> bool:
    conn = get_connection()
    cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
    return cursor.rowcount > 0
