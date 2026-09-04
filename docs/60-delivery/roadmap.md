---
id: DOC-ROADMAP
title: Roadmap
status: active
authority: authoritative
updated: 2026-09-04
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

## Beyond M5 - not scheduled

Each needs a PRD change and an ADR before any code.

| Idea | Blocked on |
| --- | --- |
| Scheduled background sync (systemd timer) | Deciding whether a stale archive is a real problem in practice |
| Media and file archiving | Storage strategy; currently a PRD non-goal |
| Group and channel archiving | PRD non-goal; would dominate the archive by volume |
| Semantic search over the archive | Requires an embedding pipeline; the trigram fallback may already be sufficient |
