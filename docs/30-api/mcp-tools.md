---
id: DOC-MCP-TOOLS
title: MCP tool contract
status: active
authority: derived
updated: 2026-09-08
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
    "description": "Send a Telegram message from the user's own account. Marks that chat read afterwards when TG_READ_ON_SEND is enabled.",
    "required": ["target", "message"],
    "properties": {
      "target":  {"type": "string", "description": "@username, +phone, numeric id, 'me', or a group/channel title"},
      "message": {"type": "string", "description": "Any length for a person; a group or channel message must fit one chunk"}
    }
  },
  "tg_get_recent_messages": {
    "description": "Read the latest messages in one chat, live from Telegram. Works for a person, group or channel you are in.",
    "required": ["target"],
    "properties": {
      "target": {"type": "string"},
      "limit":  {"type": "integer", "default": 10, "clamped": [1, 100]}
    },
    "limits": {
      "group_cooldown_seconds": 300,
      "group_reads_per_day": 20,
      "note": "Both are stored in PostgreSQL and survive a server restart."
    }
  },
  "tg_get_unread_dialogs": {
    "description": "List people who have sent unread messages. Groups and channels are excluded.",
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
  "tg_get_dialog_persona": {
    "description": "Read how the user writes to one person, before drafting a reply.",
    "required": ["target"],
    "properties": {
      "target":  {"type": "string", "description": "@username, +phone, numeric id, or a name. Archive-only; ambiguity is reported, never guessed"},
      "samples": {"type": "integer", "default": 12, "clamped": [0, 50]}
    }
  },
  "tg_set_dialog_persona": {
    "description": "Record how the user writes to one person. Never silently overwrites.",
    "required": ["target", "addressing", "tone", "relationship"],
    "properties": {
      "target":       {"type": "string"},
      "addressing":   {"type": "string", "maxLength": 79},
      "tone":         {"type": "string", "maxLength": 119},
      "relationship": {"type": "string", "maxLength": 119},
      "notes":        {"type": "string", "default": "", "maxLength": 239},
      "overwrite":    {"type": "boolean", "default": false}
    }
  },
  "tg_list_dialog_personas": {
    "description": "List which archived dialogs have a stored style persona.",
    "required": [],
    "properties": {
      "limit": {"type": "integer", "default": 20, "clamped": [1, 100]}
    }
  },
  "tg_whoami": {
    "description": "Health check: account, session, database, archive coverage.",
    "required": [],
    "properties": {}
  }
}
```

## Groups and channels

Every tool that takes a `target` accepts a group or channel **you are already a
member of**, named by its title, `@username` or numeric id.

| Rule | Behaviour |
| --- | --- |
| Not a member | Refused, with **no API call** - this server never joins or looks up a group you have not joined |
| Reading | 300 s cooldown per target, 20 reads a day, 100 messages per read. Persisted, so a restart does not clear them |
| Sending to a group | Allowed while you are a member, and only if the group permits it |
| Sending to a channel | Refused unless you have `post_messages` admin rights there. Checked from cache, so a refusal costs nothing |
| Long messages | Refused for a group or channel if they would need more than one chunk - shorten instead |
| Senders inside a group | Attribution only. `tg_send_message` refuses an id it has only seen writing in a group |
| Personas | Not available. `tg_get_dialog_persona` and `tg_set_dialog_persona` return an error for a group or channel |

The reasoning is in
[ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).

## Read receipts

**Reading never marks anything as read.** `tg_get_recent_messages`,
`tg_get_unread_dialogs`, `tg_search_local_history` and every sync leave the
sender's single checkmark alone and leave the owner's unread badges where they
are. Fetch as much of a conversation as you need; none of it is visible to
anyone.

Replying is the only thing that acknowledges, and only when the account owner
has set `TG_READ_ON_SEND=true`. When they have, `tg_send_message` marks that
one chat read *after* delivering - the same order a person's own client uses
when they open a chat to answer it. It applies to people, groups and channels
alike, never happens for a refused or partly-failed send, and a failure to mark
it read never fails the send or changes what the tool returns.

There is no per-call parameter for this and there will not be one: the
distinction is which pipeline the call lives in, not which argument was passed
(`SPEC-SND-009`,
[ADR-0011](../20-architecture/adr/0011-read-receipt-on-send.md)).

## Choosing the right tool

| The user asks | Use |
| --- | --- |
| "Send X to @someone" | `tg_send_message` |
| "Did anyone write to me?" | `tg_get_unread_dialogs` |
| "What did @someone reply?" | `tg_get_recent_messages` - reads live, sees the last minute |
| "What did we agree about X?" / "find …" | `tg_search_local_history` - reads the archive, sees years |
| "What is happening in <group>?" | `tg_get_recent_messages` - but at most once per 5 minutes per group |
| "Something is not working" | `tg_whoami` first, always - it reports the request budget and the kill switch even when Telegram is refused |

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
