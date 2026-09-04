"""Search SQL construction. Proves SPEC-SRCH-004.

Built without a database on purpose: the statement shape is what determines
whether the archive is searched safely and quickly.
"""

from tg_ai.db import build_search_query


def test_both_strategies_are_produced():
    fulltext, trigram = build_search_query(by_username=False)
    assert "websearch_to_tsquery" in fulltext
    assert "ILIKE" in trigram


def test_the_simple_configuration_is_used_for_multilingual_history():
    fulltext, _ = build_search_query(by_username=False)
    assert "websearch_to_tsquery('simple', $1)" in fulltext
    assert "'russian'" not in fulltext and "'english'" not in fulltext


def test_results_are_ranked_then_ordered_by_recency():
    fulltext, _ = build_search_query(by_username=False)
    assert "ORDER BY ts_rank" in fulltext
    assert "m.date DESC" in fulltext


def test_username_filter_is_added_to_both_statements():
    fulltext, trigram = build_search_query(by_username=True)
    assert "lower(d.username) = lower($3)" in fulltext
    assert "lower(d.username) = lower($3)" in trigram


def test_username_filter_is_absent_when_not_requested():
    fulltext, trigram = build_search_query(by_username=False)
    assert "$3" not in fulltext
    assert "$3" not in trigram


def test_values_are_never_interpolated_into_the_statement():
    # Only the optional username predicate is built by string formatting, and
    # it inserts a placeholder, never a value. Everything else is bound.
    for by_username in (True, False):
        for statement in build_search_query(by_username=by_username):
            assert "'%' || $1 || '%'" in statement or "$1" in statement
            assert "format(" not in statement


def test_both_statements_take_the_same_parameters_in_the_same_order():
    fulltext, trigram = build_search_query(by_username=True)
    for statement in (fulltext, trigram):
        assert statement.index("$1") < statement.index("$3")
        assert "LIMIT $2" in statement
