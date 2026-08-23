"""
dxrating.net song aliases API client. `GET https://miruku.dxrating.net/api/v1/aliases`
is public/unauthenticated (confirmed live this session, same host as the
tags API) and returns every alias as {song_id, name} pairs. song_id here
uses the same literal title-string scheme dxdata.json's own "songId" field
uses (confirmed live: e.g. "raputa" appears as an alias song_id here, and
dxdata.json songId values are likewise literal titles), so no
id-translation layer is needed - a straight dict join on the string.

Fetched once, synchronously, at catalog-load time - catalog loading itself
is eager/synchronous at startup (songdata/catalog.py), and this is a
one-time call, so it isn't worth threading async through the whole loader
for it.
"""

import httpx

ALIASES_API_URL = "https://miruku.dxrating.net/api/v1/aliases"


def fetch_aliases_by_song_id() -> dict[str, list[str]]:
    """Returns {song_id: [alias, ...]}. Returns an empty dict on any
    failure (network error, bad response) - aliases are enrichment only,
    never worth failing catalog load/bot startup over."""
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
