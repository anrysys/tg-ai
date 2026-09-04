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
