"""Telethon client construction, peer resolution and contact lookup.

Peer resolution is a safety concern, not a convenience: a cold
``ResolveUsername`` call for a stranger is itself rate-limited and is one of
the signals Telegram uses to detect scripted accounts. Everything here
therefore prefers peers the account already knows (SPEC-SND-001).
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Protocol, TextIO
from zoneinfo import ZoneInfo

from telethon import TelegramClient, errors
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import Channel, Chat, User
from telethon.tl.types.contacts import ContactsNotModified

from tg_ai.config import Config
from tg_ai.safety import (
    FLOOD_TRIP_WINDOW_SECONDS,
    KILL_SWITCH_COOLDOWN_SECONDS,
    MIN_RPC_GAP_SECONDS,
    RPC_BUDGET_PER_DAY,
    RPC_BUDGET_PER_HOUR,
    RPC_SCOPE,
    ToolError,
    budget_message,
    flood_trips_kill_switch,
    in_quiet_window,
    is_flood_error,
    jittered,
    kill_switch_message,
    quiet_window_message,
)

log = logging.getLogger(__name__)

#: How long a cached dialog index or contact set stays fresh, in seconds.
#: Raised from 300 with group support: GetDialogs is the heaviest call this
#: codebase makes and a monitored endpoint, and the dialog list only gets
#: longer once groups and channels are in it. Never lower this (SPEC-RCV-005).
CACHE_TTL_SECONDS = 600

#: The most dialogs any single GetDialogs walk will examine. Telethon pages
#: this 100 at a time, so an unbounded walk on a busy account is several RPCs
#: per refresh - and the previous code did exactly that, every 300 seconds.
DIALOG_SCAN_LIMIT = 200


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


def display_name(peer: object) -> str:
    """Human-readable label for any Peer, never empty."""
    title = getattr(peer, "title", None)
    if title:
        return title
    parts = [
        part
        for part in (getattr(peer, "first_name", None), getattr(peer, "last_name", None))
        if part
    ]
    if parts:
        return " ".join(parts)
    username = getattr(peer, "username", None)
    if username:
        return f"@{username}"
    return f"id:{peer.id}"


def peer_label(peer: object) -> str:
    """Label including the username when one exists."""
    name = display_name(peer)
    username = getattr(peer, "username", None)
    if username and not name.startswith("@"):
        return f"{name} (@{username})"
    return name


#: The Peer Types this project understands, as stored in `dialogs.peer_type`.
PEER_TYPE_USER = "user"
PEER_TYPE_GROUP = "group"
PEER_TYPE_CHANNEL = "channel"


def peer_type(entity: object) -> str | None:
    """Classify a Telegram entity, or ``None`` if this project cannot use it.

    A megagroup is a Group, because that is what it is to the person in it: a
    place where many people talk. A gigagroup is classified as a Channel
    despite its ``megagroup`` flag, because ordinary members cannot write in
    one - and being able to write is the distinction that matters everywhere
    this value is used (`SPEC-SND-001`).
    """
    if isinstance(entity, User):
        return PEER_TYPE_USER
    if isinstance(entity, Chat):
        return PEER_TYPE_GROUP
    if isinstance(entity, Channel):
        if getattr(entity, "gigagroup", False):
            return PEER_TYPE_CHANNEL
        if entity.megagroup:
            return PEER_TYPE_GROUP
        if entity.broadcast:
            return PEER_TYPE_CHANNEL
    return None


def is_member(entity: object) -> bool:
    """Whether the account is currently in this Group or Channel.

    Read from the cached entity, never from a fresh API call: asking Telegram
    "am I in this?" for something we are not in is itself the cold lookup the
    Peer Index exists to avoid.
    """
    if isinstance(entity, User):
        return True
    if getattr(entity, "left", False):
        return False
    if getattr(entity, "deactivated", False):
        return False
    # A migrated Chat is a husk: its history moved to a Channel, and writing to
    # it does nothing useful.
    return getattr(entity, "migrated_to", None) is None


def can_post(entity: object) -> bool:
    """Whether the account may write here (SPEC-SND-001).

    For a Channel this is ``admin_rights.post_messages`` and nothing else: a
    plain subscriber cannot post, and finding that out by trying is a wasted
    request against a monitored endpoint.
    """
    kind = peer_type(entity)
    if kind == PEER_TYPE_USER:
        return True
    if not is_member(entity):
        return False
    if kind == PEER_TYPE_GROUP:
        banned = getattr(entity, "default_banned_rights", None)
        if banned is not None and getattr(banned, "send_messages", False):
            rights = getattr(entity, "admin_rights", None)
            return bool(rights is not None)
        return True
    if kind == PEER_TYPE_CHANNEL:
        rights = getattr(entity, "admin_rights", None)
        return bool(rights is not None and getattr(rights, "post_messages", False))
    return False


def is_archivable(entity: object, *, include_bots: bool, include_groups: bool = False) -> bool:
    """Whether a dialog peer belongs in the archive (SPEC-SYNC-001).

    ``include_groups`` defaults to ``False`` so that a full sync stays what it
    has always been: 1-on-1 chats with real people. Groups and channels are
    opt-in and reachable only through ``--targets`` (`SPEC-SYNC-007`), because
    they would otherwise dominate the archive by volume and turn every routine
    sync into a large read against monitored endpoints.
    """
    kind = peer_type(entity)
    if kind is None:
        return False

    if kind == PEER_TYPE_USER:
        if entity.deleted:
            return False
        if entity.bot and not include_bots:
            return False
        # 777000 is Telegram's own service account; its messages are login codes.
        return entity.id != 777000

    return include_groups and is_member(entity)


_HASH_MASK = (1 << 64) - 1


def contacts_hash(saved_count: int, contact_ids: Iterable[int]) -> int:
    """The documented ``contacts.getContacts`` hash for a known contact set.

    Sending it lets the server answer ``contactsContactsNotModified`` instead of
    re-serialising every contact, which is what official clients do and what
    this project previously never did - passing ``hash=0`` forever is both
    wasteful and a behavioural difference visible server-side (`SPEC-SND-006`).

    Two details here are easy to get wrong and were both found by testing
    against the live API rather than by reading:

    - The value folded in first is ``saved_count`` from the previous response,
      **not** the number of users returned. They differ on a real account -
      502 users against a ``saved_count`` of 372 on the account this was built
      for - and using the wrong one silently produces a hash that never
      matches, so the optimisation does nothing and nobody notices.
    - The field is a **signed** 64-bit integer on the wire. Returning the
      unsigned accumulator makes Telethon raise ``struct.error`` for roughly
      half of all inputs.

    See <https://core.telegram.org/api/offsets#hash-generation>.

    Args:
        saved_count: ``saved_count`` from the previous ``contacts.contacts``.
        contact_ids: The contact ids that response carried.

    Returns:
        A signed 64-bit hash, or ``0`` when nothing is cached - which is the
        documented "I have nothing" value.
    """
    ids = sorted(contact_ids)
    if not ids:
        return 0

    value = 0
    for number in [saved_count, *ids]:
        value = (value ^ (value >> 21)) & _HASH_MASK
        value = (value ^ (value << 35)) & _HASH_MASK
        value = (value ^ (value >> 4)) & _HASH_MASK
        value = (value + number) & _HASH_MASK

    return value - (1 << 64) if value >= (1 << 63) else value


def normalise_target(target: str) -> str:
    """Canonicalise a user-supplied peer reference."""
    return target.strip().lstrip("@") if target else ""


def _digits(value: str) -> str:
    """Keep only the digits, so ``+380 50 123`` and ``38050123`` compare equal."""
    return "".join(character for character in value if character.isdigit())


def peer_match_keys(peer: object) -> set[str]:
    """Every string a Target may legitimately use to name this Peer.

    For a User: the numeric id, the username, the phone reduced to digits, and
    the first, last and full name. For a Group or Channel: the id, the username
    and the title. Fields are read with ``getattr`` because the three Peer
    Types genuinely do not share a shape - a ``Channel`` has a ``title`` and no
    ``phone``, and reading one off the other is how this broke the first time.

    Names are case-folded and their internal whitespace collapsed, so
    ``"anna  petrova"`` matches ``"Anna Petrova"``.
    """
    keys: set[str] = {str(peer.id)}

    username = getattr(peer, "username", None)
    if username:
        keys.add(username.casefold())

    phone = getattr(peer, "phone", None)
    if phone:
        keys.add(_digits(phone))

    first = (getattr(peer, "first_name", None) or "").strip()
    last = (getattr(peer, "last_name", None) or "").strip()
    title = (getattr(peer, "title", None) or "").strip()
    for name in (first, last, f"{first} {last}".strip(), title):
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


def matches_target(peer: object, target: str) -> bool:
    """Whether ``peer`` is the Peer named by ``target`` (SPEC-SYNC-006).

    Matching is exact per key, never a substring: ``"an"`` must not silently
    pull in ``Anna``, ``Ivan`` and ``Alexander`` when the user asked for one
    person. A target that matches nothing is reported by the caller rather
    than being widened here.
    """
    return bool(peer_match_keys(peer) & target_keys(target))


class ContactStore(Protocol):
    """Where the contact-list cache survives a process restart.

    Same shape as :class:`RpcLedger`, and for the same reason: an MCP stdio
    server is respawned constantly, so anything it holds in memory is not a
    cache, it is a warm-up cost paid on every launch.
    """

    async def load_contacts(self) -> tuple[set[int], int] | None: ...

    async def save_contacts(self, saved_count: int, contact_ids: set[int]) -> None: ...


@dataclass(slots=True)
class _Cached:
    value: object
    fetched_at: float


@dataclass(frozen=True, slots=True)
class DialogRecord:
    """One row of the shared dialog snapshot.

    Exists so that the Peer Index and ``tg_get_unread_dialogs`` can share a
    single GetDialogs walk. Two independent walks per user question is
    indefensible against a monitored endpoint (SPEC-RCV-005).
    """

    entity: object
    kind: str
    unread_count: int
    date: object
    preview: str | None


class PeerIndex:
    """A cache of the Peers the account already has Dialogs with.

    Resolving through this index costs one ``GetDialogs`` walk per TTL instead
    of one ``ResolveUsername`` per lookup, and it is the mechanism behind both
    the "do we know this person?" send guard and the "are we a member of this
    group?" check. Membership evidence, not a convenience.
    """

    def __init__(
        self,
        client: TelegramClient,
        ttl: float = CACHE_TTL_SECONDS,
        *,
        store: ContactStore | None = None,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._store = store
        self._by_username: dict[str, object] = {}
        self._by_id: dict[int, object] = {}
        self._dialogs: list[DialogRecord] = []
        self._dialogs_at = 0.0
        self._contacts: _Cached | None = None

    async def refresh(self, *, force: bool = False) -> None:
        """Rebuild the dialog index if it has expired.

        Bounded by ``DIALOG_SCAN_LIMIT``: an unbounded walk is several RPCs on
        a busy account, and this runs on a timer.
        """
        if not force and (time.monotonic() - self._dialogs_at) < self._ttl:
            return

        by_username: dict[str, object] = {}
        by_id: dict[int, object] = {}
        records: list[DialogRecord] = []

        async for dialog in self._client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
            entity = dialog.entity
            kind = peer_type(entity)
            if kind is None:
                continue
            # Only Peers the account is actually party to. A left group in the
            # dialog list is history, not membership.
            if not is_member(entity):
                continue

            by_id[entity.id] = entity
            username = getattr(entity, "username", None)
            if username:
                by_username[username.lower()] = entity

            records.append(
                DialogRecord(
                    entity=entity,
                    kind=kind,
                    unread_count=dialog.unread_count,
                    date=dialog.date,
                    preview=dialog.message.message if dialog.message else None,
                )
            )

        self._by_username = by_username
        self._by_id = by_id
        self._dialogs = records
        self._dialogs_at = time.monotonic()
        log.info(
            "peer index refreshed: %d dialogs (%d private)",
            len(by_id),
            sum(1 for record in records if record.kind == PEER_TYPE_USER),
        )

    async def dialogs(self) -> list[DialogRecord]:
        """The shared dialog snapshot, refreshed if stale."""
        await self.refresh()
        return self._dialogs

    async def lookup(self, target: str) -> object | None:
        """Return a known Peer for ``target``, or ``None`` if unknown.

        A hit is proof of membership: everything here came from GetDialogs.

        Ids and usernames resolve through the maps. Groups and Channels also
        have to resolve by **title**, because that is what they are called -
        "golka.chat" is a title whose username is "golka_chat", and a user
        naming a group will use the name they see. That falls back to a scan of
        the bounded snapshot, which is at most a couple of hundred entries.

        Raises:
            ToolError: More than one Peer matches. Ambiguity is reported with
                every candidate rather than resolved by guessing, exactly as
                the Dialog Lookup does (`SPEC-SRCH-006`).
        """
        key = normalise_target(target)
        if not key:
            return None
        await self.refresh()

        if key.isdigit():
            hit = self._by_id.get(int(key))
            if hit is not None:
                return hit

        hit = self._by_username.get(key.lower())
        if hit is not None:
            return hit

        matches = [record.entity for record in self._dialogs if matches_target(record.entity, key)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(peer_label(match) for match in matches)
            raise ToolError(
                f"{target!r} matches {len(matches)} of your chats: {names}. "
                "Nothing was requested from Telegram. Name one of them exactly, "
                "or use its @username or numeric id."
            )
        return None

    async def contact_ids(self, *, force: bool = False) -> set[int]:
        """User ids in the account's contact list, cached."""
        fresh = (
            self._contacts is not None
            and not force
            and (time.monotonic() - self._contacts.fetched_at) < self._ttl
        )
        if fresh and self._contacts is not None:
            return self._contacts.value  # type: ignore[return-value]

        known: set[int] = set()
        saved_count = 0
        if self._contacts is not None:
            known, saved_count = self._contacts.value  # type: ignore[assignment]
        elif self._store is not None:
            # Restored across a process restart. The MCP server is respawned
            # constantly, so without this the hash is 0 on every launch and the
            # whole contact list comes back down every time.
            restored = await self._store.load_contacts()
            if restored is not None:
                known, saved_count = restored

        result = await self._client(GetContactsRequest(hash=contacts_hash(saved_count, known)))
        if isinstance(result, ContactsNotModified):
            ids = known
        else:
            ids = {user.id for user in getattr(result, "users", [])}
            saved_count = int(getattr(result, "saved_count", 0) or 0)
            if self._store is not None:
                await self._store.save_contacts(saved_count, ids)

        self._contacts = _Cached(value=(ids, saved_count), fetched_at=time.monotonic())
        return ids

    def invalidate(self) -> None:
        """Drop caches, e.g. right after adding a contact."""
        self._dialogs_at = 0.0
        self._contacts = None


