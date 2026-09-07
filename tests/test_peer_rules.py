"""Which peers are archived, how targets are normalised, and target filtering.

Proves SPEC-SYNC-001 and SPEC-SYNC-006.
"""

import pytest
from telethon.tl.types import Channel, Chat, ChatAdminRights, ChatBannedRights, User

from sync_db import select_by_targets
from tg_ai.tg_client import (
    can_post,
    contacts_hash,
    display_name,
    is_archivable,
    is_member,
    matches_target,
    normalise_target,
    peer_label,
    peer_type,
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


def test_channels_and_groups_are_excluded_from_a_default_sync():
    channel = Channel(id=5, title="News", photo=None, date=None, broadcast=True)
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


# --- Peer Types (SPEC-SYNC-001, SPEC-SYNC-007) ----------------------------


def make_chat(**kwargs) -> Chat:
    defaults = {
        "id": 200,
        "title": "Team",
        "photo": None,
        "participants_count": 4,
        "date": None,
        "version": 1,
    }
    return Chat(**{**defaults, **kwargs})


def make_channel(**kwargs) -> Channel:
    defaults = {"id": 300, "title": "News", "photo": None, "date": None}
    return Channel(**{**defaults, **kwargs})


def admin_rights(**kwargs) -> ChatAdminRights:
    defaults = dict.fromkeys(
        (
            "change_info",
            "post_messages",
            "edit_messages",
            "delete_messages",
            "ban_users",
            "invite_users",
            "pin_messages",
            "add_admins",
        ),
        False,
    )
    return ChatAdminRights(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("entity", "expected"),
    [
        (make_user(), "user"),
        (make_chat(), "group"),
        (make_channel(megagroup=True), "group"),
        (make_channel(broadcast=True), "channel"),
        # A gigagroup carries megagroup=True, but ordinary members cannot write
        # in one, and writability is the distinction that matters.
        (make_channel(megagroup=True, gigagroup=True), "channel"),
    ],
)
def test_peer_types_are_classified_by_what_you_can_do_in_them(entity, expected):
    assert peer_type(entity) == expected


def test_an_unknown_entity_is_not_a_peer_at_all():
    assert peer_type(object()) is None


def test_a_full_sync_still_archives_people_only():
    # SPEC-SYNC-001: groups and channels are opt-in through --targets. A full
    # run must not quietly start pulling them, or every routine sync becomes a
    # large read against monitored endpoints.
    for entity in (make_chat(), make_channel(megagroup=True), make_channel(broadcast=True)):
        assert not is_archivable(entity, include_bots=True)


def test_groups_are_archivable_only_when_explicitly_included():
    assert is_archivable(make_chat(), include_bots=False, include_groups=True)
    assert is_archivable(make_channel(broadcast=True), include_bots=False, include_groups=True)


def test_a_group_the_account_has_left_is_not_archivable():
    left = make_channel(megagroup=True, left=True)
    assert not is_archivable(left, include_bots=True, include_groups=True)
    assert not is_member(left)


def test_a_migrated_chat_is_not_a_live_group():
    # Its history moved to a Channel; writing to the husk does nothing.
    assert not is_member(make_chat(migrated_to=object()))


# --- Who may be written to (SPEC-SND-001) ---------------------------------


def test_a_channel_subscriber_may_not_post():
    assert not can_post(make_channel(broadcast=True))


def test_a_channel_admin_with_post_rights_may_post():
    channel = make_channel(broadcast=True, admin_rights=admin_rights(post_messages=True))
    assert can_post(channel)


def test_a_channel_admin_without_post_rights_may_not_post():
    # Being an admin is not the same as being allowed to publish.
    channel = make_channel(broadcast=True, admin_rights=admin_rights(change_info=True))
    assert not can_post(channel)


def test_an_ordinary_group_member_may_post():
    assert can_post(make_chat())
    assert can_post(make_channel(megagroup=True))


def test_a_group_that_bans_sending_is_writable_only_by_an_admin():
    muted = make_channel(
        megagroup=True,
        default_banned_rights=ChatBannedRights(until_date=None, send_messages=True),
    )
    assert not can_post(muted)

    moderator = make_channel(
        megagroup=True,
        default_banned_rights=ChatBannedRights(until_date=None, send_messages=True),
        admin_rights=admin_rights(pin_messages=True),
    )
    assert can_post(moderator)


def test_a_group_you_have_left_may_not_be_posted_to():
    assert not can_post(make_channel(megagroup=True, left=True))


# --- The contacts.getContacts hash (SPEC-SND-006) -------------------------


def test_an_empty_contact_set_hashes_to_the_documented_zero():
    assert contacts_hash(0, set()) == 0
    assert contacts_hash(372, set()) == 0


@pytest.mark.parametrize("size", [1, 2, 17, 500])
def test_the_hash_always_fits_a_signed_long(size):
    # Regression: contacts.getContacts declares `hash` as a signed 64-bit
    # long, and Telethon packs it with struct '<q'. Returning the raw unsigned
    # accumulator raises struct.error for roughly half of all inputs - which is
    # exactly what happened the first time this ran against a real account.
    ids = {i * 7919 + 1 for i in range(size)}
    value = contacts_hash(size, ids)
    assert -(2**63) <= value < 2**63


def test_the_hash_depends_on_saved_count_not_on_the_number_of_ids():
    # Regression: the documented algorithm folds in the previous response's
    # saved_count, which on a real account differs from the number of users
    # returned - 372 against 502 on the account this was built for. Using the
    # id count produces a hash that simply never matches, so the caching does
    # nothing and there is no error to notice.
    ids = {10, 20, 30}
    assert contacts_hash(372, ids) != contacts_hash(len(ids), ids)


def test_the_hash_does_not_depend_on_iteration_order():
    assert contacts_hash(3, {30, 10, 20}) == contacts_hash(3, [10, 20, 30])
    assert contacts_hash(3, [30, 20, 10]) == contacts_hash(3, [10, 20, 30])


def test_a_changed_contact_list_changes_the_hash():
    assert contacts_hash(3, {1, 2, 3}) != contacts_hash(3, {1, 2, 4})
