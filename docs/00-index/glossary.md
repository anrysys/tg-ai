---
id: DOC-GLOSSARY
title: Glossary
status: active
authority: authoritative
updated: 2026-09-05
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
| **Target Filter** | The set of Targets passed to `sync_db.py --targets`, restricting a Sync to named people. Narrows the Dialog list; never widens it, so it cannot reach a bot, group or deleted account that `SPEC-SYNC-001` already excluded. | `sync_db.select_by_targets` |
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
| **Dialog Persona** | How the account writes to one Peer: the agent-authored qualitative pattern plus the Style Metrics measured from `me`'s own archived messages. Never a "chat persona" or a "conversation style". | `dialog_personas` table, `tg_ai/persona.py` |
| **Style Metrics** | The language-agnostic measurements a Dialog Persona is anchored to: length distribution, burst rate, case, punctuation, emoji and script mix. Numbers and Unicode script names only, never message text. | `persona.analyse_style` |
| **Persona Baseline** | `dialog_personas.baseline_message_id` - the message id above which nothing is ever analysed. Frozen when the Persona is created so that messages this project itself sent cannot feed back into the Persona. | `db.rebaseline_persona` |
| **Persona Drift** | The distance between a stored Dialog Persona and the account's writing today, reported on three axes: volume, age and metric drift. Reported, never silently corrected. | `persona.compare_style` |
| **Dialog Lookup** | Resolving a Target to a Dialog using the Archive alone, with no call to the live account. Exact per key, never a substring. Forbidden in the send path. | `db.resolve_dialog` |

## Naming conventions

- Tool names are `tg_<verb>_<object>`, snake_case: `tg_send_message`.
- Requirement clauses are `SPEC-<AREA>-<NNN>`; see the [ID registry](id-registry.md).
- Database columns are snake_case singular: `chat_id`, not `chatId` or `chat_ids`.
- The account's own user is `me`, never `self` or `owner`, in both code and text.
