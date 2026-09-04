---
id: ADR-0004
title: "Run the sync against a copy of the session file"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-SAD, DOC-SECURITY]
---

# ADR-0004 - Sync works on a copy of the session file

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** `SPEC-SEC-003`
**Affects:** `tg_ai/tg_client.clone_session`, `sync_db.py`

## Context

Telethon stores its session - the MTProto auth key, the datacentre, and an
entity cache - in a SQLite database. It writes to that database during normal
operation, not only at login.

[ADR-0002](0002-three-process-split-auth-sync-server.md) produced two long-lived
processes that both need the session: the MCP server the agent spawns, and the
sync. A user who runs `just tg-sync` while an agent session is open has two
SQLite writers on one file. That yields `database is locked` at best, and at
worst corrupts the file holding the only credential the project has.

## Decision

`sync_db.py` copies the session file to `<name>.sync.session` at startup and
connects with the copy. The auth key is identical, so Telegram sees one account
with two connections, which it permits. The two processes never touch the same
file.

`--in-place` opts out, for the initial backfill when no server is running.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| A lock file, refusing to sync while the server runs | Makes background syncing impossible, which is the normal way to keep the archive fresh |
| A second, independently authorised session | A second SMS login, and two credentials to secure instead of one |
| Telethon's `StringSession` held in memory by both | Still needs a persistent origin, and loses the entity cache that makes peer resolution cheap |
| Serialising all Telegram access through the MCP server | Turns an hours-long backfill into a stdio tool call. Rejected in ADR-0002 |

## Consequences

- `<name>.sync.session` exists alongside the primary session. It is git-ignored
  by the same `*.session` rule and gets mode `0600` on creation.
- It is a **full credential**. Deleting the primary session without deleting the
  clone leaves a live key on disk - the
  [session-lost runbook](../../70-ops/runbooks/session-lost-or-revoked.md) covers this.
- The clone's entity cache goes stale between syncs. Harmless: the sync
  re-reads dialogs each run.
- Revoking the session in Telegram invalidates the auth key, and therefore both
  files at once. That is the desired behaviour.
