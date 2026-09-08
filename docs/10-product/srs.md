---
id: DOC-SRS
title: Software requirements specification
status: active
authority: authoritative
updated: 2026-09-08
related: [DOC-PRD, DOC-SAD, DOC-MCP-TOOLS, DOC-QA, DOC-SECURITY]
---

# Software requirements specification

The authoritative source of behaviour. When code and this document disagree,
one of them is a defect - decide which, fix it, and record the decision.

## Clause format

```text
### SPEC-AREA-NNN - Short title

**Requirement.** What the system must do, in one testable statement.
**Rationale.** Why, when it is not obvious.
**Decided by.** The ADR that settled it, when one exists.
**Test.** The test function that proves it, or the manual step that does.
```

Areas are defined in the [ID registry](../00-index/id-registry.md):
`SND` sending, `RCV` reading live, `SRCH` searching the archive, `SYNC`
filling the archive, `PSN` per-Dialog style, `SEC` credentials and locality,
`LIM` pacing, budgets and the kill switch.

---

## SND - Sending messages

### SPEC-SND-001 - The send guard

**Requirement.** For a `User`, `tg_send_message` MUST NOT send anything when
all of the following hold: the Peer is not the account itself, the Peer is
absent from the Peer Index, no message has ever been exchanged with the Peer,
and the Peer is not in the account's contacts. In that case it returns a
`WARNING:` naming `tg_add_contact` as the next step, and sends nothing.

For a **Group**, it MUST send only when the account is currently a member,
verified from the Peer Index and never by a cold API call. For a **Channel**, it
MUST refuse unless the cached entity's `admin_rights.post_messages` is set; a
plain subscriber MUST be refused **without any API call**. A Group whose
`default_banned_rights` forbid sending is writable only by an admin.

A Peer known only from a Group message MUST NOT be a send target
(`SPEC-SND-008`), and a Group or Channel message that would need more than one
chunk MUST be refused (`SPEC-SND-007`).

There is deliberately **no** `force` parameter. Overriding the guard requires a
separate, deliberate `tg_add_contact` call. The Group and Channel rules are
strictly additional: nothing about messaging an unknown `User` is relaxed.

The Dialog Lookup path (`SPEC-SRCH-006`) MUST NOT be used to resolve a send
target. Resolving the target a second way is one of the bypasses ADR-0005
names explicitly, and a database-backed resolver in the send path is exactly
that. `tg_send_message` may read the Archive only after delivery, and only
through a helper that swallows its own failures (`SPEC-PSN-008`).

