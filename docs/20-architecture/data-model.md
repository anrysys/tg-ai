---
id: DOC-DATA-MODEL
title: Data model
status: active
authority: derived
updated: 2026-09-07
related: [DOC-SAD, DOC-SRS, ADR-0003]
---

# Data model

**The authoritative schema is [`sql/schema.sql`](../../sql/schema.sql).** If this
page and that file disagree, the SQL is right and this page is a defect. This
page explains what the columns mean and why the indexes exist.

## `dialogs` - one row per conversation

| Column | Type | Meaning |
| --- | --- | --- |
| `chat_id` | `BIGINT` PK | The Peer's Telegram id. For 1-on-1 chats the chat id and the user id are the same value |
| `username` | `TEXT` | Handle without `@`. Nullable: many accounts have none, and it can change |
| `first_name`, `last_name` | `TEXT` | As set by the Peer. Both nullable. **For a Group or Channel the title is stored in `first_name`**, so `display_name`, the Dialog Lookup and every rendered search hit work with no second code path |
| `phone` | `TEXT` | Visible only when the Peer shares it, usually only for saved contacts |
| `is_contact` | `BOOLEAN` | Whether the Peer is in the account's contact list. Refreshed on every sync |
| `last_synced_message_id` | `BIGINT` | The **Sync Cursor**: the highest `message_id` already archived for this Dialog |
| `synced_at` | `TIMESTAMPTZ` | When the cursor last advanced. `max()` of this column is the archive's freshness |
| `peer_type` | `TEXT` | `user`, `group` or `channel`. Constrained by a `CHECK`; defaults to `user` |

`dialogs_username_idx` is on `lower(username)` because
`tg_search_local_history(target_username=...)` compares case-insensitively.

### Why `peer_type` is an `ALTER` and not a column in the `CREATE TABLE`

