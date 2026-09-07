"""Environment configuration - the single source of truth for settings.

Every entrypoint calls :func:`load_config`. Nothing else reads ``os.environ``
directly, so a missing or malformed variable is reported once, in one place,
with an actionable message (SPEC-SEC-004).
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

#: Repository root, derived from this file's location.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Idempotent DDL executed by :func:`tg_ai.db.ensure_schema`.
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"

#: Default Client Identity. These strings are sent in ``initConnection`` on
#: every connection and every reconnection, so they are a fingerprint, not a
#: cosmetic label. They are deliberately honest: Telegram already knows this
#: client is unofficial because it knows the api_id, so claiming to be an
#: official client would be a detectable inconsistency rather than camouflage.
#: What matters is that they never change (ADR-0009).
DEFAULT_DEVICE_MODEL = "Desktop"
DEFAULT_APP_VERSION = "tg-ai 1.0"

#: Language reported to Telegram when nothing is configured. ``en`` is a
#: neutral default for a public repository, NOT a good value for most users:
#: an account whose phone runs a Telegram UI in another language while every
#: API connection reports ``en`` is a mismatch Telegram can see. .env.example
#: and the README carry the warning; this constant only decides the fallback.
DEFAULT_LANG_CODE = "en"

#: Quiet window, in the account owner's local time. An account that issues API
#: calls uniformly around the clock is not a person.
DEFAULT_QUIET_HOURS = "01:00-08:00"


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


@dataclass(frozen=True, slots=True)
class Config:
    """Validated runtime configuration."""

    api_id: int
    api_hash: str
    session_name: str
    expected_username: str | None
    database_url: str
    sync_include_bots: bool

    # --- Client Identity (ADR-0009) --------------------------------------
    # Replayed in initConnection on every reconnection. Treat as immutable
    # once a session exists: changing them makes the account's own "active
    # sessions" entry mutate under the user, which is both support-visible
    # and a signal.
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str

    # --- Human activity window (ADR-0009) --------------------------------
    timezone: str
    quiet_start: dt_time
    quiet_end: dt_time

    @property
    def session_path(self) -> Path:
        """Absolute path to the Telethon session file, without the suffix.

        Telethon appends ``.session`` itself, so this deliberately returns the
        base path rather than the file name on disk.
        """
        return PROJECT_ROOT / self.session_name

    @property
    def session_file(self) -> Path:
        """Absolute path to the session file as it exists on disk."""
        return PROJECT_ROOT / f"{self.session_name}.session"

    @property
    def sync_session_path(self) -> Path:
        """Base path of the read-only session clone used by ``sync_db.py``.

        See ADR-0004: Telethon sessions are SQLite databases and two writers
        deadlock, so the sync process works on a copy.
        """
        return PROJECT_ROOT / f"{self.session_name}.sync"

    @property
    def sync_session_file(self) -> Path:
        """Absolute path to the session clone as it exists on disk."""
        return PROJECT_ROOT / f"{self.session_name}.sync.session"

    @property
    def session_lock_file(self) -> Path:
        """Path of the lock that serialises connections on this auth key.

        Deliberately derived from the primary session name, never from the
        clone: the clone shares the primary's authorization key, so the two
        processes must contend for the *same* lock (ADR-0010).
        """
        return PROJECT_ROOT / f"{self.session_name}.lock"


def _default_system_version() -> str:
    """A coarse, stable description of this machine.

    Coarse on purpose. The honest value is the real platform (SPEC-SEC-007),
    but a kernel patch bump must not mutate the fingerprint on the next
    reconnect, so only the major and minor version are used. Pin
    ``TG_SYSTEM_VERSION`` in ``.env`` to freeze it completely.
    """
    system = platform.system() or "Unknown"
    parts = (platform.release() or "").split(".")
    version = ".".join(part for part in parts[:2] if part.isdigit())
    return f"{system} {version}".strip() if version else system


def _default_timezone() -> str:
    """Best-effort IANA name of this machine's timezone, falling back to UTC.

    The quiet window is meaningless in the wrong timezone, so this tries the
    sources an ordinary Linux desktop actually has before giving up.
    """
    candidates: list[str] = []
    env_tz = os.environ.get("TZ", "").strip()
    if env_tz:
        candidates.append(env_tz)

    etc_timezone = Path("/etc/timezone")
    try:
        if etc_timezone.is_file():
            candidates.append(etc_timezone.read_text(encoding="utf-8").strip())
    except OSError:
        pass

    localtime = Path("/etc/localtime")
    try:
        if localtime.is_symlink():
            resolved = str(localtime.resolve())
            if "zoneinfo/" in resolved:
                candidates.append(resolved.split("zoneinfo/", 1)[1])
    except OSError:
        pass

    for candidate in candidates:
        if _is_known_timezone(candidate):
            return candidate
    return "UTC"


def _is_known_timezone(name: str) -> bool:
    """Whether ``name`` resolves to a real IANA zone on this machine."""
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def _parse_quiet_hours(raw: str) -> tuple[dt_time, dt_time]:
    """Parse ``HH:MM-HH:MM`` into a start and end time.

    A window is allowed to wrap midnight; deciding whether a given moment
    falls inside it is ``tg_ai.safety.in_quiet_window``'s job, not this one.

    Raises:
        ConfigError: The value is not two ``HH:MM`` times separated by ``-``.
    """
    text = raw.strip()
    start_text, separator, end_text = text.partition("-")
    if not separator:
        raise ConfigError(f"TG_QUIET_HOURS must look like {DEFAULT_QUIET_HOURS!r}, got {raw!r}")
    try:
        start = dt_time.fromisoformat(start_text.strip())
        end = dt_time.fromisoformat(end_text.strip())
    except ValueError as exc:
        raise ConfigError(f"TG_QUIET_HOURS must be two 24-hour HH:MM times, got {raw!r}") from exc
    if start == end:
        raise ConfigError(
            "TG_QUIET_HOURS start and end are the same time, which would "
            f"describe an empty window; got {raw!r}"
        )
    return start, end


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(*, require_telegram: bool = True) -> Config:
    """Read ``.env`` plus the process environment and validate the result.

    Args:
        require_telegram: When ``False``, ``TG_API_ID``/``TG_API_HASH`` may be
            absent. Used by tools that only touch PostgreSQL.

    Raises:
        ConfigError: A required variable is missing or malformed.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    raw_api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()

    if require_telegram and (not raw_api_id or not api_hash):
        raise ConfigError(
            "TG_API_ID and TG_API_HASH are required. Create them at "
            "https://my.telegram.org (API development tools), then copy "
            ".env.example to .env and fill them in."
        )

    api_id = 0
    if raw_api_id:
        try:
            api_id = int(raw_api_id)
        except ValueError as exc:
            raise ConfigError(f"TG_API_ID must be an integer, got {raw_api_id!r}") from exc

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError(
            "DATABASE_URL is required. Start the local database with "
            "`just db-up`, then copy .env.example to .env."
        )

    session_name = os.environ.get("TG_SESSION_NAME", "tg_session").strip() or "tg_session"
    if "/" in session_name or "\\" in session_name:
        raise ConfigError(
            f"TG_SESSION_NAME must be a bare file name, not a path, got {session_name!r}"
        )

    expected = os.environ.get("TG_EXPECTED_USERNAME", "").strip().lstrip("@")

    # Client Identity. system_lang_code defaults to lang_code rather than to
    # "en": a connection reporting one language for the app and another for
    # the system is the same mismatch, one field further down.
    lang_code = os.environ.get("TG_LANG_CODE", "").strip() or DEFAULT_LANG_CODE
    system_lang_code = os.environ.get("TG_SYSTEM_LANG_CODE", "").strip() or lang_code

    timezone = os.environ.get("TG_TIMEZONE", "").strip() or _default_timezone()
    if not _is_known_timezone(timezone):
        raise ConfigError(
            f"TG_TIMEZONE is not a known IANA timezone: {timezone!r}. Use a "
            "name like 'Europe/Kyiv' or 'America/New_York'."
        )

    quiet_start, quiet_end = _parse_quiet_hours(
        os.environ.get("TG_QUIET_HOURS", "").strip() or DEFAULT_QUIET_HOURS
    )

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        expected_username=expected or None,
        database_url=database_url,
        sync_include_bots=_env_flag("TG_SYNC_INCLUDE_BOTS", default=False),
        device_model=os.environ.get("TG_DEVICE_MODEL", "").strip() or DEFAULT_DEVICE_MODEL,
        system_version=(
            os.environ.get("TG_SYSTEM_VERSION", "").strip() or _default_system_version()
        ),
        app_version=os.environ.get("TG_APP_VERSION", "").strip() or DEFAULT_APP_VERSION,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
        timezone=timezone,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )
