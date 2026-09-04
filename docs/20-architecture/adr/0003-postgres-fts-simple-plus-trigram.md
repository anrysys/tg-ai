---
id: ADR-0003
title: "Search with the simple text configuration plus a trigram fallback"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-DATA-MODEL, DOC-SRS]
---

# ADR-0003 - Search with `simple` plus a trigram fallback

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** `SPEC-SRCH-002`, `SPEC-SRCH-003`
**Affects:** `sql/schema.sql`, `tg_ai/db.py`

## Context

The Archive is multilingual: a single conversation mixes English, Ukrainian and
Russian, often within one sentence. PostgreSQL full-text search requires a text
search configuration, and every language-specific configuration applies that
language's stemmer to **all** input.

Stemming the wrong language does not merely fail to help - it silently produces
different lexemes for a query and its own indexed text, so the match disappears
with no error and no empty-result explanation that would hint at the cause.

## Decision

Index and query with the `simple` configuration, which lowercases and tokenises
but never stems. Cover the recall that stemming would have provided with a
`pg_trgm` GIN index, used as an explicit second pass: full-text search first,
and only if it returns nothing, a substring `ILIKE` match. The response states
which strategy matched.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| `to_tsvector('english', ...)` | Silently loses non-English matches, which is most of this archive |
| A `language` column with per-row configurations | Requires reliable language detection per message. Wrong detection reintroduces the same silent failure, with more machinery |
| Trigram matching only | No ranking, no multi-word or phrase queries, and no `OR`/exclusion syntax. Much slower on a large archive |
| An external engine (Elasticsearch, Meilisearch) | A second service to run, back up and secure, holding a full plaintext copy of every private conversation. Enormous cost for a single-user local tool |

## Consequences

- Exact-token queries are fast and ranked; partial words still resolve through
  the fallback. Verified: `Khreshchatyk` matches via full-text, `Khresh` via
  substring.
- Two GIN indexes roughly double the index footprint of `messages`. Acceptable
  for a single-user archive.
- The fallback is a full-table `ILIKE` when the trigram index cannot help
  (queries under three characters). `SPEC-RCV-004` caps `limit` at 500 to bound
  the cost.
- The strategy is reported to the agent so it does not over-trust a loose
  substring hit.
