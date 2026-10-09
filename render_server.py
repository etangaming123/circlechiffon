"""
Remote renderer for /cc-chart: run this on a faster machine on the same
network, point the bot's `chart_render_server` (config.json) at it, and the
bot sends renders here instead of drawing them itself.

    CC_RENDER_SERVER_KEY=<secret> python render_server.py [--host 0.0.0.0] [--port 8765]

The key must match the bot's `chart_render_key`. Generate one with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.

It runs the exact render the bot would (`chart_local.render_chart`, then
`chart_video.encode_capture` per part) and hands back finished MP4s, so the
videos are the same wherever they're made. The machine needs this repo's
requirements, ffmpeg, and - for the game modes - its own
`import_chart_skin.py` import. It needs no bot token: nothing here imports
`config.py` or discord.

Protocol (every request carries `X-Render-Key`):

    POST   /render                 {chart_text, hi_speed, from_measure, to_measure,
                                    size_budget_bytes, render_mode} -> 202 {job_id}
    GET    /jobs/<id>              {state, elapsed, total, fraction, error?, error_type?, parts?}
    GET    /jobs/<id>/parts/<k>    the k-th finished MP4
    DELETE /jobs/<id>              cancel (if running) and clean up
    GET    /health                 {ok, workers}

`state` is queued -> rendering -> encoding -> done | failed. Jobs run one at
a time, like the bot's own queue: each already uses every worker process.
"""

import argparse
import asyncio
import hmac
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from circlechiffon.renderers import chart_local, chart_skin
from circlechiffon.renderers.chart_local import (
    MODE_GAME,
    MODE_MISS,
    RENDER_MODES,
    ChartRenderError,
    ChartRenderUnavailable,
    clamp_hi_speed,
    render_chart,
)
from circlechiffon.renderers.chart_video import (
    FfmpegUnavailable,
    VideoEncodeError,
    encode_capture,
    ffmpeg_path,
)

DEFAULT_PORT = 8765
_MAX_CHART_BYTES = 1024 * 1024
_MAX_PENDING = 8  # queued + running; the bot itself queues at most 5
_KEEP_FINISHED = 10 * 60  # seconds a finished job's files wait for the client
_KEEP_RUNNING = 30 * 60  # a job abandoned mid-render is dropped after this
_RENDER_ERRORS = (ChartRenderError, ChartRenderUnavailable, VideoEncodeError, FfmpegUnavailable)


@dataclass
class _Job:
    id: str
    params: dict
    tmp: Path
    created: float = field(default_factory=time.monotonic)
    state: str = "queued"
    elapsed: float = 0.0
    total: float | None = None
    fraction: float = 0.0
    error: str | None = None
    error_type: str | None = None
    part_count: int = 0
    parts: list[dict] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    finished: float | None = None
    future: object = None

    def status(self) -> dict:
        out = {"state": self.state, "elapsed": self.elapsed, "total": self.total, "fraction": self.fraction}
        if self.error is not None:
            out["error"] = self.error
            out["error_type"] = self.error_type
        if self.state in ("encoding", "done"):
            out["encoded"] = len(self.parts)
            out["part_count"] = self.part_count
        if self.state == "done":
            out["parts"] = self.parts
        return out


