"""The global RPC limiter: pacing, budgets, the kill switch, the quiet window.

Proves SPEC-LIM-001 .. SPEC-LIM-005.

Exercised against an in-memory ledger with the same structural interface as
``db.PostgresRpcLedger``, plus a fake clock. That is what makes the limiter
testable at all: constructing a real client needs credentials and a session
file, and this suite touches neither the network nor a database.

The ledger is also the seam that proves persistence. A budget "surviving a
process restart" means exactly that the state lives in the store rather than
the instance, so the restart tests below throw the guard away and build a new
one over the same ledger.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time

import pytest
from telethon import errors

from tg_ai.safety import (
    FLOOD_TRIP_COUNT,
    MIN_RPC_GAP_SECONDS,
    RPC_BUDGET_PER_DAY,
    RPC_BUDGET_PER_HOUR,
    ToolError,
)
from tg_ai.tg_client import RpcGuard

NOON = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


class FakeLedger:
    """In-memory stand-in for the persisted ledger.

    Structural typing means this needs no import from ``db`` and no base
    class - which is the point of declaring ``RpcLedger`` as a Protocol.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.floods: list[tuple[str, str, str | None]] = []
        self.trips: list[tuple[str, datetime | None]] = []
        self.hourly = 0
        self.daily = 0
        self.broken = False

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("connection refused")

    async def record_call(self, scope: str, key: str) -> None:
        self._check()
        self.calls.append((scope, key))
        self.hourly += 1
        self.daily += 1

    async def rpc_counts(self) -> tuple[int, int]:
        self._check()
        return self.hourly, self.daily

    async def record_flood(self, error_type: str, method: str, target: str | None) -> None:
        self._check()
        self.floods.append((error_type, method, target))

    async def count_floods(self, window_seconds: float) -> int:
        self._check()
        return len(self.floods)

    async def active_kill_switch(self):
        self._check()
        for reason, expires_at in reversed(self.trips):
            return NOON - timedelta(minutes=5), reason, expires_at
        return None

    async def trip_kill_switch(self, reason: str, expires_at: datetime | None) -> None:
        self._check()
        self.trips.append((reason, expires_at))


class FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.elapsed = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.elapsed += seconds
        await asyncio.sleep(0)


def make_guard(ledger, clock=None, *, now=NOON, quiet=(dt_time(1), dt_time(8))):
    clock = clock if clock is not None else FakeClock()
    return RpcGuard(
        ledger,
        quiet_start=quiet[0],
        quiet_end=quiet[1],
        timezone="UTC",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        now=lambda: now,
    )


@pytest.fixture
def ledger():
    return FakeLedger()


# --- Pacing (SPEC-LIM-001) -------------------------------------------------


async def test_the_first_request_is_not_made_to_wait(ledger):
    clock = FakeClock()
    guard = make_guard(ledger, clock)
    async with guard.reserve("GetHistoryRequest"):
        pass
    assert clock.slept == []


async def test_consecutive_requests_are_spaced_by_at_least_the_minimum_gap(ledger):
    clock = FakeClock()
    guard = make_guard(ledger, clock)
    for _ in range(5):
        async with guard.reserve("GetHistoryRequest"):
            pass

    assert len(clock.slept) == 4
    for waited in clock.slept:
        assert waited >= MIN_RPC_GAP_SECONDS


async def test_the_gap_is_jittered_rather_than_a_metronome(ledger):
    clock = FakeClock()
    guard = make_guard(ledger, clock)
    for _ in range(30):
        async with guard.reserve("GetHistoryRequest"):
            pass
    # A fixed interval is itself a signal; identical waits would mean no jitter.
    assert len(set(clock.slept)) > 1


async def test_every_request_is_recorded_so_the_budget_is_auditable(ledger):
    guard = make_guard(ledger)
    async with guard.reserve("GetDialogsRequest"):
        pass
    assert ledger.calls == [("rpc", "GetDialogsRequest")]


# --- Re-entrancy: the deadlock this design exists to avoid ----------------


