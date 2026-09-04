---
id: ADR-0007
title: "Treat documentation as part of the implementation"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-DOCS-PROTOCOL, DOC-DOC-MAP, AGENTS]
---

# ADR-0007 - Documentation is part of the implementation

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** the structure of `docs/`, `AGENTS.md`, `CLAUDE.md`
**Affects:** every task in this repository

## Context

This codebase is developed by AI agents, not by a standing human team. An agent
starts each session with no memory of the last one. What it knows about this
project is exactly what it reads.

That inverts the usual economics of documentation. A stale document does not
merely fail to help - it actively misleads, and an agent will build on it
confidently and produce work that is wrong in a way that is expensive to find.
Meanwhile, reading documents that are not needed for the task at hand costs the
user tokens on every turn.

The standard remedy, "remember to update the docs", does not survive contact
with an agent that has no memory to remember with.

## Decision

Make documentation maintenance mechanical rather than conscientious:

1. **One entry point.** [`docs/README.md`](../../README.md) routes to everything.
   A document not linked from it is orphaned and must be linked or deleted.
2. **One place per fact.** Documents marked `authority: authoritative` own their
   content; `derived` documents link rather than restate. A rule written twice
   drifts in one of the copies.
3. **A lookup table, not a judgement call.**
   [`documentation-map.md`](../../00-index/documentation-map.md) maps change
   types to the exact files that must be updated.
4. **A protocol that runs every task.**
   [`docs-maintenance-protocol.md`](../../50-process/docs-maintenance-protocol.md),
   ending in a fixed `DOCS SYNC` report block.
5. **Machine-checkable invariants.** `just docs-check` proves that every
   relative link resolves and that nothing non-English has been committed.
6. **One authority for agent rules.** `AGENTS.md`. `CLAUDE.md` and
   `.github/copilot-instructions.md` are pointers that restate nothing.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| A single large `README.md` | Every session pays for the whole file to answer any question. Token cost scales with the project, not the task |
| Docstrings and code comments only | Cannot record a rejected alternative, which is the entire value of an ADR. Nothing stops a future session re-litigating a settled decision |
| A wiki or external doc site | Not in the repository, so not in the agent's context, so not read |
| "Update the docs when it matters" | Requires judgement at the moment of least attention, at the end of a task. Produces exactly the drift it was meant to prevent |

## Consequences

- Every task ends with a `DOCS SYNC` block. A task without one is incomplete.
- Adding a document means adding a router link and an ID registry entry in the
  same change.
- The numbered zone layout mirrors the convention already in use in the user's
  other projects, so an agent moving between them needs no re-orientation.
- CI enforces the checkable parts. The rest is enforced by the protocol being
  short enough to actually follow.
