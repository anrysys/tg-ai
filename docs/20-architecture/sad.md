---
id: DOC-SAD
title: Software architecture
status: active
authority: authoritative
updated: 2026-09-04
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
   └───────────────────────────────┘                              └──────────┘
```

| Process | Runs | Reads | Writes | Talks to Telegram |
| --- | --- | --- | --- | --- |
| `auth.py` | Once, interactively | nothing | the Session | yes |
| `sync_db.py` | On demand / periodically | the Session Clone | the Archive | yes, read-only |
| `server.py` | Spawned by the agent | the Session, the Archive | Telegram messages | yes |

## Layering

```text
server.py / sync_db.py / auth.py     entrypoints: orchestration and I/O only
        │
        ├── tg_ai/formatting.py      data -> agent-readable text
        ├── tg_ai/safety.py          chunking, pacing, error translation (pure)
        ├── tg_ai/tg_client.py       Telethon: peers, contacts, sessions
        ├── tg_ai/db.py              asyncpg: raw SQL, no ORM (ADR-0006)
        └── tg_ai/config.py          the only module that reads the environment
```

Dependencies point downward only. `safety.py` imports nothing from the project
and no I/O library, which is what makes the anti-ban rules unit-testable
without a network or a database.

## Why the session is copied for sync

Telethon stores its session in SQLite. Two processes writing one file produce
`database is locked`, and the file holds the only credential the project has.
`sync_db.py` therefore copies it and connects with the clone. The auth key is
the same, so Telegram sees one account with two connections - which it
permits - while the two processes never contend for the file
([ADR-0004](adr/0004-session-file-clone-for-sync.md)).

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