**Rationale.** Messaging strangers from a personal account is the primary cause
of `PeerFloodError` and account bans. An agent acting on an ambiguous
instruction must not be able to trigger it in one step.
**Decided by.** [ADR-0005](../20-architecture/adr/0005-hard-block-send-to-unknown-peers.md),
extended for Groups and Channels by
[ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py` for the Group and Channel guard end to
end (`test_a_channel_subscriber_cannot_post`,
`test_a_group_the_account_has_left_is_refused`);
`tests/test_peer_rules.py` for the posting-rights rules
(`test_a_channel_subscriber_may_not_post`,
`test_a_channel_admin_with_post_rights_may_post`,
`test_a_group_that_bans_sending_is_writable_only_by_an_admin`). Manual:
`tg_send_message` to a never-contacted account returns the warning and the
Telegram app shows no sent message.

### SPEC-SND-002 - Chunking

**Requirement.** A message body longer than 4000 characters MUST be split into
chunks of at most 4000 characters. Splits MUST fall on a paragraph, line,
sentence, clause or word boundary, in that order of preference. A word MUST NOT
be split unless that single word exceeds the limit on its own.

**Rationale.** Telegram rejects messages above 4096 characters. The margin to
4000 absorbs any client-side formatting. Splitting mid-word makes the text
unreadable and looks machine-generated.
**Test.** `tests/test_chunking.py` - in particular
`test_no_word_is_broken_in_half`, `test_every_chunk_fits_telegram_hard_limit`
and `test_single_oversized_token_is_hard_split_as_a_last_resort`.

### SPEC-SND-003 - Pacing

**Requirement.** Consecutive chunks of one logical message MUST be separated by
at least 2.5 seconds. No delay is added before the first chunk or after the last.

**Rationale.** Several messages per second to one Peer is a rate signal Telegram
acts on. The delay is the cost of not being classified as a bot.
**Test.** `tests/test_safety.py::test_chunk_delay_is_slow_enough_to_look_human`
asserts the constant stays above the threshold; `server.tg_send_message` awaits
`sleep_between_chunks()` only when `position` is non-zero.

### SPEC-SND-004 - Errors are returned, never raised

**Requirement.** Every MCP tool MUST return a string under all conditions. No
exception may escape a tool handler. A recognised Telegram condition MUST be
translated into a specific, actionable message; anything else MUST be reported
as `ERROR: <type>: <message>`.

`FloodWaitError` MUST produce exactly:
`Telegram API limit reached. We must wait <seconds> seconds`.

**Rationale.** An exception escaping a handler can terminate the stdio server
mid-session, leaving the agent with no Telegram access and no explanation. A
bad answer is recoverable; a dead transport is not.
**Test.** `tests/test_safety.py` - `test_guarded_tool_never_lets_an_exception_escape`,
`test_flood_wait_reports_the_exact_wait_in_seconds`, and the parametrised
`test_every_common_send_failure_has_a_specific_message`.

### SPEC-SND-005 - Partial sends are reported

**Requirement.** When a multi-chunk send fails partway, the failure report MUST
still reach the agent, and the number of chunks already delivered MUST be logged.

**Rationale.** The agent must not resend a message the recipient already has.
**Test.** Manual; `server.tg_send_message` logs `partial send to %s: %d/%d`
before re-raising into `guarded_tool`.

### SPEC-SND-006 - Cold resolution is a last resort, and never for a Group

**Requirement.** Resolving a Target MUST consult the Peer Index before calling
Telegram's username-resolution API.

A Group or Channel MUST be returned **only** when it came from the Peer Index.
When it is absent, `resolve_peer` MUST raise without making any API call, and
MUST NOT fall through to `client.get_entity()`.

The rule is structural: cold resolution is permitted **only** for a caller that
will accept nothing but a `User`. A caller that admits a Group or Channel has no
cold path at all. The guard lives inside `resolve_peer`, not at its call sites,
so `sync_db.py --dialog` and every present and future tool inherit it and a new
caller cannot forget it. As a consequence `--dialog` remains user-only, and
Groups and Channels are reachable through `--targets` and nothing else.

**Rationale.** `ResolveUsername` for unknown handles is itself rate-limited and
is one of the signals used to detect scripted accounts. Doing it for a channel
the account has never joined is the definitive scraper pattern - and rejecting
the *result* is not enough, because the request has already been made. A Peer
Index hit doubles as proof of membership, since everything in it came from
`GetDialogs`.
**Decided by.** [ADR-0005](../20-architecture/adr/0005-hard-block-send-to-unknown-peers.md),
extended by [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_group_rules.py` - in particular
`test_a_channel_not_in_the_index_is_refused_without_any_api_call` and
`test_a_caller_that_admits_groups_never_gets_a_cold_lookup`, both of which
assert the client was never called.

### SPEC-SND-007 - A Group message is one message or none

**Requirement.** When the target is a Group or Channel and the body would be
split into more than one chunk, `tg_send_message` MUST refuse and say so.
Nothing is sent, including the first chunk.

**Rationale.** `split_message` plus 2.5-second pacing sends N consecutive
messages. In a private chat that is a long reply; in a group it is flooding,
and in a slow-mode group the second chunk fails with `SLOWMODE_WAIT_X` - which
now surfaces as an error rather than being slept through (`SPEC-SEC-008`).
Refusing up front is better than getting there, and it leaves the user with a
message that was either sent whole or not at all.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py::test_a_message_needing_two_chunks_is_refused_for_a_group`,
which also asserts that not even the first chunk is sent. `split_message`
itself is covered by `tests/test_chunking.py`.

### SPEC-SND-008 - A Peer seen only in a Group is never a target

**Requirement.** A user id whose only provenance is a Group or Channel message
MUST NOT be resolved, added to the Peer Index, added to contacts, or accepted as
a send target. `tg_send_message` MUST refuse such a target **before making any
API call**. `messages.sender_id` MUST carry a schema comment saying it is
attribution only.

**Rationale.** Telegram delivers those users as
[`min` constructors](https://core.telegram.org/api/min), whose `access_hash`
"can't be used to generate a typical `inputPeerUser` constructor to send
messages or do other actions". Beyond that, assembling a member list one message
at a time is exactly the scraping that `channels.getParticipants` is banned for
(`SPEC-LIM-006`) - doing it one id at a time is the same thing, slower. The
archive is what makes the check possible without asking Telegram: an id that
appears as a sender in a non-user Dialog and has no Dialog of its own is such a
Peer.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py::test_a_peer_seen_only_in_a_group_is_refused_before_connecting`,
which leaves `telegram()` unstubbed so that reaching it fails the test;
`tests/test_group_rules.py` for the query shape and the schema comment.

### SPEC-SND-009 - The read receipt is bound to sending

**Requirement.** When `TG_READ_ON_SEND` is enabled, `tg_send_message` MUST mark
the target Dialog read **after** delivering every chunk into it, for a User, a
Group and a Channel alike. It MUST do so through Telethon's
`send_read_acknowledge` and the Peer already resolved for the send, never by
resolving the Target a second time and never through a raw `ReadHistory`
request class.

It MUST NOT acknowledge when the send was refused, when the send failed
partway, or when the flag is off - which is its default (`SPEC-SEC-012`). A
failure to acknowledge MUST NOT change the tool's result: the message is
already delivered, so the failure is logged and swallowed, including a failure
to read the setting itself.

No other code path may acknowledge anything (`SPEC-RCV-003`), and
`send_read_acknowledge` MUST appear in exactly one shipped file.

**Rationale.** A person who replies to a chat has read it, and every official
client marks it read before the reply is sent. Replying at length while the
recipient's message keeps a single checkmark, permanently and account-wide, is
a behavioural divergence nobody chose. Binding the acknowledgment to delivery
rather than to fetching keeps the account owner's own unread badges honest -
they still clear only when something was actually written - while removing the
divergence. Acknowledging before delivery would make the receipt a side channel
that reveals a send which did not happen.
**Decided by.** [ADR-0011](../20-architecture/adr/0011-read-receipt-on-send.md).
**Test.** `tests/test_server_limits.py` -
`test_no_read_receipt_is_sent_by_default`,
`test_a_successful_send_marks_the_chat_read`,
`test_a_channel_send_marks_the_channel_read`,
`test_a_group_send_marks_the_group_read`,
`test_a_partial_send_does_not_mark_the_chat_read`,
`test_a_refused_send_marks_nothing_read` and
`test_a_failing_read_receipt_still_reports_the_send_as_successful`;
`tests/test_blacklist.py::test_the_only_read_acknowledgment_is_in_the_send_path`
for the single call site. Manual: reply to a chat and confirm the recipient's
message shows two checkmarks.

---

## RCV - Reading the live account

### SPEC-RCV-001 - Recent messages are read live

**Requirement.** `tg_get_recent_messages` MUST read from Telegram, not from the
Archive, so that messages received seconds ago are visible.
**Test.** Manual: send a message from another device, then call the tool.

### SPEC-RCV-002 - Conversations read oldest-first

**Requirement.** `tg_get_recent_messages` MUST return messages oldest-first and
MUST mark each one `[me]` or `[them]`.

**Rationale.** Telethon returns newest-first, which reads backwards and causes
agents to misattribute who said what.
**Test.** `server.tg_get_recent_messages` reverses the Telethon result;
`formatting.render_conversation` emits the marker.

### SPEC-RCV-003 - Reading is non-destructive, and Groups are rationed

**Requirement.** `tg_get_unread_dialogs` MUST list only 1-on-1 Dialogs with
non-deleted human users, and MUST exclude bots unless `TG_SYNC_INCLUDE_BOTS`
is true. **Reading MUST NOT mark anything as read.** No read, fetch, sync or
unread scan may acknowledge anything, whatever `TG_READ_ON_SEND` is set to: the
raw `messages.readHistory`, `messages.readMentions` and `channels.readHistory`
request classes are blacklisted outright (`SPEC-LIM-006`), and Telethon's
`send_read_acknowledge` is reachable from exactly one place, the post-delivery
path in `tg_send_message` (`SPEC-SND-009`).

`tg_get_recent_messages` MAY read a Group or Channel the account is a member
of, subject to all of:

| Control | Value |
| --- | --- |
| Per-target cooldown | 300 s |
| Group/Channel reads per rolling 24 h | 20 |
| Messages per call | 100, which is one API call |

Both counters MUST be stored in PostgreSQL and MUST survive a restart of the
server. Exceeding either MUST return a `ToolError` naming the remaining wait or
the cap, and the tool docstring MUST state both numbers and tell the agent not
to poll.

**Rationale.** Marking messages read as a side effect of a status query would
make the user's own Telegram client lie to them - and instantly "reading" 100
messages across several channels is superhuman. That reasoning is about
*reading* and is unaffected by ADR-0011, which acknowledges only after the
account has itself written into a Dialog. The cooldown must be persisted
because an MCP stdio server is respawned whenever the user reopens their
editor, so an in-memory cooldown is cleared by the very restart an agent in a
loop is most likely to cause. The numbers are in the docstring because the
caller is a model that will otherwise retry rather than stop.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py` - in particular
`test_the_cooldown_survives_a_process_restart` and
`test_the_daily_cap_refuses_once_it_is_used_up`; `tests/test_blacklist.py` for
the ban on the raw request classes; and
`tests/test_server_limits.py::test_reading_messages_marks_nothing_read`, which
calls the tool with `TG_READ_ON_SEND` deliberately **on** against a client that
raises if acknowledgment is reached. Manual: unread badges in the Telegram app
are unchanged after calling.

### SPEC-RCV-005 - One bounded dialog fetch, shared

**Requirement.** `GetDialogs` MUST be bounded by an explicit `limit`, and the
Peer Index and `tg_get_unread_dialogs` MUST share one fetch rather than each
walking the list. `tg_get_unread_dialogs` MUST stop after examining a bounded
number of Dialogs, not after finding a bounded number of unread ones.
`CACHE_TTL_SECONDS` MUST be at least 600 and MUST NOT be lowered.

**Rationale.** `GetDialogs` is the heaviest call this codebase makes and a
monitored endpoint. Both previous call sites were unbounded: the Peer Index
walked every Dialog every 300 seconds, and the unread tool broke only once it
had collected `limit` *unread* Dialogs - so an account with nothing unread
walked the entire list on every call. Two independent walks per user question
is indefensible, and group support only makes the list longer.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** Observed live: one Peer Index refresh over 198 Dialogs costs exactly
two `GetDialogsRequest` calls in `api_call_log`, and `tg_get_unread_dialogs`
adds none.

### SPEC-RCV-004 - Limits are clamped, never rejected

**Requirement.** Out-of-range `limit` values MUST be clamped to the valid range
rather than returning an error. Recent messages clamp to 1-100; unread dialogs
to 1-50; search to 1-500.

**Rationale.** An agent guessing `limit=10000` should get a useful answer, not a
round trip spent on an error.
**Test.** `max(1, min(int(limit), N))` in each tool.

---

## SRCH - Searching the archive

### SPEC-SRCH-001 - Search never touches Telegram

**Requirement.** `tg_search_local_history` MUST query PostgreSQL only. It MUST
NOT make any Telegram API call, and MUST work while the account is
flood-limited, the session is missing, or `TG_API_ID`/`TG_API_HASH` are unset.

**Rationale.** This is the whole reason the Archive exists: instant search over
years of history, with no rate limit and no ban risk.
**Test.** `server.tg_search_local_history` calls `database()` and never
`telegram()`; verified live with no session file and no Telegram credentials
configured.

### SPEC-SRCH-002 - Full text first, substring as fallback

**Requirement.** Search MUST run `websearch_to_tsquery` against the `tsv`
column first. Only when that returns nothing MUST it retry as a trigram-backed
`ILIKE` substring match. The response MUST state which strategy matched.

**Rationale.** Full-text search handles multi-word queries and ranking;
substring search catches partial words and inflections a stemless
configuration misses. Telling the agent which one matched prevents it from
over-trusting a loose substring hit.
**Decided by.** [ADR-0003](../20-architecture/adr/0003-postgres-fts-simple-plus-trigram.md).
**Test.** `tests/test_search_sql.py`; verified live against both paths.

### SPEC-SRCH-003 - The `simple` text search configuration

**Requirement.** The `tsv` column and every query MUST use the `simple`
configuration. A language-specific configuration MUST NOT be used.

**Rationale.** The Archive is multilingual. Any stemmer is wrong for the
languages it was not built for, and silently drops matches.
**Decided by.** [ADR-0003](../20-architecture/adr/0003-postgres-fts-simple-plus-trigram.md).
**Test.** `tests/test_search_sql.py::test_the_simple_configuration_is_used_for_multilingual_history`.

### SPEC-SRCH-004 - Values are always bound, never interpolated

**Requirement.** Every user-supplied value MUST reach PostgreSQL as a bound
parameter. String formatting in query construction MUST only ever insert a
placeholder, never a value.

**Rationale.** The query text comes from an LLM acting on arbitrary chat
content. Interpolation here is a direct injection path into the user's archive.
**Test.** `tests/test_search_sql.py::test_values_are_never_interpolated_into_the_statement`.

### SPEC-SRCH-005 - An empty result is explained

**Requirement.** Zero matches MUST return an explanation that includes the
possibility of a stale Archive and the command that refreshes it.

**Rationale.** "No results" is ambiguous between "never said" and "not yet
synced". The agent cannot distinguish them without being told.
**Test.** `formatting.render_search_hits` with an empty list.

### SPEC-SRCH-006 - Dialog Lookup resolves against the Archive alone

**Requirement.** `db.resolve_dialog` MUST resolve a Target to a Dialog using
only the `dialogs` table, with no Telegram API call. Matching MUST be exact per
key - username, numeric id, digits-only phone, first, last or full name - and
MUST NOT be a substring match. When more than one Dialog matches, the caller
MUST be given every candidate with its numeric `chat_id`; a Dialog Lookup MUST
NOT choose one. When none matches, the failure MUST name
`just tg-sync-targets`.

The statement MUST bind every value and MUST NOT be assembled by string
formatting. Its phone predicate MUST be guarded by both `d.phone IS NOT NULL`
and a non-empty digits parameter.

**Rationale.** The Persona tools have to work when the Telegram session is dead,
as `SPEC-SRCH-001` already requires of search. Exact matching is the same rule
`SPEC-SYNC-006` gives: `an` must not silently pull in Anna, Ivan and Alexander.
Without both phone guards, a non-numeric Target reduces the phone predicate to
`'' = ''` for every row storing no phone, and the lookup silently returns the
entire archive.

**Decided by.** [ADR-0008](../20-architecture/adr/0008-dialog-persona-hybrid-authorship.md).
**Test.** `tests/test_persona_sql.py` - `test_dialog_lookup_binds_every_value_and_formats_nothing`,
`test_dialog_lookup_never_matches_every_dialog_on_a_null_phone`,
`test_dialog_lookup_matches_exactly_never_as_a_substring`,
`test_dialog_lookup_keys_normalise_a_target`.

---

## SYNC - Filling the archive

### SPEC-SYNC-001 - A default sync archives people only

**Requirement.** A sync with no Target Filter MUST archive only 1-on-1 Dialogs
with `User` peers. It MUST exclude Groups and Channels, deleted accounts, and
Telegram's service account `777000`. Bots are excluded unless
`TG_SYNC_INCLUDE_BOTS` is true.

Groups and Channels are **opt-in only**, through `--targets` (`SPEC-SYNC-007`).
`is_archivable` MUST default to excluding them, so that widening it requires an
explicit argument at the call site rather than being the ambient behaviour.

**Rationale.** Groups and channels would dominate the Archive by volume, and
pulling them on every routine sync turns a background task into a large,
repeated read against monitored endpoints. Account `777000` delivers login
codes, and archiving one-time passwords in plain text is a security defect.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_peer_rules.py` - in particular
`test_a_full_sync_still_archives_people_only` and
`test_groups_are_archivable_only_when_explicitly_included`.

### SPEC-SYNC-002 - Insertion is idempotent

**Requirement.** Re-running a sync over already-archived messages MUST add zero
rows and MUST NOT error. `(chat_id, message_id)` is the primary key; inserts
use `ON CONFLICT DO NOTHING`.

**Rationale.** `message_id` is unique per chat, never globally, so the key must
be composite. Idempotency is what makes an interrupted sync safe to rerun.
**Test.** Verified live: a second run over the same range reported 0 added.

### SPEC-SYNC-003 - Resumable progress

**Requirement.** Each Dialog's Sync Cursor MUST be advanced only after the
corresponding rows are committed, and MUST never move backwards. An interrupted
run MUST resume from the cursor rather than restarting the Dialog.

**Rationale.** A full backfill of a busy account takes hours and will hit flood
waits. Losing that progress to one interruption makes the tool unusable.
**Test.** Verified live: `advance_cursor` with a lower id left the cursor at its
higher value (`GREATEST` in the UPDATE).

### SPEC-SYNC-004 - Flood waits pause, they do not fail

**Requirement.** A `FloodWaitError` during sync MUST commit the buffered rows,
sleep for `seconds + 5`, and resume the same Dialog. After
`MAX_FLOOD_RETRIES` on one Dialog the sync MUST skip to the next Dialog with
its cursor intact, not abort the run.

**Rationale.** One rate-limited Dialog must not cost the whole archive.
**Test.** Manual; the retry loop in `sync_db.sync_dialog`.

### SPEC-SYNC-005 - Sync is paced between dialogs

**Requirement.** At least 1 second MUST elapse between finishing one Dialog and
starting the next.

**Rationale.** Dozens of history requests per second is a scripted-account
signal even though nothing is being sent.
**Test.** `SYNC_DIALOG_DELAY_SECONDS` awaited in `sync_db.run`.

### SPEC-SYNC-006 - Targeted sync

**Requirement.** `sync_db.py --targets T [T ...]` MUST restrict the run to the
Dialogs matching those Targets. A Target MUST match a Peer's username (with or
without `@`), phone number, numeric id, first name, last name or full name,
case-insensitively and with whitespace collapsed. Matching MUST be exact per
key, never a substring.

