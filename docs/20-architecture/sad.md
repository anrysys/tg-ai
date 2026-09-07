---
id: DOC-SAD
title: Software architecture
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-SRS, DOC-DATA-MODEL, DOC-ADR-INDEX, DOC-DEPLOY]
---

# Software architecture

## Three processes, one credential

MCP servers communicate over stdio: stdin and stdout carry the protocol. A
server therefore cannot prompt for an SMS code, which login requires. That
single fact produces the process split ([ADR-0002](adr/0002-three-process-split-auth-sync-server.md)).

```text
   ┌──────────────┐  phone + SMS code (interactive terminal)
   │   auth.py    │──────────────────────────────┐
   └──────────────┘                              │ writes
                                                 ▼
                                    ┌───────────────────────────┐
                                    │  tg_session.session       │
                                    │  (MTProto auth key, 0600) │
                                    └───────────┬───────────────┘
                        reads a copy            │ reads
             ┌──────────────────────────────────┴─────────────┐
             │                                                │
   ┌─────────▼─────────┐                          ┌───────────▼───────────┐
   │    sync_db.py     │                          │      server.py        │
   │  history dumper   │                          │  MCP server (stdio)   │
   └─────────┬─────────┘                          └───────────┬───────────┘
             │ writes                          reads          │        ▲
             ▼                                                │        │ MCP
   ┌───────────────────────────────┐  ◄──────────────────────-┘        │
   │  PostgreSQL 16  (127.0.0.1)   │                              ┌────┴─────┐
   │  dialogs + messages + FTS     │                              │ AI agent │
   │  + dialog_personas            │                              │          │
   └───────────────────────────────┘                              └──────────┘
```

| Process | Runs | Reads | Writes | Talks to Telegram |
| --- | --- | --- | --- | --- |
| `auth.py` | Once, interactively | nothing | the Session | yes |
| `sync_db.py` | On demand / periodically | the Session Clone | the Archive | yes, read-only |
| `server.py` | Spawned by the agent | the Session, the Archive | Telegram messages, `dialog_personas` | yes |

## Layering

```text
server.py / sync_db.py / auth.py     entrypoints: orchestration and I/O only
        │
        ├── tg_ai/formatting.py      data -> agent-readable text
        ├── tg_ai/safety.py          chunking, pacing, budgets, quiet window (pure)
        ├── tg_ai/tg_client.py       Telethon: peers, contacts, sessions, the RPC limiter
        ├── tg_ai/db.py              asyncpg: raw SQL, no ORM (ADR-0006)
        │       └── tg_ai/persona.py style measurement (pure, stdlib only)
        └── tg_ai/config.py          the only module that reads the environment
```

Dependencies point downward only. `safety.py` and `persona.py` import nothing
from the project and no I/O library, which is what makes the anti-ban rules and
the style measurement unit-testable without a network or a database.

`db.py` and `tg_client.py` are peers and must never import each other. Where
both are needed - resolving a Target against the Archive rather than the live
account - the composition happens in `server.py`, at the entrypoint.

### How the RPC limiter spans that boundary without crossing it

The global limiter (`SPEC-LIM-001`) needs two things that live on opposite sides
of that line: the Telethon client, and rate-limit state in PostgreSQL. It is
split rather than allowed to import across:

- `tg_ai/safety.py` holds the **decisions** - is this inside the quiet window,
  is the budget spent, does this flood count trip the switch - as pure functions
  over numbers someone else fetched. That is what keeps them unit-testable.
- `tg_ai/tg_client.py` declares an `RpcLedger` `Protocol` next to its only
  consumer, and `RpcGuard` calls it. The Protocol is structural, so nothing is
  imported in either direction.
- `tg_ai/db.py` provides `PostgresRpcLedger`, which satisfies that Protocol
  without knowing it exists.
- `server.py` and `sync_db.py` wire the two together, as entrypoints do.

`RpcGuard` is a plain object taking an injected clock and sleep, so the offline
test suite can exercise pacing, budgets and the kill switch without constructing
a client, a session file or a database.

### `server.py` writes to the Archive, but only to one table

The diagram above shows `sync_db.py` as the writer and `server.py` as a reader.
Since ADR-0008 that is no longer quite true: `tg_set_dialog_persona` writes.
`tg_get_dialog_persona` re-measures the frozen window on every call but stores
nothing, so it stays a reader. Writes go to `dialog_personas` and to nothing
else. `dialogs` and `messages` remain owned by
`sync_db.py`, which is what lets a resync run without coordinating with a live
server.

## Why the session is copied for sync

Telethon stores its session in SQLite. Two processes writing one file produce
`database is locked`, and the file holds the only credential the project has.
`sync_db.py` therefore copies it and connects with the clone
([ADR-0004](adr/0004-session-file-clone-for-sync.md)).

