"""Per-process limits enforced in the MCP server itself.

Proves SPEC-LIM-007. This is the first test in the suite to import `server`,
which is safe: importing it registers the FastMCP tools and nothing else. No
tool here reaches Telegram, because the ceiling is checked before the client is
ever touched.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.types import Channel, ChatAdminRights, User

import server
from tg_ai import db
from tg_ai.safety import (
    GROUP_READ_COOLDOWN_SECONDS,
    GROUP_READS_PER_DAY,
    MAX_TELEGRAM_TOOL_CALLS,
    ToolError,
)


@pytest.fixture
def fresh_process(monkeypatch):
    """Reset the per-process counter, as a newly spawned server would have."""
    monkeypatch.setattr(server, "_telegram_tool_calls", 0)
    return server


async def test_the_per_process_tool_call_ceiling_stops_a_loop(fresh_process, monkeypatch):
    # An LLM in a loop calls a tool as fast as the tool permits. That is a bug
    # rather than a workload, so the backstop is a hard stop that needs a
    # deliberate restart - not something that heals on its own.
    monkeypatch.setattr(server, "_telegram_tool_calls", MAX_TELEGRAM_TOOL_CALLS)

    with pytest.raises(ToolError) as caught:
        await server.telegram()

    message = str(caught.value)
    assert str(MAX_TELEGRAM_TOOL_CALLS) in message
    assert "Restart the MCP server" in message
    assert "tg_search_local_history" in message


async def test_the_ceiling_is_checked_before_anything_touches_telegram(fresh_process, monkeypatch):
    # If the counter were checked after connecting, the ceiling would still
    # cost a connection per call - which is the traffic it exists to stop.
    monkeypatch.setattr(server, "_telegram_tool_calls", MAX_TELEGRAM_TOOL_CALLS + 5)

    def explode(*args, **kwargs):
        raise AssertionError("the client must not be built past the ceiling")

    monkeypatch.setattr(server, "build_client", explode)
    monkeypatch.setattr(server, "_client", None)

    with pytest.raises(ToolError):
        await server.telegram()


async def test_the_counter_is_per_process_not_shared(fresh_process):
    # Deliberately not persisted, unlike the rolling budgets: this measures one
    # runaway loop, and a restart is the intended way to clear it.
    assert server._telegram_tool_calls == 0


# --- The group and channel read budget (SPEC-RCV-003) ---------------------


class FakeLedger:
    """Stands in for the persisted ledger, and outlives the "process"."""

    def __init__(self) -> None:
        self.last_read: datetime | None = None
        self.reads_today = 0
        self.recorded: list[tuple[str, str]] = []

    async def last_call_for_key(self, scope: str, key: str) -> datetime | None:
        return self.last_read

    async def count_calls(self, scope: str, window_seconds: float) -> int:
        return self.reads_today

    async def record_call(self, scope: str, key: str) -> None:
        self.recorded.append((scope, key))
        self.reads_today += 1
        self.last_read = datetime.now(UTC)


@pytest.fixture
def ledger(monkeypatch):
    """Point the server's budget helpers at an in-memory ledger."""
    fake = FakeLedger()

    async def fake_database():
        return object()

    monkeypatch.setattr(server, "database", fake_database)
    monkeypatch.setattr(server.db, "PostgresRpcLedger", lambda pool: fake)
    return fake


async def test_a_fresh_target_may_be_read(ledger):
    await server.check_group_read_budget(-100123, "Some Group")


async def test_a_target_read_moments_ago_is_refused(ledger):
    ledger.last_read = datetime.now(UTC) - timedelta(seconds=10)

    with pytest.raises(ToolError) as caught:
        await server.check_group_read_budget(-100123, "Some Group")

    message = str(caught.value)
    assert "cooldown" in message
    assert "Do not poll" in message
    assert "Nothing was requested" in message


async def test_the_cooldown_lapses_once_it_has_elapsed(ledger):
    ledger.last_read = datetime.now(UTC) - timedelta(seconds=GROUP_READ_COOLDOWN_SECONDS + 1)
    await server.check_group_read_budget(-100123, "Some Group")


