---
id: DOC-RB-SESSION
title: "Runbook: session lost or revoked"
status: active
authority: derived
updated: 2026-09-07
related: [DOC-SECURITY, ADR-0004]
---

# Runbook: session lost or revoked

## Symptoms

```text
ERROR: No Telegram session at /home/anry/projects/tg-ai/tg_session.session.
       Run `just tg-auth` in a terminal ...
```

```text
ERROR: The Telegram session is no longer valid - it was revoked or logged out.
       Run `just tg-auth` in a terminal to sign in again.
```

```text
ERROR: This session was invalidated because another connection used the same
       authorization key (AUTH_KEY_DUPLICATED). The login is already gone ...
```

## Causes

| Cause | Signal |
| --- | --- |
| Never logged in | The file does not exist |
| Session terminated in the Telegram app | The file exists but is unauthorised |
| Password changed | All sessions were revoked |
| Signed out from another device with "terminate all" | Same |
| File corrupted by two concurrent writers | `database is locked`, or SQLite errors - see [ADR-0004](../../20-architecture/adr/0004-session-file-clone-for-sync.md) |
| Two live connections on one authorization key | `AUTH_KEY_DUPLICATED` - see the section below |

## Recovery

```bash
cd /home/anry/projects/tg-ai
just tg-auth
```

Then verify:

```bash
stat -c %a tg_session.session      # 600
```

Ask the agent to call `tg_whoami()`; it should report the account.

The MCP server does **not** need restarting for a fresh login in most cases, but
if tools still report an unauthorised session, restart the agent so the server
re-reads the file.

## If the session file may have leaked

Treat it as a full account compromise
([RISK-03](../security.md#risk-03---session-file-leaked)):

1. Telegram app → **Settings → Devices → Terminate** the session. This
   invalidates the key immediately, including any copy.
2. Delete **both** files - the clone is a second copy of the same credential:

   ```bash
   rm -f tg_session.session tg_session.sync.session
   ```

3. `just tg-auth` to create a fresh session.
4. Consider revoking the API application at <https://my.telegram.org>.

## If the file is corrupted

```bash
rm -f tg_session.sync.session     # the clone is disposable; it is rebuilt each sync
just tg-sync                      # rebuilds it
```

If the **primary** session is corrupted, delete it and run `just tg-auth`. The
Archive is unaffected - it is in PostgreSQL, not in the session file.

## After AUTH_KEY_DUPLICATED

This one is different from every other cause above: **the session is already
dead when you read the error.** Telegram's own documentation says so - "when
received, the session is already invalidated and the user must re-authenticate".
There is nothing to retry and nothing to wait for.

It means two live connections were open on one authorization key. The Session
Clone shares the primary's key, so the usual way to produce it is a sync running
while the MCP server is connected (`RISK-08`).

1. **Do not retry.** Repeating the connection cannot restore the login.
2. Close the agent session, so the MCP server stops trying to reconnect.
3. Delete the stale clone. It holds a key that is now invalid, but it is a file
   the project treats as a full credential:

   ```bash
   rm -f tg_session.sync.session
   ```

4. Re-authenticate: `just tg-auth`. This needs a real terminal and a new SMS
   code.
5. Check that the lock is doing its job before resuming - `just tg-sync` should
   refuse while `just tg-serve` is running, naming the other process. If it does
   not, something has regressed in `SPEC-SEC-010` and syncing again will
   reproduce the problem.

## Prevention

The connection lock ([ADR-0010](../../20-architecture/adr/0010-one-connection-per-authorization-key.md))
now enforces this: whichever process connects first takes an exclusive lock on
`tg_session.lock`, and the other exits rather than connecting.

Do not defeat it. In particular, note that the Session Clone does **not** make
concurrent access safe on its own - it only stops two processes writing one
SQLite file (ADR-0004). The auth key inside the copy is the same key, and that
is what Telegram counts.
