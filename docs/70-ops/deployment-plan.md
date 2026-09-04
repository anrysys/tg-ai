---
id: DOC-DEPLOY
title: Deployment plan
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-SAD, DOC-SECURITY, DOC-RUNBOOK-INDEX]
---

# Deployment plan

Local machine only. There is no staging and no production
([RISK-05](security.md#risk-05---connecting-from-an-unfamiliar-ip)).

## Prerequisites

| Requirement | Check |
| --- | --- |
| Python 3.12+ | `python3 --version` |
| `uv` | `uv --version` |
| `just` | `just --version` |
| Docker | `docker ps` |
| Telegram API credentials | <https://my.telegram.org> → API development tools |

## Order of operations

Each step depends on the one before it.

### 1. Install

```bash
cd /home/anry/projects/tg-ai
just setup
```

Creates `.venv`, installs `requirements.txt`, and copies `.env.example` to
`.env` if it does not exist.

### 2. Configure

Edit `.env` and set `TG_API_ID` and `TG_API_HASH`. Everything else has a working
default.

### 3. Start the archive database

```bash
just db-up
```

Starts `tg-ai-postgres` and blocks until it is healthy.

Port **5434** is used because 5432 and 5433 are already taken on this machine.
The equivalent without compose:

```bash
docker run -d --name tg-ai-postgres -p 127.0.0.1:5434:5432 \
  -e POSTGRES_USER=tgai -e POSTGRES_PASSWORD=tgai -e POSTGRES_DB=tgai \
  -v tg-ai-pgdata:/var/lib/postgresql/data postgres:16-alpine
```

The `127.0.0.1:` prefix is mandatory. Verify:

```bash
docker compose ps      # must show 127.0.0.1:5434->5432/tcp, never 0.0.0.0
```

### 4. Log in to Telegram

```bash
just tg-auth
```

Interactive: phone number, then the code Telegram sends, then a 2FA password if
one is set. **Must be run in a real terminal** - the MCP server cannot do this
([ADR-0002](../20-architecture/adr/0002-three-process-split-auth-sync-server.md)).

Verify:

```bash
stat -c %a tg_session.session      # must print 600
git status --porcelain             # must not list the session file
```

### 5. Fill the archive

```bash
just tg-sync-full
```

Hours on a busy account, and it will pause on flood waits. Safe to interrupt:
progress is saved per dialog and `just tg-sync` resumes from the cursor.

Afterwards, keep it fresh with plain `just tg-sync` - incremental, minutes.

### 6. Register with the agent

**Claude Code:**

```bash
just mcp-add
```

which runs:

```bash
claude mcp add tg-ai -s user -- \
  /home/anry/projects/tg-ai/.venv/bin/python /home/anry/projects/tg-ai/server.py
```

**Any other MCP client** (Antigravity, Cursor, Claude Desktop) - add to its MCP
configuration:

```json
{
  "mcpServers": {
    "tg-ai": {
      "command": "/home/anry/projects/tg-ai/.venv/bin/python",
      "args": ["/home/anry/projects/tg-ai/server.py"],
      "cwd": "/home/anry/projects/tg-ai"
    }
  }
}
```

Absolute paths are required: the agent spawns the process with no shell and no
inherited virtualenv. `cwd` matters because `.env` and the session file are
resolved relative to the repository root.

### 7. Verify

Restart the agent, then ask it to call `tg_whoami()`. Expect the account, the
session path, `Database: reachable`, and non-zero archive counts.

## Keeping it running

| Task | Command | When |
| --- | --- | --- |
| Refresh the archive | `just tg-sync` | Daily, or before a broad search |
| Restart the database | `just db-up` | After a reboot |
| Full check | `just check` | Before reporting any code change done |

`just tg-sync` is safe to run while an agent session is open - it works on a
session clone ([ADR-0004](../20-architecture/adr/0004-session-file-clone-for-sync.md)).

## Rollback

| To undo | Do |
| --- | --- |
| The MCP registration | `just mcp-remove` |
| The database, keeping data | `just db-down` |
| The database and all archived history | `docker compose down -v` - irreversible |
| The Telegram login | Delete `*.session`, and terminate the session in the Telegram app |
