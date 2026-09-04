---
id: DOC-RUNBOOK-INDEX
title: Runbooks
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-DEPLOY, DOC-SECURITY]
---

# Runbooks

Step-by-step recovery procedures. One file per failure mode.

| Symptom | Runbook |
| --- | --- |
| Nothing is set up yet | [First-time setup](first-time-setup.md) |
| "Telegram API limit reached. We must wait N seconds" | [Flood wait recovery](flood-wait-recovery.md) |
| "The Telegram session is no longer valid" / "No Telegram session at ..." | [Session lost or revoked](session-lost-or-revoked.md) |
| Search finds nothing that should exist | [Resync the archive](resync-archive.md) |
| `Database: UNREACHABLE` | [Resync the archive](resync-archive.md#the-database-is-unreachable) |

Learned a recovery the hard way? Add a file here and a row above - that is a row
in the [documentation map](../../00-index/documentation-map.md).
