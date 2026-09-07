---
id: ADR-0010
title: "Serialise every Telegram connection behind one lock per authorization key"
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-ADR-INDEX, ADR-0004, DOC-SAD, DOC-SECURITY, DOC-SRS]
---

# ADR-0010 - One connection per authorization key

**Status:** Accepted
**Date:** 2026-09-07
**Decides:** `SPEC-SEC-010`, `RISK-08`
**Affects:** `tg_ai/tg_client.SessionLock`, `tg_ai/config.session_lock_file`,
`auth.py`, `server.py`, `sync_db.py`

## Context

[ADR-0004](0004-session-file-clone-for-sync.md) has `sync_db.py` copy the
Session to `<name>.sync.session` so two processes never write one SQLite file.
That reasoning is correct and still holds. It also left a worse hole open, and
nothing in the repository guarded it.

**The clone shares the primary's authorization key.** ADR-0004 says so
explicitly - "the auth key is identical, so Telegram sees one account with two
connections, which it permits". That sentence is the defect. Telegram permits
*some* parallelism, and punishes exceeding it. From
[the error documentation](https://core.telegram.org/api/errors), on 406
`AUTH_KEY_DUPLICATED`:

> Parallel sessions were opened on non-media data centers beyond the permitted
> limit. When received, the session is already invalidated and the user must
> re-authenticate.

Note **"already invalidated"**. This is not a warning that arrives in time to
back off. By the time the error is visible the login is gone, and the only
recovery is `just tg-auth` with a fresh SMS code.

Three things made this worse than theoretical:

- Nothing stopped `just tg-sync` running while the MCP server was connected.
  The server is spawned by the user's editor and stays connected for hours.
- `--in-place` was documented as "the unsafe mode", which actively implies the
  clone is the safe one. It is not; it is the mode that duplicates the key.
- Group and channel support makes sync runs longer, which makes overlap likelier.

There is a second, quieter failure in the same area: `clone_session()` copies a
live SQLite database with `shutil.copy2`. Copying a file the server may be
mid-write on can produce a torn clone.

## Decision

Every process that is about to `connect()` MUST first take an exclusive
`fcntl.flock` on `<session_name>.lock`, and MUST hold it until it disconnects.
The lock is derived from the **primary** session name, never from the clone, so
the server and the sync contend for the same file.

The loser does **not** wait and does **not** retry. `sync_db.py` exits with
status 1; `server.py` raises `ToolError`, which `@guarded_tool` renders as
`ERROR:` text. Queueing would merely connect later and hit the same wall, and a
retry loop against a resource held for hours is not a recovery.

`sync_db.py` takes the lock **before** cloning, so a locked-out run copies
nothing and cannot produce a torn clone.

The lock covers all three entrypoints: `auth.py`, `server.py`, `sync_db.py`.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Leave it; Telegram "permits" two connections | It permits a limit and punishes the excess by invalidating the login. The cost is not a failed call, it is a lost account until an SMS arrives |
| Block and wait for the other process | The MCP server holds its connection for the length of an editor session. A sync would wait hours, and the user would read it as a hang |
| Retry with backoff | Retrying is what AGENTS.md forbids for flood waits, for the same reason: the limit is on the account, not the call |
| A second, independently authorised session | Rejected in ADR-0004 already: a second SMS login and a second full credential to secure |
| Serialise all Telegram access through the MCP server | Rejected in ADR-0002: it turns an hours-long backfill into a stdio tool call |
| A PID file checked at startup | Racy, and it goes stale when a process is killed. `flock` is released by the kernel when the holder dies |

## Consequences

- **This does not supersede ADR-0004.** The SQLite reasoning there is still
  correct. This closes the hole ADR-0004 left open, by making the clone safe
  *only* under mutual exclusion. ADR-0004 stays Accepted.
- `just tg-sync` now fails fast while an agent session is open. That is a
  behaviour change the user will notice, and it is the point.
- `<session_name>.lock` appears next to the session and is git-ignored by a
  `*.lock` rule. It holds a process description, never a credential.
- A killed or crashed holder leaves nothing behind: `flock` lives on the open
  file description and the kernel drops it on exit. Proved by
  `tests/test_connection_lock.py::test_a_crashed_holder_does_not_leave_the_lock_stuck`.
- `AuthKeyDuplicatedError` is translated explicitly (`SPEC-SND-004`) and says
  plainly that the session is already dead, because the natural agent reaction
  to an untranslated error is to retry, and there is nothing left to retry.
- `--in-place` remains, and remains the mode for a first backfill with no
  server running. It is no longer the only thing standing between the user and
  a duplicated key.
