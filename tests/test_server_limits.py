"""Per-process limits enforced in the MCP server itself.

Proves SPEC-LIM-007. This is the first test in the suite to import `server`,
which is safe: importing it registers the FastMCP tools and nothing else. No
tool here reaches Telegram, because the ceiling is checked before the client is
ever touched.
"""

from datetime import UTC, datetime, timedelta

import pytest

import server
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