The filter MUST narrow the Dialog list produced by `SPEC-SYNC-001`, never widen
it: a bot, group, channel or deleted account named as a Target MUST still be
excluded.

Targets matching no Dialog MUST be reported by name. When **no** Target matches,
the run MUST exit non-zero rather than reporting a successful empty sync.

`--targets` and `--dialog` are mutually exclusive. `--full`, `--since` and
`--limit` apply unchanged to a targeted run.

**Rationale.** An account with hundreds of Dialogs accumulates hours of flood
waits during a full backfill. Archiving the people who matter first makes the
tool usable on day one. Reporting unmatched Targets matters because a typo is
otherwise indistinguishable from a person having no Dialog, and the user would
wait for a sync that was never going to include them. Substring matching is
rejected because `an` would silently pull in every Anna, Ivan and Alexander.

Unlike `--dialog`, the filter selects from Dialogs the account already has and
therefore makes no cold `ResolveUsername` call (`SPEC-SND-006`).

**Test.** `tests/test_peer_rules.py` - the target-filtering block, in particular
`test_matching_is_exact_and_never_a_substring`,
`test_select_by_targets_reports_what_matched_nothing` and
`test_select_by_targets_keeps_the_newest_active_dialog_order`.

### SPEC-SYNC-007 - Group and Channel syncing is opt-in and capped

