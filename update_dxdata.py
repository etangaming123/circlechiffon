"""Refresh data/dxdata.json from dxrating's v1 catalog API (https://miruku.dxrating.net/api/v1/dxdata,
"Data from DXRating (dxrating.net)", MIT code / free-to-use data).

    .venv/bin/python3 update_dxdata.py                  # download upstream, rewrite data/dxdata.json
    .venv/bin/python3 update_dxdata.py --from-file X    # use a local copy of upstream instead (v1 or legacy shape)
    .venv/bin/python3 update_dxdata.py --dry-run        # report only, write nothing

The download is revalidated with the ETag of the last one (If-None-Match), so an unchanged catalog
costs a bodiless 304. v1 is dxrating's stable API (additive-only changes, 6 months' deprecation
notice); it replaced the raw packages/dxdata/dxdata.json from GitHub, which has the same data in an
older shape. normalize_v1() turns v1 back into that older shape so everything below is unchanged:

* v1 songs carry a permanent `id` (dsng_...) but no title-style `songId`. The bot (and the static b50
  detail page, which builds dxrating sheet links from it) keys songs by the legacy `songId`, so
  legacy_ids() keeps every existing song's `songId` - matched by stored `id`, or on the first run by
  title + jacket + chart set - and gives new songs their title. Each song now also stores its `id`.
* v1's `serverIds` / `serverOverrides` are the old `regions` / `regionOverrides`.
* The aliases, tags and tag links bundled in v1 go to data/dxrating_extras.json
  (circlechiffon/adapters/dxrating/extras.py), mapped to `songId`, along with the ETag.

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
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from circlechiffon.adapters.dxrating import extras as dxrating_extras
from circlechiffon.songdata.catalog import MIN_INTL_SHEETS, pick_current_index

UPSTREAM_URL = "https://miruku.dxrating.net/api/v1/dxdata"
_SERVERS = ("jp", "intl", "usa", "cn")
# v1 leaves out a null note count; the legacy file spelled all six out
_NOTE_KEYS = ("tap", "hold", "slide", "touch", "break", "total")
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
    out = {"songId": raw["songId"]}
    if raw.get("id"):
        out["id"] = raw["id"]
    return out | {
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


def is_v1(upstream: dict) -> bool:
    songs = upstream.get("songs") or []
    return "schemaVersion" in upstream or bool(songs and "id" in songs[0] and "songId" not in songs[0])


def _chart_set(sheets: list[dict]) -> tuple:
    return tuple(sorted(
        str((sh.get("type"), sh.get("difficulty"), sh.get("level"), (sh.get("noteCounts") or {}).get("total")))
        for sh in sheets
    ))


def legacy_ids(v1_songs: list[dict], existing_songs: list[dict]) -> dict[str, str]:
    """v1 song id -> the bot's `songId`. Existing songs keep theirs: matched by the `id` stored
    beside it, or (first run, before any `id` was stored) by title + jacket, narrowed by chart set
    when a title/jacket pair repeats (the UTAGE "(BASIC)".."(Re:MASTER)" variants), then by title
    alone when exactly one unmatched song has it (a jacket swapped upstream). Unmatched songs are
    new and get their title, or "<title> (<id>)" if that `songId` is taken."""
    by_id = {s["id"]: s["songId"] for s in existing_songs if s.get("id")}
    pool: dict[tuple, list[dict]] = defaultdict(list)
    by_title: dict[str, list[dict]] = defaultdict(list)
    for s in existing_songs:
        if not s.get("id"):
            pool[(s.get("title"), s.get("imageName"))].append(s)
            by_title[s.get("title")].append(s)

    result: dict[str, str] = {}
    taken = set(by_id.values())
    for song in v1_songs:
        if song["id"] in by_id:
            result[song["id"]] = by_id[song["id"]]
            continue
        cands = [c for c in pool[(song.get("title"), song.get("imageName"))] if c["songId"] not in taken]
        if len(cands) > 1:
            cands = [c for c in cands if _chart_set(c.get("sheets", [])) == _chart_set(song.get("sheets", []))] or cands
        if cands:
            result[song["id"]] = cands[0]["songId"]
            taken.add(cands[0]["songId"])

    for song in v1_songs:
        if song["id"] in result:
            continue
        cands = [c for c in by_title[song.get("title")] if c["songId"] not in taken]
        if len(cands) == 1:
            result[song["id"]] = cands[0]["songId"]
            taken.add(cands[0]["songId"])

    taken |= {s["songId"] for s in existing_songs}
    for song in v1_songs:
        if song["id"] not in result:
            new_id = song["title"] if song["title"] not in taken else f"{song['title']} ({song['id']})"
            result[song["id"]] = new_id
            taken.add(new_id)
    return result


def normalize_v1(v1: dict, id_map: dict[str, str]) -> dict:
    """v1 document -> the legacy upstream shape build() reads."""

    def sheet(sh: dict) -> dict:
        servers = set(sh.get("serverIds") or [])
        out = sh | {"regions": {s: s in servers for s in _SERVERS}, "regionOverrides": sh.get("serverOverrides") or {}}
        if isinstance(sh.get("noteCounts"), dict):
            out["noteCounts"] = {k: sh["noteCounts"].get(k) for k in _NOTE_KEYS} | sh["noteCounts"]
        return out

    return {
        "updateTime": v1.get("updatedAt"),
        "difficulties": v1.get("difficulties", []),
        "types": v1.get("types", []),
        "versions": v1.get("versions", []),
        "songs": [s | {"songId": id_map[s["id"]], "sheets": [sheet(sh) for sh in s.get("sheets", [])]} for s in v1["songs"]],
    }


def extras_from_v1(v1: dict, id_map: dict[str, str]) -> dict:
    """The bundled community data, re-keyed to `songId` (entries for unknown songs dropped)."""
    aliases: dict[str, list[str]] = {}
    for a in v1.get("aliases") or []:
        song_id, name = id_map.get(a.get("song_id")), a.get("name")
        if song_id and name and name not in aliases.setdefault(song_id, []):
            aliases[song_id].append(name)
    return {
        "aliases": aliases,
        "tags": v1.get("tags") or [],
        "tagGroups": v1.get("tagGroups") or [],
        "tagSongs": [ts | {"song_id": id_map[ts["song_id"]]} for ts in v1.get("tagSongs") or [] if ts.get("song_id") in id_map],
    }


def fetch_upstream(etag: str | None = None, timeout: float = 60) -> tuple[dict | None, str | None]:
    """(document, its ETag), or (None, etag) on 304 Not Modified. Accept-Encoding is pinned so the
    weak ETag (which names the encoding) stays comparable between runs."""
    headers = {"Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    resp = httpx.get(UPSTREAM_URL, headers=headers, timeout=timeout, follow_redirects=True)
    if resp.status_code == 304:
        return None, etag
    resp.raise_for_status()
    return resp.json(), resp.headers.get("etag")


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
    """Refresh data/dxdata.json (and data/dxrating_extras.json from a v1 document). Returns
    (changed, summary); `changed` also covers an aliases/tags-only change, since the catalog reads
    its aliases from the extras file. Raises on download failure or a failed sanity check, leaving
    both files untouched. Blocking."""
    existing = read_existing()
    old_extras = dxrating_extras.load() or {}
    etag = None
    if upstream is None:
        # only revalidate when both files are there to fall back on
        cached = old_extras.get("etag") if DATA_PATH.exists() else None
        upstream, etag = fetch_upstream(cached)
        if upstream is None:
            return False, "upstream unchanged (304)"
    new_extras = None
    if is_v1(upstream):
        id_map = legacy_ids(upstream["songs"], existing.get("songs", []))
        new_extras = extras_from_v1(upstream, id_map)
        upstream = normalize_v1(upstream, id_map)
    data = build(upstream, [s["songId"] for s in existing.get("songs", [])])
    validate(data, existing)
    text = serialise(data)
    sheets = [sh for s in data["songs"] for sh in s["sheets"]]
    summary = (
        f"{len(data['songs'])} songs ({len(data['songs']) - len(existing.get('songs', []))} new), "
        f"{len(sheets)} sheets, {sum(1 for sh in sheets if not sh['intl'])} not on INTL, "
        f"INTL version {detect_intl_version(upstream)}, updateTime {data['updateTime']}"
    )
    data_changed = not DATA_PATH.exists() or DATA_PATH.read_text(encoding="utf-8") != text
    extras_changed = new_extras is not None and new_extras != {k: v for k, v in old_extras.items() if k != "etag"}
    if new_extras is not None:
        summary += f", {sum(map(len, new_extras['aliases'].values()))} aliases, {len(new_extras['tagSongs'])} tag links"
    if not dry_run:
        if data_changed:
            write_atomic(DATA_PATH, text)
        if new_extras is not None:
            # written even when unchanged: it carries the ETag for the next revalidation
            dxrating_extras.save(new_extras | {"etag": etag})
    return data_changed or extras_changed, summary


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
