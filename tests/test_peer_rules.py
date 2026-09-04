"""Which peers are archived, and how targets are normalised.

Proves SPEC-SYNC-001.
"""

import pytest
from telethon.tl.types import Channel, User

from tg_ai.tg_client import display_name, is_archivable, normalise_target, peer_label


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