**The clone does not make concurrent access safe on its own.** It shares the
primary's authorization key, and Telegram answers parallel sessions past its
limit with `AUTH_KEY_DUPLICATED` - at which point the login is already gone. So
every process takes an exclusive `flock` on `<session_name>.lock`, keyed to the
primary session name, before connecting; the loser exits rather than waiting
([ADR-0010](adr/0010-one-connection-per-authorization-key.md)). `sync_db.py`
takes it before cloning, so a locked-out run also cannot copy a file the server
is mid-write on.

## Selecting what to sync

A full backfill walks every private Dialog. On an account with hundreds of them
that is hours of work punctuated by flood waits, and the conversations the user
actually cares about may be archived last.

Three selection modes exist, in increasing cost:

| Mode | Selects | API cost |
| --- | --- | --- |
| `--targets T [T ...]` | Dialogs matching the named Peers, **including Groups and Channels** | The dialog list, plus one `getHistory` per target |
| default / `--full` | Every archivable private Dialog | The dialog list only |
| `--dialog T` | One person, resolved through the API | A cold `ResolveUsername` if the peer is unknown |

`--targets` filters the list the dialog walk already produced, so it can only
narrow it - a bot or a Group the account has left stays excluded. It is the
intended first step on a large account: archive the people who matter, then let
a plain `just tg-sync` catch up with the rest in the background.

It is also the **only** way to reach a Group or Channel. A default run archives
private chats only, and each Group target costs exactly one `getHistory` of 100
messages, with at most 5 targets per run and 20 reads per rolling day
(`SPEC-SYNC-007`).

`--dialog` is the escape hatch for someone the account has no Dialog with yet,
and is the only mode that resolves a peer through the API. It stays **user-only
by construction**: `resolve_peer` permits cold resolution only for a caller that
will accept nothing but a `User`, so this mode cannot reach a Group or Channel
even by accident (`SPEC-SND-006`).

## Why the archive is separate from the live account

Telegram's search API is rate-limited, slow over long histories, and every call
against it is a small ban risk. Copying history into PostgreSQL once turns
"search everything I ever said" into a local index scan that costs nothing and
carries no risk. The trade-off is staleness: the Archive is current only as of
the last sync, which is why `tg_get_recent_messages` reads the live account and
`tg_search_local_history` states when a result set is empty that the Archive may
need refreshing.

## Resource lifecycle in the MCP server

Both the Telethon client and the asyncpg pool are created lazily, on first use,
and shared for the life of the process:

- A registered but unused server costs nothing.
- `tg_search_local_history` works with no Telegram session and no API
  credentials at all - which is what makes search survive a flood-limited,
  logged-out or not-yet-configured account. The credential check lives in
  `server.telegram()`, not in configuration loading, precisely so a
  database-only tool is never blocked by a missing API key.
- `tg_whoami` reports each subsystem independently, so a broken session is
  distinguishable from an unreachable database.

Teardown is the other half, and it is not optional (`SPEC-SEC-011`). Over stdio
the client never says goodbye - it closes the pipe - so `server.py` registers a
FastMCP `lifespan` whose exit disconnects the Telethon client, releases the
connection lock and closes the pool, in that order:

- **Disconnect first.** Telethon's background tasks are cancelled only by
  `disconnect()`, and with `auto_reconnect=True` they respawn instead of ending
  if the loop simply tears down. A process that never exits keeps the `flock`
  and the SQLite session, and the next server dies on `database is locked`.
- **Then the lock.** Releasing it before the socket is gone opens the window in
  which a sync connects on the same authorization key (`SPEC-SEC-010`,
  [ADR-0010](adr/0010-one-connection-per-authorization-key.md)).
- **The pool last.** Telethon's auto-reconnect callback issues a `get_me()`
  that reaches the RPC ledger, so the database must outlive anything that could
  still be recording a call (`SPEC-LIM-002`).

Each step is bounded by a timeout and cannot raise: shutdown is the one place
where an exception has nowhere to go, and abandoning the remaining steps is
worse than any single failure. The lifespan starts nothing - connecting on
startup would take the session lock every time an editor spawns the server.

## Failure model

Every tool is wrapped in `safety.guarded_tool`. Nothing escapes:

| Condition | Becomes |
| --- | --- |
| `ToolError` (expected, message already written) | `ERROR: <message>`, no stack trace |
| A recognised Telegram error | A specific message naming the next step |
| Anything else | `ERROR: <type>: <message>`, logged with a traceback to stderr |

Diagnostics go to **stderr only**. A stray `print` to stdout corrupts the MCP
stream and takes the session down.
