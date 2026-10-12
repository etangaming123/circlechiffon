"""
Current-version title logo from SEGA's public maimai marketing site
(https://maimai.sega.com/ - confirmed this session to serve
`<html class="top en" lang="en">` / "maimai DX International ver.", i.e.
it IS the INTL site, matching this bot's INTL-only scope). No login, no
cookies - unrelated to adapters/maimai_net/, which scrapes the
session-authenticated DX NET service.

The logo's URL carries the *version slug* as a path segment:

    ./assets/img/circle/common/logo.png   ("circle" = CiRCLE / CiRCLE PLUS)

so it can't be hardcoded - a new major version changes the slug. The URL
is discovered by parsing the site's header <img> instead.

Disk-cached the same way adapters/dxrating/images.py and
adapters/maimai_net/badge_icons.py cache their assets, with one
deliberate difference: those cache forever (a jacket or a rank plaque
never changes), this one carries a TTL, because the bytes behind the URL
change on every version - and the slug does NOT (CiRCLE and CiRCLE PLUS
both live under "circle"), so a slug change can't be relied on to signal
it. The TTL is a day (two small requests) and cogs/dxdata.py calls
invalidate() when the catalog's INTL version moves. Failures back off so a
down site doesn't add its timeout to every render.
"""

import asyncio
import io
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image
from selectolax.parser import HTMLParser

SITE_URL = "https://maimai.sega.com/"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "version_logo_cache"
CACHE_IMAGE = CACHE_DIR / "logo.png"
CACHE_TTL_SECONDS = 24 * 60 * 60
RETRY_AFTER_FAILURE_SECONDS = 15 * 60

_force_refresh = False
_next_attempt = 0.0  # monotonic time before which a failed refresh isn't retried
_lock: asyncio.Lock | None = None

# The header logo, version slug left open. Deliberately not anchored to
# "circle" so the next version's slug matches without a code change.
_LOGO_SRC_RE = re.compile(r"assets/img/[^/\"']+/common/logo\.png")

_TIMEOUT = 10.0


def _find_logo_url(html: str) -> str | None:
    """Resolves the header logo's absolute URL from the site's HTML.
    Prefers a real <img> tag; falls back to a raw regex scan in case the
    markup around it changes shape (the path pattern itself is far more
    stable than the tag it sits in)."""
    tree = HTMLParser(html)
    for node in tree.css("img"):
        src = node.attributes.get("src") or ""
        if _LOGO_SRC_RE.search(src):
            return str(httpx.URL(SITE_URL).join(src))
    match = _LOGO_SRC_RE.search(html)
    if match:
        return str(httpx.URL(SITE_URL).join(match.group(0)))
    return None


async def _fetch() -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            page = await client.get(SITE_URL)
            if page.status_code != 200:
                return None
            url = _find_logo_url(page.text)
            if url is None:
                return None
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            if not resp.headers.get("content-type", "").startswith("image/"):
                return None
            return resp.content
    except httpx.HTTPError:
        return None


def _read_cache() -> bytes | None:
    try:
        return CACHE_IMAGE.read_bytes()
    except OSError:
        return None


def _cache_is_fresh() -> bool:
    """The cached file's own mtime is the fetch timestamp - no sidecar
    metadata file needed (and `.gitignore`'s blanket `*.json` would hide
    one from git anyway, which is a confusing place to keep state)."""
    try:
        return (time.time() - CACHE_IMAGE.stat().st_mtime) < CACHE_TTL_SECONDS
    except OSError:
        return False


def _write_cache(data: bytes) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_IMAGE.with_name(CACHE_IMAGE.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, CACHE_IMAGE)  # a crash never leaves a half-written logo


def _is_image(data: bytes) -> bool:
    """A 200 with a wrong body (error page, truncated download) must not replace a good cache."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False


def invalidate() -> None:
    """Make the next get_version_logo() refetch now (the old file stays as the fallback)."""
    global _force_refresh, _next_attempt
    _force_refresh = True
    _next_attempt = 0.0


async def get_version_logo() -> bytes | None:
    """PNG bytes of the current maimai DX version's title logo, or None
    if it can't be fetched and nothing is cached. Never raises - callers
    (the b50 renderer) simply omit the logo when this is None, same
    degrade-to-placeholder contract as dxrating jackets."""
    global _force_refresh, _next_attempt, _lock
    cached = _read_cache()
    if cached is not None and _cache_is_fresh() and not _force_refresh:
        return cached
    if time.monotonic() < _next_attempt:
        return cached  # a recent refresh failed; don't stall every render on the timeout

    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        # a concurrent caller may have refreshed while this one waited
        cached = _read_cache()
        if cached is not None and _cache_is_fresh() and not _force_refresh:
            return cached

        fetched = await _fetch()
        if fetched is not None and _is_image(fetched):
            _force_refresh = False
            try:
                await asyncio.to_thread(_write_cache, fetched)
            except OSError:
                pass  # rendering doesn't depend on the cache write succeeding
            return fetched

        # refresh failed - a stale logo beats no logo at all
        _next_attempt = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
        return cached
