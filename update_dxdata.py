"""Refresh data/dxdata.json from gekichumai/dxrating (packages/dxdata/dxdata.json, MIT).

    .venv/bin/python3 update_dxdata.py                  # download upstream, rewrite data/dxdata.json
    .venv/bin/python3 update_dxdata.py --from-file X    # use a local copy of upstream instead
    .venv/bin/python3 update_dxdata.py --dry-run        # report only, write nothing

The upstream file describes every region at once. This bot is INTL-only, so each
sheet is resolved to what an INTL player sees:

* `regionOverrides.intl` is applied (INTL often has a different level / constant
  than JP, and a few charts sit in a different version).
* `intl` records whether the chart exists on INTL at all. JP-only charts keep their
  JP values and `intl: false`; the bot tags them instead of hiding them.
* A chart's `multiverInternalLevelValue` (constant per game version) beats the intl
  override: the override is often just the level floor (13.0 for a 13.4). The value
  used is the one for the current INTL version, else the latest earlier version listed.
* When an INTL chart's constant differs from JP's, the JP value is kept as
  `jpInternalLevelValue` / `jpLevel`, so /cc-info can flag the change INTL will most
  likely get next update.

The catalog derives the current INTL version from these flags, so a refresh after
an INTL update needs no code change. Song order is preserved (existing songs keep
their index, new songs append) because b50 detail links encode a song by index.
"""

import argparse
import json
import os
import urllib.request
from collections import Counter
from pathlib import Path

from circlechiffon.songdata.catalog import MIN_INTL_SHEETS, pick_current_index

UPSTREAM_URL = "https://raw.githubusercontent.com/gekichumai/dxrating/main/packages/dxdata/dxdata.json"
DATA_PATH = Path(__file__).resolve().parent / "data" / "dxdata.json"

_VERSION_KEYS = ("version", "abbr", "releaseDate")


def level_label(value: float) -> str:
    """In-game level for a constant: x.6 and up is "x+" from level 7 upward."""
    whole = int(value)
    return f"{whole}+" if whole >= 7 and round(value - whole, 1) >= 0.6 else str(whole)


def multiver_value(raw: dict, intl_version: str | None, version_order: list[str]) -> float | None:
    """Constant the chart had in `intl_version`, or in the latest earlier version that lists one."""
    values = raw.get("multiverInternalLevelValue") or {}
    rank = {name: i for i, name in enumerate(version_order)}
    cutoff = rank.get(intl_version)
    if not values or cutoff is None:
        return None
    best: tuple[int, float] | None = None
    for name, value in values.items():
        r = rank.get(name)
        if r is not None and r <= cutoff and value is not None and (best is None or r > best[0]):
            best = (r, value)
    return best[1] if best else None


def resolve_sheet(raw: dict, intl_version: str | None = None, version_order: list[str] | None = None) -> dict:
    """One upstream sheet -> the trimmed INTL view stored in data/dxdata.json."""
    override = (raw.get("regionOverrides") or {}).get("intl") or {}
    regions = raw.get("regions") or {}
    out = {
        "type": raw.get("type"),
        "difficulty": raw.get("difficulty"),
        "level": override.get("level", raw.get("level")),
        "internalLevelValue": override.get("levelValue", raw.get("internalLevelValue")),
        "version": override.get("version", raw.get("version")),
        "isSpecial": raw.get("isSpecial", False),
        "noteDesigner": raw.get("noteDesigner"),
        "releaseDate": raw.get("releaseDate"),
        "noteCounts": raw.get("noteCounts"),
        "intl": bool(regions.get("intl", False)),
    }
    if out["intl"]:
        known = multiver_value(raw, intl_version, version_order or [])
        if known is not None and known != out["internalLevelValue"]:
            out["internalLevelValue"] = known
            out["level"] = level_label(known)
    jp_value = raw.get("internalLevelValue")
    if out["intl"] and jp_value is not None and jp_value != out["internalLevelValue"]:
        out["jpInternalLevelValue"] = jp_value
        out["jpLevel"] = raw.get("level")
    return out


def detect_intl_version(upstream: dict) -> str | None:
    """Current INTL version by the same rule the catalog uses for the B15 window."""
    names = [v["version"] for v in upstream.get("versions", [])]
    if not names:
        return None
    counts = Counter(
        ((sh.get("regionOverrides") or {}).get("intl") or {}).get("version", sh.get("version"))
        for song in upstream.get("songs", [])
        for sh in song.get("sheets", [])
        if (sh.get("regions") or {}).get("intl")
    )
    return names[pick_current_index(names, counts, MIN_INTL_SHEETS)]


