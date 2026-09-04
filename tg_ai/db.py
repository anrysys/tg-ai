"""PostgreSQL archive access - raw SQL over asyncpg, no ORM (ADR-0006).

The archive exists so that ``tg_search_local_history`` can answer questions
about years of history in milliseconds without touching the Telegram API,
which is both slow and rate-limited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from tg_ai.config import SCHEMA_PATH

log = logging.getLogger(__name__)

#: Rows buffered before a write. Large enough to amortise round-trips, small
#: enough that an interrupted sync loses at most this many rows of progress.
INSERT_BATCH_SIZE = 500

_INSERT_MESSAGE_SQL = """
INSERT INTO messages (chat_id, message_id, sender_id, text, date, is_outgoing)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (chat_id, message_id) DO NOTHING
"""

_UPSERT_DIALOG_SQL = """
INSERT INTO dialogs (chat_id, username, first_name, last_name, phone, is_contact)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (chat_id) DO UPDATE SET
    username   = EXCLUDED.username,
    first_name = EXCLUDED.first_name,
    last_name  = EXCLUDED.last_name,
    phone      = EXCLUDED.phone,
    is_contact = EXCLUDED.is_contact
"""

_ADVANCE_CURSOR_SQL = """
UPDATE dialogs
   SET last_synced_message_id = GREATEST(last_synced_message_id, $2),
       synced_at              = now()
 WHERE chat_id = $1
"""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One message returned by a local history search."""

    chat_id: int
    message_id: int
    username: str | None
    display_name: str
    text: str
    date: datetime
    is_outgoing: bool


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Open a small connection pool for the local archive."""
    return await asyncpg.create_pool(database_url, min_size=1, max_size=4, command_timeout=60)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Apply ``sql/schema.sql``. Idempotent, safe on every start."""
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(ddl)
    log.info("schema ensured from %s", SCHEMA_PATH)


async def upsert_dialog(
    pool: asyncpg.Pool,
    *,
    chat_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
    is_contact: bool,
) -> None:
    """Insert or refresh the metadata of one dialog."""
    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_DIALOG_SQL, chat_id, username, first_name, last_name, phone, is_contact
        )


async def insert_messages(pool: asyncpg.Pool, rows: list[tuple[Any, ...]]) -> int:
    """Insert a batch of messages, ignoring ones already archived.

    Args:
        rows: ``(chat_id, message_id, sender_id, text, date, is_outgoing)``.

    Returns:
        The number of rows the archive grew by.
    """
    if not rows:
        return 0
    async with pool.acquire() as conn, conn.transaction():
        before = await conn.fetchval("SELECT count(*) FROM messages WHERE chat_id = $1", rows[0][0])
        await conn.executemany(_INSERT_MESSAGE_SQL, rows)
        after = await conn.fetchval("SELECT count(*) FROM messages WHERE chat_id = $1", rows[0][0])
    return int(after) - int(before)


async def advance_cursor(pool: asyncpg.Pool, chat_id: int, message_id: int) -> None:
    """Move a dialog's resume cursor forward. Never moves it backwards."""
    async with pool.acquire() as conn:
        await conn.execute(_ADVANCE_CURSOR_SQL, chat_id, message_id)


async def get_cursor(pool: asyncpg.Pool, chat_id: int) -> int:
    """Return the highest archived message id for a dialog, or 0."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT last_synced_message_id FROM dialogs WHERE chat_id = $1", chat_id
        )
    return int(value or 0)


def build_search_query(*, by_username: bool) -> tuple[str, str]:
    """Return the ``(fulltext_sql, trigram_sql)`` pair for a search.

    Both statements take the same parameters in the same order:
    ``$1`` query text, ``$2`` limit, and ``$3`` username when ``by_username``.
    Keeping them symmetrical is what lets the caller retry without rebuilding
    arguments. Exposed separately from execution so it can be tested without
    a database (SPEC-SRCH-004).
    """
    dialog_filter = "\n      AND lower(d.username) = lower($3)" if by_username else ""

    fulltext = f"""
    SELECT m.chat_id, m.message_id, d.username, m.text, m.date, m.is_outgoing,
           trim(coalesce(d.first_name, '') || ' ' || coalesce(d.last_name, '')) AS display_name
      FROM messages m
      JOIN dialogs  d ON d.chat_id = m.chat_id
     WHERE m.tsv @@ websearch_to_tsquery('simple', $1){dialog_filter}
     ORDER BY ts_rank(m.tsv, websearch_to_tsquery('simple', $1)) DESC, m.date DESC
     LIMIT $2
    """

    trigram = f"""
    SELECT m.chat_id, m.message_id, d.username, m.text, m.date, m.is_outgoing,
           trim(coalesce(d.first_name, '') || ' ' || coalesce(d.last_name, '')) AS display_name
      FROM messages m
      JOIN dialogs  d ON d.chat_id = m.chat_id
     WHERE m.text ILIKE '%' || $1 || '%'{dialog_filter}
     ORDER BY m.date DESC
     LIMIT $2
    """

    return fulltext, trigram


async def search_messages(
    pool: asyncpg.Pool,
    query: str,
    *,
    target_username: str | None = None,
    limit: int = 50,
) -> tuple[list[SearchHit], str]:
    """Search the archive: full-text first, trigram substring as fallback.

    Returns:
        ``(hits, strategy)`` where ``strategy`` is ``"fulltext"`` or
        ``"substring"`` so the caller can tell the agent how it matched.
    """
    username = target_username.lstrip("@") if target_username else None
    fulltext_sql, trigram_sql = build_search_query(by_username=username is not None)
    args: list[Any] = [query, limit]
    if username is not None:
        args.append(username)

    async with pool.acquire() as conn:
        records = await conn.fetch(fulltext_sql, *args)
        strategy = "fulltext"
        if not records:
            records = await conn.fetch(trigram_sql, *args)
            strategy = "substring"

    return [
        SearchHit(
            chat_id=r["chat_id"],
            message_id=r["message_id"],
            username=r["username"],
            display_name=r["display_name"] or "(no name)",
            text=r["text"] or "",
            date=r["date"],
            is_outgoing=r["is_outgoing"],
        )
        for r in records
    ], strategy


async def archive_stats(pool: asyncpg.Pool) -> dict[str, Any]:
    """Summarise the archive for health checks."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM messages) AS messages,
                   (SELECT count(*) FROM dialogs)  AS dialogs,
                   (SELECT max(synced_at) FROM dialogs) AS last_sync,
                   (SELECT min(date) FROM messages) AS oldest,
                   (SELECT max(date) FROM messages) AS newest
            """
        )
    return dict(row) if row else {}
