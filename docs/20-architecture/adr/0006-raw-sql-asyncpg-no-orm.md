---
id: ADR-0006
title: "Use raw SQL over asyncpg, with no ORM"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-DATA-MODEL, AGENTS]
---

# ADR-0006 - Raw SQL over asyncpg, no ORM

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** `SPEC-SRCH-004`
**Affects:** `tg_ai/db.py`, `sql/schema.sql`

## Context

The database work in this project is narrow and unusual: a generated `tsvector`
column, two GIN indexes with different operator classes, `websearch_to_tsquery`,
`ts_rank` ordering, `ON CONFLICT DO NOTHING` batch inserts, and a `GREATEST`
update that must never move a cursor backwards.

Every one of those is a PostgreSQL feature that an ORM either cannot express or
expresses through an escape hatch that emits raw SQL anyway.

## Decision

Use `asyncpg` directly with hand-written SQL. Keep the DDL in
[`sql/schema.sql`](../../../sql/schema.sql) as a single machine-readable file.
Bind every user-supplied value as a parameter; string formatting in query
construction may only ever insert a placeholder, never a value.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| SQLAlchemy Core or ORM | The generated column, both GIN operator classes and `ts_rank` ordering all end up as `text()` escape hatches. The abstraction costs a dependency and buys nothing |
| Tortoise / Piccolo / SQLModel | Same objection, smaller ecosystems |
| SQLite with FTS5 | No `pg_trgm` equivalent for the fallback path, and far weaker concurrent access while a sync writes and the server reads |
| `psycopg` (sync) | Every caller here is async. Sync drivers block the MCP event loop and stall the whole server during a query |

## Consequences

- The schema is one readable file an agent can load in full before writing a
  query, rather than model classes that must be mentally compiled into DDL.
- Column names appear as literal strings in `tg_ai/db.py`. A schema change
  requires updating both, which is exactly why the
  [documentation map](../../00-index/documentation-map.md) pairs `sql/schema.sql`
  with `data-model.md` in one row.
- SQL injection is prevented by binding, and that property is asserted by
  `tests/test_search_sql.py`, not by an ORM's reputation.
- Adding an ORM later requires a superseding ADR.
