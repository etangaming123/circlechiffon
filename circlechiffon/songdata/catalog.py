"""
Loads the vendored song/chart catalog (data/dxdata.json, trimmed from
gekichumai/dxrating's packages/dxdata/dxdata.json by update_dxdata.py, MIT-licensed,
resolved to INTL values with a per-sheet `intl` flag) into memory and provides
lookup/search over it.
"""

import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from circlechiffon.adapters.dxrating.aliases import fetch_aliases_by_song_id
from circlechiffon.types import ChartType, Difficulty, NoteCounts, Sheet, Song

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dxdata.json"

# A game version counts as "released on INTL" once this many of its charts are
# flagged intl. The source data marks a few charts of a JP-only version as INTL
# early (event/locked songs), so "any intl chart" would jump the B15 window
# ahead of the real INTL version.
MIN_INTL_SHEETS = 20


def _parse_note_counts(raw: dict | None) -> NoteCounts | None:
    if not raw:
        return None
    return NoteCounts(
        tap=raw.get("tap"),
        hold=raw.get("hold"),
        slide=raw.get("slide"),
        touch=raw.get("touch"),
        brk=raw.get("break"),
        total=raw.get("total"),
    )


def _parse_sheet(raw: dict) -> Sheet:
    try:
        difficulty = Difficulty(raw.get("difficulty"))
    except ValueError:
        difficulty = None  # UTAGE charts use free-text difficulty labels
    try:
        chart_type = ChartType(raw.get("type"))
    except ValueError:
        chart_type = None
    return Sheet(
        type=chart_type,
        difficulty=difficulty,
        level=raw.get("level"),
        internal_level_value=raw.get("internalLevelValue"),
        version=raw.get("version"),
        is_special=raw.get("isSpecial", False),
        note_designer=raw.get("noteDesigner"),
        release_date=raw.get("releaseDate"),
        note_counts=_parse_note_counts(raw.get("noteCounts")),
        intl=raw.get("intl", True),
        jp_internal_level_value=raw.get("jpInternalLevelValue"),
        jp_level=raw.get("jpLevel"),
    )


def _parse_song(raw: dict) -> Song:
    return Song(
        song_id=raw["songId"],
        title=raw["title"],
        artist=raw.get("artist"),
        category=raw.get("category"),
        bpm=raw.get("bpm"),
        image_name=raw.get("imageName"),
        sheets=[_parse_sheet(s) for s in raw.get("sheets", [])],
        search_acronyms=raw.get("searchAcronyms", []),
    )


