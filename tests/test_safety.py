"""Error translation and the never-raise guarantee.

Proves SPEC-SND-004.
"""

import pytest
from telethon import errors

from tg_ai.safety import (
    CHUNK_DELAY_SECONDS,
    FLOOD_WAIT_TEMPLATE,
    describe_telegram_error,
    guarded_tool,
)


def make_flood_wait(seconds: int) -> errors.FloodWaitError:
    error = errors.FloodWaitError.__new__(errors.FloodWaitError)
    error.seconds = seconds
    error.request = None
    return error


def test_flood_wait_reports_the_exact_wait_in_seconds():
    message = describe_telegram_error(make_flood_wait(42))
    assert message == "ERROR: " + FLOOD_WAIT_TEMPLATE.format(seconds=42)
    assert "42 seconds" in message


def test_peer_flood_tells_the_agent_to_stop_sending():
    error = errors.PeerFloodError.__new__(errors.PeerFloodError)
    message = describe_telegram_error(error)
    assert message is not None
    assert "spam" in message.lower()
    assert "stop sending" in message.lower()


def test_revoked_session_points_at_the_login_command():
    error = errors.AuthKeyUnregisteredError.__new__(errors.AuthKeyUnregisteredError)
    message = describe_telegram_error(error)
    assert message is not None
    assert "just tg-auth" in message


def test_unknown_exception_is_not_claimed():
    assert describe_telegram_error(ValueError("something else")) is None


def test_chunk_delay_is_slow_enough_to_look_human():
    # Below ~2s per message Telegram's rate heuristics start to react.
    assert CHUNK_DELAY_SECONDS >= 2.0


async def test_guarded_tool_returns_the_value_when_nothing_fails():
    @guarded_tool
    async def tool() -> str:
        return "fine"

    assert await tool() == "fine"


async def test_guarded_tool_converts_a_telegram_error_into_text():
    @guarded_tool
    async def tool() -> str:
        raise make_flood_wait(7)

    result = await tool()
    assert result.startswith("ERROR:")
    assert "7 seconds" in result


async def test_guarded_tool_never_lets_an_exception_escape():
    @guarded_tool
    async def tool() -> str:
        raise RuntimeError("database on fire")

    result = await tool()
    assert result == "ERROR: RuntimeError: database on fire"


async def test_guarded_tool_preserves_the_signature_fastmcp_inspects():
    @guarded_tool
    async def tool(target: str, limit: int = 5) -> str:
        return f"{target}:{limit}"

    import inspect

    signature = inspect.signature(tool)
    assert list(signature.parameters) == ["target", "limit"]
    assert tool.__name__ == "tool"


@pytest.mark.parametrize(
    "error_type",
    [
        errors.UserPrivacyRestrictedError,
        errors.UserIsBlockedError,
        errors.UsernameNotOccupiedError,
        errors.ChatWriteForbiddenError,
    ],
)
def test_every_common_send_failure_has_a_specific_message(error_type):
    message = describe_telegram_error(error_type.__new__(error_type))
    assert message is not None and message.startswith("ERROR:")


async def test_tool_error_is_reported_without_a_stack_trace():
    from tg_ai.safety import ToolError

    @guarded_tool
    async def tool() -> str:
        raise ToolError("Run `just tg-auth` first.")

    assert await tool() == "ERROR: Run `just tg-auth` first."
