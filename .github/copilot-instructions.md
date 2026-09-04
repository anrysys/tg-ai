# Copilot instructions

**The rules for this repository are in [AGENTS.md](../AGENTS.md). Read it first.**

This file restates nothing. A rule written twice drifts in one of the copies.

Before any change:

1. [AGENTS.md](../AGENTS.md)
2. [docs/00-index/status.md](../docs/00-index/status.md)
3. [docs/README.md](../docs/README.md)

The one rule you must not miss: `tg_send_message` refuses to message people the
account has no relationship with, and that refusal has no override flag. Do not
add one. See [AGENTS.md §3](../AGENTS.md#3-the-send-guard-is-not-negotiable).
