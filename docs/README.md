---
id: DOC-ROUTER
title: Documentation Router
status: active
authority: authoritative
updated: 2026-09-05
related: [AGENTS, DOC-GLOSSARY, DOC-DOC-MAP, DOC-STATUS]
---

# Documentation Router

**Read this first, then read only the slice your task needs.** This page says
which document answers what. It carries no content of its own: every fact lives
in exactly one place and everything else links to it.

Every document under `docs/` is reachable from here. A document not linked from
this page is orphaned and must be either linked or deleted.

## Answer lookup

| Your question | Document |
| --- | --- |
| What is this project and how do I run it? | [README](../README.md) |
| What can I actually do with it, with example prompts? | [Use cases](../USE-CASES.md) |
| Where is the Russian documentation? | [RU README](i18n/ru/README.md), [RU use cases](i18n/ru/USE-CASES.md) |
| What are the rules I must follow while coding? | [AGENTS.md](../AGENTS.md) |
| What is this concept called in this codebase? | [Glossary](00-index/glossary.md) |
| Where does the project stand right now? | [Status](00-index/status.md) |
| I changed X - which documents must I update? | [Documentation map](00-index/documentation-map.md) |
| What identifier do I assign to a new thing? | [ID registry](00-index/id-registry.md) |
| Who is this for and what is out of scope? | [PRD](10-product/prd.md) |
| What exactly must the code do, clause by clause? | [SRS](10-product/srs.md) |
| How do the three processes fit together? | [SAD](20-architecture/sad.md) |
| What do the database columns mean? | [Data model](20-architecture/data-model.md) |
| Why was a decision made this way? | [ADR index](20-architecture/adr/README.md) |
| What are the exact MCP tool signatures? | [MCP tools](30-api/mcp-tools.md) |
| How do I keep the docs true after a change? | [Docs maintenance protocol](50-process/docs-maintenance-protocol.md) |
| What must be tested and how? | [QA and testing](50-process/qa-and-testing.md) |
| What is built and what comes next? | [Roadmap](60-delivery/roadmap.md) |
| What should I work on now? | [Task queue](60-delivery/task-queue.md) |
| How do I deploy and register the server? | [Deployment plan](70-ops/deployment-plan.md) |
| What are the security rules for the session file? | [Security](70-ops/security.md) |
| Something is broken - what do I do? | [Runbooks](70-ops/runbooks/README.md) |
| What changed and when? | [Changelog](90-history/CHANGELOG.md) |

## Reading order for a new session

1. [AGENTS.md](../AGENTS.md) - the rules. Non-negotiable.
2. [Status](00-index/status.md) - what already exists.
3. [Glossary](00-index/glossary.md) - the vocabulary, so naming stays consistent.
4. The one slice your task touches, from the table above.

Do not read the whole tree. Reading documents you do not need costs the user
tokens and gains nothing.

## Zones

| Zone | Contains |
| --- | --- |
| `00-index/` | Navigation, vocabulary, identifiers, current state |
| `10-product/` | What the system must do |
| `20-architecture/` | How it is built, and why |
| `30-api/` | The agent-facing contract |
| `50-process/` | How work is done and documented |
| `60-delivery/` | What is planned and what is next |
| `70-ops/` | Running it, securing it, fixing it |
| `90-history/` | What happened |
| `i18n/` | Translations. The only place non-English text may live |