def resolve_song(raw: dict, intl_version: str | None = None, version_order: list[str] | None = None) -> dict:
    return {
        "songId": raw["songId"],
        "category": raw.get("category"),
        "title": raw["title"],
        "artist": raw.get("artist"),
        "bpm": raw.get("bpm"),
        "imageName": raw.get("imageName"),
        "searchAcronyms": raw.get("searchAcronyms", []),
        "sheets": [resolve_sheet(s, intl_version, version_order) for s in raw.get("sheets", [])],
    }


def build(upstream: dict, existing_order: list[str]) -> dict:
    """Trimmed data file content. Songs already in `existing_order` keep that order;
    the rest follow in upstream order."""
    by_id = {s["songId"]: s for s in upstream["songs"]}
    ordered = [by_id[i] for i in existing_order if i in by_id]
    seen = {s["songId"] for s in ordered}
    ordered += [s for s in upstream["songs"] if s["songId"] not in seen]
    intl_version = detect_intl_version(upstream)
    version_order = [v["version"] for v in upstream.get("versions", [])]
    return {
        "updateTime": upstream.get("updateTime"),
        "difficulties": upstream.get("difficulties", []),
        "types": upstream.get("types", []),
        "versions": [{k: v[k] for k in _VERSION_KEYS if k in v} for v in upstream.get("versions", [])],
        "songs": [resolve_song(s, intl_version, version_order) for s in ordered],
    }


def fetch_upstream(timeout: float = 60) -> dict:
    with urllib.request.urlopen(UPSTREAM_URL, timeout=timeout) as resp:
        return json.load(resp)


def read_existing() -> dict:
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def validate(data: dict, existing: dict) -> None:
    """Raises ValueError if `data` looks like a broken upstream rather than an update:
    this file is what every command reads, so a bad refresh must never replace a good one."""
    songs = data.get("songs") or []
    if not data.get("versions"):
        raise ValueError("no versions listed")
    if not songs or any(not s.get("songId") or not s.get("title") for s in songs):
        raise ValueError("songs missing or malformed")
    old = len(existing.get("songs") or [])
    if old and len(songs) < old * 0.9:
        raise ValueError(f"song count fell from {old} to {len(songs)}")
    sheets = [sh for s in songs for sh in s["sheets"]]
    if sheets and sum(1 for sh in sheets if sh["intl"]) < len(sheets) * 0.3:
        raise ValueError("implausibly few INTL charts")
    if sheets and sum(1 for sh in sheets if sh["internalLevelValue"] is None) > len(sheets) * 0.05:
        raise ValueError("too many charts without a constant")


def serialise(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def write_atomic(path: Path, text: str) -> None:
    """Write beside the target, then rename over it: a crash never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def sync(upstream: dict | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """Refresh data/dxdata.json. Returns (changed, summary). Raises on download failure or
    a failed sanity check, leaving the existing file untouched. Blocking."""
    upstream = upstream if upstream is not None else fetch_upstream()
    existing = read_existing()
    data = build(upstream, [s["songId"] for s in existing.get("songs", [])])
    validate(data, existing)
    text = serialise(data)
    sheets = [sh for s in data["songs"] for sh in s["sheets"]]
    summary = (
        f"{len(data['songs'])} songs ({len(data['songs']) - len(existing.get('songs', []))} new), "
        f"{len(sheets)} sheets, {sum(1 for sh in sheets if not sh['intl'])} not on INTL, "
        f"INTL version {detect_intl_version(upstream)}, updateTime {data['updateTime']}"
    )
    changed = not DATA_PATH.exists() or DATA_PATH.read_text(encoding="utf-8") != text
    if changed and not dry_run:
        write_atomic(DATA_PATH, text)
    return changed, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-file", help="local upstream dxdata.json instead of downloading")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    upstream = json.loads(Path(args.from_file).read_text(encoding="utf-8")) if args.from_file else None
    changed, summary = sync(upstream, dry_run=args.dry_run)
    print(summary)
    print("no changes" if not changed else ("would write" if args.dry_run else f"wrote {DATA_PATH}"))


if __name__ == "__main__":
    main()
