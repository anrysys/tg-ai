---
id: DOC-PRD
title: Product requirements
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-SRS, DOC-ROADMAP]
---

# Product requirements

## Who this is for

A single user, `@anrysys`, operating their own personal Telegram account from
inside an AI-agent session (Claude Code, Antigravity, or any MCP client). One
account, one machine, no multi-tenancy, no other users - ever. Every design
choice may assume that.

## The four jobs

### UC-1 - Send from the agent session

> "Send a summary of this session to `@someone`."

The agent composes the text and delivers it as the user's own account. The text
may be several thousand characters. The agent must not be able to message a
stranger in a single step.

Satisfied by `tg_send_message` and `tg_add_contact`. Governed by
[`SPEC-SND-001`](srs.md#spec-snd-001---the-send-guard) through `SPEC-SND-006`.

### UC-2 - Read replies without leaving the session

> "Check new messages." / "What did `@someone` reply?"

The agent reports who is waiting and what they said, reading the live account so
that a reply from a minute ago is visible. Reading must not mark anything read.

Satisfied by `tg_get_unread_dialogs` and `tg_get_recent_messages`. Governed by
`SPEC-RCV-*`.

### UC-3 - Search the whole history

> "Find the address they sent me last year."

Instant search across every archived private conversation, in any language, with
no Telegram API call and therefore no rate limit and no ban risk.

Satisfied by `tg_search_local_history`, backed by the Archive that `sync_db.py`
fills. Governed by `SPEC-SRCH-*` and `SPEC-SYNC-*`.

### UC-4 - Reply in the user's own voice

> "Reply to `@someone` about the meeting."

A reply drafted in a generic register is obvious to anyone who knows the user.
Before drafting, the agent reads the **Dialog Persona** for that person: how the
user addresses them, the register they use, and the measured shape of their own
past messages in that conversation. The Persona is stored per Dialog and put in
front of the model at drafting time.

Satisfied by `tg_get_dialog_persona`, `tg_set_dialog_persona` and
`tg_list_dialog_personas`, and by the Persona block `tg_get_recent_messages`
prepends. Governed by `SPEC-PSN-*` and
[ADR-0008](../20-architecture/adr/0008-dialog-persona-hybrid-authorship.md).

## Constraints

| Constraint | Consequence |
| --- | --- |
| Personal account over MTProto, not a bot | Anti-spam heuristics apply; every send is a ban risk |
| MCP speaks stdio | Login cannot happen in the server; it needs its own process |
| The account must connect from its usual IP | Local-only deployment; no VPS, no container networking to the outside |
| The archive holds every private message in plaintext | Loopback-only database, owner-only session file |
| The operator is an AI agent, not a human | Every failure must be returned as readable text with a next step |

## Non-goals

Explicitly out of scope. Adding any of these requires a PRD change and an ADR.

- **Groups and channels.** Private 1-on-1 chats only.
- **Media.** Only message text is archived and sent. No photos, files or voice.
- **Deleting or editing messages.** The server never destroys anything on the
  account.
- **Marking messages read.** Reading is strictly non-destructive.
- **Multiple accounts.** One session, one archive.
- **Remote access.** No HTTP transport, no hosted deployment, no shared database.
- **Bulk or broadcast messaging.** The tool sends to one Peer at a time by
  design. Any batch-send feature would defeat every anti-spam guarantee here.
