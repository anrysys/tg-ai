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