async def test_the_cooldown_survives_a_process_restart(ledger):
    # The reason this state is in PostgreSQL rather than in a Python variable.
    # An MCP stdio server is respawned every time the user reopens their
    # editor, so an in-memory cooldown is cleared by the very thing an agent
    # stuck in a loop is most likely to trigger.
    await server.record_group_read(-100123)
    assert ledger.recorded == [("group_read", "-100123")]

    # Throw the "process" away: reset every module-level counter the server
    # holds, exactly as a fresh interpreter would. The ledger is what persists.
    server._telegram_tool_calls = 0
    server._client = None
    server._index = None

    with pytest.raises(ToolError) as caught:
        await server.check_group_read_budget(-100123, "Some Group")
    assert "cooldown" in str(caught.value)


async def test_the_daily_cap_refuses_once_it_is_used_up(ledger):
    ledger.reads_today = GROUP_READS_PER_DAY

    with pytest.raises(ToolError) as caught:
        await server.check_group_read_budget(-100999, "Another Group")

    message = str(caught.value)
    assert f"{GROUP_READS_PER_DAY}" in message
    assert "Nothing was requested" in message


async def test_recording_a_read_never_breaks_the_read_that_already_happened(monkeypatch):
    # The message has already been fetched by the time this runs. Losing the
    # bookkeeping is bad; turning a successful read into an error is worse.
    async def broken_database():
        raise RuntimeError("postgres went away")

    monkeypatch.setattr(server, "database", broken_database)
    await server.record_group_read(-100123)


# --- Personas are private-chat only (SPEC-PSN-009) ------------------------


def make_dialog_ref(chat_id: int = 500, label: str = "@team") -> db.DialogRef:
    return db.DialogRef(
        chat_id=chat_id,
        username=label.lstrip("@"),
        display_name="Team",
        message_count=100,
        outgoing_count=0,
        has_persona=False,
    )


@pytest.fixture
def archived(monkeypatch):
    """Resolve any target to one archived Dialog of a chosen Peer Type."""

    def configure(peer_type: str):
        async def fake_archived_dialog(pool, target):
            return make_dialog_ref()

        async def fake_get_peer_type(pool, chat_id):
            return peer_type

        async def fake_database():
            return object()

        monkeypatch.setattr(server, "database", fake_database)
        monkeypatch.setattr(server, "archived_dialog", fake_archived_dialog)
        monkeypatch.setattr(server.db, "get_peer_type", fake_get_peer_type)

    return configure


@pytest.mark.parametrize("kind", ["group", "channel"])
async def test_a_persona_is_refused_for_a_group_or_channel(archived, kind):
    archived(kind)

    with pytest.raises(ToolError) as caught:
        await server.persona_dialog(object(), "@team")

    message = str(caught.value)
    assert kind in message
    assert "private chats only" in message


async def test_a_persona_still_resolves_for_a_person(archived):
    archived("user")
    dialog = await server.persona_dialog(object(), "@ann")
    assert dialog.chat_id == 500


async def test_an_unknown_peer_type_is_not_treated_as_a_group(archived):
    # Rows archived before peer_type existed default to 'user' in the schema,
    # but a NULL must not turn an ordinary person's persona into an error.
    archived(None)
    assert await server.persona_dialog(object(), "@ann") is not None


async def test_both_persona_tools_go_through_the_guard():
    # The guard is only worth having if neither tool can reach around it.
    import inspect

    for tool in (server.tg_get_dialog_persona, server.tg_set_dialog_persona):
        source = inspect.getsource(tool)
        assert "persona_dialog(" in source
        assert "archived_dialog(" not in source


# --- The Group and Channel send guard (SPEC-SND-001, SPEC-SND-007) --------


class SendRecordingClient:
    """A client that records sends and fails if one it should not make happens."""

    def __init__(self, me_id: int = 1) -> None:
        self.sent: list[tuple[object, str]] = []
        self.read_acks: list[object] = []
        self.fail_send_after: int | None = None
        self.fail_read_ack = False
        self._me = User(id=me_id, first_name="Me", bot=False, deleted=False)

    async def get_me(self):
        return self._me

    async def send_message(self, peer, text):
        if self.fail_send_after is not None and len(self.sent) >= self.fail_send_after:
            raise errors.ChatWriteForbiddenError(request=None)
        self.sent.append((peer, text))

    async def send_read_acknowledge(self, peer):
        if self.fail_read_ack:
            raise errors.ChatAdminRequiredError(request=None)
        self.read_acks.append(peer)