async def resolve_peer(
    client: TelegramClient,
    index: PeerIndex,
    target: str,
    *,
    allow: tuple[str, ...] = (PEER_TYPE_USER,),
) -> tuple[object, bool]:
    """Resolve ``target`` to a Peer, preferring locally known ones.

    **The guard lives here rather than at each call site**, so `--dialog` and
    every present and future MCP tool inherit it and a new caller cannot
    forget it (`SPEC-SND-006`).

    Cold resolution - ``contacts.ResolveUsername`` for a handle the account has
    no Dialog with - is permitted **only** when the caller will accept nothing
    but a ``User``. That is the structural rule: a caller that admits Groups or
    Channels gets no cold path at all, so it is impossible to reach
    ``@some_public_channel`` this way. Resolving a channel the account has
    never joined is textbook scraper behaviour, and doing it once per target
    is how a script is identified as one.

    Groups and Channels therefore come from the Peer Index or not at all, and a
    Peer Index hit is itself the proof of membership: everything in it came
    from ``GetDialogs``.

    Args:
        target: ``@username``, a bare username, a numeric id, a phone number in
            international format, or ``me``/``self`` for Saved Messages.
        allow: Peer Types this caller can handle. The default keeps every
            existing call site user-only, including ``sync_db.py --dialog``.

    Returns:
        ``(peer, known_locally)``. ``known_locally`` is ``True`` when the Peer
        came from the Peer Index, which means no cold resolution happened and
        that the account is a member.

    Raises:
        ToolError: The target is empty, is of a type this caller cannot handle,
            or is a Group or Channel the account is not a member of - in which
            case **no API call is made**.
    """
    key = normalise_target(target)
    if not key:
        raise ToolError("Target is empty. Pass a @username, phone number or numeric id.")

    if key.lower() in {"me", "self", "saved", "saved messages"}:
        return await client.get_me(), True

    known = await index.lookup(key)
    if known is not None:
        kind = peer_type(known)
        if kind not in allow:
            raise ToolError(_wrong_type_message(target, kind, allow))
        return known, True

    # Not in the index. Whether we may ask Telegram at all depends entirely on
    # what the caller is willing to accept.
    if tuple(allow) != (PEER_TYPE_USER,):
        raise ToolError(
            f"{target!r} is not among the Groups, Channels and chats this "
            "account is part of. Nothing was requested from Telegram. Groups "
            "and channels are reachable only when you are already a member - "
            "this server never looks up or joins one you have not joined, "
            "because resolving unknown channels is what gets personal accounts "
            "flagged as scrapers. Join it in the Telegram app first, then retry."
        )

    entity = await client.get_entity(key)
    if peer_type(entity) != PEER_TYPE_USER:
        # Reached only for a user-only caller that resolved something else.
        # Refused and, deliberately, never added to the Peer Index.
        raise ToolError(
            f"{target!r} is a group or channel. This lookup path handles people "
            "only. Groups and channels are reached through the dialog list, and "
            "only when you are already a member."
        )
    return entity, False


