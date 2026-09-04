"""Which peers are archived, how targets are normalised, and target filtering.

Proves SPEC-SYNC-001 and SPEC-SYNC-006.
"""

import pytest
from telethon.tl.types import Channel, User

from sync_db import select_by_targets
from tg_ai.tg_client import (
    display_name,
    is_archivable,
    matches_target,
    normalise_target,
    peer_label,
)


def make_user(**kwargs) -> User:
    defaults = {
        "id": 111,
        "first_name": "Ann",
        "last_name": None,
        "username": "ann",
        "bot": False,
        "deleted": False,
    }
    return User(**{**defaults, **kwargs})


def test_a_real_person_is_archived():
    assert is_archivable(make_user(), include_bots=False)


def test_bots_are_excluded_by_default():
    assert not is_archivable(make_user(bot=True), include_bots=False)


def test_bots_are_archived_when_explicitly_opted_in():
    assert is_archivable(make_user(bot=True), include_bots=True)


def test_deleted_accounts_are_excluded():
    assert not is_archivable(make_user(deleted=True), include_bots=False)


def test_the_telegram_service_account_is_excluded():
    # 777000 delivers login codes. Archiving it would store one-time passwords.
    assert not is_archivable(make_user(id=777000), include_bots=True)


def test_channels_and_groups_are_excluded():
    channel = Channel(id=5, title="News", photo=None, date=None)
    assert not is_archivable(channel, include_bots=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@ann", "ann"),
        ("  @ann  ", "ann"),
        ("ann", "ann"),
        ("+380501234567", "+380501234567"),
        ("", ""),
    ],
)
def test_targets_are_normalised(raw, expected):
    assert normalise_target(raw) == expected


def test_a_user_always_gets_a_non_empty_label():
    assert display_name(make_user()) == "Ann"
    assert display_name(make_user(first_name=None, last_name=None)) == "@ann"
    assert display_name(make_user(first_name=None, last_name=None, username=None)) == "id:111"


def test_the_label_includes_the_username_when_there_is_a_name():
    assert peer_label(make_user()) == "Ann (@ann)"
    assert peer_label(make_user(first_name=None, last_name=None)) == "@ann"


# --- Target filtering (SPEC-SYNC-006) -------------------------------------


def test_a_target_matches_a_username_with_or_without_the_at_sign():
    user = make_user(username="anna_p")
    assert matches_target(user, "@anna_p")
    assert matches_target(user, "anna_p")


def test_target_matching_is_case_insensitive():
    assert matches_target(make_user(username="anna_p"), "ANNA_P")
    assert matches_target(make_user(first_name="Anna"), "anna")


def test_a_target_matches_a_first_last_or_full_name():
    user = make_user(first_name="Anna", last_name="Petrova", username=None)
    assert matches_target(user, "Anna")
    assert matches_target(user, "Petrova")
    assert matches_target(user, "Anna Petrova")
    # Collapsed whitespace, so a typed double space still matches.
    assert matches_target(user, "Anna  Petrova")


def test_a_target_matches_a_phone_however_it_is_punctuated():
    user = make_user(phone="380501234567")
    assert matches_target(user, "+380501234567")
    assert matches_target(user, "+380 50 123 4567")
    assert matches_target(user, "380501234567")


def test_a_target_matches_a_numeric_id():
    assert matches_target(make_user(id=555), "555")


def test_matching_is_exact_and_never_a_substring():
    # "an" must not pull in every Anna, Ivan and Alexander when the user asked
    # for one person.
    user = make_user(first_name="Anna", last_name="Petrova", username="anna_p")
    assert not matches_target(user, "an")
    assert not matches_target(user, "anna_")
    assert not matches_target(user, "petrov")


def test_an_empty_target_matches_nobody():
    assert not matches_target(make_user(), "")
    assert not matches_target(make_user(), "   ")


def test_select_by_targets_keeps_the_newest_active_dialog_order():
    users = [
        make_user(id=1, first_name="Ann", username="ann"),
        make_user(id=2, first_name="Bob", username="bob"),
        make_user(id=3, first_name="Cid", username="cid"),
    ]
    # Targets given in a different order than the dialog list.
    selected, unmatched = select_by_targets(users, ["cid", "ann"])
    assert [user.id for user in selected] == [1, 3]
    assert unmatched == []


def test_select_by_targets_reports_what_matched_nothing():
    users = [make_user(id=1, username="ann")]
    selected, unmatched = select_by_targets(users, ["ann", "nosuchperson"])
    assert [user.id for user in selected] == [1]
    assert unmatched == ["nosuchperson"]


def test_select_by_targets_never_returns_a_duplicate():
    user = make_user(id=1, first_name="Ann", username="ann")
    # Two targets naming the same person.
    selected, _ = select_by_targets([user], ["ann", "Ann"])
    assert len(selected) == 1


def test_select_by_targets_with_no_match_selects_nothing():
    selected, unmatched = select_by_targets([make_user(id=1, username="ann")], ["zzz"])
    assert selected == []
    assert unmatched == ["zzz"]
