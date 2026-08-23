"""
Achievement-rank/combo/sync badge icon fetch-and-cache helper for maimai DX
NET's own static UI assets (confirmed public/unauthenticated this session) -
unlike a player's profile icon or rank-course/class badges, these are
generic game-UI icons, identical for every account, so they're cacheable
the same way dxrating jackets are (see adapters/dxrating/images.py, same
shape deliberately mirrored here).

Two icon families exist on the live site:
- The circular "music_icon_*" set (`.../img/music_icon_{name}.png`) -
  matches the round badges shown on maimai DX NET's own Player's Data page
  clear-count table. Covers S and above for rank (s/sp/ss/ssp/sss/sssp) and
  all of combo/sync - but does NOT exist for D through AAA.
- The rectangular "playlog/*" plaque set (`.../img/playlog/{name}.png`) -
  matches the badges shown on the recent-plays/detail pages. Covers every
  rank tier D through SSS+.

Rank uses circular where available (S+) and falls back to the plaque style
below S, since no circular asset exists for those tiers. Combo/sync are
fully covered by the circular set, so those always use it.
"""

import asyncio
from pathlib import Path

import httpx

from circlechiffon.types import ComboFlag, SyncFlag

BASE_URL = "https://maimaidx-eng.com/maimai-mobile/img"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "badge_icons_cache"

# rank_tag (matches ratingcalc.calculator.RANK_NAMES keys) -> path relative
# to BASE_URL, no ".png". Below-S tiers have no circular asset.
RANK_FILES = {
    "d": "playlog/d", "c": "playlog/c", "b": "playlog/b", "bb": "playlog/bb", "bbb": "playlog/bbb",
    "a": "playlog/a", "aa": "playlog/aa", "aaa": "playlog/aaa",
    "s": "music_icon_s", "sp": "music_icon_sp", "ss": "music_icon_ss", "ssp": "music_icon_ssp",
    "sss": "music_icon_sss", "sssp": "music_icon_sssp",
}
COMBO_FILES = {
    ComboFlag.fc: "music_icon_fc", ComboFlag.fcp: "music_icon_fcp",
    ComboFlag.ap: "music_icon_ap", ComboFlag.app: "music_icon_app",
}
SYNC_FILES = {
    SyncFlag.sync: "music_icon_sync", SyncFlag.fs: "music_icon_fs", SyncFlag.fsp: "music_icon_fsp",
    SyncFlag.fsd: "music_icon_fdx", SyncFlag.fsdp: "music_icon_fdxp",
}
# Player's Data page clear-count grid tiers with no other caller (see
# parser.py's parse_profile() / MusicCountEntry) - "clear" has no rank/
# combo/sync equivalent at all, and the deluxe-rating star tiers are their
# own icon family entirely.
CLEAR_FILES = {"clear": "music_icon_clear"}
DXSTAR_FILES = {str(n): f"music_icon_dxstar_{n}" for n in range(1, 6)}
# generic public UI icon, same family as the above.
STAR_ICON_PATH = "icon_star"


def _cache_path(path: str) -> Path:
    return CACHE_DIR / f"{path.replace('/', '_')}.png"


async def get_icon_bytes(path: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    """`path` is relative to BASE_URL, no extension (e.g. "music_icon_sssp"
    or "playlog/d"). Returns the icon's PNG bytes, disk-cached after first
    fetch. Returns None on any failure - callers should skip the icon (fall
    back to text) rather than failing the whole command/render."""
    cache_path = _cache_path(path)
    if cache_path.exists():
        return await asyncio.to_thread(cache_path.read_bytes)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        try:
            resp = await client.get(f"{BASE_URL}/{path}.png")
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        data = resp.content
    finally:
        if owns_client:
            await client.aclose()

    def _write():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)

    await asyncio.to_thread(_write)
    return data


async def get_all_badge_icons() -> dict[str, bytes]:
    """Fetches (cache-first) every rank/combo/sync/clear/dxstar icon plus
    the star icon at once, keyed 'rank:<tag>', 'combo:<value>',
    'sync:<value>', 'clear:clear', 'dxstar:<1-5>', 'misc:star'. Small (~30
    tiny PNGs) and static, so callers can just fetch this once and reuse
    the dict for an entire render or an entire bot process lifetime."""
    results: dict[str, bytes] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:

        async def fetch(key: str, path: str):
            data = await get_icon_bytes(path, client=client)
            if data is not None:
                results[key] = data

        tasks = [fetch(f"rank:{tag}", path) for tag, path in RANK_FILES.items()]
        tasks += [fetch(f"combo:{flag.value}", path) for flag, path in COMBO_FILES.items()]
        tasks += [fetch(f"sync:{flag.value}", path) for flag, path in SYNC_FILES.items()]
        tasks += [fetch(f"clear:{tag}", path) for tag, path in CLEAR_FILES.items()]
        tasks += [fetch(f"dxstar:{tag}", path) for tag, path in DXSTAR_FILES.items()]
        tasks.append(fetch("misc:star", STAR_ICON_PATH))
        await asyncio.gather(*tasks)

    # aliases so the Player's Data grid (parser.py's MusicCountEntry, which
    # uses the on-page filename stem "fdx"/"fdxp") can look these up without
    # depending on SyncFlag's enum value naming - same bytes, no extra fetch.
    if "sync:fsd" in results:
        results["sync:fdx"] = results["sync:fsd"]
    if "sync:fsdp" in results:
        results["sync:fdxp"] = results["sync:fsdp"]

    return results