def _wrong_type_message(target: str, kind: str | None, allow: tuple[str, ...]) -> str:
    """Explain a Peer Type refusal in terms of what the caller can do."""
    wanted = " or ".join(allow)
    return (
        f"{target!r} is a {kind}, and this operation accepts a {wanted}. "
        "Nothing was requested from Telegram."
    )


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


class RpcLedger(Protocol):
    """The persisted state the limiter needs (SPEC-LIM-002).

    Declared here, next to its only consumer, rather than in ``safety`` - that
    module is pure by contract and has no business naming an async I/O
    interface. ``tg_ai.db.PostgresRpcLedger`` satisfies this structurally, so
    no import crosses between ``db`` and ``tg_client``.
    """

    async def record_call(self, scope: str, key: str) -> None: ...

    async def rpc_counts(self) -> tuple[int, int]: ...

    async def record_flood(self, error_type: str, method: str, target: str | None) -> None: ...

    async def count_floods(self, window_seconds: float) -> int: ...

    async def active_kill_switch(self) -> tuple[datetime, str, datetime | None] | None: ...

    async def trip_kill_switch(self, reason: str, expires_at: datetime | None) -> None: ...


class RpcGuard:
    """Serialises and rations every Telegram request (SPEC-LIM-001).

    Deliberately a plain object rather than part of the client subclass:
    constructing a ``TelegramClient`` needs credentials and touches a session
    file, and the offline test suite must be able to exercise this logic
    without either. The clock and the sleep are injected for the same reason.

    All the *decisions* live in :mod:`tg_ai.safety` as pure functions over
    numbers this class fetches. What is left here is the ordering, the lock and
    the bookkeeping.
    """

    def __init__(
        self,
        ledger: RpcLedger,
        *,
        quiet_start: dt_time,
        quiet_end: dt_time,
        timezone: str,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._quiet_start = quiet_start
        self._quiet_end = quiet_end
        self._zone = ZoneInfo(timezone)
        self._monotonic = monotonic
        self._sleep = sleep
        self._now = now if now is not None else lambda: datetime.now(UTC)
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        # -inf rather than 0 so the very first request is not made to wait for
        # a gap that has, in every meaningful sense, already elapsed.
        self._last_call = float("-inf")

    @asynccontextmanager
    async def reserve(self, method: str) -> AsyncIterator[None]:
        """Authorise, serialise and pace one request.

        Re-entrancy is not a nicety here, it is a correctness requirement.
        Telethon's ``_call`` issues nested requests of its own: every request's
        ``resolve()`` may call ``get_input_entity``, which calls
        ``self(GetUsersRequest(...))``, and the migrate path calls
        ``is_user_authorized`` which calls ``self(GetStateRequest())``. Those
        run inside the outer call, in the same task, so a plain
        ``asyncio.Lock`` held across the delegate deadlocks permanently and
        without a traceback.

        The owner is tracked as a task rather than a ``ContextVar`` on purpose:
        a ContextVar is copied into child tasks at creation, which would wrongly
        exempt Telethon's genuinely concurrent background work - the
        auto-reconnect callback and the update loop - from the lock they must
        queue behind.
        """
        nested = self._owner is not None and self._owner is asyncio.current_task()
        await self._authorise(nested=nested)

        if nested:
            # Already inside an authorised call. Still a real request, so it is
            # still paced and still counted - only the lock is skipped.
            await self._pace()
            await self._record(method)
            yield
            return

        async with self._lock:
            self._owner = asyncio.current_task()
            try:
                await self._pace()
                await self._record(method)
                yield
            finally:
                self._owner = None

    async def headroom(self) -> tuple[int, int]:
        """Requests left in the rolling hour and day. For ``tg_whoami``."""
        hourly, daily = await self._ledger.rpc_counts()
        return (
            max(0, RPC_BUDGET_PER_HOUR - hourly),
            max(0, RPC_BUDGET_PER_DAY - daily),
        )

    async def note_failure(
        self, exc: BaseException, method: str, target: str | None = None
    ) -> None:
        """Record a flood and trip the kill switch when the rate warrants it.

        A ``PeerFloodError`` trips it **indefinitely**: Telegram has flagged the
        account for spam, which is an escalation rather than a cooldown, and
        only the account owner should decide the account is healthy again.
        """
        if isinstance(exc, errors.PeerFloodError):
            await self._ledger.record_flood(type(exc).__name__, method, target)
            await self._ledger.trip_kill_switch(
                f"PeerFloodError on {method} - Telegram flagged this account for spam",
                None,
            )
            return

        if not is_flood_error(exc):
            return

        await self._ledger.record_flood(type(exc).__name__, method, target)
        recent = await self._ledger.count_floods(FLOOD_TRIP_WINDOW_SECONDS)
        if flood_trips_kill_switch(recent):
            await self._ledger.trip_kill_switch(
                f"{recent} flood waits within an hour, most recently on {method}",
                self._now() + timedelta(seconds=KILL_SWITCH_COOLDOWN_SECONDS),
            )

    # --- internals --------------------------------------------------------

    async def _authorise(self, *, nested: bool) -> None:
        """Raise ``ToolError`` when this request must not be made."""
        try:
            switch = await self._ledger.active_kill_switch()
        except Exception as exc:
            raise self._ledger_unreachable(exc) from exc

        if switch is not None:
            tripped_at, reason, expires_at = switch
            message = kill_switch_message(
                tripped_at=tripped_at, reason=reason, expires_at=expires_at, now=self._now()
            )
            # The kill switch stops nested requests too. It is the emergency
            # brake: finishing an in-flight call is not worth more traffic from
            # an account Telegram is already unhappy with.
            if message is not None:
                raise ToolError(message)

        if nested:
            # The outer call already cleared the quiet window and the budget.
            # Failing a request halfway through, after its peer has been
            # resolved, would be worse than the one extra RPC it costs.
            return

        local_now = self._now().astimezone(self._zone).time()
        if in_quiet_window(local_now, self._quiet_start, self._quiet_end):
            raise ToolError(quiet_window_message(self._quiet_start, self._quiet_end))

        try:
            hourly, daily = await self._ledger.rpc_counts()
        except Exception as exc:
            raise self._ledger_unreachable(exc) from exc

        message = budget_message(hourly, daily)
        if message is not None:
            raise ToolError(message)

    @staticmethod
    def _ledger_unreachable(exc: BaseException) -> ToolError:
        """Fail closed when the budget cannot be read.

        A budget that evaporates when PostgreSQL stops is not a budget - the
        cheapest way around it would be `docker stop`. Refusing costs the user
        one command; guessing costs an account.
        """
        return ToolError(
            "Cannot read the Telegram safety budget from PostgreSQL "
            f"({type(exc).__name__}: {exc}). Refusing to contact Telegram "
            "rather than proceeding unmetered. Start the database with "
            "`just db-up` and retry."
        )

    async def _pace(self) -> None:
        gap = jittered(MIN_RPC_GAP_SECONDS)
        elapsed = self._monotonic() - self._last_call
        if elapsed < gap:
            await self._sleep(gap - elapsed)
        self._last_call = self._monotonic()

    async def _record(self, method: str) -> None:
        try:
            await self._ledger.record_call(RPC_SCOPE, method)
        except Exception as exc:
            raise self._ledger_unreachable(exc) from exc


def request_name(request: object) -> str:
    """A stable, loggable name for a Telethon request.

    Kept greppable rather than clever: the budget is auditable only if the
    ``key`` column says which method spent it.
    """
    if isinstance(request, list | tuple):
        return ",".join(type(item).__name__ for item in request) or "UnknownRequest"
    return type(request).__name__


class GuardedClient(TelegramClient):
    """A Telethon client whose every request goes through :class:`RpcGuard`.

    Installed at the client level rather than at each call site, because a
    control a new call site can forget is not a control. ``__call__`` is the
    right seam: it is a one-line delegate to ``_call``, and the only path in
    Telethon that bypasses it is ``edit_message`` for inline bot messages,
    which this project has no way to reach.
    """

    def __init__(self, *args: object, guard: RpcGuard | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_guard = guard

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        guard = getattr(self, "_rpc_guard", None)
        if guard is None:
            # auth.py runs without a ledger: logging in precedes any budget,
            # and it is a deliberate human action rather than agent traffic.
            return await super().__call__(request, ordered=ordered)

        method = request_name(request)
        async with guard.reserve(method):
            try:
                return await super().__call__(request, ordered=ordered)
            except Exception as exc:
                await guard.note_failure(exc, method)
                raise


def build_client(
    config: Config,
    *,
    session_base: Path | None = None,
    guard: RpcGuard | None = None,
) -> TelegramClient:
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
        guard: The global RPC limiter (SPEC-LIM-001). ``None`` only for
            ``auth.py``, where logging in precedes any budget.
    """
    base = session_base if session_base is not None else config.session_path
    return GuardedClient(
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
        guard=guard,
    )
