---
id: ADR-0001
title: "Use Telethon and MTProto rather than the Bot API"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-SAD, DOC-PRD]
---

# ADR-0001 - Use Telethon and MTProto, not the Bot API

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** the entire `SPEC-SND-*` and `SPEC-RCV-*` families
**Affects:** `tg_ai/tg_client.py`, every entrypoint

## Context

The requirement is to act **as the user's own account** (UC-1 in the
[PRD](../../10-product/prd.md)): messages must arrive from `@anrysys`, and the
tool must read the user's existing conversation history.

Telegram exposes two client surfaces. The Bot API is a simple HTTPS interface,
but a bot is a separate identity: it cannot send from a personal account, cannot
read a human's message history, and cannot message a user who has not first
messaged the bot. MTProto is the protocol Telegram's own clients speak, and a
personal account can only be driven through it.

## Decision

Use Telethon over MTProto with the user's own API credentials from
`my.telegram.org`. Accept that this places the project under Telegram's
anti-spam enforcement for personal accounts, and make that enforcement an
explicit, tested part of the architecture rather than an afterthought.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Bot API | A bot cannot send as the user, cannot read personal history, and cannot initiate contact. It fails all three use cases |
| Exported chat history files | Static, manual, and immediately stale. Solves search only, and nothing else |
| A userbot framework such as Pyrogram | Equivalent capability. Telethon has the larger installed base and more complete typed error classes, which the error-translation layer depends on |

## Consequences

- Every send carries account-ban risk. This is why the Send Guard
  ([ADR-0005](0005-hard-block-send-to-unknown-peers.md)), chunking, pacing and
  `FloodWaitError` translation exist and are individually tested.
- The `.session` file is a full account credential, which drives every
  `SPEC-SEC-*` requirement.
- The account must connect from the user's usual IP, which forces local-only
  deployment.
- Telethon's typed exception classes are load-bearing:
  `safety.describe_telegram_error` dispatches on them. Replacing the library
  means rewriting that translation layer.
