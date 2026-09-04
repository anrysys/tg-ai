"""Rendering of Telegram data into text an agent reads cheaply.

Every MCP tool returns a string, and that string costs the user tokens on
every turn it stays in context. These helpers favour compact, scannable,
line-oriented output over pretty formatting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tg_ai.db import SearchHit

#: Characters of message body kept in list-style output.
PREVIEW_CHARS = 200


def timestamp(value: datetime | None) -> str:
    """Render a timestamp as ``YYYY-MM-DD HH:MM`` in local time."""
    if value is None:
        return "unknown"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def preview(text: str | None, chars: int = PREVIEW_CHARS) -> str:
    """Collapse a message body to a single short line."""
    if not text:
        return "(no text: media, sticker or service message)"
    flat = " ".join(text.split())
    if len(flat) <= chars:
        return flat
    return flat[: chars - 1].rstrip() + "…"


def render_conversation(messages: list[Any], *, header: str) -> str:
    """Render live messages oldest-first, marking who wrote each one."""
    if not messages:
        return f"{header}\n(no messages)"

    lines = [header]
    for message in messages:
        who = "[me]  " if message.out else "[them]"
        lines.append(f"{timestamp(message.date)} {who} {preview(message.text, 500)}")
    return "\n".join(lines)


def render_search_hits(hits: list[SearchHit], *, query: str, strategy: str) -> str:
    """Render archive search results, newest or best-ranked first."""
    if not hits:
        return (
            f"No archived message matches {query!r}.\n"
            "If you expected a hit, the archive may be stale - run `just tg-sync`."
        )

    how = "full-text" if strategy == "fulltext" else "substring (no full-text match)"
    lines = [f"{len(hits)} match(es) for {query!r} via {how} search:"]
    for hit in hits:
        who = (
            "me" if hit.is_outgoing else (f"@{hit.username}" if hit.username else hit.display_name)
        )
        peer = f"@{hit.username}" if hit.username else hit.display_name
        lines.append(f"{timestamp(hit.date)} [{peer}] {who}: {preview(hit.text, 300)}")
    return "\n".join(lines)


def render_unread(entries: list[dict[str, Any]]) -> str:
    """Render the unread-dialog summary."""
    if not entries:
        return "No unread messages."

    lines = [f"{len(entries)} dialog(s) with unread messages:"]
    for entry in entries:
        handle = f"@{entry['username']}" if entry.get("username") else entry["name"]
        lines.append(
            f"{handle} - {entry['unread']} unread - last {timestamp(entry.get('date'))}: "
            f"{preview(entry.get('text'))}"
        )
    return "\n".join(lines)
