"""PostgreSQL archive access - raw SQL over asyncpg, no ORM (ADR-0006).

The archive exists so that ``tg_search_local_history`` can answer questions
about years of history in milliseconds without touching the Telegram API,
which is both slow and rate-limited.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from tg_ai import persona
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


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Make ``jsonb`` round-trip as a plain ``dict``.

    asyncpg does not adapt a ``dict`` to ``jsonb`` on its own; without this it
    raises ``expected str, got dict``. Registering the codec once here rather
    than calling ``json.dumps`` at every call site keeps the serialisation
    decision in one place and stops a raw JSON string leaking out of this
    module, the same boundary :class:`SearchHit` already enforces for rows.
    """
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Open a small connection pool for the local archive."""
    return await asyncpg.create_pool(
        database_url, min_size=1, max_size=4, command_timeout=60, init=_init_connection
    )


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


# --------------------------------------------------------------------------
# Dialog Persona (SPEC-PSN-*)
# --------------------------------------------------------------------------

_SELECT_PERSONA_SQL = """
SELECT chat_id, addressing, tone, relationship, notes, metrics,
       baseline_message_id, analysed_message_id, analysed_count,
       analysed_at, updated_at
  FROM dialog_personas
 WHERE chat_id = $1
"""

# Insert only, and DO NOTHING rather than DO UPDATE: a stored Persona is never
# silently replaced, because a Persona is the one thing in this database a
# person wrote by hand and the only thing a resync cannot rebuild
# (SPEC-PSN-004). An overwrite goes through _UPDATE_PERSONA_SQL, deliberately.
# $7 is bound twice: on creation the analysis window is exactly the baseline.
_INSERT_PERSONA_SQL = """
INSERT INTO dialog_personas (chat_id, addressing, tone, relationship, notes,
                             metrics, baseline_message_id,
                             analysed_message_id, analysed_count)
VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $8)
ON CONFLICT (chat_id) DO NOTHING
RETURNING chat_id
"""

# This statement deliberately does NOT set baseline_message_id. That omission
# is the whole anti-feedback-loop guarantee (SPEC-PSN-003): every message this
# project itself sends is archived as is_outgoing, indistinguishable from
# something the account owner typed, so a Persona that could re-baseline on
# update would converge on a model of its own output within a few refreshes.
# tests/test_persona_sql.py asserts the column is absent from this text.
_UPDATE_PERSONA_SQL = """
UPDATE dialog_personas
   SET addressing = $2, tone = $3, relationship = $4, notes = $5,
       metrics = $6, analysed_message_id = $7, analysed_count = $8,
       analysed_at = now(), updated_at = now()
 WHERE chat_id = $1
RETURNING chat_id
"""

# The only statement that moves the baseline, kept separate so that every call
# site is greppable. GREATEST so it can never move backwards, as with the Sync
# Cursor.
_REBASELINE_PERSONA_SQL = """
UPDATE dialog_personas
   SET baseline_message_id = GREATEST(baseline_message_id, $2),
       analysed_message_id = GREATEST(analysed_message_id, $2),
       updated_at          = now()
 WHERE chat_id = $1
"""

_COUNT_OUTGOING_SINCE_SQL = """
SELECT count(*) FROM messages
 WHERE chat_id = $1 AND is_outgoing AND message_id > $2
"""

# The analysis sample. is_outgoing only: the counterparty's words must never
# reach the analyser, which is the structural half of the RISK-07 mitigation.
# NULL and empty text excluded - a sticker carries no style. $2 is the Persona
# Baseline floor and $3 its ceiling, so a frozen window is expressible.
_SELECT_OUTGOING_SQL = """
SELECT message_id, text, date
  FROM messages
 WHERE chat_id = $1
   AND is_outgoing
   AND text IS NOT NULL
   AND text <> ''
   AND message_id > $2
   AND ($3::BIGINT IS NULL OR message_id <= $3)
 ORDER BY message_id DESC
 LIMIT $4
