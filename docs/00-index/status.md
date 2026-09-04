---
id: DOC-STATUS
title: Project status
status: active
authority: authoritative
updated: 2026-09-05
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
| `sync_db.py` | Written, **never executed against Telegram**. Supports `--targets` for a partial first backfill |
| `server.py` | Done. Verified over a real MCP stdio session. Nine tools |
| Dialog Persona | Code complete (M6). Exercised end to end against the live PostgreSQL with fixture data; never yet written for a real person |
| Documentation base | Done |

## Verified

Empirically, on this machine:

- 152 offline tests pass; `ruff` and `black` clean.
- PostgreSQL 16 on `127.0.0.1:5434` is healthy; `sql/schema.sql` applies cleanly.
- Idempotency: re-inserting the same messages added **0** rows.
- Cursor monotonicity: advancing to a lower id left the cursor at its higher value.
- Search: full-text matched both a Latin-script query (`migration`) and a
  Cyrillic-script one; the trigram fallback matched a partial word
  (`Khresh`); the username filter correctly scoped results.
- A real MCP client completed `initialize`, `list_tools` and `call_tool` against
  `server.py` over stdio. All tools registered with correct schemas, and
  stdout carried protocol traffic only. `list_tools` now reports nine.
- `markdownlint-cli2`: 0 issues across 49 markdown files.
- Target filtering verified against a stubbed dialog list: correct selection,
  dialog order preserved, unmatched targets warned about, and a bot named as a
  target still excluded by `SPEC-SYNC-001`.
- Tests are independent of the developer's local `.env` (proven by running the
  suite with and without the file present).
- Graceful degradation: with no session file **and no API credentials**,
  `tg_search_local_history` worked normally, `tg_send_message` returned `ERROR:`
  text rather than crashing, and `tg_whoami` reported each subsystem separately
  with the command that fixes it.
- `.gitignore` proven: `git check-ignore` matches `tg_session.session`,
  `tg_session.sync.session` and `.env`; none appear in `git status -uall`.
- Dialog Persona, against the live database with fixture data: `dialog_personas`
  applies idempotently; an ambiguous first name listed both candidates and chose
  neither; a `notes` field carrying a URL was refused; a second write without
  `overwrite` changed nothing; `ON DELETE CASCADE` removed the persona with its
  dialog. Incoming messages did not influence the measurements.
- **The feedback-loop guard works.** 80 synthetic agent-style messages (long,
  formal, punctuated) were archived as outgoing and the persona was then
  overwritten. `baseline_message_id` did not move, `analysed_count` stayed at 60,
  and the metrics still read "typical message is 2 characters" - the persona did
  not absorb its own output (`SPEC-PSN-003`).
- Freshness flipped to `STALE - volume (80 of your messages archived since)`
  after that, naming the axis that fired.

## Not yet verified

Everything requiring a real account. See M5 in the
[roadmap](../60-delivery/roadmap.md) and the manual checklist in
[qa-and-testing.md](../50-process/qa-and-testing.md).

- Any real send, therefore the Send Guard end to end.
- A full backfill (`just tg-sync-full`); the archive holds 5 dialogs, not all.
- Whether a reply drafted from a Dialog Persona actually reads as the user's own.
  Only the user can judge that (TASK-011).
- Pattern Drift on real data: it needs a persona old enough to have drifted.

## Archive contents

**No longer empty** - a sync has been run against the real account since this
section last said otherwise.

| Fact | Value |
| --- | --- |
| Messages | 17,306 |
| Dialogs | 5 |
| Coverage | 2019-01-03 .. 2026-09-03 |
| Last sync | 2026-09-04 |
| Dialog Personas | 0 - none written yet (TASK-011) |

Numbers read from `db.archive_stats` on 2026-09-04. A full backfill has not been
confirmed, so TASK-008 stays open until `just tg-sync-full` completes.

## Environment facts

| Fact | Value |
| --- | --- |
| Python | 3.12.3 |
| Database | `tg-ai-postgres`, PostgreSQL 16, `127.0.0.1:5434` |
| Why 5434 | 5432 and 5433 are taken by other projects on this machine |
| Session file | `tg_session.session`, repository root, mode `0600` |
| Account | `@anrysys` (`TG_EXPECTED_USERNAME`) |

## Next action

Write a Dialog Persona for the busiest dialog with
`tg_get_dialog_persona` then `tg_set_dialog_persona`, draft one reply from it,
and judge whether it reads as the user's own - TASK-011.
