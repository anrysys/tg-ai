---
id: DOC-STATUS
title: Project status
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ROADMAP, DOC-TASK-QUEUE]
---

# Project status

**Read this at the start of every session.** It states what is true right now.
Replace stale lines rather than appending to them.

## Current state

**The code is complete and verified. The project has never been run against a
real Telegram account** - that is M5, and it requires the user.

| Area | State |
| --- | --- |
| Scaffolding, tooling | Done. `just check` green |
| Safety primitives | Done. 53 offline tests passing |
| Database schema and access | Done. Verified live against PostgreSQL 16 |
| `auth.py` | Written, **never executed** - needs real credentials |
| `sync_db.py` | Written, **never executed against Telegram** |
| `server.py` | Done. Verified over a real MCP stdio session |
| Documentation base | Done |

## Verified

Empirically, on this machine:

- 53 offline tests pass; `ruff` and `black` clean.
- PostgreSQL 16 on `127.0.0.1:5434` is healthy; `sql/schema.sql` applies cleanly.
- Idempotency: re-inserting the same messages added **0** rows.
- Cursor monotonicity: advancing to a lower id left the cursor at its higher value.
- Search: full-text matched both a Latin-script query (`migration`) and a
  Cyrillic-script one; the trigram fallback matched a partial word
  (`Khresh`); the username filter correctly scoped results.
- A real MCP client completed `initialize`, `list_tools` and `call_tool` against
  `server.py` over stdio. All six tools registered with correct schemas, and
  stdout carried protocol traffic only.
- `markdownlint-cli2`: 0 issues across 49 markdown files.
- Tests are independent of the developer's local `.env` (proven by running the
  suite with and without the file present).
- Graceful degradation: with no session file **and no API credentials**,
  `tg_search_local_history` worked normally, `tg_send_message` returned `ERROR:`
  text rather than crashing, and `tg_whoami` reported each subsystem separately
  with the command that fixes it.
- `.gitignore` proven: `git check-ignore` matches `tg_session.session`,
  `tg_session.sync.session` and `.env`; none appear in `git status -uall`.

## Not yet verified

Everything requiring a real account. See M5 in the
[roadmap](../60-delivery/roadmap.md) and the manual checklist in
[qa-and-testing.md](../50-process/qa-and-testing.md).

- Interactive login.
- Any real send, therefore the Send Guard end to end.
- Any real sync, therefore chunking and pacing against the live API.

## Archive contents

Empty. The fixture data used for verification was truncated.

Record the real numbers here after the first `just tg-sync-full` (TASK-008).

## Environment facts

| Fact | Value |
| --- | --- |
| Python | 3.12.3 |
| Database | `tg-ai-postgres`, PostgreSQL 16, `127.0.0.1:5434` |
| Why 5434 | 5432 and 5433 are taken by other projects on this machine |
| Session file | `tg_session.session`, repository root, mode `0600` |
| Account | `@anrysys` (`TG_EXPECTED_USERNAME`) |

## Next action

Run [first-time setup](../70-ops/runbooks/first-time-setup.md) - TASK-007.
