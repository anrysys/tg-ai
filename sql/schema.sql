-- tg-ai archive schema. Idempotent: safe to run on every start.
-- Authoritative column semantics live in docs/20-architecture/data-model.md.
-- Any change here requires a matching update there and a new ADR.

-- Trigram matching backs the fallback path of tg_search_local_history when
-- full-text search finds nothing (ADR-0003).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One row per private conversation with a human.
CREATE TABLE IF NOT EXISTS dialogs (
    chat_id                BIGINT PRIMARY KEY,
    username               TEXT,
    first_name             TEXT,
    last_name              TEXT,
    phone                  TEXT,
    is_contact             BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Resume cursor: the highest message_id already archived for this dialog.
    -- Advanced only after the corresponding rows are committed, so an
    -- interrupted sync resumes without gaps (SPEC-SYNC-003).
    last_synced_message_id BIGINT      NOT NULL DEFAULT 0,
    synced_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS dialogs_username_idx ON dialogs (lower(username));

-- Groups and channels (ADR-0009). This is an ALTER rather than a column in the
-- CREATE TABLE above, and deliberately so: this file is CREATE TABLE IF NOT
-- EXISTS with no migration runner, so on an archive that already exists a new
-- column inside the CREATE would silently never appear. Existing rows are all
-- private chats, which is what the default says.
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS peer_type TEXT NOT NULL DEFAULT 'user';

DO $$
BEGIN
    ALTER TABLE dialogs ADD CONSTRAINT dialogs_peer_type_check
        CHECK (peer_type IN ('user', 'group', 'channel'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- One row per message. message_id is unique per chat, never globally, so the
-- primary key must be composite.
CREATE TABLE IF NOT EXISTS messages (
    chat_id     BIGINT      NOT NULL REFERENCES dialogs (chat_id) ON DELETE CASCADE,
    message_id  BIGINT      NOT NULL,
    sender_id   BIGINT,
    text        TEXT,
    date        TIMESTAMPTZ NOT NULL,
    is_outgoing BOOLEAN     NOT NULL DEFAULT FALSE,
    -- 'simple' rather than a language-specific configuration: the archive is
    -- multilingual and every stemmer is wrong for half of it (ADR-0003).
    tsv         TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED,
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS messages_tsv_idx  ON messages USING GIN (tsv);
CREATE INDEX IF NOT EXISTS messages_trgm_idx ON messages USING GIN (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS messages_date_idx ON messages (date DESC);

-- Read this before using sender_id for anything. In a private chat it is the
-- other person or the account itself. In a group or channel it is whoever
-- happened to write, and Telegram delivers those users as `min` constructors:
-- per https://core.telegram.org/api/min their access_hash "can't be used to
-- generate a typical inputPeerUser constructor to send messages or do other
-- actions". So this column is an attribution number and NOTHING else. It is
-- not a foreign key to a person, and a value first seen here must never be
-- resolved, indexed as a Peer, added to contacts, or used as a send target
-- (SPEC-SND-008). Assembling a member list one message at a time is the same
-- scraping that channels.getParticipants is banned for, only slower.
COMMENT ON COLUMN messages.sender_id IS
    'Attribution only. In a group this is a min-constructor user id: never resolvable, never a send target (SPEC-SND-008).';

-- One row per Dialog: how the account writes to that specific Peer.
-- Deliberately NOT columns on `dialogs`: that table is rewritten by
-- _UPSERT_DIALOG_SQL with ON CONFLICT DO UPDATE on every sync, so anything
-- stored there would not survive `just tg-sync` (SPEC-PSN-001).
CREATE TABLE IF NOT EXISTS dialog_personas (
    chat_id             BIGINT PRIMARY KEY REFERENCES dialogs (chat_id) ON DELETE CASCADE,

    -- The agent-authored half. Length-capped in the schema as well as in
    -- Python, because these fields are re-injected into a model's context on
    -- every read and must not be able to grow into a standing instruction
    -- block (RISK-07, SPEC-PSN-006). Python caps are strictly tighter, so a
    -- user never sees a raw constraint violation.
    addressing          TEXT        NOT NULL CHECK (char_length(addressing)   BETWEEN 1 AND 80),
    tone                TEXT        NOT NULL CHECK (char_length(tone)         BETWEEN 1 AND 120),
    relationship        TEXT        NOT NULL CHECK (char_length(relationship) BETWEEN 1 AND 120),
    notes               TEXT        NOT NULL DEFAULT '' CHECK (char_length(notes) <= 240),

    -- The measured half: the Style Metrics snapshot this Persona was written
    -- against, kept so drift can be detected later. JSONB rather than one
    -- column per metric because the metric set changes with the code, and
    -- this file is CREATE TABLE IF NOT EXISTS with no migration runner - a
    -- new metric must never require an ALTER (ADR-0008).
    metrics             JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- The analysis window. baseline_message_id is frozen when the Persona is
    -- created and moved only by _REBASELINE_PERSONA_SQL: messages archived
    -- after it are never analysed, so text this project itself sent cannot
    -- be mistaken for the account owner's own writing (SPEC-PSN-003).
    baseline_message_id BIGINT      NOT NULL DEFAULT 0,
    analysed_message_id BIGINT      NOT NULL DEFAULT 0,
    analysed_count      INTEGER     NOT NULL DEFAULT 0 CHECK (analysed_count >= 0),
    analysed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (analysed_message_id >= baseline_message_id)
);

-- No index beyond the primary key, on purpose. There is at most one row per
-- Dialog, every read is by chat_id, and the only scan is the persona
-- overview over a few hundred rows.

-- --------------------------------------------------------------------------
-- API safety state (ADR-0009).
--
-- These tables exist because the MCP server is a stdio process that the user's
-- editor respawns on every launch and after every crash. A cooldown or counter
-- held in a Python variable is therefore reset constantly: in-memory rate
-- limiting in an MCP server is not rate limiting. Both entrypoints read and
-- write these, so the budget is one account-wide budget rather than one per
-- process.
-- --------------------------------------------------------------------------

-- One row per Telegram-touching operation. `scope` separates the budgets that
-- are counted differently: 'rpc' is every MTProto request, 'group_read' is the
-- much scarcer group and channel history read.
CREATE TABLE IF NOT EXISTS api_call_log (
    id        BIGSERIAL   PRIMARY KEY,
    scope     TEXT        NOT NULL,
    -- Method name for 'rpc', peer id for a per-target cooldown.
    key       TEXT        NOT NULL,
    called_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every budget query is a rolling window (now() - interval), never a calendar
-- bucket, so these indexes carry the timestamp. Without them the budget check
-- degrades into a sequential scan that grows with the log.
CREATE INDEX IF NOT EXISTS api_call_log_scope_time_idx
    ON api_call_log (scope, called_at DESC);
CREATE INDEX IF NOT EXISTS api_call_log_scope_key_time_idx
    ON api_call_log (scope, key, called_at DESC);

-- Every FloodWaitError, SlowModeWaitError and PeerFloodError, from any process.
-- Flood *frequency* is what feeds Telegram's server-side risk score, so this is
-- the input to the kill switch rather than a diagnostic.
CREATE TABLE IF NOT EXISTS api_flood_log (
    id          BIGSERIAL   PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_type  TEXT        NOT NULL,
    method      TEXT,
    target      TEXT
);

CREATE INDEX IF NOT EXISTS api_flood_log_time_idx ON api_flood_log (occurred_at DESC);

-- The kill switch. Rows are history: the switch is ON when a row exists with
-- cleared_at IS NULL that has not yet expired.
--
-- expires_at NULL means indefinite. A PeerFloodError is an escalation, not a
-- cooldown, so only the account owner clears it - deliberately, by hand, after
-- checking the account with @SpamBot from the official app.
CREATE TABLE IF NOT EXISTS api_kill_switch (
    id         BIGSERIAL   PRIMARY KEY,
    tripped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason     TEXT        NOT NULL,
    expires_at TIMESTAMPTZ,
    cleared_at TIMESTAMPTZ,
    cleared_by TEXT
);

CREATE INDEX IF NOT EXISTS api_kill_switch_active_idx
    ON api_kill_switch (tripped_at DESC) WHERE cleared_at IS NULL;

-- The Client Identity actually used, recorded once. initConnection replays
-- these on every reconnection, so a change makes the account's own
-- Settings -> Devices entry mutate under the user. Storing them turns "treat
-- as immutable" from a comment into something the code can notice.
CREATE TABLE IF NOT EXISTS client_identity (
    id               INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    device_model     TEXT        NOT NULL,
    system_version   TEXT        NOT NULL,
    app_version      TEXT        NOT NULL,
    lang_code        TEXT        NOT NULL,
    system_lang_code TEXT        NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Membership evidence derived from GetDialogs, so a restarted MCP server does
-- not have to walk the whole dialog list again just to answer "are we a member
-- of this group?" (SPEC-RCV-005).
--
-- Deliberately NOT Telethon's own session entity cache, which would be the
-- obvious shortcut: that cache also holds strangers seen inside groups, and
-- treating it as proof of membership is exactly the confusion SPEC-SND-008
-- exists to prevent.
CREATE TABLE IF NOT EXISTS peer_snapshot (
    peer_id   BIGINT      PRIMARY KEY,
    peer_type TEXT        NOT NULL CHECK (peer_type IN ('user', 'group', 'channel')),
    username  TEXT,
    title     TEXT,
    -- False once the account leaves; the row is kept so a stale membership is
    -- visibly stale rather than silently absent.
    is_member BOOLEAN     NOT NULL DEFAULT TRUE,
    -- channel.admin_rights.post_messages. The only thing that makes a send to a
    -- broadcast channel legal, and it is read from the cached entity so the
    -- check costs no API call (SPEC-SND-001).
    can_post  BOOLEAN     NOT NULL DEFAULT FALSE,
    seen_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS peer_snapshot_seen_idx ON peer_snapshot (seen_at DESC);

-- The contacts.getContacts hash, so the server can answer
-- contactsContactsNotModified instead of re-serialising the whole contact list
-- every refresh. Passing hash=0 forever is both wasteful and a behavioural
-- difference from every official client (SPEC-SND-006).
CREATE TABLE IF NOT EXISTS contacts_cache (
    id           INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- saved_count from the previous contacts.contacts, NOT the number of ids
    -- below. They differ on a real account, and the documented hash folds in
    -- this one; using the other silently produces a hash that never matches.
    saved_count  INTEGER     NOT NULL,
    contact_ids  BIGINT[]    NOT NULL,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
