"""
Encodes a Best50Result into the compact binary payload carried by the
"View Detailed List" link button on /cc-best (see cogs/records.py), and
turns that payload into the full detail-view URL.

The payload is deliberately NOT a JSON blob of titles/scores - Discord caps
a link-style button's `url` field at 512 characters, and even a compressed
JSON payload for up to 50 entries runs well over that. Instead each chart is
encoded as an index into the catalog's own song list (SongCatalog.index_of)
plus a handful of small enum/int fields, bit-packed with no per-entry byte
padding. The companion static page (b50-detail.html / siteresources/
b50-detail.js) decodes this by loading the exact same data/dxdata.json (git-
tracked, served from the same repo via GitHub Pages) and replaying the same
song ordering - see catalog.py's `_index_by_id` docstring.

Payload layout (all integers big-endian / MSB-first):
    header (5 bytes, byte-aligned):
        format_version   u8   currently 1
        catalog_stamp    u16  low 16 bits of crc32(catalog.update_time)
        b15_count        u8   number of encoded B15 entries (0-15)
        b35_count        u8   number of encoded B35 entries (0-35)
    then b15_count + b35_count entries (B15 first, then B35), each 43 bits
    packed continuously with no per-entry alignment:
        song_index          12 bits  0-4095, index into catalog.songs
        chart_type           1 bit   0=dx, 1=std (UTAGE never appears in b50)
        difficulty            3 bits  Difficulty enum declaration order
        achievement_scaled  21 bits  round(achievement * 10000), 0-2097151
        combo_flag            3 bits  0=none, then ComboFlag declaration order
        sync_flag             3 bits  0=none, then SyncFlag declaration order

Rating is not transmitted - it's fully derivable client-side from the
catalog's internal_level_value + achievement + combo_flag, the same formula
as ratingcalc/calculator.py.
"""

import base64
import zlib
from dataclasses import dataclass

from circlechiffon.ratingcalc.best50 import Best50Result, RatedEntry
from circlechiffon.songdata.catalog import SongCatalog
from circlechiffon.types import ChartType, ComboFlag, Difficulty, SyncFlag

FORMAT_VERSION = 1

# catalog.songs currently holds ~1762 entries - 12 bits (max 4095) leaves
# generous headroom for catalog growth without an encoding-format bump.
MAX_SONG_INDEX = (1 << 12) - 1
MAX_ACHIEVEMENT_SCALED = (1 << 21) - 1

_CHART_TYPE_CODE = {ChartType.dx: 0, ChartType.std: 1}
_DIFFICULTY_CODE = {d: i for i, d in enumerate(Difficulty)}
_COMBO_CODE = {None: 0} | {f: i + 1 for i, f in enumerate(ComboFlag)}
_SYNC_CODE = {None: 0} | {f: i + 1 for i, f in enumerate(SyncFlag)}

DEFAULT_BASE_URL = "https://cc.etangaming.xyz/b50-detail.html"
_MAX_URL_LENGTH = 512


class _BitWriter:
    def __init__(self):
        self._acc = 0
        self._bit_count = 0

    def write(self, value: int, width: int) -> None:
        self._acc = (self._acc << width) | (value & ((1 << width) - 1))
        self._bit_count += width

    def to_bytes(self) -> bytes:
        pad = (-self._bit_count) % 8
        return (self._acc << pad).to_bytes((self._bit_count + pad) // 8, "big")


@dataclass(slots=True, frozen=True)
class _EncodedEntry:
    song_index: int
    chart_type_code: int
    difficulty_code: int
    achievement_scaled: int
    combo_code: int
    sync_code: int


def _encode_entry(entry: RatedEntry, catalog: SongCatalog) -> _EncodedEntry | None:
    song = catalog.get_by_title(entry.score.title)
    if song is None:
        return None
    song_index = catalog.index_of(song.song_id)
    if song_index is None or song_index > MAX_SONG_INDEX:
        return None
    chart_type_code = _CHART_TYPE_CODE.get(entry.sheet.type)
    difficulty_code = _DIFFICULTY_CODE.get(entry.sheet.difficulty)
    if chart_type_code is None or difficulty_code is None:
        return None
    achievement_scaled = round(entry.score.achievement * 10000)
    if not (0 <= achievement_scaled <= MAX_ACHIEVEMENT_SCALED):
        return None
    return _EncodedEntry(
        song_index=song_index,
        chart_type_code=chart_type_code,
        difficulty_code=difficulty_code,
        achievement_scaled=achievement_scaled,
        combo_code=_COMBO_CODE.get(entry.score.combo_flag, 0),
        sync_code=_SYNC_CODE.get(entry.score.sync_flag, 0),
    )


def _catalog_stamp(catalog: SongCatalog) -> int:
    if not catalog.update_time:
        return 0
    return zlib.crc32(catalog.update_time.encode("utf-8")) & 0xFFFF


def encode_b50_payload(result: Best50Result, catalog: SongCatalog) -> bytes | None:
    """Returns the raw (pre-base64) payload bytes, or None if the result
    can't be safely encoded (e.g. a future catalog growing past
    MAX_SONG_INDEX) - callers should treat None as "don't attach the link
    this time", same graceful-degradation convention used elsewhere in this
    repo (chart-video falling back to a metadata embed, dxrating adapter
    falling back to a placeholder jacket)."""
    b15_encoded = [
        e for e in (_encode_entry(entry, catalog) for entry in result.b15 if entry is not None) if e is not None
    ]
    b35_encoded = [
        e for e in (_encode_entry(entry, catalog) for entry in result.b35 if entry is not None) if e is not None
    ]
    if len(b15_encoded) > 15 or len(b35_encoded) > 35:
        return None  # unreachable given Best50Result's own invariants, guarded anyway

    writer = _BitWriter()
    for entry in b15_encoded + b35_encoded:
        writer.write(entry.song_index, 12)
        writer.write(entry.chart_type_code, 1)
        writer.write(entry.difficulty_code, 3)
        writer.write(entry.achievement_scaled, 21)
        writer.write(entry.combo_code, 3)
        writer.write(entry.sync_code, 3)
    body = writer.to_bytes()

    header = bytes(
        [
            FORMAT_VERSION,
            (_catalog_stamp(catalog) >> 8) & 0xFF,
            _catalog_stamp(catalog) & 0xFF,
            len(b15_encoded),
            len(b35_encoded),
        ]
    )
    return header + body


def build_detail_view_url(
    result: Best50Result, catalog: SongCatalog, base_url: str = DEFAULT_BASE_URL
) -> str | None:
    """Returns the full URL for the b50 detail-view page, or None if a
    working link can't be produced (see encode_b50_payload) or if the
    assembled URL somehow exceeds Discord's 512-character link-button cap -
    callers should omit the button entirely in that case rather than send a
    button Discord will reject."""
    payload = encode_b50_payload(result, catalog)
    if payload is None:
        return None
    params = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    url = f"{base_url}?params={params}"
    if len(url) > _MAX_URL_LENGTH:
        return None
    return url
