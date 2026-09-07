---
id: ADR-0009
title: "Support groups and channels behind an explicit, persisted safety envelope"
status: active
authority: authoritative
updated: 2026-09-07
related: [DOC-ADR-INDEX, ADR-0004, ADR-0005, ADR-0010, DOC-SRS, DOC-DATA-MODEL, DOC-SECURITY, DOC-PRD]
---

# ADR-0009 - Groups and channels, behind a safety envelope

**Status:** Accepted
**Date:** 2026-09-07
**Decides:** `SPEC-SEC-006` .. `SPEC-SEC-009`, `SPEC-LIM-001` .. `SPEC-LIM-007`,
`SPEC-SYNC-001`, `SPEC-SYNC-007`, `SPEC-SND-001`, `SPEC-SND-006`,
`SPEC-SND-007`, `SPEC-SND-008`, `SPEC-RCV-003`, `SPEC-RCV-005`, `SPEC-PSN-009`,
`RISK-09`, `RISK-10`
**Affects:** `sql/schema.sql`, `tg_ai/config.py`, `tg_ai/safety.py`,
`tg_ai/tg_client.py`, `tg_ai/db.py`, `server.py`, `sync_db.py`, the PRD non-goals

## Context

The project was private 1-on-1 chats only, stated as a PRD non-goal and enforced
in `is_archivable` and `resolve_peer`. The user asked for groups, megagroups and
channels.

The feature itself is small: widen a type check, add a column, read some
history. What makes this a decision worth recording is that the existing code
had no envelope to put it in, and building the feature first would have shipped
several live defects into a much larger blast radius.

Telegram's own documentation sets the baseline:

> All accounts that log in using unofficial Telegram API clients are
> automatically put under observation to avoid violations of the Terms of
> Service.
>
> - <https://core.telegram.org/api/obtaining_api_id>

The goal is therefore not to hide. It is to make an account that is already
under observation look, to a statistical model, exactly like a person who uses a
third-party client. Volume, rhythm, consistency and restraint achieve that.
Disguise does not.

Four defects in the pre-existing 1-on-1 code had to be fixed first, because
group support makes each of them worse:

1. `build_client` passed no identity parameters at all, so every connection
   announced Telethon on Python with `lang_code='en'`.
2. `flood_sleep_threshold` was left at Telethon's default of `60`, which makes
   the library sleep on - that is, silently retry - every flood wait of 60
   seconds or less.
3. `receive_updates` was left at `True`.
4. Every cooldown and counter lived in a Python variable inside an MCP stdio
   server, which the user's editor respawns constantly.

## Decision

### Reading is opt-in; resolution stays closed

Groups and channels are archived and read **only** when named explicitly through
`--targets`, never during a full sync. `resolve_peer` returns a non-`User` peer
**only** when it came from the local Peer Index - meaning it appeared in
`GetDialogs` and the account is therefore a member - and otherwise raises before
making any API call. The guard lives in `resolve_peer` itself, not at each call
site, so `--dialog` and every present and future MCP tool inherit it.
Consequently `--dialog` remains user-only.

