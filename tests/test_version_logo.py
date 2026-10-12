"""adapters/maimai_site/version_logo.py - cache freshness, bad-body rejection, failure backoff."""

import asyncio
import io

import pytest
from PIL import Image

from circlechiffon.adapters.maimai_site import version_logo as vl


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(vl, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(vl, "CACHE_IMAGE", tmp_path / "logo.png")
    monkeypatch.setattr(vl, "_force_refresh", False)
    monkeypatch.setattr(vl, "_next_attempt", 0.0)
    monkeypatch.setattr(vl, "_lock", None)


def fake_fetch(monkeypatch, results):
    calls = []

    async def fetch():
        calls.append(1)
        return results[min(len(calls), len(results)) - 1]
    monkeypatch.setattr(vl, "_fetch", fetch)
    return calls


def test_fresh_cache_is_served_without_fetching(monkeypatch):
    vl._write_cache(b"old")
    calls = fake_fetch(monkeypatch, [png()])
    assert asyncio.run(vl.get_version_logo()) == b"old" and not calls


def test_invalidate_forces_one_refetch(monkeypatch):
    vl._write_cache(b"old")
    new = png()
    calls = fake_fetch(monkeypatch, [new])
    vl.invalidate()
    assert asyncio.run(vl.get_version_logo()) == new
    assert asyncio.run(vl.get_version_logo()) == new and len(calls) == 1


def test_non_image_body_never_replaces_the_cache(monkeypatch):
    vl._write_cache(b"old")
    fake_fetch(monkeypatch, [b"<html>error</html>"])
    vl.invalidate()
    assert asyncio.run(vl.get_version_logo()) == b"old"
    assert vl.CACHE_IMAGE.read_bytes() == b"old"


def test_failure_backs_off_instead_of_refetching_every_call(monkeypatch):
    vl._write_cache(b"old")
    calls = fake_fetch(monkeypatch, [None])
    vl.invalidate()
    assert asyncio.run(vl.get_version_logo()) == b"old"
    assert asyncio.run(vl.get_version_logo()) == b"old"
    assert len(calls) == 1