**Requirement.** A Group or Channel MUST be synced only when named in
`--targets`. `--dialog` MUST remain user-only. Each Group or Channel MUST be
read with **exactly one** `messages.getHistory` of at most 100 messages. A run
naming more than 5 Group or Channel targets MUST abort with an error naming the
cap and syncing nothing. A run that would exceed 20 Group or Channel reads in a
rolling 24 hours MUST abort. Consecutive Group reads MUST be separated by at
least `CHANNEL_SYNC_DELAY_SECONDS` (15 s) plus jitter.

**Requirement (honesty).** Because the read is capped at one request, Group
history is a **rolling window, not a backfill**: a Group that produced more than
100 messages since the last run leaves a permanent gap. The sync MUST say so
when it happens rather than reporting success silently.

**Rationale.** Telegram's limits count RPCs, not messages, so one call of 100 is
one unit of risk while pagination is several. Volume matters more than spacing:
how many distinct channels an account touches per day is more informative to a
fraud model than the gap between two reads, which is why the caps are on counts
and the delays are merely floors. A complete group history is not worth looking
like a scraper for.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_group_rules.py` pins the caps as values. Verified live:
syncing one group produced exactly one `GetHistoryRequest` in `api_call_log`
and 100 rows; a second run added 0; and a run naming 6 groups aborted, naming
all six.

---

## PSN - Per-Dialog style

### SPEC-PSN-001 - A Dialog Persona survives a resync

**Requirement.** A Dialog Persona MUST be stored in `dialog_personas`, never as
columns on `dialogs`.

**Rationale.** `_UPSERT_DIALOG_SQL` rewrites every column it names on every
sync. A Persona stored on `dialogs` would be destroyed by the next
`just tg-sync`, and it is the only content in this database that a resync
cannot rebuild.
**Test.** `tests/test_persona_sql.py::test_persona_table_is_separate_from_dialogs`
and `test_the_dialog_upsert_still_touches_only_sync_columns`.

### SPEC-PSN-002 - Only the account's own words are analysed, and only as numbers

**Requirement.** Every statement feeding a Style Metric MUST filter
`is_outgoing`. `persona.analyse_style` MUST return only numbers, timestamps and
Unicode script names; no message text may appear in a Style Metric, in a
rendered metric line, or in the stored `metrics` snapshot.

**Rationale.** This is the structural half of `RISK-07`. A Persona is derived
from chat content and then replayed into a drafting context on every later read,
so the derived half must be incapable of carrying an instruction, a destination
or a handle. Restricting the input to the account's own messages keeps the
counterparty's words out of the analyser.
**Test.** `tests/test_persona_metrics.py::test_no_metric_output_contains_verbatim_message_text`
and `test_metrics_depend_on_shape_alone_and_not_on_what_was_written`;
`tests/test_persona_sql.py::test_statements_reading_messages_are_outgoing_only`.

### SPEC-PSN-003 - The Persona Baseline is frozen

**Requirement.** `dialog_personas.baseline_message_id` MUST be set when a
Persona is created and MUST NOT be changed by an update. `_UPDATE_PERSONA_SQL`
MUST NOT name the column. Only `_REBASELINE_PERSONA_SQL` may move it, and only
forward.

**Rationale.** Every message this server sends is archived with `is_outgoing`
set and is indistinguishable from one the account owner typed. Without a frozen
baseline the analysis would re-read its own output, and a Persona would converge
on a model of the model within a few refreshes - reading as *more* consistent,
so the drift would be invisible.
**Decided by.** [ADR-0008](../20-architecture/adr/0008-dialog-persona-hybrid-authorship.md).
**Test.** `tests/test_persona_sql.py::test_persona_update_never_touches_the_baseline_column`
and `test_only_the_rebaseline_statement_moves_the_baseline`.

### SPEC-PSN-004 - A stored Persona is never silently replaced

**Requirement.** Creating a Persona MUST use `ON CONFLICT DO NOTHING`.
`tg_set_dialog_persona` MUST return the existing Persona unchanged unless
`overwrite` is true.

**Rationale.** A Persona is the only hand-written content in this database and
the only content a resync cannot reconstruct. Silently replacing one would
destroy work with no record.
**Test.** `tests/test_persona_sql.py::test_persona_insert_never_overwrites`.

### SPEC-PSN-005 - Pattern Drift is reported on three axes

**Requirement.** A rendered Persona MUST state its freshness, judged on volume
(the account's own messages archived since the analysis, counted not
subtracted), age, and metric drift. A stale verdict MUST name which axis fired.
Analysis MUST NOT rewrite a stored Persona.

**Rationale.** Volume alone is bounded above by how far the Sync has run, so a
year-old Persona reports zero new messages - and therefore fresh - on an archive
nobody has synced. Age catches that; drift catches a style that changed without
the message count moving. Message ids have gaps from deletions, so a subtraction
of ids is not a count of messages.
**Test.** `tests/test_persona_metrics.py::test_stale_verdict_names_which_axis_triggered_it`
and `test_freshness_is_fresh_when_no_axis_fired`;
`tests/test_persona_sql.py::test_drift_is_counted_never_subtracted_from_message_ids`.

### SPEC-PSN-006 - Persona text is sanitised, capped and fenced

**Requirement.** Every agent-written Persona field MUST pass
`safety.sanitise_persona_field` before storage: invisible and control characters
stripped, all whitespace collapsed to single spaces, and the field refused if it
contains a URL, an `@handle`, a `tg_` tool name or a `---` fence marker. Python
caps MUST be strictly tighter than the SQL `CHECK` constraints. Rendered Persona
text MUST appear inside a constant fence labelling it as data.

**Rationale.** `RISK-07`. Collapsing whitespace means a field cannot span lines
and so cannot counterfeit the fence. The rejections are structural, not
semantic: a keyword blacklist rejects honest style descriptions and stops nobody
who can rephrase. The tighter Python cap means a too-long field produces an
actionable sentence rather than a raw asyncpg constraint error.
**Test.** `tests/test_persona_render.py` - the `test_sanitise_*` cases and
`test_persona_block_is_fenced_and_labelled_as_data`;
`tests/test_persona_sql.py::test_python_caps_are_strictly_tighter_than_the_sql_checks`.

### SPEC-PSN-007 - The Persona is injected before drafting

**Requirement.** `tg_get_recent_messages` MUST prepend the Dialog's Persona
block, or a note naming the tools that record one. It MUST key that lookup on
the `chat_id` of the Peer it has already resolved, never on a second
resolution. The lookup MUST NOT be able to fail the tool: an unreachable
archive or an unsynced Peer MUST degrade to one line.

**Rationale.** This is the tool an agent calls immediately before drafting, so
it is the one place the style constraint is guaranteed to be in context when it
is needed. Two resolutions in one tool can disagree and describe the wrong
person. `tg_get_recent_messages` works today with no database at all, and
prepending a Persona must not change that.
**Test.** Manual: with PostgreSQL stopped, the tool still returns the
conversation with `PERSONA: unavailable`. `server.persona_header_for` catches
every exception.

### SPEC-PSN-008 - A Persona never gates a send

**Requirement.** A missing, stale or unreadable Dialog Persona MUST NOT prevent
or delay a send. `tg_send_message` MUST read the Archive only after every chunk
is delivered, only on the success path, and only through a helper that returns
`""` on any failure.

**Rationale.** The Send Guard must remain the only reason a send is refused. A
database read that could raise after delivery would report `ERROR:` for a
message that was actually sent, and the agent would send it again.
**Test.** `server.persona_hint` swallows every exception; the guard, empty-body
and exception paths of `tg_send_message` are unchanged.

### SPEC-PSN-009 - Personas are for private chats only

**Requirement.** `tg_get_recent_messages` MUST NOT prepend a Persona block when
the Peer Type is not `user`. `tg_set_dialog_persona` and
`tg_get_dialog_persona` MUST return a `ToolError` for a Group or Channel, and
`tg_list_dialog_personas` MUST NOT list one.

**Rationale.** A Dialog Persona models how one person writes to one other
person. In a Group several people write, so there is no single style to record;
in a Channel the account usually writes nothing at all, so there is nothing to
measure. Running the analyser over either produces meaningless numbers and, far
worse, would let other people's writing shape what the file claims is the
account owner's own voice - which is then replayed into the drafting context on
every later read (`RISK-07`).
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py::test_a_persona_is_refused_for_a_group_or_channel`
and `::test_both_persona_tools_go_through_the_guard`, which asserts neither tool
can reach around the guard. The overview query filters on
`d.peer_type = 'user'`.

