"""Anti-spam and anti-ban primitives.

This module is deliberately pure: no network, no database, no Telethon client.
Everything here is unit-testable, because these rules are the difference
between a working account and a banned one.

Specified by SPEC-SND-002 (chunking), SPEC-SND-003 (pacing) and
SPEC-SND-004 (error translation) in ``docs/10-product/srs.md``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import datetime
from datetime import time as dt_time
from typing import ParamSpec

from telethon import errors

log = logging.getLogger(__name__)

#: Telegram rejects messages above 4096 characters. We stop at 4000 so that
#: any formatting the client adds cannot push a chunk over the hard limit.
MAX_CHUNK_CHARS = 4000

#: Seconds to wait between consecutive chunks of one logical message.
#: Sending several messages per second to the same peer is a spam signal.
CHUNK_DELAY_SECONDS = 2.5

#: Seconds to wait between dialogs while dumping history.
SYNC_DIALOG_DELAY_SECONDS = 1.0

#: Seconds to wait between two group or channel reads. Longer than the private
#: equivalent because jumping between channels faster than a human can click
#: is what anti-bot heuristics look for.
CHANNEL_SYNC_DELAY_SECONDS = 15.0

#: Minimum gap between any two RPCs, of any kind, from any process. Telegram's
#: limits are per account across all methods, so the pacing has to be global
#: rather than per call site (SPEC-LIM-001).
MIN_RPC_GAP_SECONDS = 1.5

#: Rolling RPC budgets. Deliberately far below anything Telegram rate-limits:
#: the point is not to stay under the limit, it is to keep the account's daily
#: volume in the range a person produces. A first full backfill can exhaust the
#: daily budget and resume tomorrow - the Sync Cursor makes that lossless, so it
#: costs patience rather than data (SPEC-LIM-002).
RPC_BUDGET_PER_HOUR = 60
RPC_BUDGET_PER_DAY = 500

#: Group and channel volume caps (SPEC-SYNC-007, SPEC-RCV-003).
GROUP_FETCH_LIMIT = 100
GROUP_TARGETS_PER_RUN = 5
GROUP_READS_PER_DAY = 20
GROUP_READ_COOLDOWN_SECONDS = 300

#: Flood events inside FLOOD_TRIP_WINDOW_SECONDS that trip the kill switch, and
#: how long a trip lasts. Frequency is what feeds Telegram's risk score, so the
#: count matters more than any single wait (SPEC-LIM-003).
FLOOD_TRIP_COUNT = 3
FLOOD_TRIP_WINDOW_SECONDS = 3600
KILL_SWITCH_COOLDOWN_SECONDS = 24 * 3600

#: Scope names in `api_call_log`. Separate scopes because they are counted
#: against different budgets: every RPC, versus the far scarcer group read.
RPC_SCOPE = "rpc"
GROUP_READ_SCOPE = "group_read"

#: Absolute ceiling on Telegram-touching tool calls in one server process. This
#: is the backstop for a runaway agent loop, which is a different failure from
#: the rolling budgets: an LLM will call a tool as fast as the tool permits
#: (SPEC-LIM-007).
MAX_TELEGRAM_TOOL_CALLS = 200


def jittered(delay: float) -> float:
    """Add non-negative jitter to a reviewed delay (SPEC-LIM-005).

    Fixed, perfectly repeating intervals are a bot-detection signal, so every
    delay in this project is jittered. The jitter is **additive only**.

    A symmetric formula such as ``delay * (1 + random.uniform(-0.25, 0.25))``
    would be wrong here: it returns less than ``delay`` half the time, which is
    exactly the "lowering CHUNK_DELAY_SECONDS / SYNC_DIALOG_DELAY_SECONDS" that
    AGENTS.md forbids. The reviewed constant is a floor, not a midpoint.
    """
    if delay <= 0:
        return 0.0
    return delay + random.uniform(0, 0.5 * delay)


def in_quiet_window(moment: dt_time, start: dt_time, end: dt_time) -> bool:
    """Whether ``moment`` falls inside the configured quiet window.

    The window is allowed to wrap midnight, which is the normal case for a
    sleeping human: ``23:00-07:00`` is one window, not two.
    """
    if start <= end:
        return start <= moment < end
    return moment >= start or moment < end


def quiet_window_message(start: dt_time, end: dt_time) -> str:
    """The refusal an agent sees during the quiet window (SPEC-LIM-004)."""
    return (
        f"Outside the configured activity window (TG_QUIET_HOURS, "
        f"{start:%H:%M}-{end:%H:%M} local). Nothing was requested from "
        "Telegram. Tools that read only the local archive still work - "
        "tg_search_local_history and the persona tools are unaffected."
    )


def budget_message(hourly_used: int, daily_used: int) -> str | None:
    """Why this RPC must not happen, or ``None`` when there is headroom.

    Returns a message rather than sleeping. Queueing behind an exhausted budget
    would turn a volume cap into a delay, and the whole point is that the calls
    do not happen at all (SPEC-LIM-002).
    """
    if daily_used >= RPC_BUDGET_PER_DAY:
        return (
            f"Daily Telegram request budget exhausted ({daily_used}/"
            f"{RPC_BUDGET_PER_DAY} in the last 24 hours). It refills gradually "
            "as the oldest calls age out; nothing was requested. This budget "
            "exists to keep the account's daily volume in a human range."
        )
    if hourly_used >= RPC_BUDGET_PER_HOUR:
        return (
            f"Hourly Telegram request budget exhausted ({hourly_used}/"
            f"{RPC_BUDGET_PER_HOUR} in the last hour). It refills gradually as "
            "the oldest calls age out; nothing was requested. Do not poll - "
            "wait, or use tg_search_local_history, which never touches Telegram."
        )
    return None


#: telethon 1.36.0 defines no FloodPremiumWaitError - there is no FLOOD_PREMIUM
#: string anywhere in the package - so it is looked up rather than imported. A
#: later Telethon that adds the class is picked up automatically, and until then
#: the wire string below catches it. A premium flood wait that went uncounted
#: would silently weaken the kill switch, which is the one control that must not
#: have a blind spot.
_FLOOD_PREMIUM_ERROR = getattr(errors, "FloodPremiumWaitError", None)

_FLOOD_ERROR_TYPES: tuple[type[BaseException], ...] = tuple(
    error
    for error in (errors.FloodWaitError, errors.SlowModeWaitError, _FLOOD_PREMIUM_ERROR)
    if error is not None
)


def is_flood_error(exc: BaseException) -> bool:
    """Whether this exception counts toward the kill switch (SPEC-LIM-003).

    Covers flood waits, slow-mode waits and premium flood waits. ``PeerFloodError``
    is deliberately excluded: it is an escalation handled separately, not one
    more data point in a rolling count.
    """
    if isinstance(exc, errors.PeerFloodError):
        return False
    if _FLOOD_ERROR_TYPES and isinstance(exc, _FLOOD_ERROR_TYPES):
        return True
    return "FLOOD_PREMIUM_WAIT" in str(exc)


def flood_trips_kill_switch(recent_flood_count: int) -> bool:
    """Whether this many flood events in the rolling window trips the switch."""
    return recent_flood_count >= FLOOD_TRIP_COUNT


def kill_switch_message(
    *, tripped_at: datetime, reason: str, expires_at: datetime | None, now: datetime
) -> str | None:
    """The refusal for an active kill switch, or ``None`` when it has lapsed.

    ``expires_at is None`` means indefinite: a ``PeerFloodError`` is an
    escalation rather than a cooldown, and only the account owner clears it.
    """
    if expires_at is None:
        return (
            "Telegram safety kill switch is ON, indefinitely, since "
            f"{tripped_at:%Y-%m-%d %H:%M} UTC: {reason}. This is an escalation, "
            "not a cooldown - it does not expire on its own. Check the account "
            "by messaging @SpamBot from the official Telegram app, and do not "
            "automate an appeal. Clear it deliberately with "
            "`just tg-killswitch-clear` once the account is known good."
        )
    if now >= expires_at:
        return None
    remaining = int((expires_at - now).total_seconds() // 60)
    return (
        f"Telegram safety kill switch is ON since {tripped_at:%Y-%m-%d %H:%M} "
        f"UTC: {reason}. Nothing will be requested from Telegram for another "
        f"{remaining} minute(s). Do not retry and do not restart the server - "
        "the limit is on the account, not on this process."
    )


#: A chunk shorter than this fraction of the limit looks like spam-shaped
#: dribble, so a break point is only accepted past this offset.
_MIN_BREAK_RATIO = 0.4

#: Break points, best first. Each entry is ``(separator, chars_to_keep)`` where
#: ``chars_to_keep`` is how much of the separator stays in the chunk - a full
#: stop belongs to the sentence it ends, a newline belongs to nothing.
_BREAKPOINTS: tuple[tuple[str, int], ...] = (
    ("\n\n", 0),
    ("\n", 0),
    (". ", 1),
    ("! ", 1),
    ("? ", 1),
    ("… ", 1),
    ("; ", 1),
    (", ", 1),
    (" ", 0),
)

FLOOD_WAIT_TEMPLATE = "Telegram API limit reached. We must wait {seconds} seconds"


class ToolError(Exception):
    """An expected failure whose message is already written for the agent.

    Raising this instead of a generic exception keeps a known condition - a
    missing session, an unresolvable peer - out of the error log as a stack
    trace, while still short-circuiting the tool.
    """


def split_message(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters.

    Breaks are chosen at the highest-ranked boundary available: paragraph,
    then line, then sentence, then clause, then word. A word is only cut in
    half when a single word exceeds ``limit`` on its own, which is the sole
    case where no boundary exists.

    Args:
        text: The message body. May be empty.
        limit: Maximum characters per chunk.

    Returns:
        Chunks in order. An empty or whitespace-only ``text`` yields ``[]``.

    Raises:
        ValueError: ``limit`` is not positive.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    rest = text.strip()
    if not rest:
        return []

    chunks: list[str] = []
    min_break = max(1, int(limit * _MIN_BREAK_RATIO))

    while len(rest) > limit:
        cut, consumed = _find_break(rest, limit, min_break)
        chunk = rest[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        rest = rest[consumed:].lstrip()

    if rest:
        chunks.append(rest)

    return chunks


def _find_break(rest: str, limit: int, min_break: int) -> tuple[int, int]:
    """Locate the best break in ``rest[:limit]``.

    Returns:
        ``(cut, consumed)`` - the chunk is ``rest[:cut]`` and the next chunk
        starts at ``rest[consumed:]``. The gap between them is the separator.
    """
    for separator, keep in _BREAKPOINTS:
        end = limit - keep + len(separator)
        index = rest.rfind(separator, min_break, end)
        if index != -1:
            return index + keep, index + len(separator)

    # No boundary at all: a single token longer than the limit. Hard-cut it,
    # which is lossless because the pieces are re-joined by the reader.
    return limit, limit


async def sleep_between_chunks() -> None:
    """Pause between two chunks of the same logical message (SPEC-SND-003).

    Jittered, like every other delay here: a burst spaced at exactly 2.5s
    intervals is itself a pattern (SPEC-LIM-005).
    """
    await asyncio.sleep(jittered(CHUNK_DELAY_SECONDS))


def describe_telegram_error(exc: BaseException) -> str | None:
    """Translate a Telethon exception into a message the agent can act on.

    Returns ``None`` when the exception is not a recognised Telegram condition,
    which tells the caller to fall back to a generic report.
    """
    if isinstance(exc, errors.FloodWaitError):
        return "ERROR: " + FLOOD_WAIT_TEMPLATE.format(seconds=exc.seconds)

    if isinstance(exc, errors.PeerFloodError):
        return (
            "ERROR: Telegram has flagged this account for spam (PeerFloodError). "
            "Stop sending messages now. Wait several hours, and message only "
            "people already in your contacts. Repeated attempts escalate to a "
            "permanent ban. To appeal, write to @SpamBot from the Telegram app."
        )

    if isinstance(exc, errors.UserPrivacyRestrictedError):
        return (
            "ERROR: This user's privacy settings do not allow messages from you. "
            "Nothing was sent and nothing can be done from this side."
        )

    if isinstance(exc, errors.UserIsBlockedError):
        return "ERROR: This user has blocked your account. Nothing was sent."

    if isinstance(exc, errors.UserDeactivatedBanError):
        return (
            "ERROR: Your own account is deactivated or banned by Telegram. "
            "The MCP server cannot operate. Contact Telegram support."
        )

    if isinstance(exc, errors.AuthKeyDuplicatedError):
        # Deliberately worded to stop a retry. By the time this arrives the
        # login is already gone - Telegram's own documentation says the session
        # "is already invalidated" - so an agent that reads a vague message and
        # tries again is only wasting the user's time (SPEC-SEC-010, RISK-08).
        return (
            "ERROR: This session was invalidated because another connection "
            "used the same authorization key (AUTH_KEY_DUPLICATED). The login "
            "is already gone and retrying cannot bring it back. This normally "
            "means a sync ran while the server was connected. Run `just "
            "tg-auth` in a terminal to sign in again, and delete the stale "
            "tg_session.sync.session clone."
        )

    if isinstance(exc, errors.AuthKeyUnregisteredError | errors.SessionRevokedError):
        return (
            "ERROR: The Telegram session is no longer valid - it was revoked or "
            "logged out. Run `just tg-auth` in a terminal to sign in again."
        )

    if isinstance(exc, errors.UsernameNotOccupiedError | errors.UsernameInvalidError):
        return (
            "ERROR: No Telegram account owns that username. Check the spelling, "
            "or use a phone number in international format instead."
        )

    if isinstance(exc, errors.ChatWriteForbiddenError):
        return "ERROR: You do not have permission to write in that chat."

    return None


P = ParamSpec("P")


def guarded_tool(func: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[str]]:
    """Ensure an MCP tool always returns text and never raises (SPEC-SND-004).

    An exception escaping a tool handler is far worse than a bad answer: it
    can tear down the stdio server mid-session and leave the agent blind.
    Every failure is therefore converted into an ``ERROR:`` line the agent
    can read and reason about.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await func(*args, **kwargs)
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # the whole point of this decorator
            described = describe_telegram_error(exc)
            if described is not None:
                log.warning("%s failed: %s", func.__name__, exc)
                return described
            log.exception("%s failed unexpectedly", func.__name__)
            return f"ERROR: {type(exc).__name__}: {exc}"

    return wrapper


