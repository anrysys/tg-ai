---
id: DOC-STATUS
title: Project status
status: active
authority: authoritative
updated: 2026-09-08
related: [DOC-ROADMAP, DOC-TASK-QUEUE]
---

# Project status

**Read this at the start of every session.** It states what is true right now.
Replace stale lines rather than appending to them.

## Current state

**Reading is stealth and always has been; as of M8 a reply can optionally mark
its own chat read.** Nothing that fetches, searches or syncs acknowledges
anything, and that is now enforced behaviourally as well as by the source
blacklist. `tg_send_message` marks the target Dialog read after delivering into
it, but only when `TG_READ_ON_SEND` is set, and it is off by default
([ADR-0011](../20-architecture/adr/0011-read-receipt-on-send.md)). Groups and
channels remain supported behind the M7 safety envelope. The code has been
exercised against the real account for reads and syncs; **no message has ever
been sent** by this project, which is still M5 and needs the user.

| Area | State |
| --- | --- |
| Scaffolding, tooling | Done. `just check` green |
| Safety primitives | Done. Chunking, pacing, error translation, plus the persisted RPC limiter, budgets, kill switch and quiet window |
| Database schema and access | Done. Verified live against PostgreSQL 16 |
| `auth.py` | Written, **never executed** - a session already exists, so re-login has not been needed |
| `sync_db.py` | Verified against the real account. `--targets` also selects groups and channels, capped at 5 per run and one request each |
| `server.py` | Done. Nine tools, all verified against the real account. Reads groups and channels under a persisted cooldown, and never acknowledges a read. Shuts down cleanly on stdin EOF and Ctrl-C (`SPEC-SEC-011`) |
| Dialog Persona | Code complete (M6). Exercised end to end against the live PostgreSQL with fixture data; never yet written for a real person |
| Groups and channels | Code complete (M7). Read, sync, send guard and caps all verified live |
| Read receipts | Code complete (M8). Bound to delivery, off by default, covered offline; **never exercised live** - no message has been sent |
| Documentation base | Done |
| Public presentation | Done. README rewritten for discoverability, `USE-CASES.md` added, Russian mirror under `docs/i18n/ru/`, generated GitHub Pages site with JSON-LD |

## Verified

Empirically, on this machine:

- `server.py` no longer orphans itself. It never released anything on exit, so
  once a tool had connected, Telethon's background tasks kept the event loop
  alive and the process outlived its client holding the connection `flock` and
  the SQLite session - which is what made a later server die on `database is
  locked`. Measured before and after: Ctrl-C exited **-2** with a 72-line
  traceback and now exits **0** with one log line; stdin EOF leaves no process
  behind, and stdout stays empty in both cases. The teardown order is
  pinned by `SPEC-SEC-011` and by three mutations of `server.py` confirmed to
  turn the new tests red. The live path - a tool connects, then the client goes
  away - has not yet been exercised against the real account.
- 399 offline tests pass, 3 skipped (all three are `FloodPremiumWaitError`,
  which does not exist in telethon 1.36.0; the tests say so rather than
  pretending to cover it); `ruff` and `black` clean.
- The read receipt was checked by deliberately breaking it, the same way the
  M7 safety tests were. Removing the `TG_READ_ON_SEND` guard turns the
  default-off test red; moving the acknowledgment above the send loop turns the
  partial-send test red; adding a second `send_read_acknowledge` call site into
  `tg_get_recent_messages` turns **two** tests red - the blacklist shape test
  and the behavioural one that reads with the flag deliberately on.
- `is_flood_error` was returning `describe_telegram_error`'s message strings for
  eight non-flood conditions. Every string is truthy and the only caller tests
  truthiness, so `ChatAdminRequiredError`, `ChannelPrivateError` and four others
  counted toward the kill switch: **three ordinary permission errors in an hour
  would have stopped the whole account for 24 hours** (`RISK-12`). It now
  returns a real `bool` over exactly the four conditions `SPEC-LIM-003` names,
  and re-introducing one string branch turns the new tests red.
- CI reproduces that result again. It had been failing on `main`: `ci.yml`
  invoked bare `pytest`, which leaves the repository root off `sys.path`,
  so `tests/conftest.py` could not `import tg_ai`. Both the failure and the
  fix were reproduced locally before the change.
- PostgreSQL 16 on `127.0.0.1:5434` is healthy; `sql/schema.sql` applies cleanly.
- Idempotency: re-inserting the same messages added **0** rows.
- Cursor monotonicity: advancing to a lower id left the cursor at its higher value.
- Search: full-text matched both a Latin-script query (`migration`) and a
  Cyrillic-script one; the trigram fallback matched a partial word
  (`Khresh`); the username filter correctly scoped results.
- A real MCP client completed `initialize`, `list_tools` and `call_tool` against
  `server.py` over stdio. All tools registered with correct schemas, and
  stdout carried protocol traffic only. `list_tools` now reports nine.
