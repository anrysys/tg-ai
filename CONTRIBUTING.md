# Contributing

This repository is maintained by AI agents. The rules are in
[AGENTS.md](AGENTS.md) and they are not optional.

## Before you change anything

1. Read [AGENTS.md](AGENTS.md).
2. Read [docs/00-index/status.md](docs/00-index/status.md).
3. Route to your slice through [docs/README.md](docs/README.md).

## While you work

- English only, everywhere. `just docs-english` enforces it.
- Use the vocabulary in [the glossary](docs/00-index/glossary.md). If a concept
  is called a `Dialog`, do not introduce a `Chat`.
- Safety logic belongs in `tg_ai/safety.py` - pure and unit-testable. A rule
  that cannot be tested offline is in the wrong module.
- Never weaken the Send Guard. See [AGENTS.md §3](AGENTS.md#3-the-send-guard-is-not-negotiable).

## Before you report done

```bash
just check
```

Then run the
[Documentation Maintenance Protocol](docs/50-process/docs-maintenance-protocol.md)
and emit its `DOCS SYNC` block. A change without it is incomplete.

## Adding something new

| Adding | Also required |
| --- | --- |
| An MCP tool | A `SPEC-` clause in [the SRS](docs/10-product/srs.md), a schema entry in [mcp-tools.md](docs/30-api/mcp-tools.md), a row in the README tool table |
| A dependency | An ADR, plus `requirements.txt` and the stack table in AGENTS.md |
| A database column | `sql/schema.sql`, [data-model.md](docs/20-architecture/data-model.md), and an ADR for any index change |
| A decision with a rejected alternative | An ADR from [the template](docs/20-architecture/adr/0000-template.md) |
| A document | A link in [the router](docs/README.md) and an entry in the [ID registry](docs/00-index/id-registry.md) |

Full lookup table: [documentation-map.md](docs/00-index/documentation-map.md).

## Commits

Imperative mood, one concern per commit, no credential ever in a diff.

```text
Add trigram fallback to local history search

Full-text search with the 'simple' configuration misses partial words.
See ADR-0003.
```
