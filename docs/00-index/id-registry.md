---
id: DOC-ID-REGISTRY
title: Identifier registry
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-ROUTER, DOC-SRS, DOC-ADR-INDEX]
---

# Identifier registry

Numbers are **never reused** and **never pre-allocated**. Take the next free
one at the moment you write the thing, and record it here in the same change.

## Namespaces

| Prefix | Meaning | Lives in | Mutability |
| --- | --- | --- | --- |
| `SPEC-<AREA>-<NNN>` | A testable requirement clause | [SRS](../10-product/srs.md) | Revised in place; the clause id survives |
| `ADR-<NNNN>` | An architecture decision | [adr/](../20-architecture/adr/) | Append-only; superseded, never edited |
| `RISK-<NN>` | A named operational risk | [Security](../70-ops/security.md) | Revised in place |
| `TASK-<NNN>` | A unit of planned work | [Task queue](../60-delivery/task-queue.md) | Closed, never deleted |
| `DOC-<NAME>` | A document | that document's frontmatter | Renamed only with every link updated |

## SPEC areas

| Area | Covers |
| --- | --- |
| `SND` | Sending messages: the send guard, chunking, pacing, error translation |
| `RCV` | Reading the live account: recent messages, unread dialogs |
| `SRCH` | Searching the archive |
| `SYNC` | Filling the archive |
| `PSN` | Per-Dialog style: the Dialog Persona, its measurement, freshness and safety |
| `SEC` | Credentials, permissions, configuration, locality |
| `LIM` | Pacing, budgets, the kill switch, the quiet window, blacklisted operations |

## Allocated

| Range | Status |
| --- | --- |
| `SPEC-SND-001` .. `SPEC-SND-006` | In use |
| `SPEC-RCV-001` .. `SPEC-RCV-004` | In use |
| `SPEC-SRCH-001` .. `SPEC-SRCH-006` | In use |
| `SPEC-SYNC-001` .. `SPEC-SYNC-006` | In use |
| `SPEC-PSN-001` .. `SPEC-PSN-008` | In use |
| `SPEC-SEC-001` .. `SPEC-SEC-010` | In use |
| `ADR-0000` .. `ADR-0010` | In use (`0000` is the template) |
| `RISK-01` .. `RISK-08` | In use |
| `TASK-001` .. `TASK-013` | In use |