class _Renderer:
    """Owns the asyncio loop (on its own thread) that the render workers and
    every job live on. Flask's request threads only hand work to it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="render-loop", daemon=True)
        self.jobs: dict[str, _Job] = {}
        self.jobs_lock = threading.Lock()
        self._turn: asyncio.Lock | None = None

    def start(self) -> None:
        self.thread.start()
        self._call(self._setup())
        self._call(chart_local.start_workers())

    def stop(self) -> None:
        with self.jobs_lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            self.discard(job.id)
        self._call(chart_local.stop_workers(), timeout=30)
        self.loop.call_soon_threadsafe(self.loop.stop)

    def _call(self, coro, timeout: float | None = 120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _setup(self) -> None:
        self._turn = asyncio.Lock()  # bound to this loop

    # -- jobs -------------------------------------------------------------------

    def submit(self, params: dict) -> _Job | None:
        with self.jobs_lock:
            pending = sum(1 for j in self.jobs.values() if j.state in ("queued", "rendering", "encoding"))
            if pending >= _MAX_PENDING:
                return None
            job = _Job(id=uuid.uuid4().hex, params=params, tmp=Path(tempfile.mkdtemp(prefix="cc-render-")))
            self.jobs[job.id] = job
        job.future = asyncio.run_coroutine_threadsafe(self._run(job), self.loop)
        return job

    def get(self, job_id: str) -> _Job | None:
        with self.jobs_lock:
            return self.jobs.get(job_id)

    def discard(self, job_id: str) -> bool:
        with self.jobs_lock:
            job = self.jobs.pop(job_id, None)
        if job is None:
            return False
        if job.future is not None and not job.future.done():
            # Cancels the task on the loop; render_chart kills its workers'
            # in-flight chunks on cancel and they respawn on demand. Their
            # ffmpegs can hold chunk files open for a moment after (Windows
            # won't delete an open file), so the temp dir goes once the job
            # has stopped, a little later.
            job.future.add_done_callback(lambda _: self.loop.call_soon_threadsafe(
                self.loop.call_later, 5.0, shutil.rmtree, job.tmp, True))
            job.future.cancel()
        else:
            shutil.rmtree(job.tmp, ignore_errors=True)
        return True

    def sweep(self) -> None:
        now = time.monotonic()
        with self.jobs_lock:
            stale = [
                j.id for j in self.jobs.values()
                if (j.finished is not None and now - j.finished > _KEEP_FINISHED)
                or (j.finished is None and now - j.created > _KEEP_RUNNING)
            ]
        for job_id in stale:
            self.discard(job_id)

    async def _run(self, job: _Job) -> None:
        p = job.params
        try:
            async with self._turn:
                job.state = "rendering"
                started = time.perf_counter()

                def progress(elapsed: float, total: float | None, fraction: float) -> None:
                    job.elapsed, job.total, job.fraction = elapsed, total, fraction

                mode = p["render_mode"]
                captures = await render_chart(
                    p["chart_text"], job.tmp / "capture.h264",
                    ffmpeg=ffmpeg_path(),
                    hi_speed=p["hi_speed"],
                    from_measure=p["from_measure"],
                    to_measure=p["to_measure"],
                    size_budget_bytes=p["size_budget_bytes"],
                    progress=progress,
                    render_mode=mode,
                )
                rendered = time.perf_counter() - started
                job.part_count = len(captures)
                job.state = "encoding"
                hit_sound = chart_skin.game_hit_sound() if mode in (MODE_GAME, MODE_MISS) else None
                limit = p["size_budget_bytes"] or None
                for k, capture in enumerate(captures, start=1):
                    out = job.tmp / f"chart-{k}.mp4"
                    kwargs = {"size_limit": limit} if limit else {}
                    await encode_capture(capture, out, sfx_shift_ms=0, hit_sound=hit_sound, **kwargs)
                    capture.video_path.unlink(missing_ok=True)
                    job.files.append(out)
                    job.parts.append({
                        "frame_count": capture.frame_count,
                        "fps": capture.fps,
                        "width": capture.width,
                        "height": capture.height,
                        "hi_speed": capture.hi_speed,
                        "start_measure": capture.start_measure,
                        "end_measure": capture.end_measure,
                        "total_measures": capture.total_measures,
                        "truncated": capture.truncated,
                        "start_seconds": capture.start_seconds,
                        "size": out.stat().st_size,
                    })
                job.state = "done"
                print(f"[render] {job.id[:8]} {mode}: {len(captures)} part(s), render {rendered:.1f}s, "
                      f"total {time.perf_counter() - started:.1f}s", flush=True)
        except asyncio.CancelledError:
            job.state, job.error, job.error_type = "failed", "cancelled", "Cancelled"
            raise
        except _RENDER_ERRORS as e:
            job.state, job.error, job.error_type = "failed", str(e), type(e).__name__
            print(f"[render] {job.id[:8]} failed: {type(e).__name__}: {e}", flush=True)
        except Exception as e:  # a bug here, not a bad chart: the bot falls back to rendering itself
            job.state, job.error, job.error_type = "failed", f"{type(e).__name__}: {e}", "Internal"
            print(f"[render] {job.id[:8]} crashed: {type(e).__name__}: {e}", flush=True)
        finally:
            job.finished = time.monotonic()


# -- HTTP -------------------------------------------------------------------------


def _validate(body: dict) -> dict:
    chart_text = body.get("chart_text")
    if not isinstance(chart_text, str) or not chart_text.strip():
        abort(400, "chart_text is required")
    if len(chart_text.encode("utf-8")) > _MAX_CHART_BYTES:
        abort(413, "chart_text is too large")
    mode = body.get("render_mode", chart_local.MODE_SIMPLE)
    if mode not in RENDER_MODES:
        abort(400, f"render_mode must be one of {', '.join(RENDER_MODES)}")

    def opt_int(name: str) -> int | None:
        value = body.get(name)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            abort(400, f"{name} must be a non-negative integer")
        return value

    try:
        hi_speed = clamp_hi_speed(float(body.get("hi_speed", chart_local.HI_SPEED_DEFAULT)))
    except (TypeError, ValueError):
        abort(400, "hi_speed must be a number")
    return {
        "chart_text": chart_text,
        "hi_speed": hi_speed,
        "from_measure": opt_int("from_measure"),
        "to_measure": opt_int("to_measure"),
        "size_budget_bytes": opt_int("size_budget_bytes"),
        "render_mode": mode,
    }


def create_app(renderer: _Renderer, key: str) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * _MAX_CHART_BYTES
    expected = key.encode("utf-8")

    @app.before_request
    def _auth():
        given = request.headers.get("X-Render-Key", "").encode("utf-8")
        if not hmac.compare_digest(given, expected):
            abort(401)
        renderer.sweep()

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(503)
    def _error(e):
        return jsonify({"error": getattr(e, "description", str(e))}), e.code

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "workers": chart_local.worker_count()})

    @app.post("/render")
    def render():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, "expected a JSON object")
        job = renderer.submit(_validate(body))
        if job is None:
            abort(503, "too many renders queued")
        return jsonify({"job_id": job.id}), 202

    @app.get("/jobs/<job_id>")
    def job_status(job_id: str):
        job = renderer.get(job_id) or abort(404)
        return jsonify(job.status())

    @app.get("/jobs/<job_id>/parts/<int:k>")
    def job_part(job_id: str, k: int):
        job = renderer.get(job_id) or abort(404)
        if job.state != "done" or not 0 <= k < len(job.files):
            abort(404)
        return send_file(job.files[k], mimetype="video/mp4", as_attachment=True, download_name=f"chart-{k + 1}.mp4")

    @app.delete("/jobs/<job_id>")
    def job_delete(job_id: str):
        if not renderer.discard(job_id):
            abort(404)
        return "", 204

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Remote renderer for /cc-chart.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--key", default=None, help="shared secret (default: $CC_RENDER_SERVER_KEY)")
    args = parser.parse_args()

    key = args.key or os.environ.get("CC_RENDER_SERVER_KEY", "")
    if not key:
        parser.error("set CC_RENDER_SERVER_KEY (or pass --key) to the bot's chart_render_key")
    if not chart_local.renderer_available():
        parser.error("skia-python isn't installed - run `pip install -r requirements.txt`")
    ffmpeg_path()  # fail now, not on the first render, if ffmpeg is missing

    renderer = _Renderer()
    renderer.start()
    print(f"Render server on http://{args.host}:{args.port} with {chart_local.worker_count()} workers", flush=True)
    try:
        create_app(renderer, key).run(host=args.host, port=args.port, threaded=True)
    finally:
        renderer.stop()


if __name__ == "__main__":
    main()
