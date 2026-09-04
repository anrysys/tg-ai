---
id: DOC-SRS
title: Software requirements specification
status: active
authority: authoritative
updated: 2026-09-05
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
filling the archive, `PSN` per-Dialog style, `SEC` credentials and locality.

---

## SND - Sending messages

### SPEC-SND-001 - The send guard

**Requirement.** `tg_send_message` MUST NOT send anything to a Peer when all of
the following hold: the Peer is not the account itself, the Peer is absent from
the Peer Index, no message has ever been exchanged with the Peer, and the Peer
is not in the account's contacts. In that case it returns a `WARNING:` naming
`tg_add_contact` as the next step, and sends nothing.

There is deliberately **no** `force` parameter. Overriding the guard requires a
separate, deliberate `tg_add_contact` call.

The Dialog Lookup path (`SPEC-SRCH-006`) MUST NOT be used to resolve a send
target. Resolving the target a second way is one of the bypasses ADR-0005
names explicitly, and a database-backed resolver in the send path is exactly
that. `tg_send_message` may read the Archive only after delivery, and only
through a helper that swallows its own failures (`SPEC-PSN-008`).

**Rationale.** Messaging strangers from a personal account is the primary cause
of `PeerFloodError` and account bans. An agent acting on an ambiguous
instruction must not be able to trigger it in one step.
**Decided by.** [ADR-0005](../20-architecture/adr/0005-hard-block-send-to-unknown-peers.md).
**Test.** Manual: `tg_send_message` to a never-contacted account returns the
warning and the Telegram app shows no sent message. The four conditions are
readable as one short-circuiting expression in `server.tg_send_message`.

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

### SPEC-SND-006 - Cold resolution is a last resort

**Requirement.** Resolving a Target MUST consult the Peer Index before calling
Telegram's username-resolution API.

**Rationale.** `ResolveUsername` for unknown handles is itself rate-limited and
is one of the signals used to detect scripted accounts.
**Decided by.** [ADR-0005](../20-architecture/adr/0005-hard-block-send-to-unknown-peers.md).
**Test.** `tg_client.resolve_peer` returns `(user, True)` from the index before
any `get_entity` call; the index refreshes at most once per `CACHE_TTL_SECONDS`.

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

### SPEC-RCV-003 - Unread listing covers people only

**Requirement.** `tg_get_unread_dialogs` MUST list only 1-on-1 Dialogs with
non-deleted human users, and MUST exclude bots unless `TG_SYNC_INCLUDE_BOTS`
is true. Reading it MUST NOT mark anything as read.

**Rationale.** Marking messages read as a side effect of a status query would
make the user's own Telegram client lie to them.
**Test.** Manual: unread badges in the Telegram app are unchanged after calling.

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

### SPEC-SYNC-001 - Private human chats only

**Requirement.** `sync_db.py` MUST archive only 1-on-1 Dialogs with `User`
peers. It MUST exclude groups, channels, deleted accounts, and Telegram's
service account `777000`. Bots are excluded unless `TG_SYNC_INCLUDE_BOTS` is
true.

**Rationale.** Groups and channels are out of scope and would dominate the
Archive by volume. Account `777000` delivers login codes, and archiving
one-time passwords in plain text is a security defect.
**Test.** `tests/test_peer_rules.py`.

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