A user id first seen **inside** a group is not a peer. Telegram delivers those
as [`min` constructors](https://core.telegram.org/api/min), whose `access_hash`
"can't be used to generate a typical `inputPeerUser` constructor to send
messages or do other actions". Such an id is stored for attribution and nothing
else: never resolved, never indexed, never added to contacts, never a send
target.

### The client identifies itself honestly, and never changes its story

`device_model`, `system_version`, `app_version`, `lang_code` and
`system_lang_code` come from configuration and are passed at the single
construction site, identically for the primary Session and the Clone.
`app_version` names this application.

**It does not impersonate an official client.** Telegram already knows the
client is unofficial, because it knows the `api_id`. A personal `api_id`
announcing itself as Telegram Desktop is a trivially detectable inconsistency,
and one that can only be deliberate. Camouflage that does not survive a single
join against the app registry is not camouflage.

What protects the account is stability. `initConnection` is replayed on every
reconnection, so these strings are frozen once a Session exists; changing them
makes the account's own Settings -> Devices entry mutate under the user.
`system_version` is therefore derived only to major.minor precision, so a kernel
patch cannot mutate it.

`lang_code` defaults to `en` because this is a public repository and no language
is right for everyone. That default is **unsafe for most users** and both
`.env.example` and the README say so prominently: an account whose phone runs a
Russian Telegram UI while every API connection reports `en` is a contradiction
in Telegram's own records.

### One machine, one network, one api_id

The server and the sync run on the account owner's ordinary machine and network.
Never a VPS, cloud host, container platform, CI runner or datacentre range;
never a shared or rotating VPN. One `api_id`, the user's own, shared by all
three entrypoints and never rotated. No flag relaxes any of this.

This is the strongest signal in the whole document and it binds the operator
rather than the code. `tg_whoami` reports `client.session.dc_id` so a silent
relocation is at least visible.

### `flood_sleep_threshold=0`

Telethon documents this as the threshold below which it automatically sleeps on
flood and slow-mode waits, defaulting to 60 seconds. Three consequences were all
live in this codebase:

- AGENTS.md forbids retrying a `FloodWaitError` in a loop because retrying
  extends the limit. **Telethon was already doing exactly that**, invisibly, for
  every wait of 60 seconds or less.
- Scraping-induced waits are typically 5-30 seconds, i.e. *below* the threshold.
  So `sync_db.py`'s `except FloodWaitError` handler could not fire, and any kill
  switch built on top of it would have been dead code protecting nothing.
- The account could therefore be flood-limited repeatedly in one run with every
  log line clean, while flood *frequency* is precisely what feeds the
  server-side risk score.

Zero makes every wait surface as an exception, so it is counted, reported and
fed to the kill switch. The zero is load-bearing, not a placeholder, and the
call site says so.

### No update stream, and no event handlers

`receive_updates=False` and `catch_up=False`. A subscribed client would receive
an update for every message in every group the account belongs to - a large
traffic fingerprint for a client that reads none of them, and a route to
`updatesTooLong`, which triggers `updates.getDifference` and a bulk history
fetch.

This creates a trap, so the codebase forbids Telethon event handlers outright: a
handler silently never fires with updates off, which reads as a bug, and the
obvious fix is to turn updates back on. A test asserts zero handlers exist.

### Rate-limit state lives in PostgreSQL, not in memory

An MCP stdio server is spawned fresh by the client on every launch and again
after every crash. **In-memory rate limiting in an MCP server is not rate
limiting**: an agent that hits a 300-second cooldown gets a clean slate as soon
as the user reopens their editor.

So `api_call_log`, `api_flood_log` and `api_kill_switch` are tables, read and
written by both entrypoints. The archive is already a hard dependency, so this
adds no new component.

A single limiter serialises every RPC at the Telethon client level rather than
at each call site, so a new call site cannot bypass it. It holds a lock, enforces
a minimum gap with additive jitter, enforces rolling hourly and daily budgets,
and checks the kill switch and the quiet window.

Two implementation facts shaped it, both verified against the pinned Telethon:

- `TelegramClient._call` issues **nested** RPCs of its own - request `resolve()`
  calls `get_input_entity`, which calls `self(GetUsersRequest(...))`. A plain
  lock held across the delegate deadlocks in the same task, silently and with no
  traceback. The limiter therefore uses an owner-task guard rather than a bare
  lock, and a regression test asserts a nested call cannot hang.
- `connect()` itself costs RPCs: the `initConnection` handshake bypasses
  `__call__`, but the `get_me` and `GetState` that follow do not. An earlier
  draft of this decision exempted those from denial so that an exhausted budget
  could not make the server unreachable. That exemption was dropped before it
  was written: identifying "bootstrap" requests means matching on request class
  names, which is fragile, and `users.GetUsers` is also how ordinary entity
  resolution works, so the exemption would have had to let through the very
  calls it should meter. **Everything is counted and everything is deniable.**
  The diagnostic worry it was meant to answer is solved instead by making
  `tg_whoami` read the budget, the kill switch and the archive straight from
  PostgreSQL: when the budget is spent, the account section reports why and
  every other section still works.

When PostgreSQL is unreachable the limiter **fails closed**. A budget that
evaporates when the database stops is not a budget; the cheapest bypass would be
`docker stop`.

### Jitter is additive only

`jittered(delay)` returns `delay + random.uniform(0, 0.5 * delay)`. The
symmetric form, `delay * (1 + random.uniform(-0.25, 0.25))`, drops below the
reviewed constant half the time, which directly violates AGENTS.md's rule
against lowering `CHUNK_DELAY_SECONDS` and `SYNC_DIALOG_DELAY_SECONDS`. The
reviewed constant is a floor; jitter only ever adds.

### Volume caps, not shorter gaps

The interval between two channel reads matters far less to a fraud model than
how many distinct channels the account touches per day and how many RPCs it
makes in total. The delays are not shortened; the volume is capped: one
`messages.getHistory` of exactly 100 messages per group per run, at most 5
group or channel targets per run, at most 20 group or channel reads per rolling
24 hours, a 300-second per-target cooldown, 60 RPCs per hour and 500 per day,
and a 200-call ceiling per server process as a backstop against a runaway agent
loop.

### Blacklisted operations

Member-list scraping, mass invites, joining or leaving, bulk contact import,
bulk forwarding, presence, typing, media download, channel statistics and every
reporting method are permanently absent, and a test greps the source tree for
each name.

Banning `channels.getParticipants` achieves nothing if member data is assembled
one message at a time, so the **peer-harvesting back door is closed too**:
`inputPeerUserFromMessage` and its siblings, `contacts.search`,
`contacts.getLocated`, `contacts.resolvePhone`, `messages.getCommonChats`,
`messages.getMessageReadParticipants`, `messages.getMessageReactionsList`,
`channels.getFullChannel` and `messages.getFullChat`.

### Presence is not touched at all

Neither direction. An earlier draft of this work proposed calling
`account.updateStatus(offline=True)` after every connection. That reasoning was
wrong twice over: Telethon does not broadcast presence on its own, so there was
nothing to suppress, and calling it anyway spends an RPC per reconnect to make
the account permanently "never online" while it demonstrably makes API calls
every day - an anomaly, not a cure for one. `messages.setTyping` is likewise
never called.

### The takeout API is declined

`account.initTakeoutSession` plus `invokeWithTakeout` is Telegram's sanctioned
path for bulk export, and choosing the unsanctioned path silently would be
exactly the kind of gap this document exists to close. So: **declined,
deliberately.**

Takeout carries an out-of-band approval flow - Telegram may answer
`TAKEOUT_INIT_DELAY_X`, asking the user to confirm from the official app up to
24 hours ahead - and a takeout session is stateful in a way that interacts badly
with the Session Clone (ADR-0004) and the connection lock (ADR-0010). Against
that, the volume this project actually reads is tiny: 100 messages per group per
run, 20 group reads a day, 500 RPCs a day. Takeout exists to stop a client that
fetches a lot of history from looking like a scraper, and this client does not
fetch a lot of history.

`TakeoutInitDelayError` is still translated, so if a future change adopts
takeout the error is not a raw class name. If it is ever adopted it remains
bound by the membership rule: a safer way to read the user's *own* data, never a
way to reach peers the user is not party to.

### Personas are disabled for groups and channels

`dialog_personas` models how one person writes to one other person. In a group
many people write; in a channel the account usually writes nothing at all.
Running the style analyser there would produce meaningless metrics and could
mix other people's writing into the account owner's Persona. So
`tg_get_recent_messages` prepends no Persona block for a non-user peer, and both
Persona tools refuse a non-user target outright.

### Schema

`dialogs` gains `peer_type` (`'user'`, `'group'`, `'channel'`). Because
`sql/schema.sql` is `CREATE TABLE IF NOT EXISTS` with no migration runner, the
column ships as an explicit idempotent `ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`; editing the `CREATE TABLE` alone would silently do nothing on an
existing archive.

`messages.sender_id` carries a column comment saying it is an opaque attribution
number and never a resolvable peer, because the next person to read the schema
would otherwise assume it is a foreign key to a person.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Impersonate Telegram Desktop | Telegram knows the `api_id` is personal. A checkable lie is worse than an unfamiliar truth |
| Leave `flood_sleep_threshold` at 60 | It is a silent retry loop, forbidden by AGENTS.md, and it makes every flood-based control dead code |
| Keep cooldowns in memory | The MCP server restarts constantly; the cooldown would reset every time the user reopened their editor |
| Rate-limit at each call site | One forgotten call site removes the control. The limiter belongs at the client |
| Fail open when PostgreSQL is down | Then `docker stop` is the bypass |
| Symmetric jitter around the delay | Drops below the reviewed constant half the time, which AGENTS.md forbids |
| Shorten delays and cap nothing | Volume, not spacing, is what a fraud model scores |
| Adopt takeout for the backfill | An approval flow and a stateful session, for a volume that does not need it. Reconsider only if the read volume grows |
| Allow cold `ResolveUsername` for a public channel | Textbook scraper behaviour, and the single fastest way to be flagged |
| Resolve group members via `inputPeerUserFromMessage` | The back door around the member-scraping ban. Doing it one id at a time is the same thing, slower |
| Run personas over group history | Garbage metrics, and it injects other people's style into the account owner's Persona |
| Enable `receive_updates` for freshness | Every message in every group, pushed to a client that reads none of them |

## Consequences

- **The Send Guard is extended, not weakened.** ADR-0005 stays Accepted and its
  stranger rule is untouched. Groups additionally require verified membership,
  channels require `post_messages` admin rights read from the cached entity with
  no API call, and a peer known only from a group message can never be a target.
  `SPEC-SND-001` is revised in place and keeps its id. The user agreed to this
  reading explicitly, as AGENTS.md requires.
- **Group history is a rolling window, not a backfill.** One 100-message call
  per run means gaps are expected. Pagination would be multiple RPCs per target,
  which is the scraper signal being avoided; this is the deliberate trade.
- The PRD non-goal "Groups and channels" is replaced. Its neighbours - media,
  marking messages read, remote access - stay, and are now *structurally
  enforced* by the blacklist, by never calling `readHistory`, and by
  `SPEC-SEC-006`, rather than merely stated.
- The glossary widens: a Peer is now a User, Group or Channel, and a Dialog is a
  conversation with a Peer of any type. `dialogs` and `PeerIndex` keep their
  names.
- A first full backfill can now hit the 500/day RPC ceiling and resume the next
  day. The Sync Cursor makes that lossless - it costs patience, not data.
- `just tg-sync` fails fast while an agent session is open (ADR-0010).
- These constants are floors and caps reviewed together. Raising any of them, or
  adding an override flag to any of them, requires a superseding ADR.
