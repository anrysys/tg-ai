---
id: DOC-SAD
title: Software architecture
status: active
authority: authoritative
updated: 2026-09-05
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
        ├── tg_ai/safety.py          chunking, pacing, persona sanitisation (pure)
        ├── tg_ai/tg_client.py       Telethon: peers, contacts, sessions
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

### `server.py` writes to the Archive, but only to one table

The diagram above shows `sync_db.py` as the writer and `server.py` as a reader.
Since ADR-0008 that is no longer quite true: `tg_set_dialog_persona` writes, and
`tg_get_dialog_persona` refreshes the measurements. It writes to
`dialog_personas` and to nothing else. `dialogs` and `messages` remain owned by
`sync_db.py`, which is what lets a resync run without coordinating with a live
server.

## Why the session is copied for sync

Telethon stores its session in SQLite. Two processes writing one file produce
`database is locked`, and the file holds the only credential the project has.
`sync_db.py` therefore copies it and connects with the clone. The auth key is
the same, so Telegram sees one account with two connections - which it
permits - while the two processes never contend for the file
([ADR-0004](adr/0004-session-file-clone-for-sync.md)).

## Selecting what to sync

A full backfill walks every private Dialog. On an account with hundreds of them
that is hours of work punctuated by flood waits, and the conversations the user
actually cares about may be archived last.

Three selection modes exist, in increasing cost:

| Mode | Selects | API cost |
| --- | --- | --- |
| `--targets T [T ...]` | Dialogs matching the named people | The dialog list only. No peer resolution |
| default / `--full` | Every archivable private Dialog | The dialog list only |
| `--dialog T` | One peer, resolved through the API | A cold `ResolveUsername` if the peer is unknown |

`--targets` filters the list `SPEC-SYNC-001` already produced, so it can only
narrow that list - a bot or channel named as a target stays excluded. It is the
intended first step on a large account: archive the people who matter, then let
a plain `just tg-sync` catch up with the rest in the background.

`--dialog` is the escape hatch for someone the account has no Dialog with yet,
and is the only mode that resolves a peer through the API.

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

## Failure model

Every tool is wrapped in `safety.guarded_tool`. Nothing escapes:

| Condition | Becomes |
| --- | --- |
| `ToolError` (expected, message already written) | `ERROR: <message>`, no stack trace |
| A recognised Telegram error | A specific message naming the next step |
| Anything else | `ERROR: <type>: <message>`, logged with a traceback to stderr |

Diagnostics go to **stderr only**. A stray `print` to stdout corrupts the MCP
stream and takes the session down.
