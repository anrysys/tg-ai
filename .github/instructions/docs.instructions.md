---
applyTo: "**/*.md"
---

# Documentation instructions

Full protocol:
[docs-maintenance-protocol.md](../../docs/50-process/docs-maintenance-protocol.md).

Every file under `docs/`, and every root-level `.md`, carries this frontmatter:

```yaml
---
id: DOC-SHORT-NAME
title: Human readable title
status: draft | active | frozen | superseded
authority: authoritative | derived | reference
updated: YYYY-MM-DD
related: [ID, ID]
---
```

- **English only.** No Cyrillic or other scripts outside `docs/i18n/`. CI fails
  on it.
- One fact, one place. `authority: derived` documents link rather than restate.
- Every new document is linked from [docs/README.md](../../docs/README.md) and
  registered in [id-registry.md](../../docs/00-index/id-registry.md).
- ADRs are append-only. Reverse a decision with a new, superseding ADR.
- Bump `updated:` on every file you touch.
- End the task with the `DOCS SYNC` block.
