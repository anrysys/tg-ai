"""Dialog Persona SQL. Proves SPEC-PSN-001, SPEC-PSN-003, SPEC-PSN-004, SPEC-SRCH-006.

Built without a database on purpose, like ``test_search_sql.py``: the statement
shape is what determines whether a Persona survives a resync, whether it can
feed on its own output, and whether a Target can reach rows it should not.
"""

import re
from pathlib import Path

import pytest

from tg_ai import db
from tg_ai.config import SCHEMA_PATH

SCHEMA = Path(SCHEMA_PATH).read_text(encoding="utf-8")

PERSONA_STATEMENTS = {
    "select": db._SELECT_PERSONA_SQL,
    "insert": db._INSERT_PERSONA_SQL,
    "update": db._UPDATE_PERSONA_SQL,
    "rebaseline": db._REBASELINE_PERSONA_SQL,
    "count_since": db._COUNT_OUTGOING_SINCE_SQL,
    "sample": db._SELECT_OUTGOING_SQL,
    "max_outgoing": db._MAX_OUTGOING_SQL,
    "lookup": db._LOOKUP_DIALOG_SQL,
    "overview": db._PERSONA_OVERVIEW_SQL,
}


# --- SPEC-PSN-003: the analysis can never feed on its own output ----------


def test_persona_update_never_touches_the_baseline_column():
    """The anti-feedback-loop invariant, enforced by the statement text.

    Every message this server sends is archived with ``is_outgoing`` set and is
    indistinguishable from one the account owner typed. If an update could move
    the baseline, a Persona would converge on a model of its own output within a
    few refreshes - and would read as more self-consistent, so the drift would
    be invisible.
    """
    assert "baseline_message_id" not in db._UPDATE_PERSONA_SQL


def test_only_the_rebaseline_statement_moves_the_baseline():
    movers = [
        name
        for name, sql in PERSONA_STATEMENTS.items()
        if re.search(r"baseline_message_id\s*=", sql)
    ]
    # The insert sets it once at creation; rebaseline is the only mover after.
    assert set(movers) <= {"rebaseline"}


def test_the_rebaseline_statement_never_moves_the_baseline_backwards():
    assert "GREATEST(baseline_message_id" in db._REBASELINE_PERSONA_SQL


def test_the_sample_query_can_be_capped_at_the_baseline():
    assert "message_id <= $3" in db._SELECT_OUTGOING_SQL


# --- SPEC-PSN-002: only the account's own words are ever analysed ---------


@pytest.mark.parametrize("name", ["sample", "count_since", "max_outgoing"])
def test_statements_reading_messages_are_outgoing_only(name):
    assert "is_outgoing" in PERSONA_STATEMENTS[name]


def test_the_sample_query_skips_messages_with_no_text():
    assert "text IS NOT NULL" in db._SELECT_OUTGOING_SQL
    assert "text <> ''" in db._SELECT_OUTGOING_SQL


def test_drift_is_counted_never_subtracted_from_message_ids():
    # Ids have gaps from deletions and service messages, so max_id - analysed_id
    # is not a count of messages.
    assert "count(*)" in db._COUNT_OUTGOING_SINCE_SQL


# --- SPEC-PSN-004: a Persona is never silently replaced -------------------


def test_persona_insert_never_overwrites():
    assert "ON CONFLICT (chat_id) DO NOTHING" in db._INSERT_PERSONA_SQL
    assert "DO UPDATE" not in db._INSERT_PERSONA_SQL


def test_insert_and_update_report_whether_a_row_was_touched():
    assert "RETURNING chat_id" in db._INSERT_PERSONA_SQL
    assert "RETURNING chat_id" in db._UPDATE_PERSONA_SQL


# --- SPEC-SRCH-006: Dialog Lookup -----------------------------------------


def test_dialog_lookup_binds_every_value_and_formats_nothing():
    sql = db.build_dialog_lookup_query()
    assert "format(" not in sql
    assert "%" not in sql
    assert "{" not in sql and "}" not in sql
    for placeholder in ("$1", "$2", "$3", "$4"):
        assert placeholder in sql


