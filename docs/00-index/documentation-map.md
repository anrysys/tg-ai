---
id: DOC-DOC-MAP
title: Documentation map
status: active
authority: authoritative
updated: 2026-09-05
related: [DOC-DOCS-PROTOCOL, DOC-ROUTER]
---

# Documentation map

**The change-to-document lookup table.** Step 1 of the
[Documentation Maintenance Protocol](../50-process/docs-maintenance-protocol.md)
is: find your change in this table and update exactly the files in its row.

This table removes judgement from the update step. You are not deciding what to
document; you are reading a row.

`00-index/status.md` and `90-history/CHANGELOG.md` are updated on **every**
change and are therefore omitted from each row.

| Change type | Must update |
| --- | --- |
| Added, removed or renamed an MCP tool | [srs.md](../10-product/srs.md) clause, [mcp-tools.md](../30-api/mcp-tools.md), the tool table in [README.md](../../README.md), [glossary.md](glossary.md) if a new term appeared |
| Changed a tool's parameters, defaults or return text | [srs.md](../10-product/srs.md) clause, [mcp-tools.md](../30-api/mcp-tools.md) |
| Changed an anti-spam rule (guard, chunk size, delay, retry) | [srs.md](../10-product/srs.md) `SPEC-SND-*`, [security.md](../70-ops/security.md), a new ADR if the rule itself changed rather than its value |
| Changed SQL: table, column, index or extension | [sql/schema.sql](../../sql/schema.sql), [data-model.md](../20-architecture/data-model.md), a new ADR for any index-strategy change |
| Changed how the archive is searched | [srs.md](../10-product/srs.md) `SPEC-SRCH-*`, [data-model.md](../20-architecture/data-model.md) |
| Changed the sync algorithm, filters or cursor behaviour | [srs.md](../10-product/srs.md) `SPEC-SYNC-*`, [sad.md](../20-architecture/sad.md) |
| Added, removed or upgraded a dependency | [requirements.txt](../../requirements.txt), the stack section of [AGENTS.md](../../AGENTS.md), a new ADR if it replaces an existing approach |
| Changed process topology or session handling | [sad.md](../20-architecture/sad.md), a new ADR, [deployment-plan.md](../70-ops/deployment-plan.md) |
| Changed an environment variable | [.env.example](../../.env.example), [deployment-plan.md](../70-ops/deployment-plan.md), [srs.md](../10-product/srs.md) `SPEC-SEC-*` |
| Added or renamed a `just` recipe | [README.md](../../README.md), [deployment-plan.md](../70-ops/deployment-plan.md) |
| Added a test, or changed what a clause guarantees | [srs.md](../10-product/srs.md) clause `Test.` line, [qa-and-testing.md](../50-process/qa-and-testing.md) |
| Introduced a new domain concept, or renamed one | [glossary.md](glossary.md) **first**, then every document and identifier using the old name |
| Made an architectural choice with a rejected alternative | a new ADR from [0000-template.md](../20-architecture/adr/0000-template.md), plus [id-registry.md](id-registry.md) |
| Reversed a decision recorded in an ADR | a **new** ADR marking the old one superseded. Never edit the old one |
| Discovered a new way the account could be banned or leaked | [security.md](../70-ops/security.md) with a new `RISK-NN`, [srs.md](../10-product/srs.md) if code must change |
| Finished, added or reordered planned work | [roadmap.md](../60-delivery/roadmap.md), [task-queue.md](../60-delivery/task-queue.md) |
| Added or removed a user-facing capability | [prd.md](../10-product/prd.md) use case, [roadmap.md](../60-delivery/roadmap.md) |
| Added or renamed a module under `tg_ai/` | [sad.md](../20-architecture/sad.md) module tree, [qa-and-testing.md](../50-process/qa-and-testing.md) coverage table |
| Learned a recovery procedure the hard way | a new file under [runbooks/](../70-ops/runbooks/), linked from its README |
| Changed the public pitch: the value proposition, tool list, install steps or FAQ | [README.md](../../README.md), [USE-CASES.md](../../USE-CASES.md), the matching files under [i18n/ru/](../i18n/ru/README.md), then `just site-build` to regenerate `site/` and `llms-full.txt` |
| Changed anything an AI crawler or MCP directory reads | [llms.txt](../../llms.txt), [server.json](../../server.json), [CITATION.cff](../../CITATION.cff), then `just site-build` |

## When your change matches no row

That is a defect in **this table**, not permission to skip documenting. Add the
row in the same change that needed it.