---

## SEC - Credentials and locality

### SPEC-SEC-001 - The session file is owner-only

**Requirement.** After login the Session file MUST have mode `0600`, and the
same MUST hold for the Session Clone.

**Rationale.** The file holds the account's MTProto auth key. A group-readable
copy is equivalent to publishing the password.
**Test.** `stat -c %a tg_session.session` returns `600` after `just tg-auth`.

### SPEC-SEC-002 - The session file is never committed

**Requirement.** `*.session`, `*.session-journal`, `*.session-wal`,
`*.session-shm` and `.env` MUST be git-ignored, and `.gitignore` MUST exist
before any session file can be created.
**Test.** `git status --porcelain` lists no session file after `just tg-auth`.

### SPEC-SEC-003 - Two processes never write one session

**Requirement.** `sync_db.py` MUST operate on a copy of the Session by default.
Using the primary Session requires the explicit `--in-place` flag.

**Rationale.** Telethon sessions are SQLite databases. Concurrent writers
produce `database is locked` and can corrupt the file that holds the only
credential.
**Decided by.** [ADR-0004](../20-architecture/adr/0004-session-file-clone-for-sync.md).
**Test.** `tests/test_config.py::test_session_paths_are_derived_from_the_session_name`.

### SPEC-SEC-004 - Configuration fails loudly and once

