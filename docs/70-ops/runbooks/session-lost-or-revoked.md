---
id: DOC-RB-SESSION
title: "Runbook: session lost or revoked"
status: active
authority: derived
updated: 2026-09-04
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

## Causes

| Cause | Signal |
| --- | --- |
| Never logged in | The file does not exist |
| Session terminated in the Telegram app | The file exists but is unauthorised |
| Password changed | All sessions were revoked |
| Signed out from another device with "terminate all" | Same |
| File corrupted by two concurrent writers | `database is locked`, or SQLite errors - see [ADR-0004](../../20-architecture/adr/0004-session-file-clone-for-sync.md) |

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

## Prevention

Never run `just tg-sync --in-place` while the MCP server is running. The default
path clones the session precisely to make this safe.
