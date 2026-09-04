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
