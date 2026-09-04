# tg-ai

<p align="center">
  <img src="docs/assets/logo.svg" alt="tg-ai - Personal Telegram MCP Server" width="100%">
</p>

> [!WARNING]
> **DISCLAIMER: This tool automates personal Telegram accounts using MTProto. Telegram's anti-spam algorithms are strict. While this project includes safeguards (rate-limiting, strict checks against messaging non-contacts), the author is NOT responsible for any PeerFloodError, temporary limits, or permanent bans applied to your account. Use at your own risk. NEVER share your .session files or API_ID.**

A local MCP server that lets an AI agent drive your **personal** Telegram
account: send messages as you, read replies, and full-text search your entire
chat history instantly.

Built for one account on one machine. Everything stays local - the archive of
your conversations never leaves `127.0.0.1`.

## What it does

| You say to your agent | What happens |
| --- | --- |
| "Send a summary of this session to @anna" | The agent composes the text and sends it from your account, split into chunks if long |
| "Any new messages?" | You get who is waiting and what they said - without marking anything read |
| "What did @anna reply?" | Live read from Telegram, so a reply from a minute ago is there |
| "Find the address someone sent me last year" | Instant search across your whole archived history, in any language, with no Telegram API call |

## Tools

| Tool | Purpose |
| --- | --- |
| `tg_send_message(target, message)` | Send as you. Blocks sends to strangers, chunks long text, paces delivery |
| `tg_get_recent_messages(target, limit=10)` | Recent messages with one person, live |
| `tg_get_unread_dialogs(limit=5)` | Who has written to you |
| `tg_search_local_history(query, target_username=None, limit=50)` | Full-text search of the local archive |
| `tg_add_contact(phone_or_username, first_name, last_name="")` | Add a contact - the deliberate way to unblock a send to someone new |
| `tg_whoami()` | Health check: account, session, database, archive |

Full signatures: [docs/30-api/mcp-tools.md](docs/30-api/mcp-tools.md).

## Keeping your account safe

Driving a personal account over MTProto puts you under Telegram's anti-spam
enforcement. These protections are built in and tested, not optional:

- **Messages to strangers are blocked outright.** No history and not a contact
  means nothing is sent - you get a warning naming `tg_add_contact` as the next
  step. There is no `force` flag, on purpose.
- **Long text is split** into chunks under Telegram's 4096-character limit, on
  sentence boundaries, never mid-word.
- **Sends are paced** 2.5 seconds apart. Bursts are what rate limits react to.
- **Rate limits are reported, not retried.** `FloodWaitError` comes back as
  "we must wait N seconds" instead of a retry loop that extends the limit.
- **Your session file is a full credential.** It is git-ignored before it can
  exist and gets mode `0600`.
- **Everything is local.** The database binds to `127.0.0.1`; Telegram sees the
  same IP you always connect from.

Details: [docs/70-ops/security.md](docs/70-ops/security.md).

## Setup

Needs Python 3.12+, [`uv`](https://docs.astral.sh/uv/), [`just`](https://just.systems)
and Docker.

```bash
cd /home/anry/projects/tg-ai
just setup                       # venv + dependencies + .env
$EDITOR .env                     # set TG_API_ID and TG_API_HASH
just db-up                       # PostgreSQL on 127.0.0.1:5434
just tg-auth                     # interactive login: phone, SMS code, 2FA
just tg-sync-targets @anna @bob  # archive the people who matter first (fast)
just tg-sync-full                # then the rest (hours; resumable)
just mcp-add                     # register with Claude Code
```

On an account with hundreds of dialogs a full backfill takes hours and pauses on
Telegram rate limits. `just tg-sync-targets` archives only the people you name,
so search over those conversations works within minutes. Each target matches a
username (with or without `@`), a phone number, a numeric id, or a first, last or
full name; anything matching no dialog is reported so a typo is obvious:

```bash
just tg-sync-targets @anna "Anna Petrova" +380501234567
```

Running `just tg-sync-full` afterwards costs nothing for the dialogs already
archived - insertion is idempotent.

Get `TG_API_ID` and `TG_API_HASH` at <https://my.telegram.org> → API development
tools.

`just tg-auth` **must run in a real terminal** - it asks for the code Telegram
sends you, and an MCP server has no way to prompt.

Then restart your agent and ask it to run `tg_whoami()`.

For other MCP clients (Antigravity, Cursor, Claude Desktop):

```json
{
  "mcpServers": {
    "tg-ai": {
      "command": "/home/anry/projects/tg-ai/.venv/bin/python",
      "args": ["/home/anry/projects/tg-ai/server.py"],
      "cwd": "/home/anry/projects/tg-ai"
    }
  }
}
```

Full procedure and rollback: [docs/70-ops/deployment-plan.md](docs/70-ops/deployment-plan.md).

## How it works

Three processes, because an MCP server speaks stdio and therefore cannot prompt
for an SMS code:

| Process | Runs | Does |
| --- | --- | --- |
| `auth.py` | Once, in a terminal | Logs in, writes `tg_session.session` |
| `sync_db.py` | On demand | Copies private chat history into PostgreSQL |
| `server.py` | Spawned by the agent | Serves the six tools over stdio |

Search hits PostgreSQL, not Telegram - which is why it is instant, unlimited,
and carries no ban risk. The trade-off is that the archive is current as of the
last `just tg-sync`; `tg_get_recent_messages` covers the gap by reading live.

Architecture: [docs/20-architecture/sad.md](docs/20-architecture/sad.md).

## Commands

```bash
just              # list everything
just check        # ruff + black + pytest + docs checks
just tg-sync      # refresh the archive (incremental)
just tg-sync-targets @anna @bob   # archive only these people
just tg-status    # account, session, database, archive health
just db-psql      # psql shell into the archive
```

## Documentation

Start at [docs/README.md](docs/README.md) - it routes to everything.

Working on the code? Read [AGENTS.md](AGENTS.md) first; it is the authority on
how changes are made here.

## Scope

Private 1-on-1 text chats with people. Not groups, not channels, not bots, not
media, and nothing that deletes or edits messages on your account. See the
non-goals in [docs/10-product/prd.md](docs/10-product/prd.md).