"""

_MAX_OUTGOING_SQL = """
SELECT coalesce(max(message_id), 0) FROM messages
 WHERE chat_id = $1 AND is_outgoing AND text IS NOT NULL AND text <> ''
"""

# Dialog Lookup (SPEC-SRCH-006). Resolves a Target against the Archive alone,
# so a Persona tool works with the Telegram session dead, exactly as
# tg_search_local_history does.
#
# There is no string formatting here at all, unlike build_search_query: the
# numeric and phone branches are switched off by binding NULL and '' rather
# than by building a different statement, so this text cannot be made to carry
# a value under any argument.
#
# The `d.phone IS NOT NULL AND $3 <> ''` pair is load-bearing. Without it, a
# non-numeric target reduces the phone predicate to '' = '' for every row with
# no stored phone, and the lookup silently returns the entire archive.
_LOOKUP_DIALOG_SQL = r"""
SELECT d.chat_id, d.username,
       trim(coalesce(d.first_name,'') || ' ' || coalesce(d.last_name,'')) AS display_name,
       count(m.*)                              AS message_count,
       count(m.*) FILTER (WHERE m.is_outgoing) AS outgoing_count,
       (p.chat_id IS NOT NULL)                 AS has_persona
  FROM dialogs d
  LEFT JOIN messages m        ON m.chat_id = d.chat_id
  LEFT JOIN dialog_personas p ON p.chat_id = d.chat_id
 WHERE lower(d.username) = $1
    OR d.chat_id = $2
    OR (d.phone IS NOT NULL AND $3 <> ''
        AND regexp_replace(d.phone, '\D', '', 'g') = $3)
    OR lower(regexp_replace(trim(coalesce(d.first_name,'')), '\s+', ' ', 'g')) = $1
    OR lower(regexp_replace(trim(coalesce(d.last_name,'')), '\s+', ' ', 'g')) = $1
    OR lower(regexp_replace(trim(coalesce(d.first_name,'') || ' ' ||
                                 coalesce(d.last_name,'')), '\s+', ' ', 'g')) = $1
 GROUP BY d.chat_id, d.username, d.first_name, d.last_name, p.chat_id
 ORDER BY outgoing_count DESC, message_count DESC, d.chat_id
 LIMIT $4
"""

_PERSONA_OVERVIEW_SQL = """
SELECT d.chat_id, d.username,
       trim(coalesce(d.first_name,'') || ' ' || coalesce(d.last_name,'')) AS display_name,
       count(m.*)                              AS message_count,
       count(m.*) FILTER (WHERE m.is_outgoing) AS outgoing_count,
       (p.chat_id IS NOT NULL)                 AS has_persona
  FROM dialogs d
  LEFT JOIN messages m        ON m.chat_id = d.chat_id
  LEFT JOIN dialog_personas p ON p.chat_id = d.chat_id
 GROUP BY d.chat_id, d.username, d.first_name, d.last_name, p.chat_id
 ORDER BY outgoing_count DESC, message_count DESC, d.chat_id
 LIMIT $1