**Requirement.** All configuration MUST be read and validated in
`tg_ai.config.load_config`. No other module may read `os.environ`. A missing or
malformed value MUST raise `ConfigError` with the command that fixes it.
`TG_SESSION_NAME` MUST be a bare file name, never a path.

The MCP server loads configuration with `require_telegram=False` and defers the
credential check to `server.telegram()`, so that a database-only tool is not
blocked by a missing API key. Every other entrypoint requires credentials up
front.

**Rationale.** A path in `TG_SESSION_NAME` would let configuration write outside
the project. Scattered environment reads produce failures at the worst moment -
mid-tool-call, inside a stdio session.
**Test.** `tests/test_config.py`.

### SPEC-SEC-005 - Everything stays on the local machine

**Requirement.** PostgreSQL MUST bind to `127.0.0.1` only. The MCP server runs
on the user's own machine, over stdio, so Telegram sees the same IP the user
normally connects from.

**Rationale.** The Archive holds the plaintext of every private conversation on
the account. A sudden connection from an unfamiliar IP or datacentre range is
itself a ban signal.
**Test.** `docker compose ps` shows `127.0.0.1:5434->5432/tcp`, never `0.0.0.0`.

### SPEC-SEC-006 - One machine, one network, one api_id

**Requirement.** The MCP server and `sync_db.py` MUST run on the account
owner's ordinary personal machine, on the ordinary network that account already
connects from. They MUST NOT run on a VPS, cloud host, container platform, CI
runner or any datacentre IP range, and MUST NOT use a shared VPN or any proxy
that rotates its exit address. If a proxy is unavoidable it MUST be a single
stable endpoint configured once and used identically by every process.

The project MUST use exactly one `api_id`/`api_hash` pair, the account owner's
own, obtained from <https://my.telegram.org>. `auth.py`, `server.py` and
`sync_db.py` share it. A published, sample or borrowed `api_id` MUST NOT be
used, and a second `api_id` MUST NOT be introduced for the sync process.

There is deliberately **no** environment flag, CLI argument or configuration
override that relaxes any of this.

**Rationale.** Where the MTProto packets come from is the strongest userbot
signal Telegram has, and it dominates every pacing constant in this project: a
perfectly paced client on a datacentre IP is banned, a sloppy one on a home
laptop usually is not. Two IPs on one authorization key is the documented
trigger for `AUTH_KEY_DUPLICATED` (`SPEC-SEC-010`). Telegram answers a reused
published `api_id` with `API_ID_PUBLISHED_FLOOD` and treats the account behind
it as an abuser; it binds one `api_id` per phone number, so changing it later is
a new fingerprint on an account already under observation, not a recovery.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tg_whoami` reports `client.session.dc_id`, so a silent relocation of
the account is visible immediately. The locality rule itself binds the operator
and is enforced by documentation, not by code - see
[RISK-05](../70-ops/security.md#risk-05---connecting-from-an-unfamiliar-or-datacentre-ip).

### SPEC-SEC-007 - The Client Identity is configured, honest and frozen

**Requirement.** `build_client` MUST pass `device_model`, `system_version`,
`app_version`, `lang_code` and `system_lang_code` from `Config` on every
client it constructs, and MUST be the only place a client is constructed. The
values MUST be identical for the primary Session and the Session Clone.

`app_version` MUST name this application and MUST NOT claim to be an official
Telegram client. `lang_code` and `system_lang_code` MUST be configurable and
default to `en`; the documentation MUST warn that the default is unsafe unless
it matches the account's real Telegram app language.

Once a Session exists these values MUST be treated as immutable.

**Rationale.** Telegram already knows this client is unofficial, because it
knows the `api_id`. A personal `api_id` announcing itself as official Telegram
Desktop is a trivially detectable inconsistency, and an inconsistency that can
only be deliberate reads worse than an unfamiliar but honest client. What
actually protects the account is stability: `initConnection` is re-sent on every
reconnection, so changing these strings makes the account's own "active
sessions" entry mutate under the user - support-visible, and a signal.
Telethon's defaults would otherwise announce Telethon on Python with
`lang_code='en'` whatever language the account really uses.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_client_contract.py` - in particular
`test_identity_is_identical_for_the_primary_session_and_the_clone` and
`test_the_app_version_never_claims_to_be_an_official_client`.

### SPEC-SEC-008 - The Telethon constructor contract

**Requirement.** `build_client` MUST pass every one of these explicitly rather
than inheriting Telethon's default:

| Parameter | Required value |
| --- | --- |
| `flood_sleep_threshold` | `0` |
| `receive_updates` | `False` |
| `catch_up` | `False` |
| `request_retries` | `1` |
| `connection_retries` | `2` |
| `retry_delay` | `5` |
| `auto_reconnect` | `True` |
| `entity_cache_limit` | `500` |

