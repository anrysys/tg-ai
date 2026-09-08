"""One connection per authorization key.

Proves SPEC-SEC-010. The session clone from ADR-0004 carries the *same* auth
key as the primary session, and Telegram answers two live connections on one
key with AUTH_KEY_DUPLICATED - which invalidates the login before the error is
even visible. The lock is what makes the clone safe (ADR-0010).
"""

import subprocess
import sys
from datetime import time as dt_time

import pytest

from tg_ai.config import PROJECT_ROOT, Config
from tg_ai.safety import ToolError, guarded_tool
from tg_ai.tg_client import SessionLock, SessionLocked


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "tg_session.lock"


def test_a_second_connection_on_one_key_is_refused(lock_path):
    first = SessionLock(lock_path, "the MCP server")
    second = SessionLock(lock_path, "a sync")

    first.acquire()
    try:
        with pytest.raises(SessionLocked):
            second.acquire()
    finally:
        first.release()


def test_the_refusal_names_the_holder_and_does_not_suggest_retrying(lock_path):
    holder = SessionLock(lock_path, "the MCP server")
    holder.acquire()
    try:
        with pytest.raises(SessionLocked) as caught:
            SessionLock(lock_path, "a sync").acquire()
    finally:
        holder.release()

    message = str(caught.value)
    assert "the MCP server" in message
    assert "AUTH_KEY_DUPLICATED" in message
    assert "Nothing was connected" in message


def test_the_loser_is_a_tool_error_so_a_tool_reports_it_instead_of_dying(lock_path):
    # server.py reaches this through telegram(); @guarded_tool must turn it
    # into text, because an exception escaping a handler takes down the stdio
    # server and leaves the agent blind.
    assert issubclass(SessionLocked, ToolError)

    holder = SessionLock(lock_path, "a sync")
    holder.acquire()

    @guarded_tool
    async def tool() -> str:
        SessionLock(lock_path, "the MCP server").acquire()
        return "connected"

    try:
        import asyncio

        result = asyncio.run(tool())
    finally:
        holder.release()

    assert result.startswith("ERROR: ")
    assert "a sync" in result


def test_releasing_makes_the_lock_available_again(lock_path):
    first = SessionLock(lock_path, "a login")
    first.acquire()
    first.release()

    second = SessionLock(lock_path, "a sync")
    second.acquire()
    second.release()


def test_acquiring_twice_from_one_holder_is_a_no_op(lock_path):
    # server.py calls telegram() on every tool invocation; re-acquiring must
    # not deadlock against itself.
    lock = SessionLock(lock_path, "the MCP server")
    lock.acquire()
    lock.acquire()
    lock.release()


def test_a_crashed_holder_does_not_leave_the_lock_stuck(lock_path, tmp_path):
    # flock lives on the open file description, so the kernel drops it when
    # the holder dies. Without that property a killed sync would lock the
    # user out of their own account until they found the file.
    script = "\n".join(
        [
            f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r})",
            "from pathlib import Path",
            "from tg_ai.tg_client import SessionLock",
            f"SessionLock(Path({str(lock_path)!r}), 'a doomed sync').acquire()",
            "raise SystemExit(9)",
        ]
    )
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert child.returncode == 9, child.stderr

    survivor = SessionLock(lock_path, "the MCP server")
    survivor.acquire()
    survivor.release()


def test_the_lock_is_keyed_to_the_primary_session_never_the_clone():
    # The clone shares the primary's auth key, so both processes must contend
    # for the same file. A lock derived from the clone's name would let the
    # sync and the server each take "their own" lock and connect together -
    # exactly the failure this guards against.
    config = Config(
        api_id=1,
        api_hash="h",
        session_name="tg_session",
        expected_username=None,
        database_url="postgresql://localhost/x",
        sync_include_bots=False,
        read_on_send=False,
        device_model="Desktop",
        system_version="Linux 7.0",
        app_version="tg-ai 1.0",
        lang_code="en",
        system_lang_code="en",
        timezone="UTC",
        quiet_start=dt_time(1, 0),
        quiet_end=dt_time(8, 0),
    )
    assert config.session_lock_file.name == "tg_session.lock"
    assert "sync" not in config.session_lock_file.name
