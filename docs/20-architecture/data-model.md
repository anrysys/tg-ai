---
id: DOC-DATA-MODEL
title: Data model
status: active
authority: derived
updated: 2026-09-04
related: [DOC-SAD, DOC-SRS, ADR-0003]
---

# Data model

**The authoritative schema is [`sql/schema.sql`](../../sql/schema.sql).** If this
page and that file disagree, the SQL is right and this page is a defect. This
page explains what the columns mean and why the indexes exist.

## `dialogs` - one row per private conversation

| Column | Type | Meaning |
| --- | --- | --- |
| `chat_id` | `BIGINT` PK | The Peer's Telegram user id. For 1-on-1 chats the chat id and the user id are the same value |
| `username` | `TEXT` | Handle without `@`. Nullable: many accounts have none, and it can change |
| `first_name`, `last_name` | `TEXT` | As set by the Peer. Both nullable |
| `phone` | `TEXT` | Visible only when the Peer shares it, usually only for saved contacts |
| `is_contact` | `BOOLEAN` | Whether the Peer is in the account's contact list. Refreshed on every sync |
| `last_synced_message_id` | `BIGINT` | The **Sync Cursor**: the highest `message_id` already archived for this Dialog |
| `synced_at` | `TIMESTAMPTZ` | When the cursor last advanced. `max()` of this column is the archive's freshness |

`dialogs_username_idx` is on `lower(username)` because
`tg_search_local_history(target_username=...)` compares case-insensitively.

## `messages` - one row per message

| Column | Type | Meaning |
| --- | --- | --- |
| `chat_id` | `BIGINT` | FK to `dialogs`, `ON DELETE CASCADE` |
| `message_id` | `BIGINT` | Telegram's id **within this chat** |
| `sender_id` | `BIGINT` | Who wrote it. Nullable for service messages |
| `text` | `TEXT` | The raw body. `NULL` for media-only, stickers and service messages |
| `date` | `TIMESTAMPTZ` | When Telegram recorded it, stored in UTC |
| `is_outgoing` | `BOOLEAN` | True when the account itself wrote it |
| `tsv` | `TSVECTOR` | Generated, stored: `to_tsvector('simple', coalesce(text, ''))` |

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

## Indexes

| Index | Serves |
| --- | --- |
| `messages_tsv_idx` (GIN on `tsv`) | The primary search path: `websearch_to_tsquery('simple', $1)` |
| `messages_trgm_idx` (GIN, `gin_trgm_ops`) | The fallback path: `text ILIKE '%q%'`, which without this index is a full scan |
| `messages_date_idx` (`date DESC`) | Ordering results and computing archive coverage |

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

Media, files, reactions, edit history, forward provenance, reply threading, and
group or channel content. See the non-goals in the [PRD](../10-product/prd.md).
Adding any of them is a schema change, and therefore needs a new ADR.
