"""Operations that must never appear in this codebase.

Proves SPEC-LIM-006. These are the calls that get userbot scripts deactivated,
so the control is not "we chose not to use them" but "the build fails if they
appear". The scan covers the shipped source only - a test that names a
forbidden identifier in order to forbid it must not trip its own check.

Matching is on exact Telethon request classes and client method names rather
than on prose, so a docstring explaining why reporting is banned does not fail
the build.
"""

import re

import pytest
from source_scan import find_in_sources, project_sources

#: Membership scraping, growth and broadcast (task section 5.1).
MEMBERSHIP_AND_GROWTH = [
    "GetParticipantsRequest",
    "InviteToChannelRequest",
    "AddChatUserRequest",
    "DeleteChatUserRequest",
    "JoinChannelRequest",
    "ImportChatInviteRequest",
    "LeaveChannelRequest",
    "ForwardMessagesRequest",
    "get_participants",
    "iter_participants",
    "forward_messages",
    "kick_participant",
    "edit_admin",
    "edit_permissions",
]

#: The back door around the above: assembling member data one message at a
#: time, or discovering people the account does not know (task section 5.2).
PEER_HARVESTING = [
    "InputPeerUserFromMessage",
    "InputUserFromMessage",
    "InputChannelFromMessage",
    "GetLocatedRequest",
    "ResolvePhoneRequest",
    "GetCommonChatsRequest",
    "GetMessageReadParticipantsRequest",
    "GetMessageReactionsListRequest",
    "GetFullChannelRequest",
    "GetFullChatRequest",
    # channels.getMessages for arbitrary ids, and messages.search scoped to a
    # channel, are enumeration rather than reading. Note this bans the TL
    # classes, not client.get_messages(), which issues messages.getHistory.
    "GetMessagesRequest",
    "SearchRequest",
    "SearchGlobalRequest",
]

#: Presence, media and reporting (task section 5.3).
PRESENCE_MEDIA_AND_REPORTING = [
    "UpdateStatusRequest",
    "SetTypingRequest",
    "GetFileRequest",
    "SaveFilePartRequest",
    "download_media",
    "download_file",
    "download_profile_photo",
    "ReportRequest",
    "ReportSpamRequest",
    "ReportPeerRequest",
    "ReportProfilePhotoRequest",
]

#: Marking anything read. Telethon does not do this on its own, and reading 100
#: messages across several channels instantly is superhuman (SPEC-RCV-003).
READ_RECEIPTS = [
    "send_read_acknowledge",
    "ReadHistoryRequest",
    "ReadMentionsRequest",
    "ReadDiscussionRequest",
    "ReadMessageContentsRequest",
]

#: Whole API namespaces with no legitimate use here.
FORBIDDEN_NAMESPACES = [
    "functions.stats",
    "functions.phone",
    "tl.functions.stats",
    "tl.functions.phone",
]

BLACKLIST = (
    MEMBERSHIP_AND_GROWTH
    + PEER_HARVESTING
    + PRESENCE_MEDIA_AND_REPORTING
    + READ_RECEIPTS
    + FORBIDDEN_NAMESPACES
)


@pytest.mark.parametrize("name", BLACKLIST)
def test_a_blacklisted_operation_is_absent_from_the_source(name):
    hits = find_in_sources(name)
    assert not hits, (
        f"{name!r} appears in the shipped source:\n  "
        + "\n  ".join(hits)
        + "\nThis operation is permanently banned (SPEC-LIM-006). If it looks "
        "necessary, it is not - re-read ADR-0009 before removing this test."
    )


def test_the_blacklist_actually_covers_something_in_every_category():
    # A guard on the guard: an empty category would pass silently.
    for category in (
        MEMBERSHIP_AND_GROWTH,
        PEER_HARVESTING,
        PRESENCE_MEDIA_AND_REPORTING,
        READ_RECEIPTS,
        FORBIDDEN_NAMESPACES,
    ):
        assert category


def test_the_scan_looks_at_the_real_entrypoints():
    # If project_sources() ever returned nothing, every test above would pass
    # while checking nothing at all.
    scanned = {path.name for path in project_sources()}
    assert {"server.py", "sync_db.py", "auth.py"} <= scanned
    assert "test_blacklist.py" not in scanned


# --- Single-contact import is allowed; bulk is not ------------------------


def test_contacts_are_imported_one_at_a_time():
    # ImportContacts is not banned outright: tg_add_contact uses it for exactly
    # one person, which is the sanctioned way to unblock a send. A list of more
    # than one is mass contact import, which is not.
    sources = [
        path.read_text(encoding="utf-8")
        for path in project_sources()
        if "ImportContactsRequest" in path.read_text(encoding="utf-8")
    ]
    assert sources, "tg_add_contact should still import a single contact"

    for text in sources:
        for call in re.findall(r"ImportContactsRequest\((.*?)\n\s*\)\n", text, re.S):
            # Exactly one InputPhoneContact per request, never a comprehension
            # or a splat that could carry a list.
            assert call.count("InputPhoneContact") == 1
            assert "for " not in call
            assert "*" not in call