class SongCatalog:
    def __init__(self, path: Path = DATA_PATH):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self.update_time: str | None = raw.get("updateTime")
        # skip entries with no real title (a handful of withdrawn/placeholder
        # rows in the source data have an empty/whitespace-only title, which
        # is useless to search for or display anyway)
        self.songs: list[Song] = [s for s in (_parse_song(r) for r in raw.get("songs", [])) if s.title.strip()]
        self._by_id: dict[str, Song] = {s.song_id: s for s in self.songs}
        # stable position of each song within self.songs - the b50 detail-view
        # link (renderers/b50_share.py) encodes a chart as this index rather
        # than its title/song_id, to keep the URL payload compact. The
        # static site (b50-detail.js) rebuilds the identical ordered list from
        # the same data/dxdata.json to decode it, so this ordering must never
        # be reshuffled independently of that file.
        self._index_by_id: dict[str, int] = {s.song_id: i for i, s in enumerate(self.songs)}

        # extend (not replace) each song's own searchAcronyms with extra
        # aliases pulled live from dxrating.net - search() already treats
        # all of search_acronyms uniformly, so no matching-logic changes are
        # needed for these to be searchable too.
        extra_aliases = fetch_aliases_by_song_id()
        for song in self.songs:
            for alias in extra_aliases.get(song.song_id, []):
                if alias not in song.search_acronyms:
                    song.search_acronyms.append(alias)

        # dxdata.json's top-level "versions" list is chronologically ordered, but
        # its last entry is the newest *JP* version, which INTL trails by months.
        # The current INTL version is the latest one with MIN_INTL_SHEETS or more
        # INTL-flagged charts, so a data refresh after an INTL update moves it
        # without a code change. It is used to bucket best-50 scores into "new"
        # (B15: current version + one version prior) vs "old" (B35).
        version_names = [v["version"] for v in raw.get("versions") or []]
        intl_counts = Counter(sh.version for song in self.songs for sh in song.sheets if sh.intl)
        released = [i for i, name in enumerate(version_names) if intl_counts[name] >= MIN_INTL_SHEETS]
        current_idx = released[-1] if released else (len(version_names) - 1)
        self.current_version: str | None = version_names[current_idx] if version_names else None
        self.previous_version: str | None = version_names[current_idx - 1] if current_idx >= 1 else None

        # INTL charts tagged with a version newer than the current INTL one (a
        # locked song of the next version showing up early) belong to the
        # current version for bucketing.
        if self.current_version is not None:
            rank = {name: i for i, name in enumerate(version_names)}
            for song in self.songs:
                for sh in song.sheets:
                    if sh.intl and rank.get(sh.version, -1) > current_idx:
                        sh.version = self.current_version

        self._by_title_lower: dict[str, list[Song]] = {}
        self._by_title_lower: dict[str, list[Song]] = {}
        for song in self.songs:
            self._by_title_lower.setdefault(song.title.lower(), []).append(song)

    def get(self, song_id: str) -> Song | None:
        return self._by_id.get(song_id)

    def get_by_title(self, title: str) -> Song | None:
        matches = self._by_title_lower.get(title.lower())
        return matches[0] if matches else None

    def index_of(self, song_id: str) -> int | None:
        return self._index_by_id.get(song_id)

    def find_sheet(self, title: str, chart_type: "ChartType | None", difficulty: "Difficulty | None") -> "Sheet | None":
        """Look up a specific chart's constant by (title, type, difficulty),
        used to fill in internal_level_value for scraped scores that don't
        carry it. Falls back across candidate songs sharing that title (rare
        title collisions) and returns the first sheet matching type+difficulty."""
        candidates = self._by_title_lower.get(title.lower(), [])
        for song in candidates:
            for sheet in song.sheets:
                if sheet.difficulty == difficulty and (chart_type is None or sheet.type == chart_type):
                    return sheet
        return None

    def search(self, query: str, limit: int = 10) -> list[Song]:
        """Match against a song's title AND its search_acronyms (English/
        romanized aliases vendored from dxdata.json), so e.g. "yoasobi" finds
        "夜に駆ける" via its "YOASOBI" alias. Exact match first, then
        substring match (ranked by similarity), then a fuzzy fallback across
        the whole catalog - each tier scored by the best match among the
        song's title and all of its aliases."""
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        exact_matches: list[Song] = []
        substring_matches: list[tuple[float, Song]] = []
        fuzzy_candidates: list[tuple[float, Song]] = []

        for song in self.songs:
            names_lower = [song.title.lower()] + [a.lower() for a in song.search_acronyms]

            if query_lower in names_lower:
                exact_matches.append(song)
                continue

            substring_names = [n for n in names_lower if query_lower in n]
            if substring_names:
                best_ratio = max(SequenceMatcher(None, query_lower, n).ratio() for n in substring_names)
                substring_matches.append((best_ratio, song))
                continue

            best_ratio = max(SequenceMatcher(None, query_lower, n).ratio() for n in names_lower)
            if best_ratio > 0.4:
                fuzzy_candidates.append((best_ratio, song))

        # exact matches first, then substring/fuzzy each sorted by
        # similarity - concatenated (not returned tier-by-tier) so a query
        # with a few exact/substring hits still gets backfilled with close
        # names once those run out, instead of being capped at whichever
        # tier happened to match first.
        # INTL charts win ties (and sort ahead within the exact tier): JP-only
        # charts stay findable but never outrank an equally good INTL match.
        substring_matches.sort(key=lambda t: (-t[0], t[1].jp_only))
        fuzzy_candidates.sort(key=lambda t: (-t[0], t[1].jp_only))
        exact_matches.sort(key=lambda song: song.jp_only)
        ordered = exact_matches + [s for _, s in substring_matches] + [s for _, s in fuzzy_candidates]
        return ordered[:limit]


_catalog: SongCatalog | None = None


def get_catalog() -> SongCatalog:
    global _catalog
    if _catalog is None:
        _catalog = SongCatalog()
    return _catalog
