"""Environment configuration - the single source of truth for settings.

Every entrypoint calls :func:`load_config`. Nothing else reads ``os.environ``
directly, so a missing or malformed variable is reported once, in one place,
with an actionable message (SPEC-SEC-004).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: Repository root, derived from this file's location.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Idempotent DDL executed by :func:`tg_ai.db.ensure_schema`.
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"


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

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        expected_username=expected or None,
        database_url=database_url,
        sync_include_bots=_env_flag("TG_SYNC_INCLUDE_BOTS", default=False),
    )