"""


@dataclass(frozen=True, slots=True)
class DialogRef:
    """One archived Dialog, as matched by a Dialog Lookup."""

    chat_id: int
    username: str | None
    display_name: str
    message_count: int
    outgoing_count: int
    has_persona: bool

    @property
    def label(self) -> str:
        """Human-readable handle, never empty."""
        if self.username:
            return f"@{self.username}"
        return self.display_name or f"id:{self.chat_id}"


@dataclass(frozen=True, slots=True)
class DialogPersona:
    """A stored Dialog Persona: the agent-written half and its measurements."""

    chat_id: int
    addressing: str
    tone: str
    relationship: str
    notes: str
    metrics: dict[str, Any]
    baseline_message_id: int
    analysed_message_id: int
    analysed_count: int
    analysed_at: datetime
    updated_at: datetime


def dialog_lookup_keys(target: str) -> tuple[str, int | None, str]:
    """Derive the three Dialog Lookup bind values from a Target.

    Returns:
        ``(name_key, chat_id, digits)`` - the case-folded, whitespace-collapsed
        form for username and name matching; the numeric id, or ``None`` when
        the Target is not a number; and the digits-only form for phone
        matching, or ``''``. The ``None`` and ``''`` are what switch off the
        branches they feed, so the statement itself never changes shape.

    Note:
        This repeats a few lines of ``tg_client.normalise_target`` on purpose.
        ``db`` and ``tg_client`` are peers, and AGENTS.md section 6 forbids a
        sideways import between them; ``server.py`` is where the two compose.
    """
    cleaned = " ".join((target or "").strip().lstrip("@").split()).casefold()
    if not cleaned:
        return "", None, ""

    digits = "".join(character for character in cleaned if character.isdigit())
    # A bare run of digits is a user id; a longer one that the user wrote with
    # separators or a leading + is a phone number. Both branches are offered to
    # the statement, which matches whichever the archive actually holds.
    chat_id = int(cleaned) if cleaned.isdigit() else None
    return cleaned, chat_id, digits


def build_dialog_lookup_query() -> str:
    """Return the Dialog Lookup statement (SPEC-SRCH-006).

    Exposed separately from execution so the statement's shape - exact
    equality rather than ``LIKE``, both phone guards present, a ``LIMIT`` - can
    be proven without a database, the way ``build_search_query`` already is.
    """
    return _LOOKUP_DIALOG_SQL


def _dialog_ref(record: asyncpg.Record) -> DialogRef:
    """Map one lookup or overview row, never leaking the ``Record``."""
    return DialogRef(
        chat_id=record["chat_id"],
        username=record["username"],
        display_name=(record["display_name"] or "").strip() or "(no name)",
        message_count=int(record["message_count"] or 0),
        outgoing_count=int(record["outgoing_count"] or 0),
        has_persona=bool(record["has_persona"]),
    )


async def resolve_dialog(
    pool: asyncpg.Pool, target: str, *, limit: int = 5
) -> tuple[list[DialogRef], str]:
    """Resolve a Target to archived Dialogs without touching Telegram.

    Args:
        target: ``@username``, a numeric id, a phone number, or a first, last
            or full name. Matched exactly per key, never as a substring, for
            the same reason ``SPEC-SYNC-006`` gives: ``an`` must not silently
            pull in Anna, Ivan and Alexander.
        limit: How many candidates to report when the Target is ambiguous.

    Returns:
        ``(matches, outcome)`` where outcome is ``"one"``, ``"ambiguous"`` or
        ``"none"``. The caller phrases the failure; this function never picks
        a winner, even though the ordering makes one look obvious.
    """
    name_key, chat_id, digits = dialog_lookup_keys(target)
    if not name_key:
        return [], "none"

    async with pool.acquire() as conn:
        records = await conn.fetch(_LOOKUP_DIALOG_SQL, name_key, chat_id, digits, limit)

    matches = [_dialog_ref(record) for record in records]
    if not matches:
        return [], "none"
    if len(matches) == 1:
        return matches, "one"
    return matches, "ambiguous"


async def list_persona_overview(pool: asyncpg.Pool, limit: int = 20) -> list[DialogRef]:
    """List archived Dialogs, busiest first, flagging which have a Persona."""
    async with pool.acquire() as conn:
        records = await conn.fetch(_PERSONA_OVERVIEW_SQL, limit)
    return [_dialog_ref(record) for record in records]


async def get_persona(pool: asyncpg.Pool, chat_id: int) -> DialogPersona | None:
    """Return the stored Dialog Persona, or ``None`` when none was written."""
    async with pool.acquire() as conn:
        record = await conn.fetchrow(_SELECT_PERSONA_SQL, chat_id)
    if record is None:
        return None

    # A corrupt or hand-edited metrics blob must degrade to "no measurements",
    # never to an exception: this is read on the drafting path, and rule 4 says
    # a tool returns text under all conditions.
    metrics = record["metrics"]
    if not isinstance(metrics, dict):
        log.warning("persona %s has a non-object metrics value; ignoring it", chat_id)
        metrics = {}

    return DialogPersona(
        chat_id=record["chat_id"],
        addressing=record["addressing"],
        tone=record["tone"],
        relationship=record["relationship"],
        notes=record["notes"] or "",
        metrics=metrics,
        baseline_message_id=int(record["baseline_message_id"]),
        analysed_message_id=int(record["analysed_message_id"]),
        analysed_count=int(record["analysed_count"]),
        analysed_at=record["analysed_at"],
        updated_at=record["updated_at"],
    )


async def insert_persona(
    pool: asyncpg.Pool,
    *,
    chat_id: int,
    addressing: str,
    tone: str,
    relationship: str,
    notes: str,
    metrics: dict[str, Any],
    baseline_message_id: int,
    analysed_count: int,
) -> bool:
    """Create a Dialog Persona. Returns ``False`` when one already existed."""
    async with pool.acquire() as conn:
        created = await conn.fetchval(
            _INSERT_PERSONA_SQL,
            chat_id,
            addressing,
            tone,
            relationship,
            notes,
            metrics,
            baseline_message_id,
            analysed_count,
        )
    return created is not None


async def update_persona(
    pool: asyncpg.Pool,
    *,
    chat_id: int,
    addressing: str,
    tone: str,
    relationship: str,
    notes: str,
    metrics: dict[str, Any],
    analysed_message_id: int,
    analysed_count: int,
) -> bool:
    """Replace a Dialog Persona's prose and measurements.

    Never touches ``baseline_message_id``: see :data:`_UPDATE_PERSONA_SQL`.
    Returns ``False`` when there was no row to update.
    """
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            _UPDATE_PERSONA_SQL,
            chat_id,
            addressing,
            tone,
            relationship,
            notes,
            metrics,
            analysed_message_id,
            analysed_count,
        )
    return updated is not None


async def rebaseline_persona(pool: asyncpg.Pool, chat_id: int, baseline_message_id: int) -> None:
    """Move a Persona Baseline forward. The only place that may (SPEC-PSN-003)."""
    async with pool.acquire() as conn:
        await conn.execute(_REBASELINE_PERSONA_SQL, chat_id, baseline_message_id)
    log.info("persona baseline for %s moved to %s", chat_id, baseline_message_id)


async def count_outgoing_since(pool: asyncpg.Pool, chat_id: int, message_id: int) -> int:
    """Count the account's own archived messages above ``message_id``.

    A count, not a subtraction of ids: message ids have gaps from deletions and
    service messages, so ``max_id - analysed_id`` is not a message count.
    """
    async with pool.acquire() as conn:
        value = await conn.fetchval(_COUNT_OUTGOING_SINCE_SQL, chat_id, message_id)
    return int(value or 0)


async def max_outgoing_message_id(pool: asyncpg.Pool, chat_id: int) -> int:
    """Highest archived outgoing message id with text, or 0."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(_MAX_OUTGOING_SQL, chat_id)
    return int(value or 0)


async def fetch_outgoing_sample(
    pool: asyncpg.Pool,
    chat_id: int,
    *,
    after_message_id: int = 0,
    until_message_id: int | None = None,
    limit: int = persona.DEFAULT_SAMPLE_LIMIT,
) -> list[persona.OutgoingSample]:
    """Read the account's own messages in one Dialog, newest first.

    Only ``is_outgoing`` rows with real text are returned (SPEC-PSN-002).
    ``until_message_id`` is the Persona Baseline ceiling: passing it freezes
    the analysis window so messages archived later cannot enter it.
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(
            _SELECT_OUTGOING_SQL, chat_id, after_message_id, until_message_id, limit
        )
    return [
        persona.OutgoingSample(
            message_id=record["message_id"],
            text=record["text"],
            date=record["date"],
        )
        for record in records
    ]
