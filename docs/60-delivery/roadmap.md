---
id: DOC-ROADMAP
title: Roadmap
status: active
authority: authoritative
updated: 2026-09-08
related: [DOC-STATUS, DOC-TASK-QUEUE, DOC-PRD]
---

# Roadmap

Milestones are ordered by dependency, not by date. A milestone is done when
every acceptance criterion is objectively verifiable.

## M0 - Foundation ✅ done

Repository, tooling and safety primitives.

- [x] `git init`, `.gitignore` covering `*.session` and `.env` **before** either can exist
- [x] `requirements.txt` (dependencies) and `pyproject.toml` (tooling only)
- [x] `Justfile` with the `<area>-<verb>` command surface
- [x] `docker-compose.yml`, PostgreSQL 16 on `127.0.0.1:5434`
- [x] `tg_ai/config.py`, `safety.py`, `db.py`, `tg_client.py`, `formatting.py`

**Acceptance:** `just py-check` green; `just db-up` healthy on loopback.

## M1 - Authentication ✅ done

- [x] `auth.py` drives phone → code → 2FA in a terminal
- [x] Session written with mode `0600`, identity verified against `TG_EXPECTED_USERNAME`

**Acceptance:** `just tg-auth` prints the account; `stat -c %a` is `600`;
`git status` does not list the session.

## M2 - Archive ✅ done

- [x] `sql/schema.sql` with composite PK, generated `tsvector`, GIN + trigram indexes
- [x] `sync_db.py`: private human chats only, batched `ON CONFLICT DO NOTHING`
- [x] Resumable cursors, flood-wait pauses, inter-dialog pacing

**Acceptance:** verified live - re-running a range adds 0 rows; a cursor never
moves backwards; full-text and substring searches both return correct hits in
English and Cyrillic.

## M3 - MCP server ✅ done

- [x] Six tools over stdio, lazy client and pool
- [x] Send Guard with no override flag
- [x] Chunking at 4000 chars with 2.5s pacing
- [x] Every tool returns text; none can raise

**Acceptance:** verified live - a real MCP client completed `initialize`,
`list_tools` and `call_tool`; stdout carried protocol only; a missing session
degraded to `ERROR:` text rather than a crash.

## M4 - Documentation base ✅ done

- [x] Zoned `docs/` with router, glossary, ID registry, status
- [x] SRS with 25 testable clauses; 7 ADRs
- [x] Maintenance protocol, documentation map, runbooks
- [x] `AGENTS.md` as sole authority; `CLAUDE.md` and Copilot instructions as pointers
- [x] `just docs-check` enforcing links and English-only

**Acceptance:** `just check` green; every document reachable from the router.

## M5 - Live acceptance ⬜ requires the user

Cannot be completed by an agent: needs real credentials and a real account.

- [ ] `just tg-auth` against the real account
- [ ] `just tg-sync-full` completes; record the message count in [status.md](../00-index/status.md)
- [ ] `tg_send_message` to a stranger returns the WARNING and sends nothing
- [ ] A 5000-character message arrives as 2 parts, ~2.5s apart, no broken word
- [ ] `tg_get_unread_dialogs` matches the Telegram app, and marks nothing read

**Acceptance:** every manual check in
[qa-and-testing.md](../50-process/qa-and-testing.md) passes.

---

## M6 - Dialog Persona 🟨 code complete, live acceptance pending

Per-Dialog style so a drafted reply sounds like the user
([ADR-0008](../20-architecture/adr/0008-dialog-persona-hybrid-authorship.md)).

- [x] `dialog_personas` table, applied and idempotent against PostgreSQL 16
- [x] `tg_ai/persona.py`: language-agnostic Style Metrics, 28 offline tests
- [x] Dialog Lookup: Archive-only Target resolution, ambiguity reported
- [x] Three tools: `tg_get_dialog_persona`, `tg_set_dialog_persona`,
      `tg_list_dialog_personas`
- [x] Persona block prepended by `tg_get_recent_messages`
- [x] Frozen Persona Baseline, verified against fixture data: 80 synthetic
      agent-style messages did not move the measurements
- [x] `RISK-07` sanitisation and the data fence
- [ ] Confirm on a real dialog that a drafted reply reads as the user's own
      (needs the user's judgement, not a test)

**Acceptance:** the persona rows in
[qa-and-testing.md](../50-process/qa-and-testing.md) pass on the live account.

---

## M7 - Groups and channels 🟨 code complete, live acceptance pending

Groups and channels, behind a safety envelope that also fixed four defects in
the pre-existing private-chat code
([ADR-0009](../20-architecture/adr/0009-groups-and-channels.md),
[ADR-0010](../20-architecture/adr/0010-one-connection-per-authorization-key.md)).

| Item | State |
| --- | --- |
| Client Identity and the Telethon constructor contract | ✅ `SPEC-SEC-007`, `SPEC-SEC-008` |
| Connection lock, one per authorization key | ✅ `SPEC-SEC-010` |
| Persisted RPC limiter, budgets and flood kill switch | ✅ `SPEC-LIM-001` .. `SPEC-LIM-007` |
| Peer model: groups, channels, posting rights | ✅ `SPEC-SND-001`, `SPEC-SND-008` |
| Opt-in group sync, capped at one request per target | ✅ `SPEC-SYNC-007` |
| Group reads with persisted cooldown and daily cap | ✅ `SPEC-RCV-003` |
| Personas disabled for groups and channels | ✅ `SPEC-PSN-009` |
| Blacklisted operations, enforced by a source scan | ✅ `SPEC-LIM-006` |
| Sustained live use over days, watching the flood log | ⬜ requires the user |

## M8 - Stealth reading 🟨 code complete, live acceptance pending

Reading never acknowledged anything and still does not; M8 proves that
behaviourally and binds the one read receipt this project can emit to the send
path ([ADR-0011](../20-architecture/adr/0011-read-receipt-on-send.md)).

| Item | State |
| --- | --- |
| Audit: no acknowledgment on any ingestion, fetch or sync path | ✅ `SPEC-RCV-003` |
| Read receipt after delivery, all three Peer Types | ✅ `SPEC-SND-009` |
| Off by default, failing closed on an unreadable config | ✅ `SPEC-SEC-012` |
| One call site, enforced by the source scan | ✅ `SPEC-LIM-006` |
| `is_flood_error` counts only real floods | ✅ `SPEC-LIM-003`, `RISK-12` |
| A live reply that marks exactly one chat read | ⬜ requires the user (TASK-017) |

## Beyond M5 - not scheduled

Each needs a PRD change and an ADR before any code.

| Idea | Blocked on |
| --- | --- |
| Scheduled background sync (systemd timer) | Deciding whether a stale archive is a real problem in practice |
| Media and file archiving | Storage strategy; currently a PRD non-goal |
| Semantic search over the archive | Requires an embedding pipeline; the trigram fallback may already be sufficient |
