---
id: DOC-QA
title: QA and testing
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-SRS, DOC-DOCS-PROTOCOL]
---

# QA and testing

## Principle

The rules that protect the account are pure functions, deliberately, so they can
be proven without a network or a database. If a safety rule cannot be
unit-tested, it is in the wrong module.

The suite is fully offline: it never contacts Telegram and never needs
PostgreSQL. `just py-test` must pass on a machine with no credentials at all.

## Coverage by area

| Area | File | Proves |
| --- | --- | --- |
| Chunking | `tests/test_chunking.py` | `SPEC-SND-002` - limits, boundaries, no broken words, unicode |
| Error handling | `tests/test_safety.py` | `SPEC-SND-003`, `SPEC-SND-004` - pacing constant, never-raise, exact flood-wait wording |
| Search SQL | `tests/test_search_sql.py` | `SPEC-SRCH-002`, `-003`, `-004` - strategies, `simple`, bound parameters |
| Configuration | `tests/test_config.py` | `SPEC-SEC-003`, `SPEC-SEC-004` - validation, path rejection, clone naming |
| Peer filtering | `tests/test_peer_rules.py` | `SPEC-SYNC-001` - who is archived, Peer Types, posting rights, target normalisation, the contacts hash |
| Target filtering | `tests/test_peer_rules.py` | `SPEC-SYNC-006` - exact matching, dialog ordering, unmatched reporting |
| Style measurement | `tests/test_persona_metrics.py` | `SPEC-PSN-002`, `SPEC-PSN-003`, `SPEC-PSN-005` - totality over empty input, distributions not means, no message text in any metric, the three freshness axes |
| Persona SQL | `tests/test_persona_sql.py` | `SPEC-PSN-001`, `-003`, `-004`, `SPEC-SRCH-006` - the frozen baseline, no silent overwrite, outgoing-only reads, Dialog Lookup shape |
| Persona safety | `tests/test_persona_render.py` | `SPEC-PSN-006`, `RISK-07` - sanitisation, the data fence, ambiguity reported not guessed |
| Client contract | `tests/test_client_contract.py` | `SPEC-SEC-007`, `SPEC-SEC-008`, `SPEC-SEC-009` - every pinned Telethon parameter, identity identical for session and clone, zero event handlers |
| Connection lock | `tests/test_connection_lock.py` | `SPEC-SEC-010` - mutual exclusion, a crashed holder releasing, the lock keyed to the primary session |
| Server shutdown | `tests/test_server_shutdown.py` | `SPEC-SEC-011` - the teardown order pinned against a real `flock`, the lock released even when the disconnect fails, a hung step bounded, the lifespan registered, and a closed stdin exiting 0 |
| RPC limiter | `tests/test_rpc_guard.py` | `SPEC-LIM-001` .. `SPEC-LIM-004` - pacing, re-entrancy without deadlock, budgets surviving a restart, the kill switch, failing closed |
| Safety SQL | `tests/test_rpc_sql.py` | `SPEC-LIM-002`, `SPEC-LIM-003` - rolling windows not calendar buckets, indexed timestamps, idempotent schema |
| Peer resolution | `tests/test_group_rules.py` | `SPEC-SND-006`, `SPEC-SYNC-007` - a Group not in the Peer Index is refused **with the client asserted never called**; the volume caps pinned as values |
| Blacklist | `tests/test_blacklist.py` | `SPEC-LIM-006` - 50 forbidden identifiers absent from the shipped source |
| Server limits | `tests/test_server_limits.py` | `SPEC-LIM-007`, `SPEC-RCV-003`, `SPEC-SND-001`, `SPEC-SND-007`, `SPEC-SND-008`, `SPEC-PSN-009` - the per-process ceiling, the group cooldown and daily cap across a simulated restart, the Group and Channel send guard, and Persona isolation |

### On "coverage"

There is no coverage tool in this project and adding one would be a dependency
change needing its own ADR. Coverage here means the table above: each row names
the clause it proves. Several of the safety tests are additionally checked by
deliberately breaking the code and confirming the test fails - a regression test
that passes for the wrong reason is worth nothing. The re-entrancy test in
`test_rpc_guard.py` is bounded by `asyncio.wait_for` for that reason: without a
timeout a regression would hang the suite rather than fail it.

## What cannot be unit-tested

These need a live account and are verified by hand. Record the result in
[status.md](../00-index/status.md) when you run them.

| Check | How | Expected |
| --- | --- | --- |
| Send guard actually blocks | `tg_send_message` to a never-contacted account | `WARNING`, and the Telegram app shows nothing sent |
| Chunking end to end | Send 5000 characters to `me` | 2 messages, ~2.5s apart, no broken word |
| Sync idempotency | Run the same `--dialog --limit` twice | Second run reports `+0 msgs` |
| Targeted sync | `just tg-sync-targets @someone` | Only that dialog is processed; a mistyped target is named in a warning |
| Sync resumability | Interrupt with Ctrl-C, rerun | Resumes at the cursor, not from zero |
| Group read costs one request | `just tg-sync-targets "<a group>"`, then inspect `api_call_log` | Exactly one `GetHistoryRequest`, and no `ReadHistory` of any kind |
| A group send actually arrives | Send one short message to a group you are in | It appears once, as one message |
| Reading a group marks nothing read | Read a group, then open it on the phone | The unread badge is unchanged |
| The group cooldown survives a restart | Read a group, restart the MCP server, read it again | Still refused, naming the remaining seconds |
| A channel send is refused without an API call | `tg_send_message` to a channel you only subscribe to | `ERROR` naming posting rights, and `api_call_log` gains no row |
| An unjoined channel is unreachable | `tg_get_recent_messages("@some_public_channel")` | Refused, and `api_call_log` gains no row |
| The connection lock holds | `just tg-sync` while `just tg-serve` runs | The sync exits 1, naming the other process, and creates no clone |
| Flood accumulation is visible | `just tg-status` after a busy day | Reports the remaining hourly and daily budget, and the kill switch |
| Reading is non-destructive | `tg_get_unread_dialogs`, then check the app | Unread badges unchanged |
| A Persona survives a resync | Store one, then `just tg-sync` | `tg_get_dialog_persona` still returns it (`SPEC-PSN-001`) |
| A Persona cannot model itself | Draft and send ~20 replies, sync, re-read | `baseline_message_id` and `analysed_count` unchanged; the metrics still describe the user (`SPEC-PSN-003`) |
| A dead archive cannot break a live read | `just db-down`, then `tg_get_recent_messages` | The conversation still returns, with `PERSONA: unavailable` (`SPEC-PSN-007`) |
| A dead archive cannot break a send | `just db-down`, then `tg_send_message` to `me` | Sends, no `ERROR:`, no hint appended (`SPEC-PSN-008`) |

## Writing a new test

1. It must name the `SPEC-` clause it proves, in the module docstring or the
   test name.
2. It must not require network, credentials or a database.
3. The clause's `Test.` line in the [SRS](../10-product/srs.md) must be updated
   to name it - that is a row in the
   [documentation map](../00-index/documentation-map.md).

A safety rule with no test is not a rule; it is a comment.

## Before reporting a task done

```bash
just check     # ruff, black, pytest, docs-english, docs-links
just docs-lint # markdownlint; needs network for npx, so it is not in `check`
```

Then run the Documentation Maintenance Protocol and emit its `DOCS SYNC` block.
