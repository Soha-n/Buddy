"""SQL access for attachments and their chunks.

Mirrors conversation_store's shape: every function here is synchronous and
serialized by the shared connection lock, and callers reach it through
asyncio.to_thread. Keeping all attachment SQL in one module means the retrieval
path and the upload path cannot drift in how they read the same rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db import get_connection, with_lock
from app.models.schemas import AttachmentRecord, RetrievedChunk


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_attachment(row) -> AttachmentRecord:
    return AttachmentRecord(
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


@with_lock
def create_attachment(
    conversation_id: str,
    filename: str,
    kind: str,
    mime_type: str | None,
    size_bytes: int,
    stored_path: str | None = None,
) -> AttachmentRecord:
    """Insert a pending attachment row. Extraction happens after this returns."""
    conn = get_connection()
    attachment_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO attachments "
        "(id, conversation_id, filename, kind, mime_type, size_bytes, created_at, "
        " status, chunk_count, stored_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
        (
            attachment_id,
            conversation_id,
            filename,
            kind,
            mime_type,
            size_bytes,
            now,
            stored_path,
        ),
    )
    conn.commit()
    return AttachmentRecord(
        id=attachment_id,
        conversation_id=conversation_id,
        filename=filename,
        kind=kind,
        mime_type=mime_type,
        size_bytes=size_bytes,
        created_at=now,
        status="pending",
        error=None,
        chunk_count=0,
        has_description=False,
    )


@with_lock
def save_chunks(
    attachment_id: str, rows: list[tuple[int, str, str | None]]
) -> None:
    """Bulk-insert chunk rows: (chunk_index, content, locator).

    The FTS5 index is populated by an AFTER INSERT trigger, so there is nothing
    extra to do here to make these chunks searchable.
    """
    if not rows:
        return
    conn = get_connection()
    conn.executemany(
        "INSERT INTO attachment_chunks "
        "(attachment_id, chunk_index, content, locator) VALUES (?, ?, ?, ?)",
        [(attachment_id, index, content, locator) for index, content, locator in rows],
    )
    conn.commit()


@with_lock
def mark_ready(
    attachment_id: str, chunk_count: int, data_summary: str | None = None
) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE attachments SET status = 'ready', chunk_count = ?, data_summary = ?, "
        "error = NULL WHERE id = ?",
        (chunk_count, data_summary, attachment_id),
    )
    conn.commit()


@with_lock
def mark_error(attachment_id: str, message: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE attachments SET status = 'error', error = ? WHERE id = ?",
        (message, attachment_id),
    )
    conn.commit()


@with_lock
def set_description(attachment_id: str, description: str) -> None:
    """Store an image's generated description.

    This is the mechanism that survives a model switch: the description is
    plain text in the transcript, so a text-only model reads it as ordinary
    context long after the vision model that produced it was unloaded.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE attachments SET description = ? WHERE id = ?",
        (description, attachment_id),
    )
    conn.commit()


@with_lock
def set_stored_path(attachment_id: str, stored_path: str) -> None:
    """Record where the raw bytes were written.

    Set for images (re-sent to the model on later turns) and for tabular files
    (handed to the chart sandbox so generated pandas code reads the real file
    rather than a lossy reconstruction of it).
    """
    conn = get_connection()
    conn.execute(
        "UPDATE attachments SET stored_path = ? WHERE id = ?",
        (stored_path, attachment_id),
    )
    conn.commit()


@with_lock
def list_table_files(conversation_id: str) -> list[tuple[str, str]]:
    """(filename, stored_path) for tabular attachments that still have bytes."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT filename, stored_path FROM attachments "
        "WHERE conversation_id = ? AND kind = 'table' AND status = 'ready' "
        "AND stored_path IS NOT NULL ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    return [(row["filename"], row["stored_path"]) for row in rows]


@with_lock
def list_image_paths(conversation_id: str) -> list[tuple[str, str, str | None]]:
    """(attachment_id, stored_path, description) for this conversation's images."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, stored_path, description FROM attachments "
        "WHERE conversation_id = ? AND kind = 'image' AND stored_path IS NOT NULL "
        "ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    return [(row["id"], row["stored_path"], row["description"]) for row in rows]