class StubIndex:
    async def contact_ids(self, *, force: bool = False) -> set[int]:
        return set()


@pytest.fixture
def sending(monkeypatch):
    """Wire tg_send_message to a stub client that resolves to ``peer``.

    ``target(peer, read_on_send=...)`` also fixes the TG_READ_ON_SEND setting,
    because server.config() would otherwise read the developer's environment.
    """
    client = SendRecordingClient()

    def target(peer, *, read_on_send: bool = False):
        monkeypatch.setattr(server, "config", lambda: SimpleNamespace(read_on_send=read_on_send))

        async def fake_telegram():
            return client, StubIndex()

        async def fake_resolve(_client, _index, _target, *, allow=None):
            return peer, True

        async def fake_database():
            return object()

        async def not_group_only(pool, user_id):
            return False

        monkeypatch.setattr(server, "telegram", fake_telegram)
        monkeypatch.setattr(server, "resolve_peer", fake_resolve)
        monkeypatch.setattr(server, "database", fake_database)
        monkeypatch.setattr(server.db, "is_group_only_sender", not_group_only)
        return client

    return target


def channel(**kwargs) -> Channel:
    defaults = {"id": 900, "title": "News", "photo": None, "date": None, "broadcast": True}
    return Channel(**{**defaults, **kwargs})


def supergroup(**kwargs) -> Channel:
    defaults = {"id": 901, "title": "Team", "photo": None, "date": None, "megagroup": True}
    return Channel(**{**defaults, **kwargs})


async def test_a_message_needing_two_chunks_is_refused_for_a_group(sending):
    client = sending(supergroup())

    result = await server.tg_send_message("Team", "x" * 9000)

    assert result.startswith("ERROR: ")
    assert "separate messages" in result
    # Nothing at all, not even the first chunk: a partial send would leave the
    # user with half a message and no way to tell.
    assert client.sent == []


async def test_a_channel_subscriber_cannot_post(sending):
    client = sending(channel())

    result = await server.tg_send_message("News", "hello")

    assert result.startswith("ERROR: ")
    assert "posting rights" in result
    assert "nothing was requested from telegram" in result.lower()
    assert client.sent == []


async def test_a_group_the_account_has_left_is_refused(sending):
    client = sending(supergroup(left=True))

    result = await server.tg_send_message("Team", "hello")

    assert result.startswith("ERROR: ")
    assert "not a member" in result
    assert "will not join" in result
    assert client.sent == []


async def test_a_short_message_to_a_group_is_sent(sending):
    client = sending(supergroup())

    result = await server.tg_send_message("Team", "hello")

    assert result.startswith("Sent to ")
    assert len(client.sent) == 1


async def test_a_peer_seen_only_in_a_group_is_refused_before_connecting(monkeypatch):
    # The Send Guard for Min Peers runs before anything touches Telegram, so
    # this test deliberately leaves `telegram` unstubbed: reaching it would
    # raise, and the test would fail.
    async def fake_database():
        return object()

    async def group_only(pool, user_id):
        return True

    monkeypatch.setattr(server, "database", fake_database)
    monkeypatch.setattr(server.db, "is_group_only_sender", group_only)

    result = await server.tg_send_message("8873675373", "hello")

    assert result.startswith("ERROR: ")
    assert "only ever seen writing inside a group" in result
    assert "nothing was requested from telegram" in result.lower()


# --- The read receipt is bound to sending (SPEC-SND-009, ADR-0011) --------


def posting_rights() -> ChatAdminRights:
    """Channel admin rights that permit posting and nothing else."""
    fields = (
        "change_info",
        "post_messages",
        "edit_messages",
        "delete_messages",
        "ban_users",
        "invite_users",
        "pin_messages",
        "add_admins",
    )
    return ChatAdminRights(**{name: name == "post_messages" for name in fields})


def known_user(**kwargs) -> User:
    """A User the Send Guard lets through: resolve_peer reports known_locally."""
    defaults = {"id": 500, "first_name": "Sam", "bot": False, "deleted": False}
    return User(**{**defaults, **kwargs})


