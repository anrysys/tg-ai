#!/usr/bin/env python3
"""Interactive Telegram login. Run this once, in a real terminal.

An MCP server speaks stdio: its stdin and stdout belong to the protocol, so
it can never prompt for an SMS code. Login is therefore a separate process
(ADR-0002). It produces ``tg_session.session``, which every other entrypoint
reuses.

Usage:
    just tg-auth
"""

from __future__ import annotations

import asyncio
import sys

from telethon import TelegramClient

from tg_ai.config import ConfigError, load_config
from tg_ai.tg_client import (
    SessionLock,
    SessionLocked,
    build_client,
    peer_label,
    secure_session_file,
)


async def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"Session file: {config.session_file}")
    if config.session_file.exists():
        print("A session already exists. Logging in again will reuse it if it is valid.")

    # Logging in is still a connection on this authorization key, so it
    # contends for the same lock as the server and the sync (ADR-0010).
    lock = SessionLock(config.session_lock_file, "a login")
    try:
        lock.acquire()
    except SessionLocked as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    client: TelegramClient = build_client(config)
    try:
        # start() drives the phone -> code -> 2FA password prompts itself.
        await client.start()
        me = await client.get_me()
    except Exception as exc:  # noqa: BLE001 - this is the top-level CLI boundary
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if client.is_connected():
            await client.disconnect()
        lock.release()

    secure_session_file(config.session_file)

    print()
    print(f"Logged in as {peer_label(me)}")
    print(f"Session written to {config.session_file} (permissions 0600)")

    if config.expected_username and (me.username or "").lower() != config.expected_username.lower():
        print(
            f"\nWARNING: expected @{config.expected_username} but logged in as "
            f"@{me.username or 'no-username'}. If this is wrong, delete the session "
            "file and run `just tg-auth` again.",
            file=sys.stderr,
        )

    print("\nThe session file is a full credential for this account.")
    print("It is git-ignored. Never copy it anywhere. See docs/70-ops/security.md.")
    print("\nNext: `just db-up` then `just tg-sync-full`")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
