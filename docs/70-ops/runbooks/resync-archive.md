---
id: DOC-RB-RESYNC
title: "Runbook: archive is stale, empty or unreachable"
status: active
authority: derived
updated: 2026-09-04
related: [DOC-DEPLOY, DOC-DATA-MODEL]
---

# Runbook: archive is stale, empty or unreachable

## Diagnose first

Ask the agent to call `tg_whoami()`. It distinguishes every case below.

## Search finds nothing that should exist

Check `tg_whoami()` output:

| It says | Meaning | Fix |
| --- | --- | --- |
| `Archive: 0 messages` | Never synced | `just tg-sync-full` |
| `Last sync:` is old | Stale | `just tg-sync` |
| Counts look right | The message is genuinely not there, or the wording differs | Retry with fewer, more distinctive words - the fallback substring path catches partial words |

Remember the division of labour: the Archive stops at the last sync. For
anything recent, use `tg_get_recent_messages`, which reads the live account.

## The database is unreachable

```text
Database: UNREACHABLE - ...
HINT:     start it with `just db-up`.
```

```bash
just db-up                        # start it, wait for healthy
docker compose ps                 # expect 127.0.0.1:5434->5432/tcp
docker compose logs postgres      # if it will not start
```

If the port is taken by another project, change it in `docker-compose.yml`
**and** in `DATABASE_URL` in `.env` - they must agree.

## A sync was interrupted

Nothing is needed. Cursors are advanced only after their rows commit
(`SPEC-SYNC-003`), so:

```bash
just tg-sync
```

resumes from where it stopped. Re-running over already-archived messages adds
zero rows (`SPEC-SYNC-002`).

## One dialog looks incomplete

```bash
just tg-sync -- --dialog @someone --full
```

`--full` ignores the cursor for that dialog and walks it from the beginning.
Existing rows are not duplicated.

## Rebuilding from scratch

Only when the schema changed incompatibly. **Destroys all archived history** and
requires a full re-download:

```bash
just db-reset       # drops the volume, recreates it, applies sql/schema.sql
just tg-sync-full
```

## Checking directly

```bash
just db-psql
```

```sql
SELECT count(*) FROM messages;
SELECT username, last_synced_message_id, synced_at FROM dialogs ORDER BY synced_at DESC LIMIT 10;
```
