"""renderers/chart_remote.py: following a render server job, in particular
that the phases after "rendering" reach the caller (they didn't, so the
progress message froze on a stale percentage through the encode)."""

import asyncio

import httpx

from circlechiffon.renderers import chart_remote

PART = {
    "frame_count": 600, "fps": 60, "width": 720, "height": 720, "hi_speed": 7.0, "start_measure": 1,
    "end_measure": 10, "total_measures": 10, "truncated": False, "start_seconds": 0.0, "size": 4,
}


def follow(statuses, **kwargs):
    """Serve `statuses` from /jobs/<id> one poll at a time (the last repeats)."""
    polls = iter(statuses)
    last = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal last
        if request.url.path.endswith("/parts/0"):
            return httpx.Response(200, content=b"mp4!")
        last = next(polls, last)
        return httpx.Response(200, json=last)

    async def go():
        async with httpx.AsyncClient(base_url="http://r", transport=httpx.MockTransport(handler)) as client:
            return await chart_remote._follow(client, "http://r", "job", **kwargs)

    return asyncio.run(go())


def test_phases_and_progress_arrive_in_order(monkeypatch):
    monkeypatch.setattr(chart_remote, "_POLL_SECONDS", 0)
    progress, phases = [], []
    captures, videos = follow(
        [
            {"state": "queued"},
            {"state": "rendering", "elapsed": 5.0, "total": 10.0, "fraction": 0.75},
            {"state": "encoding", "encoded": 0, "part_count": 1},
            {"state": "encoding", "encoded": 0, "part_count": 1},          # unchanged: not repeated
            {"state": "done", "parts": [PART], "encoded": 1, "part_count": 1},
        ],
        progress=lambda *a: progress.append(a),
        on_phase=lambda *a: phases.append(a),
    )
    assert progress == [(5.0, 10.0, 0.75)]
    assert phases == [("encoding", 0, 1), ("downloading", 0, 1)]
    assert videos == [b"mp4!"] and captures[0].frame_count == 600


def test_an_older_server_without_part_counts_still_reports_encoding(monkeypatch):
    monkeypatch.setattr(chart_remote, "_POLL_SECONDS", 0)
    phases = []
    follow(
        [{"state": "encoding"}, {"state": "done", "parts": [PART]}],
        progress=None,
        on_phase=lambda *a: phases.append(a),
    )
    assert phases == [("encoding", 0, 0), ("downloading", 0, 1)]


def test_callbacks_are_optional(monkeypatch):
    monkeypatch.setattr(chart_remote, "_POLL_SECONDS", 0)
    _, videos = follow([{"state": "encoding"}, {"state": "done", "parts": [PART]}], progress=None)
    assert videos == [b"mp4!"]
