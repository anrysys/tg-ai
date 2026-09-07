"""Per-process limits enforced in the MCP server itself.

Proves SPEC-LIM-007. This is the first test in the suite to import `server`,
which is safe: importing it registers the FastMCP tools and nothing else. No
tool here reaches Telegram, because the ceiling is checked before the client is
ever touched.
"""

import pytest

import server
from tg_ai.safety import MAX_TELEGRAM_TOOL_CALLS, ToolError


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
