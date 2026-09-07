"""Peer resolution for groups and channels.

Proves SPEC-SND-006: a Group or Channel is returned only when it came from the
Peer Index, and a caller that admits non-user Peers gets no cold-resolution path
at all. Cold-resolving a channel the account has never joined is textbook
scraper behaviour, so the important assertion in most of these tests is that
**no API call was made**, not merely that an error came back.
"""

import pytest
from telethon.tl.types import Channel, Chat, User

from tg_ai.safety import ToolError
from tg_ai.tg_client import (
    PEER_TYPE_CHANNEL,
    PEER_TYPE_GROUP,
    PEER_TYPE_USER,
    resolve_peer,
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


def make_channel(**kwargs) -> Channel:
    defaults = {"id": 300, "title": "News", "photo": None, "date": None, "broadcast": True}
    return Channel(**{**defaults, **kwargs})


def make_supergroup(**kwargs) -> Channel:
    """A megagroup. Unlike a basic Chat it can carry a username."""
    defaults = {
        "id": 400,
        "title": "Team",
        "photo": None,
        "date": None,
        "megagroup": True,
    }
    return Channel(**{**defaults, **kwargs})


def make_group(**kwargs) -> Chat:
    defaults = {
        "id": 200,
        "title": "Team",
        "photo": None,
        "participants_count": 4,
        "date": None,
        "version": 1,
    }
    return Chat(**{**defaults, **kwargs})


class ForbiddenClient:
    """A client that must never be touched.

    Every method records itself and then fails loudly, so a test asserting
    "no API call" fails whether it checks the recording or just runs.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _forbid(self, name: str):
        self.calls.append(name)
        raise AssertionError(f"{name} was called; this path must make no API call")

    async def get_entity(self, *args, **kwargs):
        self._forbid("get_entity")

    async def get_input_entity(self, *args, **kwargs):
        self._forbid("get_input_entity")

    async def get_me(self, *args, **kwargs):
        self._forbid("get_me")

    async def __call__(self, *args, **kwargs):
        self._forbid("__call__")


class ResolvingClient(ForbiddenClient):
    """A client whose cold lookup returns ``entity``, recording that it ran."""

    def __init__(self, entity) -> None:
        super().__init__()
        self._entity = entity

    async def get_entity(self, key):
        self.calls.append("get_entity")
        return self._entity


class FakeIndex:
    """The Peer Index, reduced to the one thing resolve_peer asks it."""

    def __init__(self, *peers) -> None:
        self._peers = list(peers)

    async def lookup(self, target: str):
        key = target.lstrip("@").lower()
        for peer in self._peers:
            if key == str(peer.id):
                return peer
            username = getattr(peer, "username", None)
            if username and key == username.lower():
                return peer
        return None


ANY_PEER = (PEER_TYPE_USER, PEER_TYPE_GROUP, PEER_TYPE_CHANNEL)


# --- The structural rule ---------------------------------------------------


async def test_a_channel_not_in_the_index_is_refused_without_any_api_call():
    # The whole point. Before this guard, `--dialog @some_public_channel` would
    # perform a cold contacts.ResolveUsername on a channel the account has
    # never joined, and only then reject the result - the request having
    # already been made and counted against the account.
    client = ForbiddenClient()
    index = FakeIndex()

    with pytest.raises(ToolError) as caught:
        await resolve_peer(client, index, "@some_public_channel", allow=ANY_PEER)

    assert client.calls == []
    message = str(caught.value)
    assert "already a member" in message
    assert "Nothing was requested from Telegram" in message


async def test_a_caller_that_admits_groups_never_gets_a_cold_lookup():
    # The rule is structural rather than per-call-site: cold resolution exists
    # only for callers that will accept nothing but a User, so it is impossible
    # to reach a channel through it.
    for allow in [
        (PEER_TYPE_GROUP,),
        (PEER_TYPE_CHANNEL,),
        (PEER_TYPE_USER, PEER_TYPE_GROUP),
        ANY_PEER,
    ]:
        client = ForbiddenClient()
        with pytest.raises(ToolError):
            await resolve_peer(client, FakeIndex(), "anything", allow=allow)
        assert client.calls == []


async def test_a_group_in_the_index_is_returned_and_marked_known():
    group = make_group()
    client = ForbiddenClient()

    peer, known_locally = await resolve_peer(client, FakeIndex(group), "200", allow=ANY_PEER)

    # A Peer Index hit is itself the proof of membership: it came from GetDialogs.
    assert peer is group
    assert known_locally is True
    assert client.calls == []


async def test_a_channel_in_the_index_is_returned():
    channel = make_channel(username="news")
    peer, known_locally = await resolve_peer(
        ForbiddenClient(), FakeIndex(channel), "@news", allow=ANY_PEER
    )
    assert peer is channel
    assert known_locally is True


# --- The default keeps every existing call site user-only ------------------


async def test_the_default_is_user_only_so_dialog_stays_user_only():
    # sync_db.py --dialog and tg_add_contact pass no `allow`, so a group in the
    # index is still refused for them - without an API call.
    client = ForbiddenClient()
    with pytest.raises(ToolError) as caught:
        await resolve_peer(client, FakeIndex(make_supergroup(username="team")), "@team")

    assert client.calls == []
    assert "is a group" in str(caught.value)


async def test_a_user_not_in_the_index_is_still_resolved_cold():
    # Unchanged behaviour, and the reason cold resolution exists at all:
    # tg_add_contact has to reach someone the account has never messaged.
    stranger = make_user(id=999, username="newperson")
    client = ResolvingClient(stranger)

    peer, known_locally = await resolve_peer(client, FakeIndex(), "@newperson")

    assert peer is stranger
    assert known_locally is False
    assert client.calls == ["get_entity"]


async def test_a_cold_lookup_that_turns_out_to_be_a_channel_is_refused():
    # Reachable only for a user-only caller, and the result is refused rather
    # than returned - and never added to the Peer Index.
    client = ResolvingClient(make_channel(username="surprise"))

    with pytest.raises(ToolError) as caught:
        await resolve_peer(client, FakeIndex(), "@surprise")

    assert "group or channel" in str(caught.value)


async def test_a_user_in_the_index_still_resolves_without_a_call():
    user = make_user()
    peer, known_locally = await resolve_peer(ForbiddenClient(), FakeIndex(user), "@ann")
    assert peer is user
    assert known_locally is True


async def test_an_empty_target_is_refused_before_anything_else():
    client = ForbiddenClient()
    with pytest.raises(ToolError):
        await resolve_peer(client, FakeIndex(), "   ", allow=ANY_PEER)
    assert client.calls == []