def test_dialog_lookup_never_matches_every_dialog_on_a_null_phone():
    """Both phone guards must be present.

    Without them, a non-numeric target reduces the phone predicate to
    ``'' = ''`` for every row that stores no phone, and the lookup silently
    returns the entire archive instead of nothing.
    """
    sql = db.build_dialog_lookup_query()
    assert "d.phone IS NOT NULL" in sql
    assert "$3 <> ''" in sql


def test_dialog_lookup_matches_exactly_never_as_a_substring():
    sql = db.build_dialog_lookup_query()
    assert "ILIKE" not in sql.upper()
    assert "LIKE" not in sql.upper().replace("UNLIKE", "")


def test_dialog_lookup_collapses_whitespace_in_names():
    assert r"'\s+', ' ', 'g'" in db.build_dialog_lookup_query()


def test_dialog_lookup_is_limited():
    assert "LIMIT $4" in db.build_dialog_lookup_query()


def test_dialog_lookup_reports_whether_a_persona_exists():
    assert "has_persona" in db.build_dialog_lookup_query()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("@Ivan", ("ivan", None, "")),
        ("ivan", ("ivan", None, "")),
        ("771234", ("771234", 771234, "771234")),
        ("+380 50 123 45 67", ("+380 50 123 45 67".strip().casefold(), None, "380501234567")),
        ("  Anna   Petrova ", ("anna petrova", None, "")),
        ("", ("", None, "")),
        ("   ", ("", None, "")),
    ],
)
def test_dialog_lookup_keys_normalise_a_target(target, expected):
    assert db.dialog_lookup_keys(target) == expected


def test_a_numeric_target_offers_both_the_id_and_the_phone_branch():
    name_key, chat_id, digits = db.dialog_lookup_keys("380501234567")
    assert chat_id == 380501234567
    assert digits == "380501234567"
    assert name_key == "380501234567"


def test_a_non_numeric_target_disables_the_id_branch_by_binding_none():
    _, chat_id, digits = db.dialog_lookup_keys("anna")
    assert chat_id is None
    assert digits == ""


# --- SPEC-PSN-001: the schema itself ---------------------------------------


def test_persona_table_is_separate_from_dialogs():
    """A Persona must survive `just tg-sync`.

    ``_UPSERT_DIALOG_SQL`` rewrites every column it names on every sync, so a
    Persona stored on ``dialogs`` would be silently destroyed by a resync.
    """
    assert "CREATE TABLE IF NOT EXISTS dialog_personas" in SCHEMA
    for column in ("addressing", "tone", "relationship", "metrics", "baseline_message_id"):
        assert column not in db._UPSERT_DIALOG_SQL


def test_the_dialog_upsert_still_touches_only_sync_columns():
    updated = db._UPSERT_DIALOG_SQL.split("DO UPDATE SET", 1)[1]
    assert set(re.findall(r"(\w+)\s*=\s*EXCLUDED", updated)) == {
        "username",
        "first_name",
        "last_name",
        "phone",
        "is_contact",
    }


def test_persona_table_cascades_from_dialogs():
    assert "REFERENCES dialogs (chat_id) ON DELETE CASCADE" in SCHEMA


def test_persona_text_fields_carry_length_checks():
    for column in ("addressing", "tone", "relationship", "notes"):
        assert re.search(rf"char_length\({column}\)", SCHEMA), column


def test_the_analysed_window_cannot_start_below_its_baseline():
    assert "CHECK (analysed_message_id >= baseline_message_id)" in SCHEMA


def test_python_caps_are_strictly_tighter_than_the_sql_checks():
    """So a bad field is an actionable sentence, never a raw asyncpg error."""
    from tg_ai.safety import PERSONA_FIELD_LIMITS

    for column, python_limit in PERSONA_FIELD_LIMITS.items():
        match = re.search(rf"char_length\({column}\)[^)]*?(\d+)\s*\)", SCHEMA)
        assert match, column
        assert python_limit < int(match.group(1)), column


def test_the_schema_stays_idempotent():
    # ensure_schema runs this file on every server start and every sync.
    assert "CREATE TABLE dialog_personas" not in SCHEMA
