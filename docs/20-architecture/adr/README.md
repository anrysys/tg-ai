---
id: DOC-ADR-INDEX
title: Architecture decision records
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-SAD, DOC-ID-REGISTRY, DOC-DOCS-PROTOCOL]
---

# Architecture decision records

An ADR records **why** a decision was made, including the alternatives that were
rejected. Its purpose is to stop a future session from "improving" a deliberate
choice back into the problem it solved.

**ADRs are append-only.** A reversed decision gets a new ADR that marks the old
one superseded. Never edit an accepted ADR to reflect a new decision - that
destroys exactly the record that gives it value.

Start from [`0000-template.md`](0000-template.md). Take the next free number
from the [ID registry](../../00-index/id-registry.md).

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-telethon-mtproto-over-bot-api.md) | Use Telethon and MTProto, not the Bot API | Accepted |
| [0002](0002-three-process-split-auth-sync-server.md) | Split auth, sync and server into three processes | Accepted |
| [0003](0003-postgres-fts-simple-plus-trigram.md) | Search with the `simple` configuration plus a trigram fallback | Accepted |
| [0004](0004-session-file-clone-for-sync.md) | Sync works on a copy of the session file | Accepted |
| [0005](0005-hard-block-send-to-unknown-peers.md) | Hard-block sends to strangers, with no override flag | Accepted |
| [0006](0006-raw-sql-asyncpg-no-orm.md) | Raw SQL over asyncpg, no ORM | Accepted |
| [0007](0007-documentation-governance.md) | Documentation is part of the implementation | Accepted |
