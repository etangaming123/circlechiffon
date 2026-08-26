"""
mai-notes.com (maiノーツ) chart catalog.

mai-notes is a community maimai chart database whose viewer draws charts
into a canvas from simai data. Its entire catalog is one public static
JSON - `GET https://mai-notes.com/data/manifest.json`, confirmed live this
session to serve `content-type: application/json` with
`access-control-allow-origin: *` and no auth of any kind. `robots.txt`
allows `/` (only /edit.html, /login.html, /mypage.html and /versus.html
are disallowed), so both the manifest and the player are fair game.

Shape of the manifest, as observed:

    {generated_at, songs_count: 1558, charts_count: 6378,
     songs: {<song-uuid>: {id, title, artist, bpm, genre, version,
                           type: "deluxe"|"standard", release_date, ...}},
     charts: [{id, song_id, difficulty: "BASIC".."Re:MASTER", level,
               internal_level, notes_designer, has_chart_data, notes,
               taps, hold, slide, touch, breaks, top_dxscore,
               top_player_name, tags, ...}],
     tags: [{id, name, tag_group, ...}]}

Note `songs` is an object keyed by uuid while `charts` is a flat array.
Note also that mai-notes models a song's DX and STANDARD versions as two
*separate* song rows sharing one title, distinguished by `type` - so the
join key against our own catalog is (title, chart type, difficulty).

There is no shared id between dxdata.json and mai-notes (dxdata's `songId`
is a literal title string), hence the title matching in `find_chart`.
Measured against the live manifest: 6334 of our 7140 non-UTAGE sheets
match (89%), of which 2850 have `has_chart_data` and can actually be
rendered. The ~800 that don't match are overwhelmingly licence-removed
songs (君の知らない物語, おじゃま虫, This game, ヒビカセ, ...) that mai-notes
doesn't carry, plus UTAGE, which it has no concept of.

The manifest is ~3.9MB, so it is disk-cached rather than refetched per
command. mai-notes regenerates it about once a day (`generated_at` moved
by an hour over the course of one planning session), hence the 24h TTL.
"""

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import httpx

from circlechiffon.types import ChartType, Difficulty

MANIFEST_URL = "https://mai-notes.com/data/manifest.json"
PLAYER_URL = "https://mai-notes.com/player.html?chart={chart_id}"
CHART_DATA_URL = "https://mai-notes.com/data/charts/{chart_id}.txt"

CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "mainotes_cache"
CACHE_FILE = CACHE_DIR / "manifest.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 1 day

_TIMEOUT = 30.0  # 3.9MB, ~0.8s on a good link - but be generous
_USER_AGENT = "circlechiffon/1.0 (maimai Discord bot; +https://mai-notes.com/)"

# Our Difficulty enum <-> mai-notes' difficulty strings.
_DIFFICULTY_TO_MAINOTES = {
    Difficulty.basic: "BASIC",
    Difficulty.advanced: "ADVANCED",
    Difficulty.expert: "EXPERT",
    Difficulty.master: "MASTER",
    Difficulty.remaster: "Re:MASTER",
}
# mai-notes' song-level `type` <-> our per-sheet ChartType. UTAGE has no
# mai-notes equivalent at all.
_MAINOTES_TO_CHART_TYPE = {"deluxe": ChartType.dx, "standard": ChartType.std}
_CHART_TYPE_TO_MAINOTES = {v: k for k, v in _MAINOTES_TO_CHART_TYPE.items()}


def _normalize_title(title: str) -> str:
    """Title join key. NFKC folds the full-width/half-width split that the
    two catalogs don't agree on, and `Cf` (format) characters have to go
    because one real mai-notes song title is a single invisible character."""
    folded = unicodedata.normalize("NFKC", title)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", folded).strip().casefold()


@dataclass(slots=True, frozen=True)
class MaiNotesSong:
    id: str
    title: str
    artist: str | None
    bpm: str | None
    genre: str | None
    version: str | None
    chart_type: ChartType | None


@dataclass(slots=True, frozen=True)
class MaiNotesChart:
    id: str
    song: MaiNotesSong
    difficulty: str
    level: str | None
    internal_level: float | None
    version: str | None
    notes_designer: str | None
    has_chart_data: bool
    notes: int | None
    taps: int | None
    hold: int | None
    slide: int | None
    touch: int | None
    breaks: int | None
    top_dxscore: int | None
    top_player_name: str | None
    tags: tuple[str, ...]

    @property
    def player_url(self) -> str:
        return PLAYER_URL.format(chart_id=self.id)


