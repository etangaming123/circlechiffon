"""
The community data that rides along in dxrating's v1 dxdata document (aliases,
tags, tag groups, tag-to-chart links), saved by update_dxdata.py next to
data/dxdata.json so neither catalog load nor /cc-info has to call dxrating's
API for it. Song ids in here are already mapped to the bot's own `songId`.

Kept out of data/dxdata.json on purpose: that file is tracked and served to
the static b50 detail page, and tags change far more often than charts do.
Also holds the ETag the last full download came with, for If-None-Match.

Gitignored (`*.json`). Missing or unreadable -> load() returns None and
callers fall back to the standalone /aliases and /tags endpoints.
"""

import json
import os
from pathlib import Path

EXTRAS_PATH = Path(__file__).resolve().parents[3] / "data" / "dxrating_extras.json"


def load(path: Path = EXTRAS_PATH) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def mtime(path: Path = EXTRAS_PATH) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def save(data: dict, path: Path = EXTRAS_PATH) -> None:
    """Atomic, like update_dxdata.write_atomic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