async def test_no_read_receipt_is_sent_by_default(sending):
    # TG_READ_ON_SEND is off unless the account owner turns it on, so the
    # shipped default leaves the sender's single checkmark alone.
    client = sending(known_user())

    result = await server.tg_send_message("Sam", "hello")

    assert result.startswith("Sent to ")
    assert client.sent
    assert client.read_acks == []


async def test_a_successful_send_marks_the_chat_read(sending):
    client = sending(known_user(), read_on_send=True)

    result = await server.tg_send_message("Sam", "hello")

    assert result.startswith("Sent to ")
    assert len(client.read_acks) == 1


async def test_a_channel_send_marks_the_channel_read(sending):
    # send_read_acknowledge picks channels.ReadHistory from the entity, so the
    # channel case needs no branch of ours - but it must still be reached.
    peer = channel(admin_rights=posting_rights())
    client = sending(peer, read_on_send=True)

    result = await server.tg_send_message("News", "hello")

    assert result.startswith("Sent to ")
    assert client.read_acks == [peer]


async def test_a_group_send_marks_the_group_read(sending):
    peer = supergroup()
    client = sending(peer, read_on_send=True)

    await server.tg_send_message("Team", "hello")

    assert client.read_acks == [peer]


async def test_a_partial_send_does_not_mark_the_chat_read(sending, monkeypatch):
    # A send that failed partway is not a read conversation, and the failure
    # must still reach the agent (SPEC-SND-005).
    client = sending(known_user(), read_on_send=True)
    client.fail_send_after = 1

    # The real 2.5s chunk gap is proved elsewhere; paying it here would make the
    # offline suite slower without proving anything about read receipts.
    async def no_delay():
        return None

    monkeypatch.setattr(server, "sleep_between_chunks", no_delay)

    result = await server.tg_send_message("Sam", "x" * 9000)

    assert result.startswith("ERROR: ")
    assert len(client.sent) == 1
    assert client.read_acks == []


async def test_a_refused_send_marks_nothing_read(sending):
    # The guard fires before anything is delivered, so there is nothing to have
    # read. This is also what keeps the receipt from becoming a side channel
    # that reveals a refused send.
    client = sending(channel(left=False), read_on_send=True)

    result = await server.tg_send_message("News", "hello")

    assert result.startswith("ERROR: ")
    assert client.sent == []
    assert client.read_acks == []


async def test_a_failing_read_receipt_still_reports_the_send_as_successful(sending):
    # The message is already delivered. Reporting ERROR: here would invite the
    # agent to send it a second time, which is the worst outcome available.
    client = sending(known_user(), read_on_send=True)
    client.fail_read_ack = True

    result = await server.tg_send_message("Sam", "hello")

    assert result.startswith("Sent to ")
    assert len(client.sent) == 1
    assert client.read_acks == []


# --- Reading still acknowledges nothing (SPEC-RCV-003) -------------------


class ReadingClient:
    """A client whose read acknowledgment fails loudly if it is ever reached.

    Modelled on ForbiddenClient in test_group_rules.py: the method records
    itself and then raises, so the test fails whether it inspects the recording
    or merely runs.
    """

    def __init__(self) -> None:
        self.acknowledged = False

    async def get_messages(self, peer, limit=None):
        return []

    async def send_read_acknowledge(self, peer):
        self.acknowledged = True
        raise AssertionError(
            "tg_get_recent_messages marked a chat read. Reading is "
            "non-destructive; only sending may acknowledge (SPEC-RCV-003)."
        )


async def test_reading_messages_marks_nothing_read(monkeypatch):
    # The flag is deliberately ON. TG_READ_ON_SEND governs the send path only,
    # so turning it on must not leak a receipt into any read path - that is the
    # whole distinction ADR-0011 rests on.
    client = ReadingClient()
    peer = known_user()

    async def fake_telegram():
        return client, StubIndex()

    async def fake_resolve(_client, _index, _target, *, allow=None):
        return peer, True

    async def no_persona(chat_id, label):
        return ""

    monkeypatch.setattr(server, "telegram", fake_telegram)
    monkeypatch.setattr(server, "resolve_peer", fake_resolve)
    monkeypatch.setattr(server, "persona_header_for", no_persona)
    monkeypatch.setattr(server, "config", lambda: SimpleNamespace(read_on_send=True))

    result = await server.tg_get_recent_messages("Sam")

    assert not result.startswith("ERROR: "), result
    assert client.acknowledged is False
