---
id: DOC-RB-FLOOD
title: "Runbook: flood wait and rate limits"
status: active
authority: derived
updated: 2026-09-04
related: [DOC-SECURITY, DOC-SRS]
---

# Runbook: flood wait and rate limits

## Symptom

```text
ERROR: Telegram API limit reached. We must wait 143 seconds
```

## What it means

Telegram has imposed a cooldown on this API method. It is a normal, temporary
rate limit - not a ban, and not a sign anything is broken.

## Do

1. **Tell the user the number of seconds.** It is in the message.
2. **Wait.** Do nothing until it elapses.
3. Then retry the single operation once.

## Do not

- Do not retry in a loop. Retrying during a wait extends it.
- Do not switch to a different tool to "get around" it.
- Do not restart the MCP server. The limit is server-side, on the account.

`tg_search_local_history` still works throughout - it never touches Telegram
(`SPEC-SRCH-001`). Use it for anything that does not require the live account.

## During a sync

The sync handles this itself: it commits what it has, sleeps `seconds + 5`, and
resumes the same dialog (`SPEC-SYNC-004`). Long waits during a full backfill are
expected. Let it run, or interrupt with Ctrl-C - progress is saved and
`just tg-sync` resumes from the cursor.

## Escalation: this is not a flood wait

```text
ERROR: Telegram has flagged this account for spam (PeerFloodError).
```

That is different and serious. **Stop sending entirely** and follow
[Security → if the account is restricted](../security.md#if-the-account-is-restricted).

## If it keeps happening

Rate limits during normal use suggest a rule regressed. Check:

- `CHUNK_DELAY_SECONDS` is still `>= 2.5` in `tg_ai/safety.py`.
- `SYNC_DIALOG_DELAY_SECONDS` is still `>= 1.0`.
- Nothing is calling `tg_send_message` in a loop.

Record what triggered it in [status.md](../../00-index/status.md).
