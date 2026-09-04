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
