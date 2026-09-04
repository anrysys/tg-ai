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
import unicodedata
from collections.abc import Awaitable, Callable
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
    """Pause between two chunks of the same logical message (SPEC-SND-003)."""
    await asyncio.sleep(CHUNK_DELAY_SECONDS)


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
