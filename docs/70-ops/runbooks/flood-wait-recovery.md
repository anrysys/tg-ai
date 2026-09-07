---
id: DOC-RB-FLOOD
title: "Runbook: flood wait and rate limits"
status: active
authority: derived
updated: 2026-09-07
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

## The kill switch has tripped

```text
ERROR: Telegram safety kill switch is ON since 2026-09-07 09:58 UTC:
       3 flood waits within an hour ...
```

Three flood events inside a rolling hour trip a project-wide kill switch
(`SPEC-LIM-003`). Both the MCP server and the sync then refuse everything that
touches Telegram, for 24 hours, from every process. That is deliberate: the
length of any one wait matters far less than how often the account is being told
to slow down.

1. **Stop.** Do not restart the MCP server - the limit is on the account, not on
   the process, and a restart cannot clear it.
2. Run `just tg-killswitch` to see when it tripped and why.
3. **Check the account from the official Telegram app**: message **@SpamBot** and
   read what it says.
4. If @SpamBot reports a limitation, **wait**. Do not automate an appeal.
   Automated appeal mail is itself a documented ban trigger, so a script that
   "helps" here makes things worse.
5. Once @SpamBot says the account is free, clear it deliberately:

   ```bash
   just tg-killswitch-clear
   ```

   It asks for confirmation and refuses to run without a terminal, because a
   piped "yes" is not a decision.

The switch lapses on its own after 24 hours. Clearing it early is a claim that
you have checked the account, so only make that claim if you have.

## Escalation: this is not a flood wait

```text
ERROR: Telegram has flagged this account for spam (PeerFloodError).
```

That is different and serious. It trips the kill switch **indefinitely** - there
is no 24-hour expiry, because this is an escalation rather than a cooldown.
**Stop sending entirely** and follow
[Security → if the account is restricted](../security.md#if-the-account-is-restricted).
Only `just tg-killswitch-clear`, run by a person who has checked @SpamBot, turns
it back on.

## If it keeps happening

Rate limits during normal use suggest a rule regressed. Check:

- `CHUNK_DELAY_SECONDS` is still `>= 2.5` in `tg_ai/safety.py`.
- `SYNC_DIALOG_DELAY_SECONDS` is still `>= 1.0`.
- `MIN_RPC_GAP_SECONDS` is still `>= 1.5`.
- `flood_sleep_threshold=0` is still passed in `build_client`. If it is not,
  short flood waits are being silently retried inside Telethon and none of the
  controls above can see them (`RISK-09`).
- `jittered()` still only ever adds - it must never return less than the
  constant it is given (`SPEC-LIM-005`).
- Nothing is calling `tg_send_message` in a loop.

`just tg-status` shows the remaining hourly and daily budget, which is the
fastest way to tell "busy day" from "runaway loop".

Record what triggered it in [status.md](../../00-index/status.md).
