---
id: DOC-TASK-QUEUE
title: Task queue
status: active
authority: authoritative
updated: 2026-09-05
related: [DOC-ROADMAP, DOC-STATUS]
---

# Task queue

What to work on next. Tasks are closed, never deleted - a closed task is the
record that the question was already settled.

## Open

| ID | Task | Blocked on |
| --- | --- | --- |
| TASK-007 | Run the M5 live acceptance checklist | The user: needs real credentials and a real account |
| TASK-008 | Record the real archive size in `status.md` after the first full sync | TASK-007 |
| TASK-011 | Write a Dialog Persona for each active dialog, and judge whether drafted replies read as the user's own | The user: only they can tell |

## Open questions

Do not pick a default for these. Ask the user.

| ID | Question | Why it matters |
| --- | --- | --- |
| Q-001 | Should the archive be refreshed automatically (systemd timer), or only on demand? | Determines whether staleness is a real problem worth solving |
| Q-002 | Should media messages record *that* media was sent, even without archiving the file? | Currently such messages have `text IS NULL` and are invisible to search |

## Closed

| ID | Task | Outcome |
| --- | --- | --- |
| TASK-001 | Project scaffolding, tooling, dependency pinning | M0 |
| TASK-002 | Safety primitives: chunking, pacing, error translation | M0, proven by `tests/test_chunking.py` and `test_safety.py` |
| TASK-003 | Schema and asyncpg layer with FTS plus trigram fallback | M2, [ADR-0003](../20-architecture/adr/0003-postgres-fts-simple-plus-trigram.md) |
| TASK-004 | `auth.py` and the session-clone strategy | M1, M2, [ADR-0004](../20-architecture/adr/0004-session-file-clone-for-sync.md) |
| TASK-005 | `sync_db.py` with resumable cursors and flood handling | M2 |
| TASK-006 | MCP server with six tools and the Send Guard | M3, [ADR-0005](../20-architecture/adr/0005-hard-block-send-to-unknown-peers.md) |
| TASK-009 | Targeted sync (`--targets`, `just tg-sync-targets`) to avoid hours of flood waits on a large account | `SPEC-SYNC-006` |
| TASK-010 | Dialog Persona: per-Dialog style, measured in-repo and described by the agent | M6, [ADR-0008](../20-architecture/adr/0008-dialog-persona-hybrid-authorship.md), `SPEC-PSN-001`..`008` |
