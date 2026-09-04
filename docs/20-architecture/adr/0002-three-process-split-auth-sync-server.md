---
id: ADR-0002
title: "Split authentication, sync and serving into three processes"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-SAD]
---

# ADR-0002 - Split auth, sync and server into three processes

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** the repository's entrypoint layout
**Affects:** `auth.py`, `sync_db.py`, `server.py`

## Context

MTProto login is interactive: Telegram sends a code by SMS or in-app, and may
then ask for a 2FA password. An MCP server communicates over stdio - stdin and
stdout **are** the protocol channel. It can never prompt a human.

Separately, a full history backfill takes hours on a busy account and will hit
flood waits. Running that inside a tool call would block the agent for the
entire duration and time out.

## Decision

Three entrypoints with distinct lifetimes:

1. `auth.py` - runs once, interactively, in a real terminal. Produces the
   Session file.
2. `sync_db.py` - runs on demand or on a schedule. Fills the Archive. Resumable.
3. `server.py` - spawned by the agent, speaks MCP over stdio, never blocks for
   more than one tool call.

They share only the Session file and the Archive.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| One process, login prompted through an MCP tool | stdio is the protocol channel. Prompting through it corrupts the session, and the agent has no way to relay an SMS code interactively |
| Sync as an MCP tool call | Hours-long tool calls time out and block the agent. The tool would also have to duplicate the resume logic |
| Login via a pre-generated `StringSession` in `.env` | Moves a full credential into an environment file and still needs an interactive step to produce it. Strictly worse than a file with mode `0600` |

## Consequences

- The user must run `just tg-auth` once, in a terminal, before the MCP server
  works. `tg_whoami` and every tool report this precisely when the session is
  missing, so the agent can tell the user exactly what to do.
- Sync can run while the agent session is live, which forces
  [ADR-0004](0004-session-file-clone-for-sync.md).
- The Archive can lag the live account. `tg_get_recent_messages` reads live
  precisely to cover that gap.
