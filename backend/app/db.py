"""SQLite connection management and schema initialization.

Stdlib sqlite3 rather than an ORM or aiosqlite: this is a single-user local
app, so there is no concurrency pressure that would justify either. One
module-level connection is reused (WAL mode lets a chat stream's writes not
block a concurrent sidebar refresh), guarded by a lock since sqlite3
connections are not thread-safe and calls arrive via asyncio.to_thread from
whatever thread pool happens to run them.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.config import settings

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None


def _resolve_db_path() -> Path:
    path = Path(settings.db_path)
    if not path.is_absolute():
        # Relative to the backend/ directory (this file's grandparent), not the
        # process's current working directory, so it doesn't matter where
        # uvicorn was launched from.
        path = Path(__file__).resolve().parent.parent / path
    return path


def get_connection() -> sqlite3.Connection:
    """Return the shared connection, opening it on first use."""
    global _connection
    if _connection is None:
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(str(db_path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON")
        _connection.execute("PRAGMA journal_mode = WAL")
    return _connection


def init_db() -> None:
    """Create tables/indexes if they don't exist yet. Safe to call every startup."""
    with _lock:
        conn = get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'New chat',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                last_model  TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id          TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role                     TEXT NOT NULL CHECK (role IN ('system','user','assistant')),
                content                  TEXT NOT NULL,
                created_at               TEXT NOT NULL,
                model_used_for_this_turn TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_conversations_updated
                ON conversations(updated_at DESC);

            -- One row per uploaded file. Scoped to a conversation: retrieval
            -- must never reach across chats, which is what makes "RAG in this
            -- particular chat" true rather than a global document search.
            CREATE TABLE IF NOT EXISTS attachments (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                filename        TEXT NOT NULL,
                kind            TEXT NOT NULL,
                mime_type       TEXT,
                size_bytes      INTEGER NOT NULL,
                created_at      TEXT NOT NULL,
                -- 'pending' -> 'ready' | 'error'. Extraction runs after the
                -- upload responds, so the UI needs a status to poll.
                status          TEXT NOT NULL DEFAULT 'pending',
                error           TEXT,
                chunk_count     INTEGER NOT NULL DEFAULT 0,
                -- Images only: where the bytes live, plus the generated
                -- description that lets text-only models reason about them
                -- after a model switch.
                stored_path     TEXT,
                description     TEXT,
                -- Tabular files only: a compact schema/preview summary that is
                -- always injected, so the model knows the real column names
                -- before it writes any pandas code.
                data_summary    TEXT
            );

            -- Retrieval unit.
            CREATE TABLE IF NOT EXISTS attachment_chunks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                chunk_index   INTEGER NOT NULL,
                content       TEXT NOT NULL,
                -- Human-readable origin ("page 3", "Sheet1 rows 20-40") so
                -- answers can cite where a passage came from.
                locator       TEXT
            );

            -- Full-text index over chunk content, ranked with BM25. FTS5 ships
            -- inside SQLite, so document search needs no extra model and no
            -- separate service - indexing is a plain INSERT.
            --
            -- 'content=' makes this an external-content index: the text is not
            -- stored twice, the virtual table just points at attachment_chunks
            -- by rowid. The triggers below keep the two in step, which an
            -- external-content table does not do on its own.
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(
                content,
                content='attachment_chunks',
                content_rowid='id',
                tokenize='unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS chunk_search_insert
            AFTER INSERT ON attachment_chunks BEGIN
                INSERT INTO chunk_search(rowid, content) VALUES (new.id, new.content);
            END;

            -- 'delete' is FTS5's command for retracting a row's tokens; a plain
            -- DELETE on an external-content table would leave them behind and
            -- keep matching text that no longer exists.
            CREATE TRIGGER IF NOT EXISTS chunk_search_delete
            AFTER DELETE ON attachment_chunks BEGIN
                INSERT INTO chunk_search(chunk_search, rowid, content)
                    VALUES ('delete', old.id, old.content);
            END;

            -- Which attachments were sent with which turn. A join table rather
            -- than a column on attachments: the same file can accompany more
            -- than one turn (ask a follow-up about the same image), and the
            -- transcript needs the per-message grouping to re-render.
            CREATE TABLE IF NOT EXISTS message_attachments (
                message_id    INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                PRIMARY KEY (message_id, attachment_id)
            );

            CREATE INDEX IF NOT EXISTS idx_attachments_conversation
                ON attachments(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_chunks_attachment
                ON attachment_chunks(attachment_id, chunk_index);
            """
        )
        conn.commit()


def with_lock(fn):
    """Decorator serializing access to the shared connection.

    sqlite3 connections aren't thread-safe for concurrent use even with
    check_same_thread=False, and to_thread calls can land on different pool
    threads, so every write/read that isn't already inside init_db's lock goes
    through this.
    """

    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)

    return wrapper
