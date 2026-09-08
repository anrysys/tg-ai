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
    is_flood_error,
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


# --- Error translation for groups and channels (SPEC-SND-004) -------------


def build_error(name: str, **attributes):
    """Construct a Telethon error without running its __init__.

    ``FloodPremiumWaitError`` does not exist in telethon 1.36.0, so it is
    looked up rather than imported and the test skips it there rather than
    pretending to cover it.
    """
    cls = getattr(errors, name, None)
    if cls is None:
        pytest.skip(f"{name} is not defined in this telethon version")
    error = cls.__new__(cls)
    error.request = None
    for key, value in attributes.items():
        setattr(error, key, value)
    return error


#: Every row of the group/channel error table, with a phrase that proves the
#: message says the right thing rather than merely saying something.
TRANSLATED_ERRORS = [
    ("AuthKeyDuplicatedError", {}, "retrying cannot bring it back"),
    ("SlowModeWaitError", {"seconds": 30}, "slow mode"),
    ("FloodPremiumWaitError", {"seconds": 12}, "Telegram API limit reached"),
    ("ChannelPrivateError", {}, "never joins a channel"),
    ("ChannelInvalidError", {}, "never joins a channel"),
    ("ChatAdminRequiredError", {}, "nothing to retry"),
    ("UserBannedInChannelError", {}, "@SpamBot"),
    ("ChatGuestSendForbiddenError", {}, "will not join it for you"),
    ("TakeoutInitDelayError", {"seconds": 3600}, "does not use the takeout API"),
    ("ApiIdPublishedFloodError", {}, "my.telegram.org"),
    ("PhoneNumberBannedError", {}, "banned this phone number"),
]


@pytest.mark.parametrize(("name", "attributes", "phrase"), TRANSLATED_ERRORS)
def test_every_group_and_channel_error_is_translated(name, attributes, phrase):
    # An untranslated error reaches the agent as a raw class name, which it
    # will very likely retry - and most of these must never be retried.
    described = describe_telegram_error(build_error(name, **attributes))
    assert described is not None, f"{name} reaches the agent as a raw class name"
    assert described.startswith("ERROR: ")
    assert phrase in described


def test_slow_mode_is_not_mistaken_for_an_ordinary_flood_wait():
    # SlowModeWaitError is a sibling of FloodWaitError, not a subclass, so it
    # needs its own branch or it falls through untranslated.
    assert not issubclass(errors.SlowModeWaitError, errors.FloodWaitError)
    described = describe_telegram_error(build_error("SlowModeWaitError", seconds=45))
    assert "45 seconds" in described
    assert "Do not retry" in described


def test_no_translated_message_suggests_joining_or_retrying_its_way_out():
    # These messages are read by an agent looking for a next action, so none of
    # them may hint at joining a channel or hammering the same call.
    for name, attributes, _ in TRANSLATED_ERRORS:
        cls = getattr(errors, name, None)
        if cls is None:
            continue
        described = describe_telegram_error(build_error(name, **attributes)).lower()
        assert "try again immediately" not in described
        assert "join the channel" not in described


# --- What counts toward the kill switch (SPEC-LIM-003) --------------------

#: Ordinary permission and state errors. Every one of these was once counted as
#: a flood, because is_flood_error returned describe_telegram_error's *message*
#: for it and every message is truthy. Three in a rolling hour then tripped the
#: 24-hour account-wide kill switch, from errors that say nothing about rate.
NOT_FLOODS = [
    ("ChatAdminRequiredError", {}),
    ("ChannelPrivateError", {}),
    ("ChannelInvalidError", {}),
    ("ChatGuestSendForbiddenError", {}),
    ("PhoneNumberBannedError", {}),
    ("TakeoutInitDelayError", {"seconds": 3600}),
    ("ApiIdPublishedFloodError", {}),
    ("UserBannedInChannelError", {}),
]

#: The conditions SPEC-LIM-003 names, minus PeerFloodError, which is an
#: escalation handled separately rather than one more point in a rolling count.
REAL_FLOODS = [
    ("FloodWaitError", {"seconds": 30}),
    ("SlowModeWaitError", {"seconds": 30}),
    ("FloodPremiumWaitError", {"seconds": 12}),
]


@pytest.mark.parametrize(("name", "attributes"), NOT_FLOODS)
def test_a_permission_error_is_not_counted_as_a_flood(name, attributes):
    assert is_flood_error(build_error(name, **attributes)) is False


@pytest.mark.parametrize(("name", "attributes"), REAL_FLOODS)
def test_a_real_flood_is_counted(name, attributes):
    assert is_flood_error(build_error(name, **attributes)) is True


def test_peer_flood_is_not_counted_because_it_is_handled_separately():
    # It trips the switch indefinitely on its own path, so counting it here as
    # well would be the same event recorded twice.
    assert is_flood_error(build_error("PeerFloodError")) is False


@pytest.mark.parametrize(
    ("name", "attributes"), NOT_FLOODS + REAL_FLOODS + [("PeerFloodError", {})]
)
def test_is_flood_error_returns_a_real_boolean(name, attributes):
    # The defect this guards against was invisible to every caller, because the
    # one call site only asks whether the result is truthy. A returned message
    # string passes that test and is wrong.
    assert isinstance(is_flood_error(build_error(name, **attributes)), bool)


def test_an_unrecognised_exception_is_not_a_flood():
    assert is_flood_error(ValueError("something else")) is False
