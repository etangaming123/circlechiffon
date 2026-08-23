"""
dxrating.net chart tag API client.

`GET https://miruku.dxrating.net/api/v1/tags` is public/unauthenticated
(confirmed by source inspection of gekichumai/dxrating's route definitions
and its own test suite this session) and returns the *entire* tag catalog
in one call as {tags, tagGroups, tagSongs}. A tag attaches to a chart via an
exact (song_id, sheet_type, sheet_difficulty) triple in tagSongs - there is
no song-level fallback on dxrating's side, so a chart with no difficulty
selected just gets no tags shown (matches dxrating's own frontend behavior).

The backend itself caches this response for 30 minutes; we mirror that with
our own client-side cache (matching dxrating's frontend SWR window) so we
don't hammer their API on every /cc-info call.
"""

import time
from dataclasses import dataclass

import httpx

TAGS_API_URL = "https://miruku.dxrating.net/api/v1/tags"
CACHE_TTL_SECONDS = 60 * 60  # 1 hour


@dataclass(slots=True, frozen=True)
class Tag:
    id: int
    name: str
    description: str
    group_name: str | None


class DxRatingTagsClient:
    def __init__(self):
        self._tags: list[dict] = []
        self._tag_groups: list[dict] = []
        self._tag_songs: list[dict] = []
        self._fetched_at: float = 0.0

    async def _ensure_fresh(self) -> None:
        if self._tag_songs and (time.monotonic() - self._fetched_at) < CACHE_TTL_SECONDS:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(TAGS_API_URL)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            # keep whatever (possibly empty, possibly stale) data we already
            # have - tags are enrichment, never worth failing a command over
            return

        self._tags = data.get("tags", [])
        self._tag_groups = data.get("tagGroups", [])
        self._tag_songs = data.get("tagSongs", [])
        self._fetched_at = time.monotonic()

    async def find_tags(self, song_id: str, sheet_type: str, sheet_difficulty: str) -> list[Tag]:
        await self._ensure_fresh()

        tags_by_id = {t["id"]: t for t in self._tags}
        groups_by_id = {g["id"]: g for g in self._tag_groups}

        result = []
        for ts in self._tag_songs:
            if (
                ts.get("song_id") != song_id
                or ts.get("sheet_type") != sheet_type
                or ts.get("sheet_difficulty") != sheet_difficulty
            ):
                continue
            tag = tags_by_id.get(ts.get("tag_id"))
            if tag is None:
                continue
            group = groups_by_id.get(tag.get("group_id"))
            result.append(
                Tag(
                    id=tag["id"],
                    name=(tag.get("localized_name") or {}).get("en") or "?",
                    description=(tag.get("localized_description") or {}).get("en") or "",
                    group_name=(group.get("localized_name") or {}).get("en") if group else None,
                )
            )
        return result


_client: DxRatingTagsClient | None = None


def get_tags_client() -> DxRatingTagsClient:
    global _client
    if _client is None:
        _client = DxRatingTagsClient()
    return _client
