"""The SQL behind the RPC budget, the flood log and the kill switch.

Proves the persistence half of SPEC-LIM-002 and SPEC-LIM-003. Asserts on the
statement text and on `sql/schema.sql`, in the same style as
`tests/test_persona_sql.py`, because the suite never opens a database.
"""

import re

from tg_ai import db, safety
from tg_ai.config import SCHEMA_PATH

SCHEMA = SCHEMA_PATH.read_text(encoding="utf-8")

#: The schema with `--` comment lines removed. The file explains itself at
#: length, and prose that quotes DDL would otherwise be counted as DDL.
STATEMENTS = "\n".join(line for line in SCHEMA.splitlines() if not line.lstrip().startswith("--"))

BUDGET_STATEMENTS = (
    db._RPC_COUNTS_SQL,
    db._COUNT_CALLS_SQL,
    db._COUNT_FLOODS_SQL,
)


def test_every_budget_window_is_rolling_never_a_calendar_bucket():
    # A calendar bucket resets at the top of the hour, which would let an agent
    # spend the whole hourly budget at 10:59 and the whole of it again at 11:01.
    for statement in BUDGET_STATEMENTS:
        assert "now() -" in statement
        assert "date_trunc" not in statement


def test_budget_windows_bind_their_interval_rather_than_formatting_it():
    # The window length reaches these statements from Python. AGENTS.md forbids
    # interpolating a value into SQL, and make_interval takes it as a parameter.
    assert "make_interval(secs => $2::double precision)" in db._COUNT_CALLS_SQL
    assert "make_interval(secs => $1::double precision)" in db._COUNT_FLOODS_SQL


def test_no_api_safety_statement_uses_python_string_formatting():
    for name, statement in vars(db).items():
        if not name.startswith("_") or not isinstance(statement, str):
            continue
        if "api_call_log" not in statement and "api_flood_log" not in statement:
            continue
        assert "%s" not in statement
        assert not re.search(r"\{[a-z_]*\}", statement)


def test_the_hourly_and_daily_counts_come_from_one_round_trip():
    # Two queries would be two chances to disagree, and the limiter runs this
    # before every single request.
    assert db._RPC_COUNTS_SQL.count("SELECT") == 1
    assert "interval '1 hour'" in db._RPC_COUNTS_SQL
    assert "interval '24 hours'" in db._RPC_COUNTS_SQL


def test_the_kill_switch_reads_the_most_recent_uncleared_trip():
    assert "cleared_at IS NULL" in db._ACTIVE_KILL_SWITCH_SQL
    assert "ORDER BY tripped_at DESC" in db._ACTIVE_KILL_SWITCH_SQL
    assert "LIMIT 1" in db._ACTIVE_KILL_SWITCH_SQL


def test_expiry_is_decided_in_python_not_in_sql():
    # "expires_at IS NULL means indefinite" must live in exactly one place, and
    # that place is safety.kill_switch_message, which is unit-testable.
    assert "expires_at >" not in db._ACTIVE_KILL_SWITCH_SQL
    assert "now()" not in db._ACTIVE_KILL_SWITCH_SQL


def test_clearing_the_kill_switch_records_who_did_it():
    assert "cleared_by = $1" in db._CLEAR_KILL_SWITCH_SQL
    assert "cleared_at IS NULL" in db._CLEAR_KILL_SWITCH_SQL


def test_trips_are_appended_never_overwritten():
    # The table is history. An UPDATE would lose the record of how often the
    # account has been in trouble, which is the interesting part.
    assert db._TRIP_KILL_SWITCH_SQL.strip().startswith("INSERT INTO api_kill_switch")
    assert "ON CONFLICT" not in db._TRIP_KILL_SWITCH_SQL


# --- Schema ---------------------------------------------------------------


def test_every_rolling_window_column_is_indexed():
    # A rolling window over an unindexed timestamp degrades into a sequential
    # scan that grows with the log, and this runs before every request.
    assert "api_call_log_scope_time_idx" in SCHEMA
    assert "api_call_log_scope_key_time_idx" in SCHEMA
    assert "api_flood_log_time_idx" in SCHEMA


def test_the_safety_tables_exist_and_stay_idempotent():
    for table in ("api_call_log", "api_flood_log", "api_kill_switch", "client_identity"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA
    # The whole file is re-executed on every start by db.ensure_schema, so
    # every statement in it has to tolerate already having been applied.
    assert STATEMENTS.count("CREATE TABLE ") == STATEMENTS.count("CREATE TABLE IF NOT EXISTS ")
    assert STATEMENTS.count("CREATE INDEX ") == STATEMENTS.count("CREATE INDEX IF NOT EXISTS ")


def test_every_alter_is_idempotent_too():
    # Postgres has no ADD CONSTRAINT IF NOT EXISTS, so a constraint has to be
    # wrapped in a DO block that swallows duplicate_object. Without that, the
    # second `just db-schema` on an existing archive fails.
    for line in STATEMENTS.splitlines():
        stripped = line.strip()
        if not stripped.startswith("ALTER TABLE"):
            continue
        assert "ADD COLUMN IF NOT EXISTS" in stripped or "ADD CONSTRAINT" in stripped
    if "ADD CONSTRAINT" in STATEMENTS:
        assert "WHEN duplicate_object THEN NULL" in STATEMENTS


def test_the_peer_type_column_is_added_by_alter_not_by_the_create():
    # A column added inside CREATE TABLE IF NOT EXISTS silently never appears
    # on an archive that already exists, and this file has no migration runner.
    assert "ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS peer_type" in STATEMENTS
    create = STATEMENTS[STATEMENTS.index("CREATE TABLE IF NOT EXISTS dialogs") :]
    create = create[: create.index(");")]
    assert "peer_type" not in create


def test_peer_type_is_constrained_to_the_three_known_kinds():
    assert "peer_type IN ('user', 'group', 'channel')" in STATEMENTS


def test_sender_id_carries_the_warning_that_it_is_not_a_peer():
    # The next person to read this schema will otherwise assume it is a foreign
    # key to a resolvable person, which is exactly the mistake SPEC-SND-008
    # exists to prevent.
    assert "COMMENT ON COLUMN messages.sender_id" in STATEMENTS
    assert "never a send target" in STATEMENTS


def test_the_kill_switch_allows_an_indefinite_trip():
    # A PeerFloodError trip has no expiry, so the column must be nullable.
    match = re.search(r"CREATE TABLE IF NOT EXISTS api_kill_switch \((.*?)\n\);", SCHEMA, re.S)
    assert match is not None
    # No NOT NULL: a PeerFloodError trip is indefinite and stores no expiry.
    assert re.search(r"expires_at\s+TIMESTAMPTZ,\s*$", match.group(1), re.M)


def test_client_identity_holds_exactly_one_row():
    match = re.search(r"CREATE TABLE IF NOT EXISTS client_identity \((.*?)\n\);", SCHEMA, re.S)
    assert match is not None
    assert "CHECK (id = 1)" in match.group(1)


def test_the_scope_names_have_exactly_one_home():
    # safety defines them; db and tg_client import them. A second spelling
    # anywhere would silently split one budget into two that never meet.
    assert safety.RPC_SCOPE == "rpc"
    assert safety.GROUP_READ_SCOPE == "group_read"
    assert db.RPC_SCOPE is safety.RPC_SCOPE
