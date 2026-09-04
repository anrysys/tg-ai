#!/usr/bin/env python3
"""tg-ai MCP server: the AI agent's interface to a personal Telegram account.

Transport is stdio, so **stdout belongs to the protocol**. Every diagnostic
goes to stderr; a stray ``print`` would corrupt the session.

Two invariants hold for every tool in this file:

1. It returns a string. It never raises. An exception escaping a handler can
   take down the server mid-conversation and leave the agent blind, so
   failures come back as ``ERROR:`` / ``WARNING:`` text the agent can read
   and act on (SPEC-SND-004).
2. It never sends a message to a peer the account has no relationship with.
   That is the single fastest way to get a personal account banned
   (SPEC-SND-001, ADR-0005).

Usage:
    just tg-serve            # manual smoke test
    just mcp-add             # register with Claude Code
"""

# NOTE: no `from __future__ import annotations` here on purpose. FastMCP
# inspects tool signatures at import time and cannot resolve string
# annotations, so tool parameters must be real runtime objects.

import logging
import sys

import asyncpg
from mcp.server.fastmcp import FastMCP
from telethon import TelegramClient
from telethon.tl.functions.contacts import AddContactRequest, ImportContactsRequest
from telethon.tl.types import InputPhoneContact, User

from tg_ai import db
from tg_ai.config import Config, ConfigError, load_config
from tg_ai.formatting import (
    render_conversation,
    render_search_hits,
    render_unread,
    timestamp,
)
from tg_ai.safety import (
    CHUNK_DELAY_SECONDS,
    MAX_CHUNK_CHARS,
    ToolError,
    guarded_tool,
    sleep_between_chunks,
    split_message,
)
from tg_ai.tg_client import (
    PeerIndex,
    build_client,
    has_conversation,
    peer_label,
    resolve_peer,
)

