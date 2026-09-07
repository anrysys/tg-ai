"""The MCP server releases what it holds when it stops.

Proves SPEC-SEC-011. Over stdio there is no goodbye message: the client simply
closes stdin. Before this existed the process kept Telethon's background tasks
alive, so it never exited - and an orphan that never exits keeps both the
connection lock (SPEC-SEC-010) and the SQLite session file locked away from the
next server, which then dies on "database is locked".

Offline like the rest of the suite: the client and the pool are stubs, but the
lock is the real ``SessionLock``, because "release() was called" is a weaker
claim than "the flock is gone".
"""

import asyncio
import logging
import os
import subprocess
import sys

import pytest

import server
from tg_ai.config import PROJECT_ROOT
from tg_ai.safety import MAX_TELEGRAM_TOOL_CALLS
from tg_ai.tg_client import SessionLock


class StubClient:
    """A Telethon client that records its teardown instead of performing one."""

    def __init__(self, events, *, fails=False, hangs=False):
        self.events = events
        self.disconnects = 0
        self._fails = fails
        self._hangs = hangs

    def is_connected(self):
        return True

    async def disconnect(self):
        self.disconnects += 1
        if self._hangs:
            await asyncio.Event().wait()
        if self._fails:
            raise OSError("the socket is already gone")
        self.events.append("client")


class StubPool:
    """An asyncpg pool that records its close instead of performing one."""

    def __init__(self, events, *, fails=False):
        self.events = events
        self.closes = 0
        self._fails = fails

    async def close(self):
        self.closes += 1
        if self._fails:
            raise OSError("the database went away")
        self.events.append("pool")


class RecordingLock(SessionLock):
    """The real lock, with the release recorded so ordering can be asserted."""

    def __init__(self, path, events):
        super().__init__(path, "the MCP server")
        self.events = events
        self.releases = 0

    def release(self):
        self.releases += 1
        self.events.append("lock")
        super().release()


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "tg_session.lock"


@pytest.fixture
def wired(monkeypatch, lock_path):
    """Install stub resources on the server module as a used process would hold them.

    ``monkeypatch.setattr`` restores every global afterwards, which matters
    because ``shutdown`` nulls them on purpose.
    """
    events = []

    def install(*, client_fails=False, client_hangs=False, pool_fails=False):
        client = StubClient(events, fails=client_fails, hangs=client_hangs)
        pool = StubPool(events, fails=pool_fails)
        lock = RecordingLock(lock_path, events)
        lock.acquire()
        monkeypatch.setattr(server, "_client", client)
        monkeypatch.setattr(server, "_index", object())
        monkeypatch.setattr(server, "_guard", object())
        monkeypatch.setattr(server, "_lock", lock)
        monkeypatch.setattr(server, "_pool", pool)
        return client, pool, lock

    install.events = events
    return install


async def test_shutdown_disconnects_releases_the_lock_and_closes_the_pool(wired):
    client, pool, lock = wired()

    await server.shutdown()

    assert client.disconnects == 1
    assert lock.releases == 1
    assert pool.closes == 1
    assert server._client is None
    assert server._index is None
    assert server._guard is None
    assert server._pool is None


async def test_the_lock_is_released_after_the_client_and_before_the_pool(wired):
    # Two separate hazards pin this order. Releasing the flock before the
    # socket is gone opens the window in which a sync connects on the same
    # authorization key, and Telegram answers that with AUTH_KEY_DUPLICATED,
    # which invalidates the login rather than failing the call (ADR-0010).
    # Closing the pool before the client is gone breaks the other end:
    # Telethon's auto-reconnect callback issues a get_me() that goes through
    # GuardedClient into the RPC ledger, which needs the pool (SPEC-LIM-002).
    wired()

    await server.shutdown()

    assert wired.events == ["client", "lock", "pool"]


async def test_a_released_lock_can_be_taken_again(wired, lock_path):
    # The point of the whole change: after a clean stop the next process can
    # actually connect. Asserting on release() alone would pass even if the
    # file description stayed open.
    wired()

    await server.shutdown()

    successor = SessionLock(lock_path, "a sync")
    successor.acquire()
    successor.release()


