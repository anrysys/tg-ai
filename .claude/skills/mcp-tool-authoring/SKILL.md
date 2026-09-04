---
name: mcp-tool-authoring
description: How to add or change an MCP tool in this repository. Use when adding a tool to server.py, changing a tool's parameters or return text, or debugging FastMCP registration.
---

# Authoring an MCP tool

Authority: [docs/10-product/srs.md](../../../docs/10-product/srs.md) and
[docs/30-api/mcp-tools.md](../../../docs/30-api/mcp-tools.md).

## The shape

```python
@mcp.tool()
@guarded_tool
async def tg_do_thing(target: str, limit: int = 10) -> str:
    """One line saying what this does for the user.

    When to use it rather than a neighbouring tool, and any consequence the
    agent must know about before calling.

    Args:
        target: What forms are accepted.
        limit: What range; note that it is clamped, not rejected.

    Returns:
        What the text contains, and what a failure looks like.
    """
```

Decorator order matters: `@mcp.tool()` outermost, `@guarded_tool` directly on
the function.

## Hard requirements

| Rule | Why |
| --- | --- |
| Return `str` under all conditions; never raise | An escaping exception can kill the stdio server mid-session (`SPEC-SND-004`) |
| No `from __future__ import annotations` in `server.py` | FastMCP inspects signatures at import time and cannot resolve string annotations. It fails with `issubclass() arg 1 must be a class` |
| Clamp `limit`, never reject it | `max(1, min(int(limit), N))`. An agent guessing 10000 should get an answer, not a round trip (`SPEC-RCV-004`) |
| `raise ToolError(...)` for expected failures | Produces `ERROR: <message>` without stack-trace noise |
| Never `print()` | stdout is the protocol channel. Use `log.*` (stderr) |
| Acquire the client via `await telegram()`, the pool via `await database()` | Both are lazy and shared. A DB-only tool must keep working with no Telegram session |

## The docstring is the agent's only manual

FastMCP publishes it verbatim as the tool description. Write it for an agent
deciding **which** tool to call:

- Say when to use this one rather than a similar one. `tg_get_recent_messages`
  sees the last minute; `tg_search_local_history` sees years but only to the
  last sync.
- State consequences up front. "Blocks sends to strangers" belongs in the first
  paragraph, not in the return description.
- Describe what failure text looks like, so the agent can react rather than
  retry blindly.

## After adding or changing a tool

Required by the [documentation map](../../../docs/00-index/documentation-map.md):

1. A `SPEC-` clause in the SRS, naming its test.
2. A schema entry in `docs/30-api/mcp-tools.md`.
3. A row in the README tool table.
4. A glossary entry if a new concept appeared.
5. `just check`, then the `DOCS SYNC` block.

## Verifying registration

```bash
.venv/bin/python -c "
import asyncio, server
print([t.name for t in asyncio.run(server.mcp.list_tools())])"
```
