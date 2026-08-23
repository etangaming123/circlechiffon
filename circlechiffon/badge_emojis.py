"""
Shows maimai DX NET's achievement-rank/combo/sync badge icons inline in
embeds (e.g. the real "FC+" plaque, the real "SSS+" plaque) instead of
plain text, via Discord custom emoji.

Two sources, manual takes priority:
1. `emoji_ids.json` (repo root, gitignored like config.json) - if you've
   manually uploaded assets/badge_icons/*.png as emoji yourself (e.g. to a
   guild, so they're also typeable by members, not just usable by the bot)
   and pasted the resulting emoji IDs in there, those are used as-is.
2. Any key left blank in that file falls back to this bot automatically
   uploading the icon as one of its own *application* emoji at startup
   (usable by the bot in its own messages, but not typeable by users).

Best-effort throughout: any failure (missing permission, rate limit, no
network, malformed emoji_ids.json) just means the affected badge falls back
to plain text - this never blocks startup or breaks a command.
"""

import asyncio
import json
from pathlib import Path

import httpx

from circlechiffon.adapters.maimai_net.badge_icons import COMBO_FILES, RANK_FILES, SYNC_FILES, get_icon_bytes
from circlechiffon.ratingcalc.calculator import RANK_NAMES
from circlechiffon.types import ComboFlag, SyncFlag

_COMBO_FALLBACK = {ComboFlag.fc: "FC", ComboFlag.fcp: "FC+", ComboFlag.ap: "AP", ComboFlag.app: "AP+"}
_SYNC_FALLBACK = {
    SyncFlag.sync: "SYNC", SyncFlag.fs: "FS", SyncFlag.fsp: "FS+",
    SyncFlag.fsd: "FDX", SyncFlag.fsdp: "FDX+",
}

EMOJI_IDS_PATH = Path(__file__).resolve().parent.parent / "emoji_ids.json"

_emoji_strings: dict[str, str] = {}


def _emoji_name(prefix: str, key: str) -> str:
    return f"cc_{prefix}_{key}"


def _load_manual_ids() -> dict[str, str]:
    if not EMOJI_IDS_PATH.exists():
        return {}
    try:
        with open(EMOJI_IDS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except (OSError, ValueError) as e:
        print(f"Couldn't read {EMOJI_IDS_PATH.name}, ignoring it: {type(e).__name__}: {e}")
        return {}


async def load(bot) -> None:
    """Call once from setup_hook, after login. Fills in `<:name:id>`
    strings for every badge from emoji_ids.json first, then uploads any
    still-missing ones as this bot's own application emoji. Never raises."""
    wanted = (
        [("rank", tag, stem) for tag, stem in RANK_FILES.items()]
        + [("combo", flag.value, stem) for flag, stem in COMBO_FILES.items()]
        + [("sync", flag.value, stem) for flag, stem in SYNC_FILES.items()]
    )

    manual_ids = _load_manual_ids()
    for prefix, key, _stem in wanted:
        cache_key = f"{prefix}:{key}"
        emoji_id = manual_ids.get(cache_key)
        if emoji_id:
            _emoji_strings[cache_key] = f"<:{_emoji_name(prefix, key)}:{emoji_id}>"

    remaining = [(prefix, key, stem) for prefix, key, stem in wanted if f"{prefix}:{key}" not in _emoji_strings]
    if remaining:
        try:
            existing = {e.name: e for e in await bot.fetch_application_emojis()}
        except Exception as e:
            print(f"Couldn't fetch application emojis - remaining badge icons will show as text: {type(e).__name__}: {e}")
            existing = None

        if existing is not None:
            to_upload = []
            for prefix, key, filename_stem in remaining:
                cache_key = f"{prefix}:{key}"
                emoji = existing.get(_emoji_name(prefix, key))
                if emoji is not None:
                    _emoji_strings[cache_key] = str(emoji)
                else:
                    to_upload.append((prefix, key, filename_stem))

            # Icon bytes are independent fetches - grab them all concurrently
            # through one shared client instead of opening/closing a new
            # connection per icon, sequentially.
            async with httpx.AsyncClient(timeout=10.0) as client:
                icon_bytes_list = await asyncio.gather(
                    *(get_icon_bytes(filename_stem, client=client) for _, _, filename_stem in to_upload)
                )

            for (prefix, key, _filename_stem), icon_bytes in zip(to_upload, icon_bytes_list):
                if icon_bytes is None:
                    continue
                name = _emoji_name(prefix, key)
                cache_key = f"{prefix}:{key}"
                # Discord's own API - keep this sequential, its own
                # rate-limit bucket handling is safer one at a time.
                try:
                    emoji = await bot.create_application_emoji(name=name, image=icon_bytes)
                except Exception as e:
                    print(f"Couldn't upload badge emoji {name}: {type(e).__name__}: {e}")
                    continue
                _emoji_strings[cache_key] = str(emoji)

    print(f"Badge emojis ready: {len(_emoji_strings)}/{len(wanted)} ({len(manual_ids)} from emoji_ids.json)")


def rank_badge(rank_tag: str) -> str:
    return _emoji_strings.get(f"rank:{rank_tag}") or RANK_NAMES.get(rank_tag, rank_tag.upper())


def combo_badge(combo_flag: ComboFlag | None) -> str:
    if combo_flag is None:
        return "-"
    return _emoji_strings.get(f"combo:{combo_flag.value}") or _COMBO_FALLBACK.get(combo_flag, combo_flag.value.upper())


def sync_badge(sync_flag: SyncFlag | None) -> str:
    if sync_flag is None:
        return "-"
    return _emoji_strings.get(f"sync:{sync_flag.value}") or _SYNC_FALLBACK.get(sync_flag, sync_flag.value.upper())
