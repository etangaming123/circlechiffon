"""
Jacket/cover image fetch-and-cache helper for dxrating.net's public asset
CDN (`https://shama.dxrating.net/images/cover/v2/{imageName}.jpg`, confirmed
public/unauthenticated by source inspection of gekichumai/dxrating this
session). Images are fetched once per song and cached to local disk
(data/jackets_cache/, gitignored) rather than either linking a third-party
URL on every embed or bulk-downloading the entire ~1762-song catalog upfront
- "download once on first use, reuse after that."
"""

import asyncio
from pathlib import Path

import httpx

JACKET_BASE_URL = "https://shama.dxrating.net/images/cover/v2"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "jackets_cache"


def jacket_url(image_name: str) -> str:
    return f"{JACKET_BASE_URL}/{image_name}.jpg"


def _cache_path(image_name: str) -> Path:
    return CACHE_DIR / f"{image_name}.jpg"


async def get_jacket_bytes(image_name: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    """Returns the jacket image bytes for `image_name`, using the local disk
    cache if present, else fetching from dxrating's CDN and caching it.
    Returns None if the image can't be fetched (network error, 404, etc.) -
    callers should fall back to a placeholder rather than failing the whole
    command/render."""
    cache_path = _cache_path(image_name)
    if cache_path.exists():
        return await asyncio.to_thread(cache_path.read_bytes)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        try:
            resp = await client.get(jacket_url(image_name))
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


async def get_jackets_bulk(image_names: list[str], concurrency: int = 10) -> dict[str, bytes]:
    """Fetches (cache-first) multiple jackets concurrently, e.g. for a best-50
    render that may need up to 50 distinct jacket images. Missing/failed
    fetches are simply absent from the returned dict."""
    results: dict[str, bytes] = {}
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=10.0) as client:

        async def fetch_one(name: str):
            async with semaphore:
                data = await get_jacket_bytes(name, client=client)
                if data is not None:
                    results[name] = data

        await asyncio.gather(*(fetch_one(name) for name in {n for n in image_names if n}))

    return results
