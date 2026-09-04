---
applyTo: "**/*.sql"
---

# SQL instructions

Full rules: [AGENTS.md](../../AGENTS.md).
Column semantics: [data-model.md](../../docs/20-architecture/data-model.md).

- Raw SQL over `asyncpg`. No ORM
  ([ADR-0006](../../docs/20-architecture/adr/0006-raw-sql-asyncpg-no-orm.md)).
- Bind every user value as `$1`, `$2`, … Never interpolate a value into a
  statement; string formatting may only insert a placeholder.
- `messages` is keyed on `(chat_id, message_id)`. `message_id` is unique per
  chat, not globally - a single-column key silently discards messages.
- Text search uses the `'simple'` configuration, never a language-specific one
  ([ADR-0003](../../docs/20-architecture/adr/0003-postgres-fts-simple-plus-trigram.md)).
- `sql/schema.sql` must stay idempotent: `IF NOT EXISTS` on everything.
- A schema change updates `sql/schema.sql` and `data-model.md` together, and
  needs an ADR for any index-strategy change.