@with_lock
def get_attachment(attachment_id: str) -> AttachmentRecord | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    return _row_to_attachment(row) if row else None


@with_lock
def get_stored_path(attachment_id: str) -> tuple[str | None, str | None]:
    """Return (stored_path, mime_type) for serving image bytes back."""
    conn = get_connection()
    row = conn.execute(
        "SELECT stored_path, mime_type FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        return None, None
    return row["stored_path"], row["mime_type"]


@with_lock
def list_attachments(conversation_id: str) -> list[AttachmentRecord]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM attachments WHERE conversation_id = ? ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    return [_row_to_attachment(row) for row in rows]


@with_lock
def list_descriptions(conversation_id: str) -> list[tuple[str, str]]:
    """(filename, description) for every described image in a conversation."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT filename, description FROM attachments "
        "WHERE conversation_id = ? AND description IS NOT NULL AND description != '' "
        "ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    return [(row["filename"], row["description"]) for row in rows]


@with_lock
def list_data_summaries(conversation_id: str) -> list[tuple[str, str]]:
    """(filename, data_summary) for tabular attachments.

    Always injected rather than retrieved - the model needs real column names
    before it can write code against them, and a summary that only shows up on
    a lucky semantic match would make generated code intermittently wrong.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT filename, data_summary FROM attachments "
        "WHERE conversation_id = ? AND data_summary IS NOT NULL AND status = 'ready' "
        "ORDER BY created_at ASC",
        (conversation_id,),
    ).fetchall()
    return [(row["filename"], row["data_summary"]) for row in rows]


@with_lock
def search_chunks(
    conversation_id: str, query: str, limit: int
) -> list[RetrievedChunk]:
    """Rank this conversation's chunks against a query with BM25.

    Conversation-scoped by construction: the join to attachments in the WHERE
    clause is what stops a document uploaded in a different chat from ever
    surfacing here.

    bm25() returns a *negative* score where more negative is a better match, so
    ordering is ascending and the sign is flipped on the way out to give callers
    a conventional "higher is better" number.

    The magnitude is not rounded: BM25 values shrink toward zero as the index
    grows small (two documents can score around 1e-06), and rounding to a few
    decimals would flatten every result to 0.0 and make the ordering look
    arbitrary in logs and API responses.
    """
    if not query.strip():
        return []
    conn = get_connection()
    rows = conn.execute(
        "SELECT c.content, c.locator, a.filename, bm25(chunk_search) AS score "
        "FROM chunk_search "
        "JOIN attachment_chunks c ON c.id = chunk_search.rowid "
        "JOIN attachments a ON a.id = c.attachment_id "
        "WHERE chunk_search MATCH ? AND a.conversation_id = ? AND a.status = 'ready' "
        "ORDER BY score LIMIT ?",
        (query, conversation_id, limit),
    ).fetchall()
    return [
        RetrievedChunk(
            filename=row["filename"],
            locator=row["locator"],
            content=row["content"],
            score=-row["score"],
        )
        for row in rows
    ]


@with_lock
def list_chunks(conversation_id: str, limit: int) -> list[RetrievedChunk]:
    """First N chunks in upload order, ignoring relevance.

    The fallback when a query produces no FTS match at all - a question phrased
    entirely in words that do not appear in the document ("summarise this")
    should still see the document rather than nothing.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT c.content, c.locator, a.filename FROM attachment_chunks c "
        "JOIN attachments a ON a.id = c.attachment_id "
        "WHERE a.conversation_id = ? AND a.status = 'ready' "
        "ORDER BY a.created_at ASC, c.chunk_index ASC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [
        RetrievedChunk(
            filename=row["filename"],
            locator=row["locator"],
            content=row["content"],
            score=0.0,
        )
        for row in rows
    ]


@with_lock
def has_ready_attachments(conversation_id: str) -> bool:
    """Whether retrieval is worth attempting at all for this conversation."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM attachments WHERE conversation_id = ? AND status = 'ready' LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row is not None


@with_lock
def delete_attachment(attachment_id: str) -> str | None:
    """Delete an attachment, returning its stored_path so the file can be removed.

    Chunks go with it via ON DELETE CASCADE.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT stored_path FROM attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if row is None:
        return None
    stored_path = row["stored_path"]
    conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    conn.commit()
    return stored_path
