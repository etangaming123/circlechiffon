"""
Whether maimai DX NET (INTL) is under SEGA maintenance, from dxrating.net's
public `GET https://miruku.dxrating.net/api/v1/io/import/maimai-net/status`
(no auth; confirmed live: {"intl": {"status": "ok", "checkedAt": ...}, "jp": ...},
status one of "ok" / "maintenance" / "unknown").

Advisory only, and fail-open: anything but a fresh "maintenance" answer -
network error, timeout, "unknown", a stale `checkedAt` - is treated as "not
known to be down", so dxrating being unreachable never blocks a command. The
bot's own maintenance detection on the page itself (parser.is_maintenance)
still runs either way; this just lets it say so before touching DX NET.
"""

import asyncio
import time
from datetime import datetime, timezone

import httpx

STATUS_URL = "https://miruku.dxrating.net/api/v1/io/import/maimai-net/status"

# how long one answer is reused; dxrating's own check runs on its own cadence
CACHE_SECONDS = 60
# an answer whose checkedAt is older than this is ignored (their checker stalled)
MAX_AGE_SECONDS = 15 * 60
TIMEOUT_SECONDS = 3.0

_cache: tuple[float, bool] | None = None
_refresh: asyncio.Task | None = None


def _parse_checked_at(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_maintenance_answer(data: dict, now: datetime | None = None) -> bool:
    """True only for a recent, explicit INTL "maintenance"."""
    intl = (data or {}).get("intl") or {}
    if intl.get("status") != "maintenance":
        return False
    checked = _parse_checked_at(intl.get("checkedAt"))
    if checked is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - checked).total_seconds() <= MAX_AGE_SECONDS


async def _fetch() -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(STATUS_URL)
            resp.raise_for_status()
            return is_maintenance_answer(resp.json())
    except (httpx.HTTPError, ValueError):
        return False


async def _refresh_cache() -> bool:
    global _cache
    down = await _fetch()
    _cache = (time.monotonic(), down)
    return down


async def intl_under_maintenance() -> bool:
    """Never raises, and only the first call ever waits on the network (at most
    TIMEOUT_SECONDS): after that the last answer is returned at once and a
    stale one (older than CACHE_SECONDS) is refreshed in the background, so
    this adds no latency to commands."""
    global _refresh
    if _cache is None:
        if _refresh is None or _refresh.done():
            _refresh = asyncio.create_task(_refresh_cache())
        return await asyncio.shield(_refresh)
    if time.monotonic() - _cache[0] >= CACHE_SECONDS and (_refresh is None or _refresh.done()):
        _refresh = asyncio.create_task(_refresh_cache())
    return _cache[1]
