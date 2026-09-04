---
name: docs-maintenance
description: Run at the end of every task in this repository to keep the documentation true. Use when you have changed code, schema, tools, dependencies or operations and are about to report the task done.
---

# Documentation maintenance

The authority is
[docs/50-process/docs-maintenance-protocol.md](../../../docs/50-process/docs-maintenance-protocol.md).
This is the condensed loop.

**A task is not done until this has run.**

## The seven steps

1. **Classify.** Find your change in
   [docs/00-index/documentation-map.md](../../../docs/00-index/documentation-map.md).
   No matching row is a defect in that table - add the row now.
2. **Resolve.** Take the union of matching rows, plus always
   `docs/00-index/status.md` and `docs/90-history/CHANGELOG.md`.
3. **Glossary first.** New or renamed concept? Update
   `docs/00-index/glossary.md` before anything else, then use that exact term
   everywhere.
4. **Apply.** Respect mutability: ADRs are append-only, SRS clauses are revised
   in place keeping their id, derived documents link rather than restate. Bump
   `updated:` in every file you touch. Register new identifiers in
   `docs/00-index/id-registry.md`.
5. **Update state.** Rewrite the stale lines in `status.md` (do not append),
   close finished tasks in `task-queue.md`, add one changelog line.
6. **Verify.** `just docs-check`, then confirm every new document is linked from
   `docs/README.md`.
7. **Report.** Emit the `DOCS SYNC` block.

## Report block

```text
DOCS SYNC
  Change type:    <from the documentation map>
  Updated:        <files>
  IDs added:      <identifiers>
  IDs superseded: <identifiers, or none>
  docs-check:     PASS|FAIL
```

## Never

| Never | Because |
| --- | --- |
| Defer updates to "one pass later" | Reconstruction from memory produces confident fiction |
| Edit an accepted ADR to reflect a new decision | Write a superseding ADR. The old record is the value |
| Restate a rule in a second document | It drifts in one of the copies |
| Add a document without a router link | Orphaned documents are invisible to the next session |
| Write `TODO: document this` | Write a `TASK-NNN` in the task queue instead |

## Missing a decision

Do not pick a default. A scope question goes to `task-queue.md` and to the user.
A technical choice with real alternatives gets a `status: draft` ADR and a
question. A safety question resolves to the more conservative option, with the
reason recorded.