async def test_a_nested_request_in_the_same_task_does_not_deadlock(ledger):
    # Telethon's _call issues nested requests: request.resolve() calls
    # get_input_entity, which calls self(GetUsersRequest(...)). Holding a plain
    # asyncio.Lock across the delegate hangs forever, in the same task, with no
    # traceback. wait_for turns that hang into a failing test.
    guard = make_guard(ledger)

    async def outer():
        # The nesting is the thing under test: one reserve() entered while
        # another is still open in the same task. Flattening these into a
        # single `async with` would stop reproducing that shape, so the lint
        # is suppressed rather than obeyed.
        async with guard.reserve("SendMessageRequest"):  # noqa: SIM117
            async with guard.reserve("GetUsersRequest"):
                pass

    await asyncio.wait_for(outer(), timeout=1.0)


async def test_a_nested_request_is_still_counted(ledger):
    guard = make_guard(ledger)

    async def nested():
        async with guard.reserve("SendMessageRequest"):  # noqa: SIM117 - see above
            async with guard.reserve("GetUsersRequest"):
                pass

    # Every nested reserve() is behind a timeout. Without one, a regression in
    # the re-entrancy guard hangs the whole suite instead of failing it, and a
    # hung CI job is much harder to read than a red one.
    await asyncio.wait_for(nested(), timeout=1.0)
    # It is a real RPC. Exempting it from the lock must not exempt it from the
    # budget, or the budget under-counts exactly the traffic it is metering.
    assert [key for _, key in ledger.calls] == ["SendMessageRequest", "GetUsersRequest"]


async def test_two_separate_tasks_are_serialised_and_never_overlap(ledger):
    guard = make_guard(ledger)
    order: list[str] = []

    async def worker(name: str):
        async with guard.reserve(name):
            order.append(f"enter {name}")
            await asyncio.sleep(0)
            order.append(f"exit {name}")

    await asyncio.gather(worker("A"), worker("B"))

    # Concurrent tasks are exactly what the lock is for: Telethon pipelines
    # happily, and a bursty parallel pattern is a stronger signal than a fast
    # serial one.
    assert order in (
        ["enter A", "exit A", "enter B", "exit B"],
        ["enter B", "exit B", "enter A", "exit A"],
    )


# --- Budgets, and that they survive a restart (SPEC-LIM-002) --------------


async def test_the_hourly_budget_refuses_rather_than_queueing(ledger):
    ledger.hourly = RPC_BUDGET_PER_HOUR
    guard = make_guard(ledger)
    with pytest.raises(ToolError) as caught:
        async with guard.reserve("GetHistoryRequest"):
            pass
    assert "Hourly" in str(caught.value)
    assert "Do not poll" in str(caught.value)
    assert ledger.calls == []


async def test_the_daily_budget_refuses(ledger):
    ledger.daily = RPC_BUDGET_PER_DAY
    guard = make_guard(ledger)
    with pytest.raises(ToolError) as caught:
        async with guard.reserve("GetHistoryRequest"):
            pass
    assert "Daily" in str(caught.value)


async def test_an_exhausted_budget_survives_a_process_restart(ledger):
    # The whole reason this state is in PostgreSQL. An MCP stdio server is
    # respawned on every editor launch, so a limiter that kept counters in
    # memory would hand a fresh budget to anyone who reopened their editor.
    first = make_guard(ledger)
    for _ in range(RPC_BUDGET_PER_HOUR):
        async with first.reserve("GetHistoryRequest"):
            pass

    del first  # the process goes away
    restarted = make_guard(ledger)

    with pytest.raises(ToolError) as caught:
        async with restarted.reserve("GetHistoryRequest"):
            pass
    assert "budget exhausted" in str(caught.value)


async def test_headroom_reports_what_is_left(ledger):
    ledger.hourly, ledger.daily = 10, 100
    assert await make_guard(ledger).headroom() == (
        RPC_BUDGET_PER_HOUR - 10,
        RPC_BUDGET_PER_DAY - 100,
    )


# --- The quiet window (SPEC-LIM-004) --------------------------------------


async def test_nothing_is_requested_inside_the_quiet_window(ledger):
    guard = make_guard(ledger, now=datetime(2026, 9, 7, 3, 30, tzinfo=UTC))
    with pytest.raises(ToolError) as caught:
        async with guard.reserve("GetHistoryRequest"):
            pass
    assert "TG_QUIET_HOURS" in str(caught.value)
    assert ledger.calls == []


