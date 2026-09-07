#!/usr/bin/env python3
"""Report or clear the Telegram safety kill switch (SPEC-LIM-003).

Clearing is deliberately a separate, manual command rather than anything the
agent or the sync can do. The switch trips because Telegram has been telling
this account to slow down, and the only way to know the account is healthy
again is for a person to check it - by messaging @SpamBot from the official
Telegram app, and waiting.

Usage:
    just tg-killswitch          # report
    just tg-killswitch-clear    # clear, after confirming
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

# This lives in scripts/ but composes the project's own modules, so the repo
# root has to be importable whether it is run through `just` or directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tg_ai import db
from tg_ai.config import ConfigError, load_config
from tg_ai.safety import kill_switch_message


async def run(clear: bool) -> int:
    try:
        config = load_config(require_telegram=False)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    pool = await db.create_pool(config.database_url)
    try:
        await db.ensure_schema(pool)
        ledger = db.PostgresRpcLedger(pool)
        switch = await ledger.active_kill_switch()

        if switch is None:
            print("Kill switch: OFF. Nothing to clear.")
            return 0

        tripped_at, reason, expires_at = switch
        message = kill_switch_message(
            tripped_at=tripped_at, reason=reason, expires_at=expires_at, now=datetime.now(UTC)
        )
        if message is None:
            print("Kill switch: OFF (the last trip has lapsed).")
            return 0

        print(message)

        recent = await ledger.count_floods(3600)
        print(f"\nFlood events in the last hour: {recent}")

        if not clear:
            print("\nRun `just tg-killswitch-clear` to clear it, once the account is known good.")
            return 0

        if not sys.stdin.isatty():
            print(
                "\nAborted: clearing the kill switch needs an interactive terminal. "
                "A piped confirmation is not a decision.",
                file=sys.stderr,
            )
            return 1

        print(
            "\nBefore clearing, check the account from the official Telegram app:\n"
            "  1. Message @SpamBot and read what it says.\n"
            "  2. If it reports a limitation, WAIT. Do not automate an appeal -\n"
            "     automated appeal mail is itself a documented ban trigger.\n"
            "  3. Clear this only when @SpamBot says the account is free.\n"
        )
        reply = input("Type CLEAR to proceed, anything else to abort: ").strip()
        if reply != "CLEAR":
            print("Aborted. The kill switch is still on.")
            return 1

        cleared = await ledger.clear_kill_switch("manual, via just tg-killswitch-clear")
        print(f"Cleared {cleared} active trip(s). Telegram access is enabled again.")
        return 0
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="Clear the switch after confirming.")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.clear))


if __name__ == "__main__":
    raise SystemExit(main())
