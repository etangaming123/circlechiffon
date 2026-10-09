"""
Client for `render_server.py`: /cc-chart's renders sent to another machine.

`render_remote` does what `cogs/chart.py` otherwise does itself -
`chart_local.render_chart` and then `chart_video.encode_capture` per part -
on the machine at `config.chart_render_server`, and returns the same pair:
CaptureResults (metadata only; the server has already muxed) and the
finished MP4s' bytes.

Only `RemoteUnavailable` means "render it here instead": the server can't be
reached, refused the job, or vanished mid-render. A render the server ran
and *failed* comes back as the same exception a local render would raise -
the same chart would fail here too.
"""

import asyncio
import time
from pathlib import Path
from typing import Callable

import httpx

from circlechiffon.adapters.mainotes.player import CaptureResult
from circlechiffon.renderers.chart_local import ChartRenderError, ChartRenderUnavailable, ProgressCallback
from circlechiffon.renderers.chart_video import FfmpegUnavailable, VideoEncodeError

# Short enough that a machine that's off costs a few seconds before falling
# back, not the whole render.
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 30.0
_POLL_SECONDS = 1.0
_JOB_TIMEOUT = 15 * 60

_FAILURES = {
    "ChartRenderError": ChartRenderError,
    "ChartRenderUnavailable": ChartRenderUnavailable,
    "VideoEncodeError": VideoEncodeError,
    "FfmpegUnavailable": FfmpegUnavailable,
}


# on_phase(phase, done, total): "encoding" (parts muxed so far / parts) and
# "downloading" (0 / parts) - the stretches after rendering, which `progress`
# says nothing about. `done`/`total` are 0 from a server that predates them.
PhaseCallback = Callable[[str, int, int], None]


class RemoteUnavailable(RuntimeError):
    """The render server couldn't do this render; render it locally."""


def _capture(part: dict) -> CaptureResult:
    return CaptureResult(
        video_path=Path(),  # the video comes back as bytes, not a file here
        frame_count=int(part["frame_count"]),
        fps=int(part["fps"]),
        width=int(part["width"]),
        height=int(part["height"]),
        hi_speed=float(part["hi_speed"]),
        start_measure=int(part["start_measure"]),
        end_measure=int(part["end_measure"]),
        total_measures=int(part["total_measures"]),
        truncated=bool(part["truncated"]),
        start_seconds=float(part["start_seconds"]),
    )


async def render_remote(
    url: str,
    key: str,
    *,
    chart_text: str,
    hi_speed: float,
    from_measure: int | None,
    to_measure: int | None,
    size_budget_bytes: int | None,
    render_mode: str,
    progress: ProgressCallback | None = None,
    on_phase: PhaseCallback | None = None,
) -> tuple[list[CaptureResult], list[bytes]]:
    timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
    async with httpx.AsyncClient(base_url=url, headers={"X-Render-Key": key}, timeout=timeout) as client:
        try:
            r = await client.post("/render", json={
                "chart_text": chart_text,
                "hi_speed": hi_speed,
                "from_measure": from_measure,
                "to_measure": to_measure,
                "size_budget_bytes": size_budget_bytes,
                "render_mode": render_mode,
            })
        except httpx.HTTPError as e:
            raise RemoteUnavailable(f"can't reach {url} ({type(e).__name__})") from e
        if r.status_code != 202:
            raise RemoteUnavailable(f"{url} refused the render (HTTP {r.status_code}: {_error_text(r)})")
        try:
            job_id = str(r.json()["job_id"])
        except (ValueError, KeyError, TypeError) as e:
            raise RemoteUnavailable(f"{url} sent an unexpected reply") from e

        try:
            return await _follow(client, url, job_id, progress, on_phase)
        finally:
            # Done, failed or cancelled (the command was abandoned): either
            # way the server can drop the job and stop rendering it.
            try:
                await asyncio.shield(client.delete(f"/jobs/{job_id}"))
            except (httpx.HTTPError, asyncio.CancelledError):
                pass


async def _follow(client: httpx.AsyncClient, url: str, job_id: str,
                  progress: ProgressCallback | None,
                  on_phase: PhaseCallback | None = None) -> tuple[list[CaptureResult], list[bytes]]:
    deadline = time.monotonic() + _JOB_TIMEOUT
    last_phase = None
    while True:
        if time.monotonic() > deadline:
            raise RemoteUnavailable(f"{url} took longer than {_JOB_TIMEOUT // 60} minutes")
        try:
            r = await client.get(f"/jobs/{job_id}")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RemoteUnavailable(f"lost {url} mid-render ({type(e).__name__})") from e
        status = r.json()
        state = status.get("state")
        if state == "done":
            break
        if state == "failed":
            error = status.get("error") or "the render failed"
            failure = _FAILURES.get(status.get("error_type") or "")
            if failure is None:  # cancelled or crashed server-side: not this chart's fault
                raise RemoteUnavailable(f"{url} couldn't render it ({error})")
            raise failure(error)
        if state == "rendering" and progress is not None:
            progress(float(status.get("elapsed") or 0.0), status.get("total"), float(status.get("fraction") or 0.0))
        elif state == "encoding" and on_phase is not None:
            # rendering ends in a burst (the last wave of chunks finishes
            # together), so a poll rarely sees it at 100% - without this the
            # message would sit on a stale percentage through the whole mux
            phase = ("encoding", int(status.get("encoded") or 0), int(status.get("part_count") or 0))
            if phase != last_phase:
                last_phase = phase
                on_phase(*phase)
        await asyncio.sleep(_POLL_SECONDS)

    parts = status.get("parts") or []
    if on_phase is not None:
        on_phase("downloading", 0, len(parts))
    videos = []
    for k in range(len(parts)):
        try:
            r = await client.get(f"/jobs/{job_id}/parts/{k}")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RemoteUnavailable(f"lost {url} while downloading the video ({type(e).__name__})") from e
        videos.append(r.content)
    if not videos:
        raise RemoteUnavailable(f"{url} returned no video")
    return [_capture(p) for p in parts], videos


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error"))
    except ValueError:
        return r.text[:200]
