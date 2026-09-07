"""Error translation and the never-raise guarantee.

Proves SPEC-SND-004.
"""

import pytest
from telethon import errors

from tg_ai.safety import (
    CHANNEL_SYNC_DELAY_SECONDS,
    CHUNK_DELAY_SECONDS,
    FLOOD_WAIT_TEMPLATE,
    GROUP_READ_COOLDOWN_SECONDS,
    MIN_RPC_GAP_SECONDS,
    SYNC_DIALOG_DELAY_SECONDS,
    describe_telegram_error,
    guarded_tool,
    jittered,
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


def test_a_duplicated_auth_key_is_reported_as_already_fatal():
    # The session is dead before this error is visible, so the message must
    # not invite a retry - an agent reading a vague error will try again
    # (SPEC-SEC-010, RISK-08).
    described = describe_telegram_error(
        errors.AuthKeyDuplicatedError.__new__(errors.AuthKeyDuplicatedError)
    )
    assert described is not None
    assert "AUTH_KEY_DUPLICATED" in described
    assert "retrying cannot bring it back" in described
    assert "just tg-auth" in described


# --- Jitter (SPEC-LIM-005) ------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        CHUNK_DELAY_SECONDS,
        SYNC_DIALOG_DELAY_SECONDS,
        CHANNEL_SYNC_DELAY_SECONDS,
        MIN_RPC_GAP_SECONDS,
        GROUP_READ_COOLDOWN_SECONDS,
    ],
)
def test_jitter_never_returns_less_than_the_reviewed_constant(base):
    # A property test over many samples, because the failure mode is
    # probabilistic: a symmetric formula would pass a single-sample test half
    # the time while quietly halving the pacing margin in production.
    samples = [jittered(base) for _ in range(20_000)]
    assert min(samples) >= base
    assert max(samples) <= base * 1.5


def test_jitter_actually_varies_so_the_delay_is_not_a_metronome():
    assert len({jittered(2.5) for _ in range(100)}) > 1


def test_jitter_of_zero_is_zero_rather_than_a_surprise_delay():
    assert jittered(0) == 0.0
