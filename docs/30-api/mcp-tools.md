---
id: DOC-MCP-TOOLS
title: MCP tool contract
status: active
authority: derived
updated: 2026-09-04
related: [DOC-SRS, DOC-ROUTER]
---

# MCP tool contract

The machine-readable interface the agent sees. Behaviour is specified in the
[SRS](../10-product/srs.md); this page is the signature reference.

Server name: `tg-ai`. Transport: stdio.

**Every tool returns a single text block.** Failures arrive as text beginning
with `ERROR:` or `WARNING:`, never as a protocol-level error (`SPEC-SND-004`).

## Schemas

```json
{
  "tg_send_message": {
    "description": "Send a Telegram message from the user's own account.",
    "required": ["target", "message"],
    "properties": {
      "target":  {"type": "string", "description": "@username, +phone, numeric id, or 'me'"},
      "message": {"type": "string", "description": "Any length; split automatically"}
    }
  },
  "tg_get_recent_messages": {
    "description": "Read the latest messages with one person, live from Telegram.",
    "required": ["target"],
    "properties": {
      "target": {"type": "string"},
      "limit":  {"type": "integer", "default": 10, "clamped": [1, 100]}
    }
  },
  "tg_get_unread_dialogs": {
    "description": "List people who have sent unread messages.",
    "required": [],
    "properties": {
      "limit": {"type": "integer", "default": 5, "clamped": [1, 50]}
    }
  },
  "tg_search_local_history": {
    "description": "Search the whole archived history in PostgreSQL. No Telegram API calls.",
    "required": ["query"],
    "properties": {
      "query":           {"type": "string", "description": "Supports quoted phrases, OR, -exclusion"},
      "target_username": {"type": ["string", "null"], "default": null, "description": "Without @"},
      "limit":           {"type": "integer", "default": 50, "clamped": [1, 500]}
    }
  },
  "tg_add_contact": {
    "description": "Add someone to contacts. Unblocks tg_send_message for a new person.",
    "required": ["phone_or_username", "first_name"],
    "properties": {
      "phone_or_username": {"type": "string"},
      "first_name":        {"type": "string"},
      "last_name":         {"type": "string", "default": ""}
    }
  },
  "tg_whoami": {
    "description": "Health check: account, session, database, archive coverage.",
    "required": [],
    "properties": {}
  }
}
```

## Choosing the right tool

| The user asks | Use |
| --- | --- |
| "Send X to @someone" | `tg_send_message` |
| "Did anyone write to me?" | `tg_get_unread_dialogs` |
| "What did @someone reply?" | `tg_get_recent_messages` - reads live, sees the last minute |
| "What did we agree about X?" / "find …" | `tg_search_local_history` - reads the archive, sees years |
| "Something is not working" | `tg_whoami` first, always |

`tg_get_recent_messages` sees new messages but only a short window.
`tg_search_local_history` sees the whole history but only as of the last sync.
They are complements, not alternatives.

## Worked examples

### Sending to a known contact

```text
tg_send_message(target="@anna", message="Summary of today's session: ...")
-> "Sent to Anna Petrova (@anna) (612 characters)."
```

### Sending a long message

```text
tg_send_message(target="@anna", message="<9000 characters>")
-> "Sent to Anna Petrova (@anna) in 3 parts (9000 characters total, 2.5s between parts)."
```

### Sending to a stranger - blocked by design

```text
tg_send_message(target="@newperson", message="hi")
-> "WARNING: Nothing was sent. There is no prior conversation with @newperson
    and they are not in your contacts. ...
    To proceed deliberately, call:
      tg_add_contact(phone_or_username="@newperson", first_name="...")
    and then retry this send. ..."
```

The correct response is to tell the user, or add the contact if they already
asked for it. Do not look for another way to send.

### Hitting a rate limit

```text
tg_send_message(target="@anna", message="...")
-> "ERROR: Telegram API limit reached. We must wait 143 seconds"
```

Report the wait to the user. Do not retry in a loop.

### Searching

```text
tg_search_local_history(query="deployment plan", target_username="bob", limit=20)
-> "2 match(es) for 'deployment plan' via full-text search:
    2026-03-01 15:00 [@bob] me: I pushed the migration script
    2026-03-01 14:00 [@bob] @bob: deployment plan for the postgres migration"
```

`via substring (no full-text match)` means the hit is a loose partial match -
weigh it accordingly.
