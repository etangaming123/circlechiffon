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
* When an INTL chart's constant differs from JP's, the JP value is kept as
  `jpInternalLevelValue` / `jpLevel`, so /cc-info can flag the change INTL will most
  likely get next update.

The catalog derives the current INTL version from these flags, so a refresh after
an INTL update needs no code change. Song order is preserved (existing songs keep
their index, new songs append) because b50 detail links encode a song by index.
"""

import argparse
import json
import urllib.request
from pathlib import Path

UPSTREAM_URL = "https://raw.githubusercontent.com/gekichumai/dxrating/main/packages/dxdata/dxdata.json"
DATA_PATH = Path(__file__).resolve().parent / "data" / "dxdata.json"

_VERSION_KEYS = ("version", "abbr", "releaseDate")


def resolve_sheet(raw: dict) -> dict:
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
    jp_value = raw.get("internalLevelValue")
    if out["intl"] and jp_value is not None and jp_value != out["internalLevelValue"]:
        out["jpInternalLevelValue"] = jp_value
        out["jpLevel"] = raw.get("level")
    return out


def resolve_song(raw: dict) -> dict:
    return {
        "songId": raw["songId"],
        "category": raw.get("category"),
        "title": raw["title"],
        "artist": raw.get("artist"),
        "bpm": raw.get("bpm"),
        "imageName": raw.get("imageName"),
        "searchAcronyms": raw.get("searchAcronyms", []),
        "sheets": [resolve_sheet(s) for s in raw.get("sheets", [])],
    }


def build(upstream: dict, existing_order: list[str]) -> dict:
    """Trimmed data file content. Songs already in `existing_order` keep that order;
    the rest follow in upstream order."""
    by_id = {s["songId"]: s for s in upstream["songs"]}
    ordered = [by_id[i] for i in existing_order if i in by_id]
    seen = {s["songId"] for s in ordered}
    ordered += [s for s in upstream["songs"] if s["songId"] not in seen]
    return {
        "updateTime": upstream.get("updateTime"),
        "difficulties": upstream.get("difficulties", []),
        "types": upstream.get("types", []),
        "versions": [{k: v[k] for k in _VERSION_KEYS if k in v} for v in upstream.get("versions", [])],
        "songs": [resolve_song(s) for s in ordered],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-file", help="local upstream dxdata.json instead of downloading")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    if args.from_file:
        upstream = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=60) as resp:
            upstream = json.load(resp)

    existing_order: list[str] = []
    if DATA_PATH.exists():
        existing_order = [s["songId"] for s in json.loads(DATA_PATH.read_text(encoding="utf-8")).get("songs", [])]

    data = build(upstream, existing_order)
    sheets = [sh for s in data["songs"] for sh in s["sheets"]]
    jp_only = sum(1 for sh in sheets if not sh["intl"])
    print(
        f"{len(data['songs'])} songs ({len(data['songs']) - len(existing_order)} new), "
        f"{len(sheets)} sheets, {jp_only} not on INTL, updateTime {data['updateTime']}"
    )
    if args.dry_run:
        return
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {DATA_PATH}")


if __name__ == "__main__":
    main()