#: Field length caps for a Dialog Persona, deliberately one character tighter
#: than the CHECK constraints in ``sql/schema.sql``. The database constraint is
#: the backstop; this is what the agent actually hits, so it gets a sentence it
#: can act on instead of a raw asyncpg error string.
PERSONA_FIELD_LIMITS: dict[str, int] = {
    "addressing": 79,
    "tone": 119,
    "relationship": 119,
    "notes": 239,
}

#: Characters that let text lie about its own structure: bidirectional
#: overrides can reverse how a field renders, and zero-width characters can
#: split a URL or a handle so the checks below miss it.
_INVISIBLE_CHARACTERS = (
    "\u200b\u200c\u200d\u2060\ufeff"  # zero-width space, non-joiner, joiner, word joiner, BOM
    "\u202a\u202b\u202c\u202d\u202e"  # bidirectional embedding, override and pop
)

#: What a Persona field must not contain. These are structural, not semantic:
#: no keyword blacklist appears here. Blocking the word "ignore" is theatre -
#: it rejects honest descriptions of a writing style and stops no attacker who
#: can phrase a sentence differently. What actually matters is that a stored
#: field cannot carry a destination, an action or a forged fence.
_PERSONA_REJECTIONS: tuple[tuple[str, str], ...] = (
    ("://", "a URL"),
    ("www.", "a URL"),
    ("@", "a handle"),
    ("tg_", "a tool name"),
    ("---", "a fence marker"),
)


