---
applyTo: "**/*.py"
---

# Python instructions

Full rules: [AGENTS.md](../../AGENTS.md).

- Python 3.12, `ruff` + `black`, line length 100.
- `from __future__ import annotations` everywhere **except `server.py`**, where
  FastMCP inspects signatures at import time and cannot resolve string
  annotations.
- Type-annotate every signature. Docstring every public function, naming the
  `SPEC-` clause it implements.
- Comments explain **why**. A comment restating the code is noise.
- No `os.environ` outside `tg_ai/config.py`.
- No `print()` in `server.py` - stdout is the MCP protocol channel.
- MCP tools return `str` under all conditions and never raise. Use
  `raise ToolError(...)` for expected failures.
- Safety logic goes in `tg_ai/safety.py`: pure, no I/O, unit-testable offline.
- Never lower `MAX_CHUNK_CHARS` (4000) or `CHUNK_DELAY_SECONDS` (2.5).
