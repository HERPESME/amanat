"""Minimal .env loader.

Deliberately dependency-free and ~20 lines. The alternative is another package
in the install path of a project whose main claim is that its guarantees hold
without any of that.

Real environment variables always win over the file, so `GEMINI_API_KEY=... python
-m ...` overrides a stale `.env` rather than being silently ignored.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE lines into os.environ. Returns the names it set."""
    path = path or ROOT / ".env"
    if not path.exists():
        return []

    loaded = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:     # real env wins
            os.environ[key] = value
            loaded.append(key)
    return loaded
