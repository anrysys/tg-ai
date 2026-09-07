---
id: DOC-GLOSSARY
title: Glossary
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-ROUTER, DOC-SRS, DOC-DATA-MODEL]
---

# Glossary

The vocabulary of this codebase. A term that reaches code before it reaches
this file is a term that will be spelled two ways within a week.

**Rule:** if a concept here is called `Dialog`, never name a variable, column,
function or document section `Chat`, `Conversation` or `Thread`.

| Term | Meaning | Where it appears in code |
| --- | --- | --- |
| **Peer** | Any Telegram entity a message can be addressed to: a User, a Group or a Channel. Which of the three is its Peer Type. | `tg_client.resolve_peer`, `tg_client.peer_type` |
| **Peer Type** | `user`, `group` or `channel`. Stored in `dialogs.peer_type`. A megagroup is a Group; a gigagroup is a Channel, because members cannot write in one. | `dialogs.peer_type`, `tg_client.peer_type` |
| **Group** | A Peer where several people write: a basic `Chat` or a megagroup `Channel`. Never "chat", never "supergroup" in prose. | `tg_client.PEER_TYPE_GROUP` |
| **Channel** | A broadcast Peer the account reads and usually cannot write to. Writing needs `admin_rights.post_messages`. | `tg_client.PEER_TYPE_CHANNEL` |
| **Min Peer** | A user id first seen inside a Group or Channel. Telegram delivers it as a `min` constructor whose `access_hash` cannot address it. Stored for attribution only: never resolved, never indexed, never a send target. | `messages.sender_id` |
| **Dialog** | One conversation with a Peer of any type, and the archive row describing it. Never "chat", never "conversation". | `dialogs` table, `PeerIndex` |
| **Target** | The user-supplied reference to a Peer: `@username`, phone number, numeric id, or `me`. Normalised by `normalise_target`. | every tool's `target` parameter |
| **Target Filter** | The set of Targets passed to `sync_db.py --targets`, restricting a Sync to named Peers. Narrows the existing Dialog list, never widens it, so it can never reach a Peer the account is not already party to. It is also the **only** way to sync a Group or Channel. | `sync_db.select_by_targets` |
| **Archive** | The local PostgreSQL copy of message history. Distinct from Telegram itself, which is always called "the live account". | `tg_ai/db.py` |
| **Sync** | The process of copying messages from the live account into the Archive. | `sync_db.py` |
| **Sync Cursor** | `dialogs.last_synced_message_id` - the highest message id already archived for a Dialog. Advanced only after a commit, so it is always safe to resume from. | `db.advance_cursor` |
| **Chunk** | One outgoing message produced by splitting a longer body under the 4096-character Telegram limit. | `safety.split_message` |
| **Send Guard** | The pre-flight check that refuses to send: to a User with no shared history and no contact entry, to a Group the account has left, to a Channel without post rights, or to a Min Peer. | `server.tg_send_message` |
| **Stranger** | A Peer with no message history and no contact entry. Messaging one is the primary ban vector. | `is_stranger` in `tg_send_message` |
| **Peer Index** | The cached map of Peers the account already has Dialogs with, built from `GetDialogs`. Consulted before any cold username resolution. A hit is proof of membership, which is what makes it the authority for Groups and Channels. | `tg_client.PeerIndex` |
| **Session** | The Telethon `.session` file holding the account's MTProto auth key. A full credential, never a "config file". | `config.session_file` |
| **Session Clone** | The copy of the Session used by `sync_db.py` so two processes never write one SQLite file. | `tg_client.clone_session` |
| **Flood Wait** | Telegram's `FloodWaitError`: a mandatory cooldown in seconds. Always reported to the agent, never silently swallowed - which is why `flood_sleep_threshold` is pinned to 0. | `safety.describe_telegram_error` |
| **Client Identity** | The `device_model`, `system_version`, `app_version`, `lang_code` and `system_lang_code` sent in `initConnection`. Honest, never impersonating an official client, and frozen once a Session exists. | `config.Config`, `tg_client.build_client` |
| **Connection Lock** | The exclusive `flock` on `<session_name>.lock` that lets only one process connect on the authorization key. Prevents `AUTH_KEY_DUPLICATED`. | `tg_client.SessionLock` |
| **RPC Budget** | The rolling hourly and daily ceiling on Telegram requests, counted in PostgreSQL so it survives a server restart. | `tg_client.RpcGuard`, `api_call_log` |
| **Kill Switch** | The project-wide stop tripped by three Flood Waits in an hour, or indefinitely by a `PeerFloodError`. Honoured by both entrypoints. | `api_kill_switch`, `just tg-killswitch` |
| **Quiet Window** | The nightly hours (`TG_QUIET_HOURS`) in which nothing touches Telegram. Archive-only tools keep working. | `safety.in_quiet_window` |
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
