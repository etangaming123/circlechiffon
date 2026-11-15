"""
Song aliases from dxrating.net, keyed by the bot's `songId`.

First choice is data/dxrating_extras.json (extras.py), which update_dxdata.py
fills from the aliases bundled in the v1 dxdata document - no request at
catalog load. Fallback, when that file doesn't exist yet: the standalone
`GET https://miruku.dxrating.net/api/v1/aliases`, public/unauthenticated,
whose `song_id` is still the legacy title-string id dxdata.json's `songId`
uses (confirmed live: "raputa", "with U"), so it joins straight on songId.

Called synchronously at catalog-load time (songdata/catalog.py loads eagerly
at startup), so the fallback is a plain blocking call.
"""

import httpx

from circlechiffon.adapters.dxrating import extras

ALIASES_API_URL = "https://miruku.dxrating.net/api/v1/aliases"


def fetch_aliases_by_song_id() -> dict[str, list[str]]:
    """Returns {song_id: [alias, ...]}. Returns an empty dict on any
    failure (network error, bad response) - aliases are enrichment only,
    never worth failing catalog load/bot startup over."""
    bundled = (extras.load() or {}).get("aliases")
    if isinstance(bundled, dict):
        return bundled

    try:
        resp = httpx.get(ALIASES_API_URL, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}

    result: dict[str, list[str]] = {}
    for entry in data:
        song_id = entry.get("song_id")
        name = entry.get("name")
        if song_id and name:
            result.setdefault(song_id, []).append(name)
    return result
