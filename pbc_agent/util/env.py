"""Tiny .env loader (no dependency).

Lets a local run pick up ``ANTHROPIC_API_KEY`` from a git-ignored ``.env`` file instead of
re-exporting it every shell. Existing environment variables always win, and the key is never
written or logged by this code — ``.env`` stays local (see ``.gitignore``).
"""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:      # never override an already-set variable
            os.environ[key] = value