async def test_outside_the_quiet_window_requests_proceed(ledger):
    guard = make_guard(ledger, now=datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
    async with guard.reserve("GetHistoryRequest"):
        pass
    assert ledger.calls


async def test_a_window_that_wraps_midnight_is_one_window(ledger):
    guard = make_guard(
        ledger, now=datetime(2026, 9, 7, 2, 0, tzinfo=UTC), quiet=(dt_time(23), dt_time(7))
    )
    with pytest.raises(ToolError):
        async with guard.reserve("GetHistoryRequest"):
            pass


# --- The kill switch (SPEC-LIM-003) ---------------------------------------


async def test_three_flood_waits_in_an_hour_trip_the_switch(ledger):
    guard = make_guard(ledger)
    flood = errors.FloodWaitError.__new__(errors.FloodWaitError)
    flood.seconds, flood.request = 12, None

    for _ in range(FLOOD_TRIP_COUNT - 1):
        await guard.note_failure(flood, "GetHistoryRequest")
    assert ledger.trips == []

    await guard.note_failure(flood, "GetHistoryRequest")
    assert len(ledger.trips) == 1
    reason, expires_at = ledger.trips[0]
    assert "flood waits within an hour" in reason
    assert expires_at == NOON + timedelta(hours=24)


async def test_a_tripped_switch_blocks_every_request(ledger):
    ledger.trips.append(("3 flood waits within an hour", NOON + timedelta(hours=24)))
    guard = make_guard(ledger)
    with pytest.raises(ToolError) as caught:
        async with guard.reserve("GetHistoryRequest"):
            pass
    assert "kill switch is ON" in str(caught.value)
    assert "do not restart the server" in str(caught.value)


async def test_a_lapsed_switch_stops_blocking(ledger):
    ledger.trips.append(("old trip", NOON - timedelta(minutes=1)))
    guard = make_guard(ledger)
    async with guard.reserve("GetHistoryRequest"):
        pass


async def test_peer_flood_trips_the_switch_indefinitely(ledger):
    # An escalation, not a cooldown: Telegram has flagged the account for spam,
    # and only its owner should decide it is healthy again.
    guard = make_guard(ledger)
    await guard.note_failure(
        errors.PeerFloodError.__new__(errors.PeerFloodError), "SendMessageRequest"
    )

    assert len(ledger.trips) == 1
    reason, expires_at = ledger.trips[0]
    assert expires_at is None
    assert "spam" in reason

    with pytest.raises(ToolError) as caught:
        async with make_guard(ledger).reserve("GetHistoryRequest"):
            pass
    assert "indefinitely" in str(caught.value)
    assert "@SpamBot" in str(caught.value)


async def test_a_single_flood_wait_is_recorded_but_does_not_trip(ledger):
    guard = make_guard(ledger)
    flood = errors.FloodWaitError.__new__(errors.FloodWaitError)
    flood.seconds, flood.request = 5, None
    await guard.note_failure(flood, "GetHistoryRequest")
    assert ledger.floods == [("FloodWaitError", "GetHistoryRequest", None)]
    assert ledger.trips == []


async def test_an_unrelated_failure_is_not_counted_as_a_flood(ledger):
    guard = make_guard(ledger)
    await guard.note_failure(ValueError("something else"), "GetHistoryRequest")
    assert ledger.floods == []


async def test_the_kill_switch_stops_a_nested_request_too(ledger):
    # The emergency brake outranks finishing an in-flight call.
    ledger.trips.append(("peer flood", None))
    guard = make_guard(ledger)
    with pytest.raises(ToolError):
        await asyncio.wait_for(guard.reserve("GetUsersRequest").__aenter__(), timeout=1.0)


# --- Failing closed --------------------------------------------------------


async def test_an_unreachable_ledger_refuses_rather_than_running_unmetered(ledger):
    # A budget that evaporates when PostgreSQL stops is not a budget: the
    # cheapest way around it would be `docker stop`.
    ledger.broken = True
    guard = make_guard(ledger)
    with pytest.raises(ToolError) as caught:
        async with guard.reserve("GetHistoryRequest"):
            pass
    assert "Refusing to contact Telegram" in str(caught.value)
    assert "just db-up" in str(caught.value)
