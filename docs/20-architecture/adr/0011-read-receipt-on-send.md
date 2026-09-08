---
id: ADR-0011
title: "Bind the read receipt to sending, and to nothing else"
status: active
authority: authoritative
updated: 2026-09-08
related: [DOC-ADR-INDEX, ADR-0009, ADR-0005, DOC-SRS, DOC-SECURITY, DOC-PRD]
---

# ADR-0011 - Bind the read receipt to sending, and to nothing else

**Status:** Accepted
**Date:** 2026-09-08
**Decides:** `SPEC-SND-009`, `SPEC-SEC-012`, `RISK-11`
**Supersedes:** the read-receipt consequence of
[ADR-0009](0009-groups-and-channels.md), which stays Accepted in every other
respect
**Affects:** `server.acknowledge_read`, `server.tg_send_message`,
`tg_ai/config.Config.read_on_send`, `tests/test_blacklist.py`

## Context

[ADR-0009](0009-groups-and-channels.md) banned every way of marking a message
read, and the ban was enforced structurally: `SPEC-LIM-006` put
`send_read_acknowledge` and the `ReadHistory` family on a blacklist that
`tests/test_blacklist.py` greps the shipped source for.

The reasoning it gave is worth quoting exactly, because it decides how far the
ban actually reaches:

> Marking messages read as a side effect of a status query would make the
> user's own Telegram client lie to them - and instantly "reading" 100 messages
> across several channels is superhuman.

Both halves are about **reading**. An agent that fetches a conversation has not
read it in any sense the account owner would recognise, and clearing their
badge on their behalf is a lie told to the only person who cannot check it.

Sending is the opposite case, and the ban caught it only by accident. The
account currently replies to conversations it never acknowledges, indefinitely.
On the other side of that conversation a person watches a single checkmark sit
under their message while a fluent reply arrives beneath it. No human client
behaves that way: opening a chat to answer it is what marks it read, and every
official client does so before the reply is sent. A permanent, account-wide
divergence from that is a behavioural signal in the same family as uniform
request timing - the thing ADR-0009's jitter, quiet window and budgets exist to
avoid - and it is one the account owner never chose.

So the ban is right about reads and wrong about sends, and it cannot be
narrowed by reinterpretation: it is written down, it is tested, and
`AGENTS.md` §5 requires a superseding decision rather than an edit.

## Decision

`tg_send_message` MUST mark a Dialog read **after** it has delivered every
chunk into that Dialog, and nothing else in the project may mark anything read.

The rule is enforced by four constraints, each of which is load-bearing:

1. **One helper, one call site.** `server.acknowledge_read` is the only place
   that acknowledges, and `tests/test_blacklist.py` fails the build if
   `send_read_acknowledge(` appears anywhere but `server.py`. The four raw
   `ReadHistory` / `ReadMentions` request classes stay blacklisted outright, so
   there is exactly one sanctioned way to do this and it is the one that picks
   `channels.ReadHistory` for a Channel and `messages.ReadHistory` otherwise
   from the entity itself.
2. **Off by default.** `TG_READ_ON_SEND` defaults to `false`
   (`SPEC-SEC-012`). This account has never sent a message; a receipt is
   visible to the other person and cannot be withdrawn, so it waits for a
   deliberate opt-in.
3. **After delivery, never before.** The call sits below the send loop, so a
   partial or refused send acknowledges nothing. A receipt is therefore never a
   side channel revealing a send that did not happen.
4. **It cannot fail a send.** The helper swallows every exception, including
   the one `config()` can raise while reading the flag. By the time it runs the
   message is delivered, and reporting `ERROR:` for a delivered message invites
   the agent to send it twice - which is worse than any receipt.

The Send Guard is untouched. Every check in `SPEC-SND-001` runs before this
code is reachable, so it can neither refuse a send nor permit one, and it
resolves no Peer: it reuses the entity `resolve_peer` already returned, because
resolving the target a second way is a bypass [ADR-0005](0005-hard-block-send-to-unknown-peers.md)
names explicitly.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Keep the total ban | It is right about reads and wrong about sends. Replying at length to a chat the account never acknowledged is itself the anomaly, and it is permanent rather than occasional |
| Acknowledge during ingestion, sync or the unread scan | This is precisely the case ADR-0009 decided, and its reasoning still holds completely. It would make the owner's own client lie to them, and 100 messages read instantly across several channels is superhuman. Still banned, now also covered by a behavioural test |
| Un-ban the raw `ReadHistoryRequest` / `channels.ReadHistoryRequest` classes | Then the user-versus-channel distinction becomes a branch of ours to get wrong. `send_read_acknowledge` reads it off the entity. Banning the TL classes while permitting the helper is what forces the correct call |
| Compute a `max_id` from the newest incoming message first | Another request per send against a 60/hour budget, to reason about per-Dialog message id spaces, for an outcome no different from what opening the chat does. `max_id=0` is what a human client sends |
| Default `TG_READ_ON_SEND` to true | A receipt cannot be withdrawn and is visible to someone who did not consent to it. The conservative default is the one the account owner turns on |
| Give the read tools an `acknowledge=` parameter | An optional flag on a read path is the blacklist decaying back into what it was written to prevent. The distinction is which pipeline the call lives in, not which argument was passed |
| Put the receipt in a background task so the send returns sooner | A fire-and-forget task outliving the tool call is unobservable, unpaced by the caller, and can be orphaned by shutdown. The 1.5 s the limiter adds is the honest cost |

## Consequences

- **A send now costs two RPCs, not one**, against both the 60/hour and 500/day
  budgets (`SPEC-LIM-002`), and `tg_send_message` returns roughly 1.5-2 seconds
  later because the receipt queues behind the limiter's minimum gap. This is
  the accepted price and is not to be optimised away by exempting the receipt
  from the limiter.
- **Reading is still non-destructive, and now provably so.** The claim used to
  rest on the blacklist alone; it is now also a behavioural test that calls
  `tg_get_recent_messages` with `TG_READ_ON_SEND` **on** and a client that
  raises if acknowledgment is reached.
- ADR-0009 stays `Accepted`. Only its "marking messages read" consequence is
  superseded, and only for the send path. Its blacklist, its budgets, its
  cooldowns and its group rationing are untouched.
- The PRD non-goal "Marking messages read" narrows rather than disappears:
  reading is strictly non-destructive, sending acknowledges.
- `README.md` and `USE-CASES.md` promised that this project never marks a chat
  read. That promise is now conditional and both files say so.
- Reversing this needs another superseding ADR. Weakening constraint 1, 3 or 4
  - a second call site, acknowledging before delivery, or letting the receipt
  raise - is a defect rather than a decision, and each has a test.
