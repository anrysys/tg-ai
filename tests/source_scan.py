"""Helpers for tests that assert something is absent from the source tree.

Two rules in this project are enforced by scanning rather than by types: the
blacklisted Telegram operations (SPEC-LIM-006) and the ban on Telethon event
handlers (SPEC-SEC-009). Both need the same "every Python file we actually
ship" list, so it lives here rather than being written twice.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Directories that are not this project's source. ``tests`` is excluded
#: because a test that names a forbidden identifier in order to forbid it must
#: not itself trip the check.
_SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "site",
    "tests",
}


def project_sources() -> list[Path]:
    """Every Python file that ships as part of this project."""
    return sorted(
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not _SKIP_DIRECTORIES & set(path.relative_to(PROJECT_ROOT).parts)
    )


def find_in_sources(needle: str) -> list[str]:
    """Return ``path:line`` for every occurrence of ``needle``."""
    hits: list[str] = []
    for path in project_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if needle in line:
                hits.append(f"{path.relative_to(PROJECT_ROOT)}:{number}: {line.strip()}")
    return hits
