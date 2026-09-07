"""Telethon client construction, peer resolution and contact lookup.

Peer resolution is a safety concern, not a convenience: a cold
``ResolveUsername`` call for a stranger is itself rate-limited and is one of
the signals Telegram uses to detect scripted accounts. Everything here
therefore prefers peers the account already knows (SPEC-SND-001).
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from telethon import TelegramClient
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import User

from tg_ai.config import Config
from tg_ai.safety import ToolError

log = logging.getLogger(__name__)

#: How long a cached dialog index or contact set stays fresh, in seconds.
CACHE_TTL_SECONDS = 300


def build_client(config: Config, *, session_base: Path | None = None) -> TelegramClient:
    """Create a Telethon client bound to a session file (SPEC-SEC-007, SPEC-SEC-008).

    This is the only construction site in the project. ``auth.py``,
    ``server.py`` and ``sync_db.py`` all come through here, which is what
    makes the Client Identity below identical on every connection - the
    property that actually matters, far more than the strings themselves
    (ADR-0009).

    Telethon's defaults are tuned for convenience, not for a personal account
    under observation, so every parameter that matters is pinned explicitly
    rather than inherited. Do not add a Telethon event handler anywhere in
    this codebase: ``receive_updates=False`` means handlers silently never
    fire, and re-enabling updates to "fix" that would subscribe the account to
    every message in every group it belongs to (ADR-0009).

    Args:
        config: Validated configuration.
        session_base: Session path without the ``.session`` suffix. Defaults
            to the primary session; ``sync_db.py`` passes the clone.
    """
    base = session_base if session_base is not None else config.session_path
    return TelegramClient(
        str(base),
        config.api_id,
        config.api_hash,
        # --- Client Identity -------------------------------------------
        # Honest and stable, never impersonating an official client. Telegram
        # already knows this client is unofficial because it knows the api_id.
        device_model=config.device_model,
        system_version=config.system_version,
        app_version=config.app_version,
        lang_code=config.lang_code,
        system_lang_code=config.system_lang_code,
        # --- The constructor contract ----------------------------------
        # The zero here is load-bearing, not a placeholder. Telethon's default
        # of 60 makes it sleep through - that is, silently retry - every flood
        # wait of 60 seconds or less. Scraping-induced waits are typically
        # 5-30 seconds, so with the default every one of them is invisible:
        # the FloodWaitError handlers in this project would never fire, and
        # AGENTS.md's rule against retrying a flood wait would be violated
        # inside the library. Zero makes every wait surface as an exception,
        # so it is counted, reported, and fed to the kill switch.
        flood_sleep_threshold=0,
        # No real-time push. The server reads on demand; with group support
        # this would otherwise stream every message from every group.
        receive_updates=False,
        # Never enable. On connect it calls updates.getDifference, which is a
        # bulk history fetch and the exact flood risk receive_updates=False
        # exists to avoid.
        catch_up=False,
        # A failing request must not silently become five requests.
        request_retries=1,
        # Every reconnect replays initConnection, so a network blip must not
        # produce a handshake burst.
        connection_retries=2,
        retry_delay=5,
        auto_reconnect=True,
        # Group history fills this cache with strangers' ids and access
        # hashes, and the cache lives in the session file that clone_session()
        # copies on every sync run. Cap it: this project has no business
        # accumulating access hashes for people the user never spoke to.
        entity_cache_limit=500,
    )


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


def _digits(value: str) -> str:
    """Keep only the digits, so ``+380 50 123`` and ``38050123`` compare equal."""
    return "".join(character for character in value if character.isdigit())


def peer_match_keys(user: User) -> set[str]:
    """Every string a Target may legitimately use to name this Peer.

    Covers the numeric id, the username, the phone number reduced to digits,
    and the first, last and full name. Names are case-folded and their internal
    whitespace collapsed, so ``"anna  petrova"`` matches ``"Anna Petrova"``.
    """
    keys: set[str] = {str(user.id)}

    if user.username:
        keys.add(user.username.casefold())
    if user.phone:
        keys.add(_digits(user.phone))

    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    for name in (first, last, f"{first} {last}".strip()):
        if name:
            keys.add(" ".join(name.split()).casefold())

    return keys


def target_keys(target: str) -> set[str]:
    """The keys one Target may match on.

    A digit-bearing target yields its digits-only form as well, so a phone
    number matches however the user typed the separators.
    """
    cleaned = " ".join(normalise_target(target).split()).casefold()
    if not cleaned:
        return set()

    keys = {cleaned}
    digits = _digits(cleaned)
    if digits:
        keys.add(digits)
    return keys


def matches_target(user: User, target: str) -> bool:
    """Whether ``user`` is the Peer named by ``target`` (SPEC-SYNC-006).

    Matching is exact per key, never a substring: ``"an"`` must not silently
    pull in ``Anna``, ``Ivan`` and ``Alexander`` when the user asked for one
    person. A target that matches nothing is reported by the caller rather
    than being widened here.
    """
    return bool(peer_match_keys(user) & target_keys(target))


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


class SessionLocked(ToolError):
    """Another process already holds this account's connection lock."""


class SessionLock:
    """Exclusive lock over one authorization key (SPEC-SEC-010, ADR-0010).

    ADR-0004 has ``sync_db.py`` work on a copy of the session file so two
    processes never write one SQLite database. That solved the file-locking
    problem and left a worse one open: the clone carries the *same*
    authorization key, and Telegram punishes parallel connections on one key
    with ``AUTH_KEY_DUPLICATED``. That error is not a warning - by the time it
    arrives the login is already invalidated and only a fresh SMS code brings
    it back.

    So the clone is safe only under mutual exclusion, and this is it. The
    lock is advisory (``flock``) and lives on a file next to the session; the
    kernel releases it when the holder exits or crashes, so a killed process
    can never leave a stale lock behind.

    The loser does not wait and does not retry. Queueing behind a running
    sync would just connect later and hit the same wall.
    """

    def __init__(self, path: Path, holder: str) -> None:
        self._path = path
        self._holder = holder
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        """Take the lock, or raise naming whoever holds it.

        Raises:
            SessionLocked: Another process is already connected on this key.
        """
        if self._handle is not None:
            return

        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.seek(0)
            occupant = handle.read().strip() or "another tg-ai process"
            handle.close()
            raise SessionLocked(
                f"{occupant} is already connected to Telegram with this "
                "session. Two live connections on one authorization key make "
                "Telegram invalidate the login outright (AUTH_KEY_DUPLICATED), "
                "and recovering from that needs a new SMS code. Let the other "
                "process finish, then try again. Nothing was connected."
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(f"{self._holder} (pid {os.getpid()})")
        handle.flush()
        self._handle = handle
        log.debug("session lock acquired: %s", self._path.name)

    def release(self) -> None:
        """Drop the lock. Safe to call when it was never taken."""
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
