# CLAUDE.md

**The rules for this repository are in [AGENTS.md](AGENTS.md). Read it first.**

This file deliberately restates nothing. A rule that exists in two places drifts
in one of them.

## Session start

1. [AGENTS.md](AGENTS.md)
2. [docs/00-index/status.md](docs/00-index/status.md)
3. [docs/README.md](docs/README.md) - route to the slice your task needs

## The one thing to know before touching anything

This project sends messages from a real person's personal Telegram account.
`tg_send_message` refuses to message strangers, and that refusal has no override
flag. Do not add one. See
[AGENTS.md §3](AGENTS.md#3-the-send-guard-is-not-negotiable).

## Common commands

```bash
just            # list every recipe
just check      # everything CI enforces - run before reporting done
just tg-status  # account, session, database, archive health
```