# stderr only: stdout is the MCP channel.
logging.basicConfig(
    level=logging.INFO, format="[tg-ai] %(levelname)s %(message)s", stream=sys.stderr
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("tg_ai.server")

mcp = FastMCP("tg-ai")

_config: Config | None = None
_client: TelegramClient | None = None
_index: PeerIndex | None = None
_pool: asyncpg.Pool | None = None


def config() -> Config:
    """Load configuration once per process.

    Raises:
        ToolError: The environment is missing or malformed. Translated here
            because ``tg_ai.config`` is the lowest layer and must not depend on
            the tool vocabulary.
    """
    global _config
    if _config is None:
        try:
            # Telegram credentials are validated in telegram(), not here, so
            # that tg_search_local_history keeps working on a machine where
            # only the database is configured (SPEC-SRCH-001).
            _config = load_config(require_telegram=False)
        except ConfigError as exc:
            raise ToolError(f"{exc}") from exc
    return _config


async def telegram() -> tuple[TelegramClient, PeerIndex]:
    """Return the shared, connected Telethon client and its peer index.

    Connection is lazy so that a server registered but never used costs
    nothing, and so a database-only tool works even if Telegram is down.

    Raises:
        ToolError: The session is missing or no longer authorised.
    """
    global _client, _index
    if _client is None:
        cfg = config()
        if not cfg.api_id or not cfg.api_hash:
            raise ToolError(
                "TG_API_ID and TG_API_HASH are not set. Create them at "
                "https://my.telegram.org (API development tools) and put them "
                "in .env, then run `just tg-auth`."
            )
        if not cfg.session_file.exists():
            raise ToolError(
                f"No Telegram session at {cfg.session_file}. Run `just tg-auth` "
                "in a terminal - login needs an SMS code and cannot happen here."
            )
        _client = build_client(cfg)
        _index = PeerIndex(_client)

    if not _client.is_connected():
        await _client.connect()

    if not await _client.is_user_authorized():
        raise ToolError(
            "The Telegram session exists but is not authorised - it was likely "
            "revoked. Run `just tg-auth` in a terminal to sign in again."
        )

    assert _index is not None
    return _client, _index


async def database() -> asyncpg.Pool:
    """Return the shared asyncpg pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = await db.create_pool(config().database_url)
        await db.ensure_schema(_pool)
    return _pool


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
@guarded_tool
async def tg_send_message(target: str, message: str) -> str:
    """Send a Telegram message from the user's own account.

    Refuses to write to anyone the account has no history with and who is not
    a saved contact, because Telegram treats that as spam and bans personal
    accounts for it. Long text is split at sentence boundaries into chunks
    under Telegram's 4096-character limit and sent with a pause between them.

    Args:
        target: Recipient - "@username", a phone number in international
            format, a numeric user id, or "me" for Saved Messages.
        message: The text to send. Any length; it will be split if needed.

    Returns:
        A confirmation naming the recipient and chunk count, or a WARNING
        explaining what must happen first, or an ERROR describing the failure.
    """
    body = (message or "").strip()
    if not body:
        return "ERROR: message is empty - nothing was sent."

    client, index = await telegram()
    user, known_locally = await resolve_peer(client, index, target)
    label = peer_label(user)
    me = await client.get_me()

    # --- Send guard (SPEC-SND-001) ----------------------------------------
    # Saved Messages is always safe: the account is writing to itself.
    # Conditions are ordered cheapest-first and short-circuit, so a peer we
    # already know costs no extra API call at all.
    is_stranger = (
        user.id != me.id
        and not known_locally
        and not await has_conversation(client, user)
        and user.id not in await index.contact_ids()
    )
    if is_stranger:
        return (
            f"WARNING: Nothing was sent. There is no prior conversation with "
            f"{label} and they are not in your contacts. Messaging a stranger "
            "from a personal account triggers Telegram's anti-spam system "
            "(PeerFloodError) and can get the account limited or banned.\n\n"
            "To proceed deliberately, call:\n"
            f'  tg_add_contact(phone_or_username="{target}", first_name="...")\n'
            "and then retry this send. If you cannot add them, ask the user "
            "how they want to reach this person."
        )

    chunks = split_message(body, MAX_CHUNK_CHARS)
    sent = 0
    try:
        for position, chunk in enumerate(chunks):
            if position:
                # Pacing matters more than latency: several messages per
                # second to one peer is a spam signal (SPEC-SND-003).
                await sleep_between_chunks()
            await client.send_message(user, chunk)
            sent += 1
    except Exception:
        if sent:
            log.warning("partial send to %s: %d/%d chunks", label, sent, len(chunks))
        raise

    if len(chunks) == 1:
        return f"Sent to {label} ({len(body)} characters)."
    return (
        f"Sent to {label} in {len(chunks)} parts ({len(body)} characters total, "
        f"{CHUNK_DELAY_SECONDS}s between parts)."
    )


@mcp.tool()
@guarded_tool
async def tg_get_recent_messages(target: str, limit: int = 10) -> str:
    """Read the latest messages exchanged with one person, live from Telegram.

    Use this to check what someone replied. It reads the live account, not the
    local archive, so it sees messages that arrived seconds ago.

    Args:
        target: "@username", phone number, numeric user id, or "me".
        limit: How many recent messages to fetch (1-100).

    Returns:
        One line per message, oldest first, each tagged [me] or [them].
    """
    limit = max(1, min(int(limit), 100))
    client, index = await telegram()
    user, _ = await resolve_peer(client, index, target)

    messages = await client.get_messages(user, limit=limit)
    # Telethon returns newest-first; conversations read better oldest-first.
    ordered = list(reversed(messages))
    header = f"Last {len(ordered)} message(s) with {peer_label(user)}:"
    return render_conversation(ordered, header=header)


@mcp.tool()
@guarded_tool
async def tg_get_unread_dialogs(limit: int = 5) -> str:
    """List the people who have sent unread messages.

    Answers "do I have new messages?" without opening Telegram. Only 1-on-1
    chats with people are considered; groups, channels and bots are ignored.

    Args:
        limit: Maximum number of dialogs to report (1-50).

    Returns:
        One line per dialog: handle, unread count, and a preview of the last
        message. Reading this does not mark anything as read.
    """
    limit = max(1, min(int(limit), 50))
    client, _ = await telegram()
    cfg = config()

    entries: list[dict] = []
    async for dialog in client.iter_dialogs():
        if len(entries) >= limit:
            break
        entity = dialog.entity
        if not isinstance(entity, User) or entity.deleted:
            continue
        if entity.bot and not cfg.sync_include_bots:
            continue
        if dialog.unread_count <= 0:
            continue
        entries.append(
            {
                "username": entity.username,
                "name": peer_label(entity),
                "unread": dialog.unread_count,
                "date": dialog.date,
                "text": dialog.message.message if dialog.message else None,
            }
        )

    return render_unread(entries)


@mcp.tool()
@guarded_tool
async def tg_search_local_history(
    query: str, target_username: str | None = None, limit: int = 50
) -> str:
    """Search the entire archived chat history in the local database.

    This is the tool for questions like "what did we agree about X?" or "find
    the address someone sent me last year". It queries PostgreSQL and makes no
    Telegram API calls at all, so it is instant and carries no ban risk.

    The archive is filled by `just tg-sync`. Messages newer than the last sync
    are not in it - use tg_get_recent_messages for those.

    Args:
        query: Words to look for. Supports quoted phrases, OR and -exclusion.
            Falls back to plain substring matching when nothing matches.
        target_username: Restrict the search to one person's chat, without "@".
        limit: Maximum matches to return (1-500).

    Returns:
        One line per match: timestamp, whose chat, who wrote it, and the text.
    """
    text = (query or "").strip()
    if not text:
        return "ERROR: query is empty - nothing to search for."

    limit = max(1, min(int(limit), 500))
    pool = await database()
    hits, strategy = await db.search_messages(
        pool, text, target_username=target_username, limit=limit
    )
    return render_search_hits(hits, query=text, strategy=strategy)


@mcp.tool()
@guarded_tool
async def tg_add_contact(phone_or_username: str, first_name: str, last_name: str = "") -> str:
    """Add someone to the account's Telegram contacts.

    This is the sanctioned way to unblock tg_send_message for a person the
    account has never spoken to. A message to a saved contact is an ordinary
    action; the same message to a stranger is what Telegram's anti-spam system
    punishes.

    Ask the user before adding someone they did not name.

    Args:
        phone_or_username: "@username", or a phone number in international
            format such as "+380501234567".
        first_name: Name to save the contact under. Required by Telegram.
        last_name: Optional surname.

    Returns:
        Confirmation, or an ERROR explaining why the contact could not be added.
    """
    handle = (phone_or_username or "").strip()
    if not handle:
        return "ERROR: phone_or_username is empty."
    if not (first_name or "").strip():
        return "ERROR: first_name is required - Telegram will not save a nameless contact."

    client, index = await telegram()

    digits_only = handle.replace(" ", "").lstrip("+").isdigit()
    if handle.startswith("+") or (digits_only and len(handle) > 9):
        # Phone numbers go through the import API, which also tells us whether
        # that number is on Telegram at all.
        result = await client(
            ImportContactsRequest(
                contacts=[
                    InputPhoneContact(
                        client_id=0,
                        phone=handle,
                        first_name=first_name,
                        last_name=last_name or "",
                    )
                ]
            )
        )
        if not result.users:
            return (
                f"ERROR: {handle} is not registered on Telegram, or its privacy "
                "settings hide it from contact import. Nothing was added."
            )
        index.invalidate()
        return f"Added {peer_label(result.users[0])} to contacts. tg_send_message will now work."

    user, _ = await resolve_peer(client, index, handle)
    await client(
        AddContactRequest(
            id=user,
            first_name=first_name,
            last_name=last_name or "",
            phone="",
            add_phone_privacy_exception=False,
        )
    )
    index.invalidate()
    return f"Added {peer_label(user)} to contacts. tg_send_message will now work."


@mcp.tool()
@guarded_tool
async def tg_whoami() -> str:
    """Report the health of this server: account, session, database, archive.

    Run this first when anything behaves unexpectedly. It distinguishes a
    broken session from an empty archive from an unreachable database.

    Returns:
        A short status report.
    """
    lines: list[str] = []

    try:
        cfg = config()
    except ToolError as exc:
        return f"Configuration: BROKEN - {exc}"

    try:
        client, _ = await telegram()
        me = await client.get_me()
        lines.append(f"Account:  {peer_label(me)} (id {me.id})")
        if cfg.expected_username and (me.username or "").lower() != cfg.expected_username.lower():
            lines.append(
                f"WARNING:  expected @{cfg.expected_username} - this is a different account."
            )
    except Exception as exc:  # noqa: BLE001 - a health check reports, never fails
        lines.append(f"Account:  UNAVAILABLE - {exc}")

    lines.append(f"Session:  {cfg.session_file}")

    try:
        pool = await database()
        stats = await db.archive_stats(pool)
        lines.append(f"Database: reachable ({cfg.database_url.rsplit('@', 1)[-1]})")
        lines.append(
            f"Archive:  {stats.get('messages', 0)} messages across "
            f"{stats.get('dialogs', 0)} dialogs"
        )
        if stats.get("oldest"):
            lines.append(f"Covering: {timestamp(stats['oldest'])} .. {timestamp(stats['newest'])}")
        lines.append(f"Last sync: {timestamp(stats.get('last_sync'))}")
        if not stats.get("messages"):
            lines.append("HINT:     the archive is empty - run `just tg-sync-full`.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Database: UNREACHABLE - {exc}")
        lines.append("HINT:     start it with `just db-up`.")

    return "\n".join(lines)


def main() -> None:
    try:
        config()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc
    log.info("tg-ai MCP server starting on stdio")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