class PersonaFieldError(ValueError):
    """A Dialog Persona field that must not be stored, with the reason why."""


def sanitise_persona_field(name: str, value: str, *, required: bool) -> str:
    """Normalise and vet one agent-written Dialog Persona field (SPEC-PSN-006).

    A Persona is written by a model that has just read a conversation written
    by other people, and what it writes is then replayed into a model's context
    on every later read of that Dialog. That makes it the one place in this
    project where content can become a standing instruction, which is why the
    controls here are structural rather than a matter of prompting.

    Three things happen, in order. Invisible and control characters are
    removed, so a field cannot hide what it contains. All whitespace collapses
    to single spaces, so a field cannot span lines and therefore cannot forge
    the fence that marks Persona text as data. Anything carrying a destination
    or an action - a URL, a handle, a tool name - is refused outright, because
    a writing-style description has no legitimate need of one.

    Args:
        name: The field name, used in the error text and to pick the cap.
        value: What the agent supplied.
        required: Whether an empty result is an error or an acceptable blank.

    Returns:
        The cleaned value, ready to bind.

    Raises:
        PersonaFieldError: The field is empty when required, too long, or
            carries something a style description must not carry. The caller
            turns this into ``ERROR:`` text; nothing is stored.
    """
    cleaned = "".join(
        character
        for character in (value or "")
        if character not in _INVISIBLE_CHARACTERS
        and (character.isspace() or unicodedata.category(character)[0] != "C")
    )
    cleaned = " ".join(cleaned.split())

    if not cleaned:
        if required:
            raise PersonaFieldError(
                f"`{name}` is empty. Describe the writing style in a few words."
            )
        return ""

    limit = PERSONA_FIELD_LIMITS.get(name, 120)
    if len(cleaned) > limit:
        raise PersonaFieldError(
            f"`{name}` is {len(cleaned)} characters; the limit is {limit}. "
            "A Persona field is a short description of style, not a briefing - "
            "it is re-read on every message drafted for this dialog."
        )

    lowered = cleaned.lower()
    for needle, description in _PERSONA_REJECTIONS:
        if needle in lowered:
            raise PersonaFieldError(
                f"`{name}` was rejected: it contains {description}. Persona fields "
                "describe how the account writes and nothing else - no links, "
                "handles, tool names or instructions. Nothing was stored."
            )

    return cleaned
