"""Telethon client construction, peer resolution and contact lookup.

Peer resolution is a safety concern, not a convenience: a cold
``ResolveUsername`` call for a stranger is itself rate-limited and is one of
the signals Telegram uses to detect scripted accounts. Everything here
therefore prefers peers the account already knows (SPEC-SND-001).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import User

from tg_ai.config import Config
from tg_ai.safety import ToolError

log = logging.getLogger(__name__)

#: How long a cached dialog index or contact set stays fresh, in seconds.
CACHE_TTL_SECONDS = 300


def build_client(config: Config, *, session_base: Path | None = None) -> TelegramClient:
    """Create a Telethon client bound to a session file.

    Args:
        config: Validated configuration.
        session_base: Session path without the ``.session`` suffix. Defaults
            to the primary session; ``sync_db.py`` passes the clone.
    """
    base = session_base if session_base is not None else config.session_path
    return TelegramClient(str(base), config.api_id, config.api_hash)


def secure_session_file(path: Path) -> None:
    """Restrict the session file to the owner (SPEC-SEC-001).

    The file holds the account's MTProto auth key; group- or world-readable
    permissions are equivalent to publishing the password.
    """
    if path.exists():
        os.chmod(path, 0o600)


def clone_session(config: Config) -> Path:
    """Copy the primary session to the sync clone and return its base path.

    Telethon sessions are SQLite databases. Two processes writing the same
    file produce ``database is locked``, so the sync process works on a copy
    of the auth key rather than fighting the running MCP server (ADR-0004).
    """
    source = config.session_file
    if not source.exists():
        raise FileNotFoundError(f"No session at {source}. Run `just tg-auth` in a terminal first.")
    destination = config.sync_session_file
    shutil.copy2(source, destination)
    secure_session_file(destination)
    log.info("cloned session %s -> %s", source.name, destination.name)
    return config.sync_session_path


def display_name(user: User) -> str:
    """Human-readable label for a user, never empty."""
    parts = [p for p in (user.first_name, user.last_name) if p]
    if parts:
        return " ".join(parts)
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


def peer_label(user: User) -> str:
    """Label including the username when one exists."""
    name = display_name(user)
    if user.username and not name.startswith("@"):
        return f"{name} (@{user.username})"
    return name


def is_archivable(entity: object, *, include_bots: bool) -> bool:
    """Whether a dialog peer belongs in the archive (SPEC-SYNC-001).

    Only 1-on-1 chats with real people: groups, channels and - unless opted
    in - bots are out of scope. Deleted accounts carry no useful history.
    """
    if not isinstance(entity, User):
        return False
    if entity.deleted:
        return False
    if entity.bot and not include_bots:
        return False
    # 777000 is Telegram's own service account; its messages are login codes.
    return entity.id != 777000


def normalise_target(target: str) -> str:
    """Canonicalise a user-supplied peer reference."""
    return target.strip().lstrip("@") if target else ""


@dataclass(slots=True)
class _Cached:
    value: object
    fetched_at: float


class PeerIndex:
    """A short-lived index of peers the account already has dialogs with.

    Resolving through this index costs one ``GetDialogs`` call per five
    minutes instead of one ``ResolveUsername`` per lookup, and it is the
    mechanism behind the "do we know this person?" send guard.
    """

    def __init__(self, client: TelegramClient, ttl: float = CACHE_TTL_SECONDS) -> None:
        self._client = client
        self._ttl = ttl
        self._by_username: dict[str, User] = {}
        self._by_id: dict[int, User] = {}
        self._dialogs_at = 0.0
        self._contacts: _Cached | None = None

    async def refresh(self, *, force: bool = False) -> None:
        """Rebuild the dialog index if it has expired."""
        if not force and (time.monotonic() - self._dialogs_at) < self._ttl:
            return
        by_username: dict[str, User] = {}
        by_id: dict[int, User] = {}
        async for dialog in self._client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, User):
                continue
            by_id[entity.id] = entity
            if entity.username:
                by_username[entity.username.lower()] = entity
        self._by_username = by_username
        self._by_id = by_id
        self._dialogs_at = time.monotonic()
        log.info("peer index refreshed: %d private dialogs", len(by_id))

    async def lookup(self, target: str) -> User | None:
        """Return a known peer for ``target``, or ``None`` if unknown."""
        key = normalise_target(target)
        if not key:
            return None
        await self.refresh()
        if key.isdigit():
            return self._by_id.get(int(key))
        return self._by_username.get(key.lower())

    async def contact_ids(self, *, force: bool = False) -> set[int]:
        """User ids in the account's contact list, cached."""
        fresh = (
            self._contacts is not None
            and not force
            and (time.monotonic() - self._contacts.fetched_at) < self._ttl
        )
        if fresh and self._contacts is not None:
            return self._contacts.value  # type: ignore[return-value]

        result = await self._client(GetContactsRequest(hash=0))
        ids = {user.id for user in getattr(result, "users", [])}
        self._contacts = _Cached(value=ids, fetched_at=time.monotonic())
        return ids

    def invalidate(self) -> None:
        """Drop caches, e.g. right after adding a contact."""
        self._dialogs_at = 0.0
        self._contacts = None


async def resolve_peer(client: TelegramClient, index: PeerIndex, target: str) -> tuple[User, bool]:
    """Resolve ``target`` to a user, preferring locally known peers.

    Args:
        target: ``@username``, a bare username, a numeric id, a phone number
            in international format, or ``me``/``self`` for Saved Messages.

    Returns:
        ``(user, known_locally)``. ``known_locally`` is ``True`` when the peer
        came from the existing dialog index, which means no cold resolution
        call was made.

    Raises:
        ToolError: The target is empty or cannot be resolved.
    """
    key = normalise_target(target)
    if not key:
        raise ToolError("Target is empty. Pass a @username, phone number or numeric id.")

    if key.lower() in {"me", "self", "saved", "saved messages"}:
        return await client.get_me(), True

    known = await index.lookup(key)
    if known is not None:
        return known, True

    entity = await client.get_entity(key)
    if not isinstance(entity, User):
        raise ToolError(f"{target!r} is a group or channel. This server only handles 1-on-1 chats.")
    return entity, False


async def has_conversation(client: TelegramClient, user: User) -> bool:
    """Whether any message has ever been exchanged with ``user``.

    This is the primary input to the send guard: writing to someone with no
    shared history and no contact entry is what triggers ``PeerFloodError``.
    """
    messages = await client.get_messages(user, limit=1)
    return len(messages) > 0
