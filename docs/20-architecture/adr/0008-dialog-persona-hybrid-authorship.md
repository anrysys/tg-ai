---
id: ADR-0008
title: "Produce a Dialog Persona from in-repo metrics plus an agent-written pattern"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-DATA-MODEL, DOC-SRS, DOC-SECURITY]
---

# ADR-0008 - Dialog Persona: hybrid authorship, own table, frozen baseline

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** `SPEC-PSN-001` .. `SPEC-PSN-008`, `SPEC-SRCH-006`, `TASK-010`
**Affects:** `sql/schema.sql`, `tg_ai/persona.py`, `tg_ai/db.py`,
`tg_ai/safety.py`, `tg_ai/formatting.py`, `server.py`

## Context

A reply drafted in a model's default register is obvious to anyone who knows the
account owner. Matching how they write to one specific Peer - formal or informal
address, message length, whether they punctuate, whether they answer in three
short lines instead of one - requires per-Dialog style to be stored and put in
front of the model before drafting.

Four constraints, none of them negotiable, decided the shape:

1. **There is no LLM in this repository.** Drafting happens in the MCP client.
   Nothing here can infer "addresses this person formally" on its own.
2. **`just docs-english` fails CI on non-English text in `*.py` and `*.sql`.**
   Detecting an addressing form by matching pronouns would require a
   per-language lexicon in source, which AGENTS.md rule 1 forbids.
3. **`sql/schema.sql` is `CREATE TABLE IF NOT EXISTS` with no migration runner.**
   Whatever columns ship are the columns, short of hand-written `ALTER`
   statements accreting in the file.
4. **Every message this server sends is archived as `is_outgoing`**, identical in
   the database to one the account owner typed by hand.

Constraint 4 is the dangerous one and was not obvious. Left alone, a Persona
re-analysed after a few drafted replies measures its own output. Two or three
refreshes and it is a fixed point of the model rather than a description of the
person - and because model output is more self-consistent than human writing,
the result reads *better*, so the drift is invisible.

## Decision

Split authorship. `tg_ai/persona.py` computes **Style Metrics**: numbers,
timestamps and Unicode script names, from the account's own messages only. The
agent reads those metrics plus verbatim samples and writes the qualitative
pattern - addressing, tone, relationship - back through
`tg_set_dialog_persona`. Both halves live in `dialog_personas`, keyed by
`chat_id`.

Freeze the analysis window. `baseline_message_id` is set when a Persona is
created and is absent from the UPDATE statement, so the invariant is a property
of the SQL rather than a convention a later session can forget.

Deliver the Persona through tool output - prepended by
`tg_get_recent_messages`, and named in the `tg_send_message` docstring - because
that is the only channel this repository owns that reaches the model unprompted.

Never let any of it gate a send.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Persona columns on `dialogs` | `_UPSERT_DIALOG_SQL` rewrites every column it names on every sync. A Persona there would not survive `just tg-sync`, and it is the only content in this database a resync cannot rebuild |
| An in-repo LLM call (`anthropic`, `openai`) to infer style | A dependency, a second API key, a network call and a cost on every read - for a job the MCP client's model is already doing with the conversation in hand. Would need its own ADR under AGENTS.md section 4 |
| Pure heuristics, no agent-written half | Addressing form, formality and relationship need a per-language lexicon. That means non-English literals in `*.py`, which fails `just docs-english` |
| Pure agent prose, no metrics | Nothing anchors the description to reality and drift becomes undetectable. The metrics are what make `SPEC-PSN-005` possible |
| One discrete column per metric | The metric set changes whenever the code does, and `CREATE TABLE IF NOT EXISTS` cannot add a column. Every new metric would need an unsupported `ALTER` |
| An all-JSONB persona row | The agent-written fields are the injection surface. A per-column `CHECK` is a real control that survives a future session rewriting the Python; a JSONB blob is an unbounded, unvalidated channel |
| Re-baselining on every analysis | The feedback loop above. The Persona would model the model |
| A mean message length rather than a distribution | Human writing per Dialog is bimodal - "ok" and four paragraphs from the same person on the same day. Drafting to the mean produces a uniform middle register that reads *more* synthetic than not trying |
| Blocking a send when no Persona exists | Adds a second refusal path beside the Send Guard, firing on every new Dialog. `SPEC-PSN-008` forbids it |
| A persona hint computed before the send | A database read that can raise before delivery is a new way for a send to fail; after delivery it would report `ERROR:` for a sent message and invite a duplicate. Hence the success-path-only helper that swallows everything |
| `@mcp.prompt()` or a Resource as the delivery channel | Neither is auto-read by the model in Claude Code: prompts surface as user-invoked slash commands and resources are attached by hand. Neither guarantees the Persona is in context at drafting time. Evaluated and declined for v1; recorded here so it is not re-litigated |
| A partial index on `messages (chat_id) WHERE is_outgoing` | The composite primary key `(chat_id, message_id)` already gives the per-Dialog range scan, with `is_outgoing` as a cheap residual filter. An index-strategy change needs its own ADR; deferred until a real archive shows it is needed |
| A keyword blacklist in `sanitise_persona_field` | Blocking "ignore" or "instruction" rejects honest descriptions of a writing style and stops nobody who can rephrase a sentence. The controls are structural instead: strip the invisible, collapse the whitespace, refuse a destination |

## Consequences

- `server.py` now **writes** to PostgreSQL, which it never did before. It writes
  to `dialog_personas` and nothing else; `dialogs` and `messages` stay owned by
  `sync_db.py`. The [SAD](../sad.md) diagram reflects this.
- `dialog_personas` holds the only rows in this database that a resync cannot
  rebuild. That is why `just db-reset` now prints what it is about to destroy
  and requires typing `DELETE` on a terminal; the backup command is in the
  [resync runbook](../../70-ops/runbooks/resync-archive.md).
- A new risk class exists and is recorded as `RISK-07`: unlike `RISK-06`, which
  is transient and per-turn, a Persona persists and is re-injected on every read.
- Style Metrics may safely record that the account writes in a non-Latin script,
  because the script *name* comes from `unicodedata` at runtime. Stored Persona
  text may be in any language: it lives in PostgreSQL, and `docs-english` scans
  only `*.md`, `*.py` and `*.sql`. A future session must not "fix" this.
- Adding a metric costs one field and no migration. Adding an agent-written
  field costs a column, and `CREATE TABLE IF NOT EXISTS` will not add it to an
  existing database - that would need an explicit `ALTER` and a superseding ADR.
