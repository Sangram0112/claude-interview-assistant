"""Fail if anything that looks like a real credential is present in tracked files.

Usage:  python scripts/check_secrets.py [paths...]   (defaults to the repository root)
Also run against dist/ after packaging to verify no key was bundled.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = [
    re.compile(rb"sk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{20,}"),
    re.compile(rb"(?i)deepgram[_-]?api[_-]?key\s*=\s*[a-f0-9]{32,}"),
]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".png", ".ico", ".whl"}


def scan(root: Path) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts) or path.suffix in SKIP_SUFFIXES:
            continue
        if path.name == ".env":
            hits.append(f"{path}: .env file present (must not be committed or bundled)")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for pattern in PATTERNS:
            if pattern.search(data):
                hits.append(f"{path}: matches credential pattern {pattern.pattern[:30]!r}")
    return hits


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [Path(__file__).resolve().parent.parent]
    hits = [h for root in roots for h in scan(root)]
    for hit in hits:
        print(hit)
    print("OK: no credentials found" if not hits else f"FAIL: {len(hits)} potential credential(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