**Rationale.** `flood_sleep_threshold` is the load-bearing one. Telethon's
default of `60` makes the library sleep on - that is, silently retry - every
flood wait of 60 seconds or less. Scraping-induced waits are typically 5-30
seconds, so with the default every one is invisible: the `FloodWaitError`
handling in `sync_db.py` and the kill switch (`SPEC-LIM-003`) would be dead
code, while flood *frequency* is exactly what feeds Telegram's risk score.
`request_retries=5` turns one failing request into six. `connection_retries` and
`retry_delay` bound the handshake burst a network blip would otherwise produce,
since every reconnect replays `initConnection`. `entity_cache_limit` caps how
many strangers' access hashes accumulate in the Session file that
`clone_session()` copies on every run.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_client_contract.py::test_every_risky_telethon_default_is_pinned`,
parametrised over the whole table, plus
`test_flood_waits_are_never_slept_through`.

### SPEC-SEC-009 - No update stream, and no event handlers

**Requirement.** The client MUST be constructed with `receive_updates=False`
and `catch_up=False` (`SPEC-SEC-008`), and the codebase MUST register **zero**
Telethon event handlers.

**Rationale.** With group and channel support, a subscribed client would receive
an update for every message in every group the account belongs to - a large
traffic fingerprint for a client that never reads any of them, and a route to
`updatesTooLong`, which triggers `updates.getDifference` and a bulk history
fetch. The handler ban exists because the two rules interact badly: with
updates off a handler silently never fires, which reads as a bug, and the
obvious "fix" is to turn updates back on. Forbidding handlers outright removes
the temptation.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_client_contract.py::test_the_codebase_registers_no_telethon_event_handlers`,
which scans the shipped source for every registration form.

### SPEC-SEC-010 - One connection per authorization key

**Requirement.** Every process MUST hold an exclusive lock on
`<session_name>.lock` before calling `connect()`, and MUST hold it until it
disconnects. The lock MUST be derived from the primary Session name, never from
the Session Clone. A process that cannot take the lock MUST NOT wait or retry:
`sync_db.py` exits with status 1 and `server.py` returns a `ToolError` naming
the holder. `sync_db.py` MUST take the lock before cloning the Session.

**Rationale.** The Session Clone carries the *same* authorization key as the
primary Session, and Telegram answers parallel sessions beyond its limit with
`AUTH_KEY_DUPLICATED` - at which point, per its own documentation, "the session
is already invalidated and the user must re-authenticate". There is no warning
that arrives in time to back off, so the only defence is never to open the
second connection. Cloning before locking would additionally copy a SQLite file
the server may be mid-write on.
**Decided by.** [ADR-0010](../20-architecture/adr/0010-one-connection-per-authorization-key.md).
**Test.** `tests/test_connection_lock.py`. Shutdown, the other half of "until it
disconnects", is `SPEC-SEC-011`.

### SPEC-SEC-011 - The server releases what it holds when it stops

**Requirement.** When the MCP client closes stdin, or the operator sends SIGINT,
`server.py` MUST disconnect the Telethon client, then release the connection
lock, then close the database pool, and MUST exit with status 0. The teardown
MUST be idempotent, MUST NOT raise, and each step MUST be bounded by a timeout
so one wedged resource cannot prevent the exit. The ordering is normative:
release before disconnect, or pool close before disconnect, are both defects.
Startup MUST remain lazy - the shutdown hook MUST NOT connect anything.

**Rationale.** stdio has no goodbye message; the client just closes the pipe.
Telethon's sender, keepalive and update tasks are cancelled only by
`disconnect()`, and with `auto_reconnect=True` they respawn rather than end
during interpreter teardown, so the process never exits. That orphan keeps both
the `flock` and the SQLite session file, and the next server dies on `database
is locked`. `disconnect()` is also the only caller of `session.close()`, so
without it every stop leaves a `.session-journal` behind.

The ordering carries the safety. Releasing the lock before the socket is gone
opens the window in which a sync connects on the same authorization key, which
Telegram answers with `AUTH_KEY_DUPLICATED` (`SPEC-SEC-010`). Closing the pool
first breaks the other end: Telethon's auto-reconnect callback issues a
`get_me()` that reaches the RPC ledger (`SPEC-LIM-002`).

The hook is FastMCP's `lifespan`, entered by the SDK on an `AsyncExitStack`
outside the task group that runs tool calls - so in-flight tools finish first
and the event loop is still alive during teardown. **Rejected:** an `atexit`
hook, which runs after the loop is closed and cannot await futures belonging to
it; a SIGTERM handler, since the kernel already drops the flock on an
unconditional kill; and a hard `os._exit`, which would skip the session flush
that is half the point. SIGTERM handling is recorded as an open task rather
than a comment.
**Decided by.** Implements [ADR-0010](../20-architecture/adr/0010-one-connection-per-authorization-key.md);
decides nothing new.
**Test.** `tests/test_server_shutdown.py`.

### SPEC-SEC-012 - Read receipts are opt-in

**Requirement.** `TG_READ_ON_SEND` MUST default to `false`, so a freshly
installed server acknowledges nothing. When it is unset, malformed or the
configuration cannot be read at all, the effective behaviour MUST be "do not
acknowledge". It governs the send path only and MUST NOT enable acknowledgment
anywhere else (`SPEC-RCV-003`).

**Rationale.** A read receipt is visible to the other person and cannot be
withdrawn, and this project drives an account whose owner may not want their
correspondents to know when a chat was opened. Every other default in this
project fails toward doing less to the account; this one does too. Failing
closed on an unreadable configuration matters because the alternative is a
receipt sent because something was broken.
**Decided by.** [ADR-0011](../20-architecture/adr/0011-read-receipt-on-send.md).
**Test.** `tests/test_config.py::test_read_on_send_is_off_unless_the_account_owner_turns_it_on`
and `::test_read_on_send_is_enabled_by_the_usual_truthy_spellings`;
`tests/test_server_limits.py::test_no_read_receipt_is_sent_by_default` for the
end-to-end default.

---

## LIM - Pacing, budgets and the kill switch

### SPEC-LIM-001 - One serialising limiter for every request

**Requirement.** Every Telegram request MUST pass through a single limiter
installed at the Telethon client level, not at each call site. The limiter MUST
hold a lock so two concurrent tool calls can never issue overlapping requests,
and MUST enforce a minimum gap of 1.5 seconds plus jitter between any two
requests of any kind.

The limiter MUST be re-entrant for nested requests issued from within an
already-authorised request in the same task, and those nested requests MUST
still be counted and paced.

**Rationale.** Telegram's limits are per account across all methods, so pacing
has to be global; a control that a new call site can forget is not a control.
Telethon pipelines happily, and a bursty parallel pattern is a stronger signal
than a fast serial one.