`sql/schema.sql` is `CREATE TABLE IF NOT EXISTS` re-executed on every start,
with no migration runner. On an archive that already exists, a column added
inside the `CREATE TABLE` would silently never appear - the statement is a
no-op. So the column ships as `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and
its `CHECK` constraint in a `DO` block that swallows `duplicate_object`, because
Postgres has no `ADD CONSTRAINT IF NOT EXISTS`. Any future column on an existing
table needs the same treatment.

## `messages` - one row per message

| Column | Type | Meaning |
| --- | --- | --- |
| `chat_id` | `BIGINT` | FK to `dialogs`, `ON DELETE CASCADE` |
| `message_id` | `BIGINT` | Telegram's id **within this chat** |
| `sender_id` | `BIGINT` | Who wrote it. Nullable for service messages. **Attribution only** - see below |
| `text` | `TEXT` | The raw body. `NULL` for media-only, stickers and service messages |
| `date` | `TIMESTAMPTZ` | When Telegram recorded it, stored in UTC |
| `is_outgoing` | `BOOLEAN` | True when the account itself wrote it |
| `tsv` | `TSVECTOR` | Generated, stored: `to_tsvector('simple', coalesce(text, ''))` |

### `sender_id` is not a foreign key to a person

In a private chat it is the Peer or the account itself. In a Group or Channel it
is whoever happened to write, and Telegram delivers those users as
[`min` constructors](https://core.telegram.org/api/min): their `access_hash`
"can't be used to generate a typical `inputPeerUser` constructor to send
messages or do other actions".

So the column carries a `COMMENT` saying it is attribution and nothing else. A
value first seen there must never be resolved, added to the Peer Index, added to
contacts, or used as a send target (`SPEC-SND-008`). Assembling a member list
one message at a time is the same scraping that `channels.getParticipants` is
banned for (`RISK-10`).

### The primary key is composite, and must be

`PRIMARY KEY (chat_id, message_id)`.

Telegram's `message_id` is unique **per chat**, not globally. Two different
people's chats routinely both contain a message with id `1`. A single-column key
on `message_id` would silently discard messages under
`ON CONFLICT DO NOTHING` - the failure would look like a working sync that
quietly loses data.

### Why `text` is nullable, and why that is not a bug

A media message with no caption has no text. Storing `''` instead of `NULL`
would make "messages that contain nothing to search" indistinguishable from
"messages we failed to read". `coalesce(text, '')` inside the generated column
handles the search side.

## `dialog_personas` - one row per Dialog Persona

| Column | Type | Meaning |
| --- | --- | --- |
| `chat_id` | `BIGINT` PK | FK to `dialogs`, `ON DELETE CASCADE` |
| `addressing` | `TEXT` | Agent-written: how the account addresses this Peer. 1-80 characters |
| `tone` | `TEXT` | Agent-written: the register. 1-120 |
| `relationship` | `TEXT` | Agent-written: who this Peer is to the account owner. 1-120 |
| `notes` | `TEXT` | Agent-written, optional: one further habit worth reproducing. Up to 240 |
| `metrics` | `JSONB` | The **Style Metrics** snapshot the Persona was written against, so drift can be measured later |
| `baseline_message_id` | `BIGINT` | The **Persona Baseline**: nothing above this id is ever analysed |
| `analysed_message_id` | `BIGINT` | Top of the window the current numbers came from |
| `analysed_count` | `INTEGER` | How many of the account's own messages that was |
| `analysed_at` | `TIMESTAMPTZ` | When the numbers were last computed. The age axis of freshness |
| `updated_at` | `TIMESTAMPTZ` | When the prose last changed |

### Why this is its own table and not columns on `dialogs`

`_UPSERT_DIALOG_SQL` rewrites `username`, `first_name`, `last_name`, `phone` and
`is_contact` on every sync. A Persona stored on `dialogs` would be destroyed by
the next `just tg-sync` - and unlike every other row in this database, a Persona
cannot be rebuilt from Telegram, because a person wrote it (`SPEC-PSN-001`).

### Why `baseline_message_id` exists, and why it is frozen

Every message this server sends is archived with `is_outgoing` set. In the
database it is indistinguishable from one the account owner typed. If the
analysis window moved forward on every refresh, a Persona would start measuring
its own drafted output and converge on a model of the model - and because model
output is more self-consistent than human writing, the result would look better
while being wrong.

So `_UPDATE_PERSONA_SQL` does not name the column. Only
`_REBASELINE_PERSONA_SQL` moves it, and only forward, the way the Sync Cursor
does (`SPEC-PSN-003`).

### Why `metrics` is `JSONB` and the prose is not

The metric set changes whenever the code does, and this schema is
`CREATE TABLE IF NOT EXISTS` with no migration runner - a new metric must not
require an `ALTER`. The metrics are also never queried by key; they are written
whole and read whole.

The agent-written fields get the opposite treatment for the opposite reason.
They are the injection surface (`RISK-07`), so each carries a `CHECK` constraint
on its length - a real control that survives a future session rewriting the
Python, which a JSONB blob could not express. The Python caps in
`safety.PERSONA_FIELD_LIMITS` are deliberately one character tighter, so the
database constraint is a backstop the user never sees.

### No index, deliberately

There is at most one row per Dialog and every read is by primary key. The only
scan is `tg_list_dialog_personas` over a few hundred rows. An index here would
be cargo cult.

## API safety state

Five small tables that exist for one reason: **an MCP stdio server is respawned
by the user's editor on every launch and after every crash**, so any counter or
cooldown held in a Python variable is reset constantly. Rate limiting that a
restart clears is not rate limiting. Both entrypoints read and write these, so
the budget is one account-wide budget rather than one per process
([ADR-0009](adr/0009-groups-and-channels.md)).

| Table | Holds |
| --- | --- |
| `api_call_log` | One row per Telegram-touching operation: `scope` (`rpc` or `group_read`), `key` (the method name, or the peer id for a cooldown), `called_at`. Backs the rolling budgets and the per-target cooldown |
| `api_flood_log` | Every `FloodWaitError`, `SlowModeWaitError` and `PeerFloodError`, with method and target. Flood *frequency* is what feeds Telegram's risk score |
| `api_kill_switch` | Trip history. The switch is ON when an uncleared row has not yet expired; `expires_at IS NULL` means indefinite, which is what a `PeerFloodError` sets |
| `peer_snapshot` | Membership evidence from `GetDialogs`, so a restarted server need not re-walk the dialog list to answer "are we in this group?" |
| `client_identity` | The Client Identity actually used, so drift in strings that are meant to be frozen becomes detectable |
| `contacts_cache` | `saved_count` and the contact ids, so `contacts.getContacts` can send the documented hash instead of `0` on every launch |

Every window queried over these is rolling (`now() - interval`), never a
calendar bucket: a calendar hour would let an agent spend the whole budget at
10:59 and the whole budget again at 11:01. The timestamp columns are indexed
because the budget check runs before every single request.

## Indexes

| Index | Serves |
| --- | --- |
| `messages_tsv_idx` (GIN on `tsv`) | The primary search path: `websearch_to_tsquery('simple', $1)` |
| `messages_trgm_idx` (GIN, `gin_trgm_ops`) | The fallback path: `text ILIKE '%q%'`, which without this index is a full scan |
| `messages_date_idx` (`date DESC`) | Ordering results and computing archive coverage |
| `api_call_log_scope_time_idx`, `api_call_log_scope_key_time_idx` | The rolling RPC budget and the per-target group cooldown |
| `api_flood_log_time_idx` | Counting flood events inside the kill switch's rolling hour |
| `api_kill_switch_active_idx` (partial, `cleared_at IS NULL`) | Finding the active trip |

### Why `simple` and not a language configuration

The Archive is multilingual - the same conversation mixes English, Ukrainian and
Russian. Every stemmer is wrong for the languages it was not built for and
silently drops matches. `simple` performs no stemming, so it never mis-stems;
the trigram index recovers the partial and inflected matches a stemmer would
have caught. Recorded as
[ADR-0003](adr/0003-postgres-fts-simple-plus-trigram.md).

The practical consequence: searching `Khreshchatyk` finds the exact token via
full-text search, and searching `Khresh` finds it via the trigram fallback.

## What is deliberately not stored

Media, files, reactions, edit history, forward provenance and reply threading.
Access hashes for people the account has not spoken to are also deliberately not
stored - `sender_id` is a bare number, and Telethon's own entity cache is capped
at 500 so the session file does not become one either (`RISK-10`). See the
non-goals in the [PRD](../10-product/prd.md). Adding any of them is a schema
change, and therefore needs a new ADR.
