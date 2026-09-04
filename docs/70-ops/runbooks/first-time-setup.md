---
id: DOC-RB-SETUP
title: "Runbook: first-time setup"
status: active
authority: derived
updated: 2026-09-04
related: [DOC-DEPLOY]
---

# Runbook: first-time setup

The condensed path. Full detail and rationale are in the
[deployment plan](../deployment-plan.md).

```bash
cd /home/anry/projects/tg-ai
just setup                       # venv + dependencies + .env
$EDITOR .env                     # set TG_API_ID and TG_API_HASH
just db-up                       # postgres on 127.0.0.1:5434
just tg-auth                     # interactive: phone, SMS code, 2FA
just tg-sync-full                # hours; interruptible and resumable
just mcp-add                     # register with Claude Code
```

Restart the agent, then ask it to run `tg_whoami()`.

## Verification checklist

```bash
stat -c %a tg_session.session          # 600
git status --porcelain | grep session  # must print nothing
docker compose ps                      # 127.0.0.1:5434->5432/tcp
just check                             # all green
```

## If a step fails

| Failure | Cause | Fix |
| --- | --- | --- |
| `TG_API_ID and TG_API_HASH are required` | `.env` not filled in | Get credentials at <https://my.telegram.org> |
| `DATABASE_URL is required` | `.env` missing or empty | `cp .env.example .env` |
| Port 5434 already allocated | Something else took the port | Find it with `docker ps`, then change the port in `docker-compose.yml` **and** `.env` together |
| `Cannot connect to the Docker daemon` | Docker not running | Start Docker, then `just db-up` |
| Login says the code is invalid | Codes expire in minutes | Rerun `just tg-auth` and enter the newest code |
| `just tg-auth` hangs with no prompt | Not run in a real terminal | Run it directly in a shell, not through an agent tool call |
