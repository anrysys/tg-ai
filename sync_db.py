#!/usr/bin/env python3
"""Dump private Telegram history into the local PostgreSQL archive.

The archive is what makes ``tg_search_local_history`` instant: searching years
of conversation locally costs milliseconds and zero Telegram API calls.

The sync is resumable. Each dialog carries a cursor
(``dialogs.last_synced_message_id``) advanced only after its rows are
committed, so an interrupted or flood-waited run continues where it stopped
rather than starting over (SPEC-SYNC-003).

A full run walks every private dialog, which on an account with hundreds of
them is slow and accumulates flood waits. ``--targets`` restricts the run to a
named set of people so the ones that matter are archived first (SPEC-SYNC-006).

Usage:
    just tg-sync-full                      # first run: everything
    just tg-sync                           # afterwards: only what is new
    just tg-sync-targets @anna @bob        # only these people
    just tg-sync -- --dialog @someone --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

import asyncpg
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from tg_ai import db
from tg_ai.config import Config, ConfigError, load_config
from tg_ai.safety import SYNC_DIALOG_DELAY_SECONDS
from tg_ai.tg_client import (
    PeerIndex,
    build_client,
    clone_session,
    is_archivable,
    matches_target,
    peer_label,
    resolve_peer,
)

log = logging.getLogger("tg_ai.sync")

#: How many times one dialog may be retried after a flood wait before it is
#: skipped. Its cursor survives, so the next run picks it up again.
MAX_FLOOD_RETRIES = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore the stored cursors and walk every dialog from its first message.",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--dialog",
        metavar="TARGET",
        help=(
            "Sync only this peer (@username, phone or numeric id). Resolves "
            "through the Telegram API, so it reaches a peer with no dialog yet."
        ),
    )
    selection.add_argument(
        "--targets",
        nargs="+",
        metavar="TARGET",
        help=(
            "Sync only these people. Each target matches a username (with or "
            "without @), a phone number, a numeric id, or a first, last or "
            "full name. Filters the existing dialog list, so no peer is "
            "resolved through the API. Targets that match nothing are reported."
        ),
    )
    parser.add_argument("--limit", type=int, help="Stop after this many messages per dialog.")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Archive only messages sent on or after this date.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Use the primary session file instead of a clone. Only safe when "
            "the MCP server is not running (ADR-0004)."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Log every batch.")
    return parser.parse_args(argv)


def parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise SystemExit(f"--since must be YYYY-MM-DD, got {raw!r}") from exc


async def sync_dialog(
    client: TelegramClient,
    pool: asyncpg.Pool,
    user: User,
    *,
    start_cursor: int,
    limit: int | None,
    since: datetime | None,
) -> tuple[int, int]:
    """Archive one dialog from ``start_cursor`` onward.

    Returns:
        ``(rows_added, final_cursor)``.
    """
    added = 0
    cursor = start_cursor
    retries = 0

    while True:
        buffer: list[tuple] = []
        try:
            kwargs: dict = {"reverse": True, "min_id": cursor}
            if since is not None:
                kwargs["offset_date"] = since
            if limit is not None:
                kwargs["limit"] = limit

            async for message in client.iter_messages(user, **kwargs):
                buffer.append(
                    (
                        user.id,
                        message.id,
                        message.sender_id,
                        message.message or None,
                        message.date,
                        bool(message.out),
                    )
                )
                if len(buffer) >= db.INSERT_BATCH_SIZE:
                    added += await db.insert_messages(pool, buffer)
                    cursor = max(cursor, buffer[-1][1])
                    await db.advance_cursor(pool, user.id, cursor)
                    log.debug("  batch committed, cursor=%s", cursor)
                    buffer = []
            break

        except FloodWaitError as exc:
            # Commit what we already hold so the wait costs us no progress.
            if buffer:
                added += await db.insert_messages(pool, buffer)
                cursor = max(cursor, buffer[-1][1])
                await db.advance_cursor(pool, user.id, cursor)
            retries += 1
            if retries > MAX_FLOOD_RETRIES:
                log.warning(
                    "  giving up on %s after %d flood waits; rerun to continue",
                    peer_label(user),
                    MAX_FLOOD_RETRIES,
                )
                return added, cursor
            wait = exc.seconds + 5
            log.warning("  Telegram flood wait: sleeping %ds, then resuming at %s", wait, cursor)
            await asyncio.sleep(wait)

    if buffer:
        added += await db.insert_messages(pool, buffer)
        cursor = max(cursor, buffer[-1][1])

    await db.advance_cursor(pool, user.id, cursor)
    return added, cursor


def select_by_targets(users: list[User], targets: list[str]) -> tuple[list[User], list[str]]:
    """Filter archivable dialogs down to the requested people (SPEC-SYNC-006).

    Returns:
        ``(selected, unmatched)`` - the dialogs to sync, in their original
        newest-active order, and the targets that matched nothing. Unmatched
        targets are returned rather than ignored because a typo would otherwise
        look identical to a person having no dialog, and the user would wait
        for a sync that was never going to include them.
    """
    selected: list[User] = []
    unmatched: list[str] = []

    for target in targets:
        hits = [user for user in users if matches_target(user, target)]
        if not hits:
            unmatched.append(target)
            continue
        for user in hits:
            if user not in selected:
                selected.append(user)

    # Preserve the newest-active ordering of the dialog list rather than the
    # order the targets happened to be typed in.
    order = {id(user): position for position, user in enumerate(users)}
    selected.sort(key=lambda user: order[id(user)])
    return selected, unmatched


async def collect_targets(
    client: TelegramClient,
    index: PeerIndex,
    config: Config,
    only: str | None,
    targets: list[str] | None = None,
) -> list[User]:
    """Return the dialogs to sync, newest-active first.

    ``only`` resolves one peer through the API. ``targets`` filters the
    account's existing dialogs, which costs no cold ``ResolveUsername`` call
    (SPEC-SND-006) and is the fast path for a first, partial backfill.
    """
    if only:
        user, _ = await resolve_peer(client, index, only)
        return [user]

    users: list[User] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if is_archivable(entity, include_bots=config.sync_include_bots):
            users.append(entity)

    if not targets:
        return users

    selected, unmatched = select_by_targets(users, targets)
    if unmatched:
        log.warning(
            "No private dialog matched: %s. Check the spelling, or use "
            "--dialog to reach someone you have never messaged.",
            ", ".join(unmatched),
        )
    return selected


async def run(args: argparse.Namespace, config: Config) -> int:
    session_base = config.session_path if args.in_place else clone_session(config)
    since = parse_since(args.since)

    pool = await db.create_pool(config.database_url)
    client = build_client(config, session_base=session_base)

    try:
        await db.ensure_schema(pool)
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Session is not authorised. Run `just tg-auth` first.")
            return 1

        me = await client.get_me()
        log.info("Syncing history for %s", peer_label(me))

        index = PeerIndex(client)
        targets = await collect_targets(client, index, config, args.dialog, args.targets)
        if args.targets and not targets:
            log.error("None of the requested targets has a private dialog. Nothing to sync.")
            return 1
        contact_ids = await index.contact_ids()
        log.info("%d private dialog(s) to process", len(targets))

        total_added = 0
        for position, user in enumerate(targets, start=1):
            await db.upsert_dialog(
                pool,
                chat_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                phone=user.phone,
                is_contact=user.id in contact_ids,
            )

            start_cursor = 0 if args.full else await db.get_cursor(pool, user.id)
            added, cursor = await sync_dialog(
                client, pool, user, start_cursor=start_cursor, limit=args.limit, since=since
            )
            total_added += added
            log.info(
                "[%d/%d] %s  +%d msgs  (cursor %s)",
                position,
                len(targets),
                peer_label(user),
                added,
                cursor,
            )

            # Pace the walk: dozens of history requests per second is a
            # scripted-account signal even though nothing is being sent.
            if position < len(targets):
                await asyncio.sleep(SYNC_DIALOG_DELAY_SECONDS)

        stats = await db.archive_stats(pool)
        log.info(
            "Done. Added %d new message(s). Archive now holds %s messages across %s dialogs.",
            total_added,
            stats.get("messages"),
            stats.get("dialogs"),
        )
        return 0

    finally:
        if client.is_connected():
            await client.disconnect()
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)

    try:
        config = load_config()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    try:
        return asyncio.run(run(args, config))
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted. Progress is saved; rerun `just tg-sync` to continue.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
