---
id: DOC-DOCS-PROTOCOL
title: Documentation maintenance protocol
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-DOC-MAP, DOC-GLOSSARY, DOC-ID-REGISTRY, AGENTS]
---

# Documentation maintenance protocol

**Mandatory. Runs at the end of every task, before the task is reported done.**

This is the mechanism that keeps the documentation true. It works by removing
judgement from the update step: classify the change, read the row, update those
files.

> **Documentation is part of the implementation.** A task that affects
> behaviour, architecture, interfaces or operations is **not complete** until
> the documents are updated, or a decision to defer is explicitly recorded as a
> `TASK-NNN`.

The reason is in [ADR-0007](../20-architecture/adr/0007-documentation-governance.md):
this codebase is worked on by agents that hold no memory between sessions. The
documentation is not a courtesy to a future human, it is the index the next
session works from. A stale document does not merely fail to help - it actively
misleads, and an agent will confidently build on it.

## Step 1 - Classify

Find your change in the [documentation map](../00-index/documentation-map.md).

Matching no row is a defect in that table. Add the row in this same change.

## Step 2 - Resolve the file set

Take the union of every matching row, then always add:

- `docs/00-index/status.md`
- `docs/90-history/CHANGELOG.md`

## Step 3 - Check the glossary first

If your change introduced or renamed a concept, update
[the glossary](../00-index/glossary.md) **before** touching anything else, then
use that exact term everywhere. A term that reaches code before the glossary is
a term that will be spelled two ways within a week.

## Step 4 - Apply

Respect each document's mutability:

| Document | Mutability |
| --- | --- |
| ADRs | **Append-only.** A reversed decision gets a new ADR that marks the old one `superseded`. Never rewrite history |
| SRS clauses | Revised in place. The clause id survives its wording |
| Glossary | Revised in place, but a rename propagates everywhere in the same change |
| Data model | Must match `sql/schema.sql` exactly. If they disagree, the SQL is right |
| Router, map, registry | Revised in place. Must list everything that exists and nothing that does not |
| Derived documents | Restate nothing. Link to the authoritative document instead |

Bump `updated:` in the frontmatter of every file you touch. Add new identifiers
to the [ID registry](../00-index/id-registry.md).

## Step 5 - Update state

- `docs/00-index/status.md` - what is now true. Replace stale lines; do not append.
- `docs/60-delivery/task-queue.md` - close what you finished, add what you found.
- `docs/90-history/CHANGELOG.md` - one line, newest first.

## Step 6 - Verify

```bash
just docs-check
```

Then confirm by hand:

- Every new document is linked from [the router](../README.md).
- Every relative link resolves (`just docs-links` proves this).
- No document contradicts another. When two disagree, the one marked
  `authority: authoritative` wins and the other is corrected.
- No non-English text anywhere (`just docs-english` proves this).

## Step 7 - Report

End the task with this block, verbatim:

```text
DOCS SYNC
  Change type:    <from the documentation map>
  Updated:        <files>
  IDs added:      <identifiers>
  IDs superseded: <identifiers, or none>
  docs-check:     PASS|FAIL
```

## When a decision is missing

Do not pick a default and proceed silently.

| Situation | Action |
| --- | --- |
| A product or scope question | Add it to [task-queue.md](../60-delivery/task-queue.md) as an open question and ask the user |
| A technical choice with real alternatives | Write the ADR with the alternatives, mark it `status: draft`, and ask |
| A safety rule you are unsure of | Choose the more conservative option and record why. An over-cautious send is recoverable; a banned account is not |

## Anti-patterns

| Anti-pattern | Why it fails |
| --- | --- |
| Updating documents "later, in one pass" | Reconstruction from memory produces confident fiction |
| Restating a rule in a second document "for convenience" | A rule written twice drifts in one of the copies |
| Editing an ADR to reflect a new decision | Destroys the record of why the old one was made, which is the entire value of an ADR |
| Adding a document without linking it from the router | Orphaned documents are invisible to the next session and rot |
| Writing "TODO: document this" | The next session will not find it. Write the `TASK-NNN` instead |