Re-entrancy is a correctness requirement rather than a convenience. Telethon's
`_call` issues nested requests of its own - every request's `resolve()` may call
`get_input_entity`, which calls `self(GetUsersRequest(...))` - so a plain lock
held across the delegate deadlocks in the same task, permanently and with no
traceback.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_rpc_guard.py` - in particular
`test_a_nested_request_in_the_same_task_does_not_deadlock`,
`test_two_separate_tasks_are_serialised_and_never_overlap` and
`test_consecutive_requests_are_spaced_by_at_least_the_minimum_gap`.

### SPEC-LIM-002 - The request budget is rolling and persisted

**Requirement.** The limiter MUST enforce a rolling budget of 60 requests per
hour and 500 per 24 hours, counted from `api_call_log` in PostgreSQL and shared
by every process. Windows MUST be rolling (`now() - interval`), never calendar
buckets. An exhausted budget MUST return an `ERROR:` naming the budget; it MUST
NOT queue and wait. When the budget cannot be read, the limiter MUST refuse.

**Rationale.** An MCP stdio server is spawned fresh by the client on every
launch and again after every crash, so a counter in a Python variable resets
whenever the user reopens their editor: in-memory rate limiting in an MCP server
is not rate limiting. A calendar bucket would allow the whole hourly budget at
10:59 and again at 11:01. Queueing would turn a volume cap into a delay, and the
point is that the requests do not happen. Failing open would make `docker stop`
the cheapest bypass.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_rpc_guard.py::test_an_exhausted_budget_survives_a_process_restart`,
`::test_an_unreachable_ledger_refuses_rather_than_running_unmetered`, and
`tests/test_rpc_sql.py`.

### SPEC-LIM-003 - The flood kill switch

**Requirement.** Every `FloodWaitError`, `SlowModeWaitError`,
`FloodPremiumWaitError` and `PeerFloodError` MUST be recorded in `api_flood_log`
with its timestamp, method and target. Three or more flood events within a
rolling hour, from any process, MUST trip a kill switch that blocks every
Telegram-touching operation in both entrypoints for 24 hours. A `PeerFloodError`
MUST trip it **indefinitely**, cleared only by the account owner through
`just tg-killswitch-clear`.

**Rationale.** Flood *frequency* is what feeds Telegram's server-side risk
score, so the count matters more than any single wait. A `PeerFloodError` is an
escalation rather than a cooldown: Telegram has flagged the account for spam,
and only a person who has checked it with @SpamBot should decide it is healthy.
`FloodPremiumWaitError` does not exist in the pinned telethon 1.36.0, so it is
resolved defensively and also matched by its wire string - a flood that went
uncounted would be a blind spot in the one control that must not have one.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_rpc_guard.py::test_three_flood_waits_in_an_hour_trip_the_switch`
and `::test_peer_flood_trips_the_switch_indefinitely`. What must *not* be
counted is proved by `tests/test_safety.py` -
`test_a_permission_error_is_not_counted_as_a_flood`,
`test_a_real_flood_is_counted` and `test_is_flood_error_returns_a_real_boolean`.
The last of those exists because `is_flood_error` once returned
`describe_telegram_error`'s *message* for eight other conditions; every message
is truthy, so ordinary permission errors were recorded as floods and three in an
hour tripped this switch.

### SPEC-LIM-004 - The quiet window

**Requirement.** Every tool that touches Telegram MUST refuse during
`TG_QUIET_HOURS` (default `01:00-08:00`, in `TG_TIMEZONE`), and `sync_db.py`
MUST refuse to start inside it and stop cleanly when a long run crosses into it.
Database-only tools - `tg_search_local_history`, the persona tools, and
`tg_whoami`'s archive and safety sections - MUST keep working.

**Rationale.** An account that issues API calls uniformly around the clock is
not a person. Stopping a long run is lossless because the Sync Cursor already
makes it resumable, so it costs patience rather than data.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_rpc_guard.py::test_nothing_is_requested_inside_the_quiet_window`
and `::test_a_window_that_wraps_midnight_is_one_window`.

### SPEC-LIM-005 - Jitter is additive and never subtracts

**Requirement.** Every delay constant MUST carry random jitter, and
`jittered(delay)` MUST NEVER return less than `delay`.

**Rationale.** Fixed, perfectly repeating intervals are a bot-detection signal.
But a symmetric formula such as `delay * (1 + random.uniform(-0.25, 0.25))`
returns less than the constant half the time, which is exactly the lowering of
`CHUNK_DELAY_SECONDS` and `SYNC_DIALOG_DELAY_SECONDS` that `AGENTS.md` forbids.
The reviewed constant is a floor, not a midpoint.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_safety.py::test_jitter_never_returns_less_than_the_reviewed_constant`,
a property test over many samples and every delay constant.

### SPEC-LIM-006 - Blacklisted operations

**Requirement.** The methods listed in
[ADR-0009](../20-architecture/adr/0009-groups-and-channels.md) MUST NOT appear
anywhere in the shipped source: member-list scraping, mass invites, joining or
leaving, bulk contact import, bulk forwarding, the peer-harvesting family
(`inputPeerUserFromMessage` and its siblings, `contacts.search`,
`contacts.getLocated`, `contacts.resolvePhone`, `messages.getCommonChats`,
`messages.getMessageReadParticipants`, `messages.getMessageReactionsList`,
`channels.getFullChannel`, `messages.getFullChat`), presence, typing, media
download, channel statistics and every reporting method. A request for one MUST
return a `ToolError` explaining that it is blocked to protect the account.

The `ReadHistoryRequest`, `ReadMentionsRequest`, `ReadDiscussionRequest` and
`ReadMessageContentsRequest` classes are on that list and stay there.
Telethon's `send_read_acknowledge` is **not**, since ADR-0011; it is
constrained by shape instead, exactly as single-contact `ImportContacts` is -
it must appear in exactly one shipped file, in the send path (`SPEC-SND-009`).
Banning the request classes while permitting the helper is what forces the
Channel-versus-User distinction to be read off the entity by Telethon rather
than hand-rolled here.

**Rationale.** These are the operations that get userbot scripts deactivated.
Banning member-list scraping achieves nothing if the same data is assembled one
message at a time, so the harvesting back door is closed with it: doing it one
id at a time is the same thing, slower.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_blacklist.py`, which scans the shipped source for every
name, plus `test_the_only_read_acknowledgment_is_in_the_send_path` and
`test_the_raw_read_history_requests_are_still_banned` for the one shape-
constrained exception.

### SPEC-LIM-007 - A per-process ceiling on Telegram-touching tool calls

**Requirement.** A single MCP server process MUST refuse to touch Telegram after
200 tool calls, returning an `ERROR:` naming the cap and telling the user to
restart deliberately.

**Rationale.** An LLM in a loop calls a tool as fast as the tool permits, and
that is a different failure from ordinary volume: it is a bug, not a workload.
This is the backstop, separate from the rolling budgets, and per-process because
a restart is exactly the deliberate human act it should require.
**Decided by.** [ADR-0009](../20-architecture/adr/0009-groups-and-channels.md).
**Test.** `tests/test_server_limits.py::test_the_per_process_tool_call_ceiling_stops_a_loop`.
