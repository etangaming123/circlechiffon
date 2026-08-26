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
change on every major version. Major versions ship roughly every 6
months, so the TTL is long - one refetch a month is plenty to pick up a
new one, and costs a single request.
"""

import asyncio
import re
import time
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

SITE_URL = "https://maimai.sega.com/"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "version_logo_cache"
CACHE_IMAGE = CACHE_DIR / "logo.png"
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

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
    CACHE_IMAGE.write_bytes(data)


async def get_version_logo() -> bytes | None:
    """PNG bytes of the current maimai DX version's title logo, or None
    if it can't be fetched and nothing is cached. Never raises - callers
    (the b50 renderer) simply omit the logo when this is None, same
    degrade-to-placeholder contract as dxrating jackets."""
    cached = _read_cache()
    if cached is not None and _cache_is_fresh():
        return cached

    fetched = await _fetch()
    if fetched is not None:
        try:
            await asyncio.to_thread(_write_cache, fetched)
        except OSError:
            pass  # rendering doesn't depend on the cache write succeeding
        return fetched

    # refresh failed - a stale logo beats no logo at all
    return cached
