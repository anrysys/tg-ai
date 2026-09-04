---
id: ADR-0005
title: "Hard-block sends to strangers, with no override flag"
status: active
authority: authoritative
updated: 2026-09-04
related: [DOC-ADR-INDEX, DOC-SRS, DOC-SECURITY]
---

# ADR-0005 - Hard-block sends to strangers, with no override flag

**Status:** Accepted
**Date:** 2026-09-04
**Decides:** `SPEC-SND-001`, `SPEC-SND-006`
**Affects:** `server.tg_send_message`, `server.tg_add_contact`

## Context

`PeerFloodError` is the enforcement Telegram applies to a personal account that
messages people it has no relationship with. It escalates from a temporary send
restriction to a permanent ban, and appeals go through `@SpamBot` with no
guarantee.

The distinguishing signal is not volume. A single message to an account with no
shared history and no contact entry is the pattern. Meanwhile, the caller here
is an LLM acting on natural language: "send this to Anna" can resolve to the
wrong Anna, and "let everyone know" can resolve to a stranger.

The user was asked directly and chose the strictest option available.

## Decision

`tg_send_message` computes whether the Peer is a Stranger - not the account
itself, not in the Peer Index, no message ever exchanged, not in contacts - and
if so returns a `WARNING`, sends nothing, and names `tg_add_contact` as the
required next step.

**No `force` parameter exists.** Proceeding requires a separate, deliberate
`tg_add_contact` call, which is a different tool with a different name that the
agent must choose to invoke.

Peer resolution consults the Peer Index before Telegram's username resolution,
because a cold `ResolveUsername` for an unknown handle is itself a rate-limited
scripted-account signal.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| `force: bool = False` on the tool | An agent that sees a parameter that makes its error go away will set it. A safety rule reachable by one boolean is not a safety rule |
| Warn but send anyway | Maximum convenience, and exactly the behaviour that gets accounts banned. The warning arrives after the damage |
| Rate-limit instead of blocking | Volume is not the signal. One message to one stranger is enough |
| Ask the user for confirmation from inside the tool | MCP tools cannot prompt. Returning a `WARNING` to the agent, which can then ask the user, is the same thing done correctly |

## Consequences

- Messaging a new person is a two-step flow: `tg_add_contact`, then
  `tg_send_message`. This is intentional friction on the one operation that can
  destroy the account.
- Saved Messages (`target="me"`) always bypasses the guard - the account writing
  to itself cannot be spam.
- A Peer with history but not in contacts passes the guard. History is a
  stronger signal of a real relationship than a contact entry.
- The guard's four conditions short-circuit in cost order, so a known Peer costs
  no extra API call.
- The guard cannot be relaxed without a superseding ADR.
