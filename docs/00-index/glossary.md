---
id: DOC-GLOSSARY
title: Glossary
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ROUTER, DOC-SRS, DOC-DATA-MODEL]
---

# Glossary

The vocabulary of this codebase. A term that reaches code before it reaches
this file is a term that will be spelled two ways within a week.

**Rule:** if a concept here is called `Dialog`, never name a variable, column,
function or document section `Chat`, `Conversation` or `Thread`.

| Term | Meaning | Where it appears in code |
| --- | --- | --- |
| **Peer** | Any Telegram entity a message can be addressed to. In this project a Peer is always a `User`; groups and channels are out of scope. | `tg_client.resolve_peer` |
| **Dialog** | One 1-on-1 conversation with a Peer, and the archive row describing it. Never "chat", never "conversation". | `dialogs` table, `PeerIndex` |
| **Target** | The user-supplied reference to a Peer: `@username`, phone number, numeric id, or `me`. Normalised by `normalise_target`. | every tool's `target` parameter |
| **Archive** | The local PostgreSQL copy of message history. Distinct from Telegram itself, which is always called "the live account". | `tg_ai/db.py` |
| **Sync** | The process of copying messages from the live account into the Archive. | `sync_db.py` |
| **Sync Cursor** | `dialogs.last_synced_message_id` - the highest message id already archived for a Dialog. Advanced only after a commit, so it is always safe to resume from. | `db.advance_cursor` |
| **Chunk** | One outgoing message produced by splitting a longer body under the 4096-character Telegram limit. | `safety.split_message` |
| **Send Guard** | The pre-flight check that refuses to message a Peer with no shared history and no contact entry. | `server.tg_send_message` |
| **Stranger** | A Peer with no message history and no contact entry. Messaging one is the primary ban vector. | `is_stranger` in `tg_send_message` |
| **Peer Index** | The cached map of Peers the account already has Dialogs with. Consulted before any cold username resolution. | `tg_client.PeerIndex` |
| **Session** | The Telethon `.session` file holding the account's MTProto auth key. A full credential, never a "config file". | `config.session_file` |
| **Session Clone** | The copy of the Session used by `sync_db.py` so two processes never write one SQLite file. | `tg_client.clone_session` |
| **Flood Wait** | Telegram's `FloodWaitError`: a mandatory cooldown in seconds. Always reported to the agent, never silently swallowed in a tool. | `safety.describe_telegram_error` |
| **Tool** | An MCP function the agent can call. Always returns text, never raises. | `server.py`, `@mcp.tool()` |
| **Live account** | Telegram itself, reached over MTProto. Contrasted with the Archive. | `tg_get_recent_messages` |

## Naming conventions

- Tool names are `tg_<verb>_<object>`, snake_case: `tg_send_message`.
- Requirement clauses are `SPEC-<AREA>-<NNN>`; see the [ID registry](id-registry.md).
- Database columns are snake_case singular: `chat_id`, not `chatId` or `chat_ids`.
- The account's own user is `me`, never `self` or `owner`, in both code and text.
