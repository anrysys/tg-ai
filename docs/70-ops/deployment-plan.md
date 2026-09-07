---
id: DOC-DEPLOY
title: Deployment plan
status: active
authority: authoritative
updated: 2026-09-07
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

Edit `.env` and set `TG_API_ID` and `TG_API_HASH`.

Then set **`TG_LANG_CODE`** to the interface language of the official Telegram
app on the account owner's phone. It defaults to `en` because this is a public
repository and no language is right for everyone, and that default is unsafe for
anyone whose app is in another language: every connection reports it, alongside
a phone number and an `api_id` Telegram has already seen used from the official
app for years, so a mismatch is a contradiction in Telegram's own records
(`SPEC-SEC-007`).

Everything else has a working default. `TG_TIMEZONE` is detected from the
machine, and `TG_QUIET_HOURS` defaults to `01:00-08:00` local, during which
nothing touches Telegram. `TG_DEVICE_MODEL`, `TG_SYSTEM_VERSION` and
`TG_APP_VERSION` are derived if left blank - but once a session exists, treat
all five as **frozen**: `initConnection` replays them on every reconnection, so
changing one makes the account's own Settings -> Devices entry mutate under the
user.

### The two rules that are not in `.env`

Neither has a flag, and neither can be relaxed (`SPEC-SEC-006`):

- **Run this on the account owner's own machine and network.** Never a VPS,
  cloud host, container platform or CI runner; never a shared or rotating VPN.
  Where the packets come from is the strongest userbot signal Telegram has.
- **Use the account owner's own `api_id`.** Never a published or borrowed one -
  Telegram answers those with `API_ID_PUBLISHED_FLOOD` and treats the account
  behind them as an abuser.

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

On an account with hundreds of dialogs, archive the people who matter first:

```bash
just tg-sync-targets @anna @bob "Anna Petrova" +380501234567
```

Each target matches a username (with or without `@`), a phone number, a numeric
id, or a first, last or full name. Anything that matches no dialog is named in a
warning, so a typo is visible immediately rather than looking like a person with
no history. Search over those people works as soon as this finishes.

Then let the rest follow:

```bash
just tg-sync-full
```

Hours on a busy account, and it will pause on flood waits. Safe to interrupt:
progress is saved per dialog and `just tg-sync` resumes from the cursor. Because
insertion is idempotent (`SPEC-SYNC-002`), the dialogs already covered by the
targeted run cost nothing the second time.

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
| Archive specific people first | `just tg-sync-targets @a @b` | On a large account, before the full backfill |
| Archive a group you are in | `just tg-sync-targets "Some Group"` | Deliberately, at most 5 per run and 20 reads a day |
| Check the safety stop | `just tg-killswitch` | When a tool says the kill switch is on |
| Restart the database | `just db-up` | After a reboot |
| Full check | `just check` | Before reporting any code change done |

**`just tg-sync` cannot run while an agent session is connected.** It will exit
immediately, naming the process holding the connection.

That is a deliberate change from how this used to be described. The session
clone ([ADR-0004](../20-architecture/adr/0004-session-file-clone-for-sync.md))
stops two processes writing one SQLite file, and nothing more - the clone
carries the *same* authorization key, and two live connections on one key make
Telegram invalidate the login outright
([ADR-0010](../20-architecture/adr/0010-one-connection-per-authorization-key.md),
`RISK-08`). Close the agent session, or wait for the sync to finish.

## Rollback

| To undo | Do |
| --- | --- |
| The MCP registration | `just mcp-remove` |
| The database, keeping data | `just db-down` |
| The database and all archived history | `just db-reset` - prints what will be lost and requires typing `DELETE` on a terminal. `docker compose down -v` does the same thing with no warning at all |
| The Telegram login | Delete `*.session`, and terminate the session in the Telegram app |
