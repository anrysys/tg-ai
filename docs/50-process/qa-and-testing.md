---
id: DOC-QA
title: QA and testing
status: active
authority: authoritative
updated: 2026-09-05
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
| Peer filtering | `tests/test_peer_rules.py` | `SPEC-SYNC-001` - who is archived, target normalisation |
| Target filtering | `tests/test_peer_rules.py` | `SPEC-SYNC-006` - exact matching, dialog ordering, unmatched reporting |
| Style measurement | `tests/test_persona_metrics.py` | `SPEC-PSN-002`, `SPEC-PSN-003`, `SPEC-PSN-005` - totality over empty input, distributions not means, no message text in any metric, the three freshness axes |
| Persona SQL | `tests/test_persona_sql.py` | `SPEC-PSN-001`, `-003`, `-004`, `SPEC-SRCH-006` - the frozen baseline, no silent overwrite, outgoing-only reads, Dialog Lookup shape |
| Persona safety | `tests/test_persona_render.py` | `SPEC-PSN-006`, `RISK-07` - sanitisation, the data fence, ambiguity reported not guessed |

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
