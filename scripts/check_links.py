#!/usr/bin/env python3
"""Fail if any relative markdown link points at a file that does not exist.

Broken links are worse in an agent-maintained repository than in a human one:
an agent follows a link, finds nothing, and invents the missing content. Run
via `just docs-links`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#", "<"))


def main() -> int:
    broken: list[str] = []
    checked = 0

    for path in sorted(ROOT.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).strip()
            if is_external(target):
                continue
            target = target.split("#", 1)[0].split(" ", 1)[0]
            if not target:
                continue
            checked += 1
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    if broken:
        print(f"{len(broken)} broken link(s):", file=sys.stderr)
        for item in broken:
            print(f"  {item}", file=sys.stderr)
        return 1

    print(f"links: OK ({checked} relative links checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
