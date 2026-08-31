"""
Loads the vendored song/chart catalog (data/dxdata.json, trimmed from
gekichumai/dxrating's packages/dxdata/dxdata.json, MIT-licensed) into memory
and provides lookup/search over it.
"""

import json
from difflib import SequenceMatcher
from pathlib import Path

from circlechiffon.adapters.dxrating.aliases import fetch_aliases_by_song_id
from circlechiffon.types import ChartType, Difficulty, NoteCounts, Sheet, Song

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dxdata.json"


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

        # dxdata.json's top-level "versions" list is chronologically ordered
        # (confirmed against the source: last entry is the most recently
        # released game version) - used to bucket best-50 scores into
        # "new" (B15: current version + one version prior) vs "old" (B35:
        # everything else), per the user's own rating-window definition.
        versions = raw.get("versions") or []
        self.current_version: str | None = versions[-1]["version"] if versions else None
        self.previous_version: str | None = versions[-2]["version"] if len(versions) >= 2 else None

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
        substring_matches.sort(key=lambda t: t[0], reverse=True)
        fuzzy_candidates.sort(key=lambda t: t[0], reverse=True)
        ordered = exact_matches + [s for _, s in substring_matches] + [s for _, s in fuzzy_candidates]
        return ordered[:limit]


_catalog: SongCatalog | None = None


def get_catalog() -> SongCatalog:
    global _catalog
    if _catalog is None:
        _catalog = SongCatalog()
    return _catalog