- `markdownlint-cli2`: 0 issues across the markdown tree.
- `scripts/check_links.py`: 310 relative links resolve.
- The generated site rebuilds byte-identically from `README.md` and
  `USE-CASES.md` (`just site-check`), and its JSON-LD parses as one
  `SoftwareApplication`, one `HowTo` of 9 steps and one `FAQPage` of 9
  questions - all extracted from the page rather than hand-written.
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

Against the real account, on 2026-09-07 (M7):

- The RPC limiter meters real traffic: `just tg-status` reported `57/60`
  requests left after a connect, and `api_call_log` showed the three connect
  RPCs spaced 1.75 s and 1.59 s apart - both above the 1.5 s floor, both
  jittered.
- Syncing one group produced **exactly one** `GetHistoryRequest` and 100 rows.
  A second run added 0. A run naming 6 groups aborted at the cap of 5, naming
  all six. **The sync sent no read receipt** - provable because every RPC is
  logged and no `ReadHistory` appears. That is still true of every read and
  sync path; since M8 the one code path that can acknowledge is
  `tg_send_message`, after delivery, and only with `TG_READ_ON_SEND` on.
- The group read cooldown **survives a process restart**: a brand new
  interpreter still refused, naming the seconds remaining. That is the whole
  argument for keeping this state in PostgreSQL.
- A send to a channel the account only subscribes to was refused for lack of
  posting rights, with no API call. A 9000-character group message was refused
  rather than split. `@durov`, a channel this account has not joined, was
  refused without contacting Telegram at all.
- Both Persona tools refused a group.
- `just tg-sync` refused to start while another process held the connection
  lock, exited 1, and **created no session clone**.
- A synthetic quiet window blocked a sync before it cloned anything; a manually
  tripped kill switch blocked it with a message rather than a traceback.
- The `contacts.getContacts` hash now earns `contactsContactsNotModified`. Two
  bugs were found only by running it: the field is a **signed** long (the
  unsigned accumulator raised `struct.error`), and the documented algorithm
  folds in `saved_count`, which on this account is 372 against 502 returned
  users.
- The peer index classified 198 dialogs as 125 users, 59 channels and 14
  groups; the two channels the account administers reported `can_post=True` and
  every subscriber-only channel `False`.
- Three safety tests were checked by deliberately breaking the code: removing
  the limiter's re-entrancy guard makes both nested tests fail in ~2 s rather
  than hang, removing the cold-resolution guard makes the peer tests fail on
  "the client was called", and adding a blacklisted name to `server.py` fails
  the source scan.

## Not yet verified

Everything requiring a real account. See M5 in the
[roadmap](../60-delivery/roadmap.md) and the manual checklist in
[qa-and-testing.md](../50-process/qa-and-testing.md).

- Any real send, therefore the Send Guard end to end. Every refusal path has
  been exercised; no successful send has.
- The read receipt against the real account. It has never run outside the
  offline suite, because it is only reachable after a successful send. The
  manual checks are in
  [qa-and-testing.md](../50-process/qa-and-testing.md): a reply with
  `TG_READ_ON_SEND=true` should add exactly one `ReadHistory` to `api_call_log`
  *after* the send and clear that chat's badge, and a read with the same flag on
  should add none.
- A full backfill (`just tg-sync-full`); the archive holds 7 dialogs, not all.
  It will now also meet the 500/day request budget and resume the next day.
- Sustained use over days, which is what would show whether the flood log
  stays empty in practice (M7).
- Whether a reply drafted from a Dialog Persona actually reads as the user's own.
  Only the user can judge that (TASK-011).
- Pattern Drift on real data: it needs a persona old enough to have drifted.
- Whether the GitHub Pages deployment succeeds: Pages is not enabled on the
  repository yet, and enabling it is the user's call.

## Archive contents

**No longer empty** - a sync has been run against the real account since this
section last said otherwise.

| Fact | Value |
| --- | --- |
| Messages | 18,449 |
| Dialogs | 7 - six private, one group |
| Coverage | 2019-01-03 .. 2026-09-07 |
| Last sync | 2026-09-07 |
| Dialog Personas | 0 - none written yet (TASK-011) |

Numbers read from `db.archive_stats` on 2026-09-07. A full backfill has not been
confirmed, so TASK-008 stays open until `just tg-sync-full` completes. The one
group holds exactly 100 messages, which is one request: group history is a
rolling window rather than a backfill (`SPEC-SYNC-007`).

## Environment facts

| Fact | Value |
| --- | --- |
| Python | 3.12.3 |
| Database | `tg-ai-postgres`, PostgreSQL 16, `127.0.0.1:5434` |
| Why 5434 | 5432 and 5433 are taken by other projects on this machine |
| Session file | `tg_session.session`, repository root, mode `0600` |
| Account | `@anrysys` (`TG_EXPECTED_USERNAME`) |

## Next action

Two, in either order. Write a Dialog Persona for the busiest dialog with
`tg_get_dialog_persona` then `tg_set_dialog_persona`, draft one reply from it,
and judge whether it reads as the user's own - TASK-011. And **set
`TG_LANG_CODE` in `.env` to the language of the Telegram app on the phone**: it
currently defaults to `en`, which is the safe default for a public repository
and the wrong value for most people (`SPEC-SEC-007`, TASK-014).