class MaiNotesCatalog:
    def __init__(self):
        self._generated_at: str | None = None
        self._songs_by_title: dict[str, list[MaiNotesSong]] = {}
        self._charts: dict[tuple[str, str], MaiNotesChart] = {}
        self._loaded_at: float = 0.0

    # -- fetching / caching -------------------------------------------------

    def _cache_is_fresh(self) -> bool:
        """The cached file's own mtime is the fetch timestamp - same
        no-sidecar approach as adapters/maimai_site/version_logo.py."""
        try:
            return (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_TTL_SECONDS
        except OSError:
            return False

    def _read_cache(self) -> dict | None:
        try:
            with CACHE_FILE.open("rb") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _write_cache(self, raw: bytes) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_bytes(raw)

    async def _fetch(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
                resp = await client.get(MANIFEST_URL)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        try:
            await asyncio.to_thread(self._write_cache, resp.content)
        except OSError:
            pass  # lookups don't depend on the cache write succeeding
        return data

    async def ensure_loaded(self) -> bool:
        """Loads the manifest into memory if it isn't already, refreshing
        from mai-notes when the disk cache has aged out. Returns False only
        when there is nothing usable at all (no network, no cache) - a
        stale cache is always preferred over nothing."""
        if self._charts and (time.monotonic() - self._loaded_at) < CACHE_TTL_SECONDS:
            return True

        data = None
        if self._cache_is_fresh():
            data = await asyncio.to_thread(self._read_cache)
        if data is None:
            data = await self._fetch()
        if data is None:
            data = await asyncio.to_thread(self._read_cache)  # stale beats nothing
        if data is None:
            return False

        try:
            self._index(data)
        except (AttributeError, TypeError, ValueError):
            return False
        self._loaded_at = time.monotonic()
        return True

    def _index(self, data: dict) -> None:
        songs_by_id: dict[str, MaiNotesSong] = {}
        songs_by_title: dict[str, list[MaiNotesSong]] = {}
        for raw in (data.get("songs") or {}).values():
            song = MaiNotesSong(
                id=raw["id"],
                title=raw.get("title") or "",
                artist=raw.get("artist"),
                bpm=raw.get("bpm"),
                genre=raw.get("genre"),
                version=raw.get("version"),
                chart_type=_MAINOTES_TO_CHART_TYPE.get(raw.get("type")),
            )
            songs_by_id[song.id] = song
            songs_by_title.setdefault(_normalize_title(song.title), []).append(song)

        charts: dict[tuple[str, str], MaiNotesChart] = {}
        for raw in data.get("charts") or []:
            song = songs_by_id.get(raw.get("song_id"))
            if song is None:
                continue
            tags = tuple(t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name"))
            charts[(song.id, raw.get("difficulty"))] = MaiNotesChart(
                id=raw["id"],
                song=song,
                difficulty=raw.get("difficulty") or "",
                level=raw.get("level"),
                internal_level=raw.get("internal_level"),
                version=raw.get("version"),
                notes_designer=raw.get("notes_designer"),
                has_chart_data=bool(raw.get("has_chart_data")),
                notes=raw.get("notes"),
                taps=raw.get("taps"),
                hold=raw.get("hold"),
                slide=raw.get("slide"),
                touch=raw.get("touch"),
                breaks=raw.get("breaks"),
                top_dxscore=raw.get("top_dxscore"),
                top_player_name=raw.get("top_player_name"),
                tags=tags,
            )

        self._generated_at = data.get("generated_at")
        self._songs_by_title = songs_by_title
        self._charts = charts

    # -- lookup -------------------------------------------------------------

    @property
    def generated_at(self) -> str | None:
        return self._generated_at

    def available_chart_types(self, title: str) -> list[ChartType]:
        """Which chart types mai-notes carries for a title, in DX-then-STD
        order. Empty when it doesn't have the song at all."""
        songs = self._songs_by_title.get(_normalize_title(title), [])
        found = {s.chart_type for s in songs if s.chart_type is not None}
        return [t for t in (ChartType.dx, ChartType.std) if t in found]

    def find_chart(
        self,
        title: str,
        chart_type: ChartType,
        difficulty: Difficulty,
        artist: str | None = None,
    ) -> MaiNotesChart | None:
        """The (title, type, difficulty) join. `artist` only ever breaks a
        tie: exactly one title in the live manifest ("link") has two rows
        of the same type, and they are genuinely different songs."""
        wanted_type = _CHART_TYPE_TO_MAINOTES.get(chart_type)
        wanted_difficulty = _DIFFICULTY_TO_MAINOTES.get(difficulty)
        if wanted_type is None or wanted_difficulty is None:
            return None  # UTAGE, or a difficulty mai-notes doesn't model

        candidates = [
            s for s in self._songs_by_title.get(_normalize_title(title), [])
            if s.chart_type is chart_type
        ]
        if not candidates:
            return None
        if len(candidates) > 1 and artist:
            wanted_artist = _normalize_title(artist)
            candidates.sort(key=lambda s: _normalize_title(s.artist or "") != wanted_artist)

        for song in candidates:
            chart = self._charts.get((song.id, wanted_difficulty))
            if chart is not None:
                return chart
        return None


_catalog: MaiNotesCatalog | None = None


def get_mainotes_catalog() -> MaiNotesCatalog:
    global _catalog
    if _catalog is None:
        _catalog = MaiNotesCatalog()
    return _catalog