async def test_shutdown_still_releases_the_lock_when_the_client_fails_to_disconnect(wired):
    # The dangerous failure. A disconnect that raises must not abandon the
    # release that follows it, or a failing network leaves the user locked out
    # of their own account.
    client, pool, lock = wired(client_fails=True)

    await server.shutdown()

    assert client.disconnects == 1
    assert lock.releases == 1
    assert pool.closes == 1


async def test_a_pool_that_fails_to_close_does_not_raise(wired):
    _, pool, _ = wired(pool_fails=True)

    await server.shutdown()

    assert pool.closes == 1


async def test_a_teardown_step_that_hangs_does_not_block_the_exit(wired, monkeypatch):
    monkeypatch.setattr(server, "SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    _, pool, lock = wired(client_hangs=True)

    # Bounded so a regression fails the suite instead of hanging it.
    await asyncio.wait_for(server.shutdown(), 5)

    assert lock.releases == 1
    assert pool.closes == 1


async def test_shutdown_is_idempotent(wired):
    # main()'s safety net may release the lock a second time, and a lifespan
    # that unwinds twice must not double-close anything.
    client, pool, lock = wired()

    await server.shutdown()
    await server.shutdown()

    assert client.disconnects == 1
    assert pool.closes == 1
    assert lock.releases == 1


async def test_shutdown_does_not_reset_the_runaway_loop_counter(wired, monkeypatch):
    # The per-process ceiling counts one runaway agent loop, not one
    # connection. Clearing it here would hand a looping agent a fresh 200
    # calls every time the client is rebuilt (SPEC-LIM-007).
    monkeypatch.setattr(server, "_telegram_tool_calls", MAX_TELEGRAM_TOOL_CALLS)
    wired()

    await server.shutdown()

    assert server._telegram_tool_calls == MAX_TELEGRAM_TOOL_CALLS


async def test_a_server_that_never_connected_shuts_down_silently(monkeypatch, caplog):
    # The common case: an editor spawns the server and the session ends without
    # a single tool call. Nothing was taken, so nothing is released and nothing
    # reaches the agent's log.
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setattr(server, "_pool", None)
    monkeypatch.setattr(server, "_lock", None)
    caplog.set_level(logging.DEBUG, logger="tg_ai.server")

    await server.shutdown()

    assert caplog.records == []


def test_the_lifespan_is_registered_on_the_server():
    # Without this, dropping the lifespan= keyword silently reverts the entire
    # fix and every other test in this file still passes.
    assert server.mcp.settings.lifespan is server.lifespan


async def test_the_lifespan_starts_nothing_and_tears_down_on_exit(wired):
    client, pool, lock = wired()

    async with server.lifespan(server.mcp):
        # Startup stays empty on purpose: connecting here would take the
        # session lock every time an editor spawns the server.
        assert client.disconnects == 0
        assert lock.releases == 0

    assert client.disconnects == 1
    assert lock.releases == 1
    assert pool.closes == 1


def test_the_server_exits_cleanly_when_its_client_closes_stdin():
    # The end-to-end shape of the bug: the client goes away, stdin hits EOF,
    # and the process must exit rather than linger holding the locks. The
    # unreachable database is deliberate - it proves startup connects to
    # nothing at all.
    child = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "server.py")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=os.environ | {"DATABASE_URL": "postgresql://nobody@127.0.0.1:1/nowhere"},
        timeout=60,
        check=False,
    )

    assert child.returncode == 0, child.stderr
    # stdout is the MCP protocol channel and this session carried no protocol.
    assert child.stdout == ""
    assert "Traceback" not in child.stderr


def test_a_configuration_error_exits_two():
    # Adjacent to the same code path: main() caught ConfigError, but config()
    # had already translated it into ToolError, so the clause was dead and a
    # misconfigured server died with a raw traceback instead.
    child = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "server.py")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=os.environ | {"TG_TIMEZONE": "Not/AZone"},
        timeout=60,
        check=False,
    )

    assert child.returncode == 2, child.stderr
    assert "Traceback" not in child.stderr
