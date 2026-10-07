"""
/cc-chart's local renderer: simai text -> H.264, with no browser involved.

Replaces the headless-Chromium capture of mai-notes' player
(`adapters/mainotes/player.py`). The chart text still comes from mai-notes,
but drawing happens here, with skia (the same CPU rasteriser Chrome's canvas
uses) driven from Python.

## Why it's faster

Every frame is a pure function of its timestamp - nothing carries over from
one frame to the next - so the frame range is cut into chunks and each chunk
is drawn by a long-lived worker process (`renderers/chart_worker.py`) and
piped into its own ffmpeg/x264. The
chunks are raw Annex-B H.264, each opening on an IDR frame, so joining them
is a byte-for-byte concatenation. The joined stream then goes through the
exact mux `renderers/chart_video.py` already does for the browser capture
(and inherits its `-r` and no-`-shortest` handling).

`-bf 0` matters: with B-frames, the raw-H.264 demuxer's timestamps at the
mux step can't be trusted to come out in order, so the encoder is held to
I/P frames only. At these bitrates the cost is negligible.

## Timing

All note times come straight from the parsed chart, so the SFX track is
exact - none of the browser capture's 20ms-window or rebase corrections
apply (`encode_capture` is called with `sfx_shift_ms=0`).

## Look

Deliberately plain for now: functional shapes in roughly maimai's colours.
Matching the cabinet's look is a separate pass; every size and colour is a
module constant below so that pass doesn't have to touch the logic.
"""

import asyncio
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from circlechiffon.adapters.mainotes.player import CaptureResult, SfxHit
from circlechiffon.simai import geometry
from circlechiffon.simai.parser import Chart, parse_chart

# -- output ---------------------------------------------------------------------

SIZE = 600
FPS = 30
_MAX_SECONDS = 8 * 60  # nothing in maimai comes close; a runaway guard
_MIN_CHUNK_FRAMES = 90

_DEFAULT_BITRATE = 1_200_000
# Low enough that a 4-minute chart still fits 10MB on the first encode. A
# floor above the budget forces chart_video's corrective re-encode, which
# measured slower than the whole render (5.8s against 4.7s on a 243s chart).
# Flat shapes on a flat background survive this bitrate fine.
_MIN_BITRATE = 150_000
_MAX_BITRATE = 2_500_000

# -- note speed -----------------------------------------------------------------

HI_SPEED_DEFAULT = 7.5
HI_SPEED_MIN = 1.0
HI_SPEED_MAX = 10.0
HI_SPEED_STEP = 0.25


def clamp_hi_speed(value: float) -> float:
    snapped = round(value / HI_SPEED_STEP) * HI_SPEED_STEP
    return min(HI_SPEED_MAX, max(HI_SPEED_MIN, snapped))


def travel_time(hi_speed: float) -> float:
    """Seconds a tap takes to cross from its spawn point to the ring. A
    first approximation of the cabinet's curve (7.5 -> 0.48s)."""
    return 3.6 / hi_speed


# -- look -----------------------------------------------------------------------

RING_RADIUS = 0.43 * SIZE  # judgement ring, px
SPAWN_RADIUS = 0.28  # where notes appear, in ring radii
GROW_FRACTION = 0.5  # spawn "grow" phase, as a fraction of travel time

TAP_RADIUS = 0.105  # ring radii
TAP_STROKE = 0.042
STAR_RADIUS = 0.13
ARROW_SPACING = 0.085
ARROW_HALF_WIDTH = 0.055
ARROW_DEPTH = 0.04
ARROW_STROKE = 0.028
TOUCH_SPREAD = 0.16  # how far apart a touch's petals start
TOUCH_PETAL = 0.055
HIT_EFFECT_SECONDS = 0.16
FIREWORK_SECONDS = 0.6

BACKGROUND = (16, 18, 30)
RING_COLOR = (235, 235, 245)
COLOR_TAP = (255, 80, 170)
COLOR_STAR = (0, 170, 255)
COLOR_EACH = (255, 205, 0)
COLOR_BREAK = (255, 110, 10)
COLOR_TOUCH = (0, 170, 255)
COLOR_EX = (255, 255, 255)
COLOR_HIT = (255, 250, 220)
COLOR_TEXT = (200, 200, 215)


class ChartRenderError(RuntimeError):
    pass


class ChartRenderUnavailable(RuntimeError):
    """skia isn't installed on this host."""


def renderer_available() -> bool:
    try:
        import skia  # noqa: F401
    except ImportError:
        return False
    return True


# -- the scene: everything precomputed once per worker --------------------------


def _note_color(is_break: bool, is_each: bool, base: tuple[int, int, int]) -> tuple[int, int, int]:
    if is_break:
        return COLOR_BREAK
    if is_each:
        return COLOR_EACH
    return base


@dataclass(slots=True)
class _Arrow:
    dist: float  # arc length along the slide at the arrow's tip
    path: object  # skia.Path, already in pixel space


@dataclass(slots=True)
class _SlideDraw:
    show: float
    full: float  # fully faded in
    head_time: float
    launch: float
    end: float
    color: tuple[int, int, int]
    path: geometry.SlidePath
    arrows: list[_Arrow]


class Scene:
    def __init__(self, chart: Chart, hi_speed: float, size: int = SIZE):
        import skia

        self.skia = skia
        self.chart = chart
        self.size = size
        self.scale = size / SIZE
        self.R = RING_RADIUS * self.scale
        self.cx = self.cy = size / 2
        self.t_move = travel_time(hi_speed)
        self.t_grow = self.t_move * GROW_FRACTION
        self.t_touch = self.t_move * 1.2

        self._fill = skia.Paint(AntiAlias=True, Style=skia.Paint.kFill_Style)
        self._stroke = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style)
        self._stroke.setStrokeJoin(skia.Paint.kRound_Join)
        self._stroke.setStrokeCap(skia.Paint.kRound_Cap)
        self._font = skia.Font(None, 15 * self.scale)

        self._star = self._star_path(STAR_RADIUS * self.R)
        self._petal = self._petal_path(TOUCH_PETAL * self.R)
        self._build_notes()
        self.background = self._draw_background()

    # -- coordinates ------------------------------------------------------------

    def px(self, p) -> tuple[float, float]:
        return self.cx + float(p[0]) * self.R, self.cy + float(p[1]) * self.R

    # -- static shapes ----------------------------------------------------------

    def _star_path(self, r: float):
        skia = self.skia
        path = skia.Path()
        for k in range(10):
            radius = r if k % 2 == 0 else r * 0.45
            a = -math.pi / 2 + k * math.pi / 5
            pt = (math.cos(a) * radius, math.sin(a) * radius)
            path.moveTo(*pt) if k == 0 else path.lineTo(*pt)
        path.close()
        return path

    def _petal_path(self, r: float):
        """A triangle pointing at +x (towards the touch point when rotated)."""
        skia = self.skia
        path = skia.Path()
        path.moveTo(r, 0)
        path.lineTo(-r * 0.8, r * 0.9)
        path.lineTo(-r * 0.8, -r * 0.9)
        path.close()
        return path

    def _draw_background(self) -> np.ndarray:
        skia = self.skia
        arr = np.zeros((self.size, self.size, 4), np.uint8)
        surface = skia.Surface(arr)
        canvas = surface.getCanvas()
        canvas.clear(skia.Color(*BACKGROUND))
        paint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style,
                           StrokeWidth=2.5 * self.scale, Color=skia.Color(*RING_COLOR))
        canvas.drawCircle(self.cx, self.cy, self.R, paint)
        dot = skia.Paint(AntiAlias=True, Color=skia.Color(*RING_COLOR))
        for pos in range(1, 9):
            x, y = self.px(geometry.button_point(pos))
            canvas.drawCircle(x, y, 6 * self.scale, dot)
        faint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style,
                           StrokeWidth=1 * self.scale, Color=skia.Color(*RING_COLOR, 40))
        canvas.drawCircle(self.cx, self.cy, self.R * SPAWN_RADIUS, faint)
        del canvas, surface
        return arr

    # -- precompute -------------------------------------------------------------

    def _build_notes(self) -> None:
        chart = self.chart
        lead = self.t_move + self.t_grow

        self.taps = sorted(chart.taps, key=lambda n: n.time)
        self.tap_show = np.array([n.time - lead for n in self.taps])
        self.tap_hide = np.array([n.time for n in self.taps])

        self.holds = sorted(chart.holds, key=lambda n: n.time)
        self.hold_show = np.array([n.time - lead for n in self.holds])
        self.hold_hide = np.array([n.time + n.duration for n in self.holds])

        self.touches = sorted(chart.touches, key=lambda n: n.time)
        self.touch_show = np.array([n.time - self.t_touch for n in self.touches])
        self.touch_hide = np.array([n.time + (n.duration or 0.0) for n in self.touches])

        self.slides: list[_SlideDraw] = []
        for s in chart.slides:
            path = geometry.build_slide_path(s)
            show = s.launch_time if s.sudden else s.head_time - lead
            full = s.launch_time if s.sudden else s.head_time - self.t_move
            self.slides.append(_SlideDraw(
                show=show, full=full, head_time=s.head_time, launch=s.launch_time, end=s.end_time,
                color=_note_color(s.is_break, s.is_each, COLOR_STAR),
                path=path, arrows=self._arrows_for(path),
            ))
        self.slide_show = np.array([s.show for s in self.slides])
        self.slide_hide = np.array([s.end for s in self.slides])

        # Hit flashes: one per note head that reaches the ring / sensor.
        hits = [(n.time, *self.px(geometry.button_point(n.position))) for n in [*self.taps, *self.holds]]
        hits += [(n.time, *self.px(geometry.touch_point(n.area, n.index))) for n in self.touches]
        hits.sort()
        self.hit_times = np.array([h[0] for h in hits])
        self.hit_xy = [(h[1], h[2]) for h in hits]

        fw = [n for n in self.touches if n.firework]
        self.firework_times = np.array([n.time + (n.duration or 0.0) for n in fw])
        self.firework_xy = [self.px(geometry.touch_point(n.area, n.index)) for n in fw]

    def _arrows_for(self, path: geometry.SlidePath) -> list[_Arrow]:
        skia = self.skia
        arrows = []
        length = path.length
        if length <= 0:
            return arrows
        if path.is_wifi:
            return self._wifi_arrows(path)
        n = max(1, int(length / ARROW_SPACING))
        hw, depth = ARROW_HALF_WIDTH * self.R, ARROW_DEPTH * self.R
        for k in range(1, n + 1):
            d = k * length / (n + 1)
            x, y, heading = path.point_at(d)
            px, py = self.px((x, y))
            c, s = math.cos(heading), math.sin(heading)
            # Chevron pointing along the heading.
            back_l = (px - c * depth - s * hw, py - s * depth + c * hw)
            back_r = (px - c * depth + s * hw, py - s * depth - c * hw)
            p = skia.Path()
            p.moveTo(*back_l)
            p.lineTo(px, py)
            p.lineTo(*back_r)
            arrows.append(_Arrow(dist=d, path=p))
        return arrows

    def _wifi_arrows(self, path: geometry.SlidePath) -> list[_Arrow]:
        """Wifi slides fan out: bars across the travel line, widening until
        they span the three end buttons."""
        skia = self.skia
        start = path.points[0]
        ends = [geometry.button_point(p) for p in path.wifi_ends]
        length = path.length
        n = max(3, int(length / (ARROW_SPACING * 1.4)))
        arrows = []
        for k in range(1, n + 1):
            f = k / (n + 1)
            pts = [start + (e - start) * f for e in ends]
            p = skia.Path()
            p.moveTo(*self.px(pts[0]))
            p.lineTo(*self.px(pts[1]))
            p.lineTo(*self.px(pts[2]))
            arrows.append(_Arrow(dist=f * length, path=p))
        return arrows

    # -- per frame --------------------------------------------------------------

    def _paint(self, paint, color, alpha: float = 1.0):
        paint.setColor(self.skia.Color(*color, int(255 * max(0.0, min(1.0, alpha)))))
        return paint

    def _approach(self, note_time: float, t: float) -> tuple[float, float]:
        """(radius in ring radii, scale) of a note due at `note_time`."""
        dt = note_time - t
        if dt > self.t_move:
            grow = 1.0 - (dt - self.t_move) / self.t_grow
            return SPAWN_RADIUS, max(0.0, min(1.0, grow))
        r = SPAWN_RADIUS + (1.0 - SPAWN_RADIUS) * (1.0 - dt / self.t_move)
        return min(r, 1.0), 1.0

    def draw(self, canvas, frame: np.ndarray, t: float) -> None:
        frame[:] = self.background
        self._draw_slides(canvas, t)
        self._draw_holds(canvas, t)
        self._draw_touches(canvas, t)
        self._draw_taps(canvas, t)
        self._draw_effects(canvas, t)
        self._draw_hud(canvas, t)

    @staticmethod
    def _active(show: np.ndarray, hide: np.ndarray, t: float):
        if len(show) == 0:
            return ()
        return np.flatnonzero((show <= t) & (hide >= t))

    def _draw_slides(self, canvas, t: float) -> None:
        stroke = self._stroke
        stroke.setStrokeWidth(ARROW_STROKE * self.R)
        for i in self._active(self.slide_show, self.slide_hide, t):
            s = self.slides[i]
            alpha = 1.0 if t >= s.full else (t - s.show) / max(1e-6, s.full - s.show)
            gone = s.path.progress_at(t) if t > s.launch else 0.0
            self._paint(stroke, s.color, alpha * 0.9)
            for arrow in s.arrows:
                if arrow.dist > gone:
                    canvas.drawPath(arrow.path, stroke)

            if t >= s.head_time:
                star_alpha = 1.0 if t >= s.launch else (t - s.head_time) / max(1e-6, s.launch - s.head_time)
                if s.path.is_wifi:
                    start = s.path.points[0]
                    f = gone / s.path.length if s.path.length else 0.0
                    for end in s.path.wifi_ends:
                        e = geometry.button_point(end)
                        x, y = self.px(start + (e - start) * f)
                        self._draw_star(canvas, x, y, math.atan2(e[1] - start[1], e[0] - start[0]),
                                        s.color, star_alpha, 0.8)
                else:
                    x, y, heading = s.path.point_at(gone)
                    px, py = self.px((x, y))
                    self._draw_star(canvas, px, py, heading, s.color, star_alpha, 0.85)

    def _draw_star(self, canvas, x, y, heading, color, alpha, scale=1.0, filled=True):
        canvas.save()
        canvas.translate(x, y)
        canvas.rotate(math.degrees(heading) + 90)
        if scale != 1.0:
            canvas.scale(scale, scale)
        if filled:
            canvas.drawPath(self._star, self._paint(self._fill, color, alpha * 0.35))
        stroke = self._stroke
        stroke.setStrokeWidth(TAP_STROKE * self.R * 0.75 / max(scale, 0.1))
        canvas.drawPath(self._star, self._paint(stroke, color, alpha))
        canvas.restore()

    def _draw_taps(self, canvas, t: float) -> None:
        stroke = self._stroke
        for i in self._active(self.tap_show, self.tap_hide, t):
            n = self.taps[i]
            r, scale = self._approach(n.time, t)
            if scale <= 0:
                continue
            angle = geometry.button_angle(n.position)
            x, y = self.px(geometry.polar(angle, r))
            base = COLOR_STAR if n.is_star else COLOR_TAP
            color = _note_color(n.is_break, n.is_each, base)
            if n.is_star:
                spin = t * 4.0
                self._draw_star(canvas, x, y, angle - math.pi / 2 + spin, color, 1.0, scale)
                continue
            radius = TAP_RADIUS * self.R * scale
            if n.is_ex:
                stroke.setStrokeWidth(TAP_STROKE * self.R * 0.6 * scale)
                canvas.drawCircle(x, y, radius + TAP_STROKE * self.R * scale, self._paint(stroke, COLOR_EX, 0.7))
            stroke.setStrokeWidth(TAP_STROKE * self.R * scale)
            canvas.drawCircle(x, y, radius, self._paint(stroke, color))

    def _draw_holds(self, canvas, t: float) -> None:
        skia = self.skia
        stroke = self._stroke
        for i in self._active(self.hold_show, self.hold_hide, t):
            n = self.holds[i]
            r_head, scale = self._approach(n.time, t)
            if scale <= 0:
                continue
            r_tail, _ = self._approach(n.time + n.duration, t)
            if t >= n.time:
                r_head = 1.0
            angle = geometry.button_angle(n.position)
            u = np.array([math.sin(angle), -math.cos(angle)])
            v = np.array([u[1], -u[0]])
            hw = TAP_RADIUS * scale
            head, tail = u * r_head, u * min(r_tail, r_head)
            pts = [
                head + u * hw, head + u * hw * 0.5 + v * hw * 0.87, tail - u * hw * 0.5 + v * hw * 0.87,
                tail - u * hw, tail - u * hw * 0.5 - v * hw * 0.87, head + u * hw * 0.5 - v * hw * 0.87,
            ]
            path = skia.Path()
            path.addPoly([skia.Point(*self.px(p)) for p in pts], True)
            color = _note_color(n.is_break, n.is_each, COLOR_TAP)
            canvas.drawPath(path, self._paint(self._fill, color, 0.25))
            if n.is_ex:
                stroke.setStrokeWidth(TAP_STROKE * self.R * 1.6 * scale)
                canvas.drawPath(path, self._paint(stroke, COLOR_EX, 0.5))
            stroke.setStrokeWidth(TAP_STROKE * self.R * scale)
            canvas.drawPath(path, self._paint(stroke, color))

    def _draw_touches(self, canvas, t: float) -> None:
        stroke = self._stroke
        for i in self._active(self.touch_show, self.touch_hide, t):
            n = self.touches[i]
            x, y = self.px(geometry.touch_point(n.area, n.index))
            color = _note_color(n.is_break, n.is_each, COLOR_TOUCH)
            dt = n.time - t
            if dt > 0:
                f = dt / self.t_touch  # 1 -> 0 as it lands
                alpha = min(1.0, (1.0 - f) * 3.0)
                spread = (TOUCH_PETAL + TOUCH_SPREAD * f) * self.R
            else:
                alpha, spread = 1.0, TOUCH_PETAL * self.R
            self._paint(self._fill, color, alpha)
            for k in range(4):
                a = math.pi / 4 + k * math.pi / 2
                canvas.save()
                canvas.translate(x + math.cos(a) * spread, y + math.sin(a) * spread)
                canvas.rotate(math.degrees(a + math.pi))
                canvas.drawPath(self._petal, self._fill)
                canvas.restore()
            canvas.drawCircle(x, y, 3.5 * self.scale, self._fill)
            if n.duration and dt <= 0:
                frac = min(1.0, -dt / n.duration) if n.duration > 0 else 1.0
                stroke.setStrokeWidth(TAP_STROKE * self.R * 0.8)
                rr = (TOUCH_PETAL * 2.2) * self.R
                rect = self.skia.Rect(x - rr, y - rr, x + rr, y + rr)
                canvas.drawArc(rect, -90, 360 * frac, False, self._paint(stroke, color))

    def _draw_effects(self, canvas, t: float) -> None:
        stroke = self._stroke
        if len(self.hit_times):
            lo = int(np.searchsorted(self.hit_times, t - HIT_EFFECT_SECONDS))
            hi = int(np.searchsorted(self.hit_times, t, side="right"))
            stroke.setStrokeWidth(3 * self.scale)
            for i in range(lo, hi):
                f = (t - self.hit_times[i]) / HIT_EFFECT_SECONDS
                x, y = self.hit_xy[i]
                radius = TAP_RADIUS * self.R * (1.0 + 0.8 * f)
                canvas.drawCircle(x, y, radius, self._paint(stroke, COLOR_HIT, 1.0 - f))
        if len(self.firework_times):
            stroke.setStrokeWidth(4 * self.scale)
            for i in np.flatnonzero((self.firework_times <= t) & (self.firework_times + FIREWORK_SECONDS > t)):
                f = (t - self.firework_times[i]) / FIREWORK_SECONDS
                x, y = self.firework_xy[i]
                canvas.drawCircle(x, y, self.R * (0.15 + 0.9 * f), self._paint(stroke, COLOR_EACH, 0.8 * (1 - f)))

    def _draw_hud(self, canvas, t: float) -> None:
        measure = self.chart.measure_at(max(t, 0.0)) if t >= 0 else 0
        bpm = self.chart.bpms[0].bpm
        for p in self.chart.bpms:
            if p.time > t:
                break
            bpm = p.bpm
        paint = self._paint(self._fill, COLOR_TEXT, 0.9)
        canvas.drawString(f"Measure {measure}", 10 * self.scale, 22 * self.scale, self._font, paint)
        canvas.drawString(f"BPM {bpm:g}", 10 * self.scale, 42 * self.scale, self._font, paint)


# -- worker side ----------------------------------------------------------------


@dataclass(slots=True)
class _ChunkJob:
    chart_text: str
    hi_speed: float
    t0: float
    frame_start: int
    frame_end: int
    fps: int
    size: int
    bitrate: int
    out_path: str
    ffmpeg: str


def _x264_command(job: _ChunkJob) -> list[str]:
    return [
        job.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{job.size}x{job.size}", "-r", str(job.fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "animation", "-bf", "0",
        "-pix_fmt", "yuv420p", "-threads", "2",
        "-b:v", str(job.bitrate), "-maxrate", str(job.bitrate), "-bufsize", str(job.bitrate),
        "-g", str(job.fps * 2),
        "-f", "h264", job.out_path,
    ]


def _render_chunk(job: _ChunkJob) -> int:
    """Runs in a worker process. Returns the number of frames written."""
    import skia

    chart = parse_chart(job.chart_text)
    scene = Scene(chart, job.hi_speed, job.size)
    frame = np.zeros((job.size, job.size, 4), np.uint8)
    surface = skia.Surface(frame)
    canvas = surface.getCanvas()

    proc = subprocess.Popen(
        _x264_command(job), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    written = 0
    try:
        for k in range(job.frame_start, job.frame_end):
            scene.draw(canvas, frame, job.t0 + k / job.fps)
            proc.stdin.write(frame.data)
            written += 1
        proc.stdin.close()
        # -loglevel error keeps stderr tiny, so reading it after the frames
        # can't deadlock against a full pipe.
        err = proc.stderr.read()
        proc.wait(timeout=120)
    except BaseException:
        proc.kill()
        raise
    if proc.returncode != 0:
        lines = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise ChartRenderError(lines[-1] if lines else f"ffmpeg exited {proc.returncode}")
    return written


# -- worker pool ----------------------------------------------------------------
#
# Long-lived `python -m circlechiffon.renderers.chart_worker` processes, one
# job at a time each, spoken to over stdin/stdout. See chart_worker.py for
# why this isn't a multiprocessing pool.

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_JOB_TIMEOUT = 300


def worker_count() -> int:
    configured = os.environ.get("CC_CHART_WORKERS")
    if configured and configured.isdigit() and int(configured) > 0:
        return int(configured)
    # Leave a core for the bot's own event loop.
    return max(1, min(8, (os.cpu_count() or 2) - 1))


class _Worker:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "circlechiffon.renderers.chart_worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            cwd=str(_REPO_ROOT),
        )

    async def run(self, job: _ChunkJob) -> int:
        await self.start()
        proc = self.proc
        try:
            proc.stdin.write((json.dumps(asdict(job)) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=_JOB_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionError, BrokenPipeError):
            self.kill()
            raise ChartRenderError("a render worker stopped responding")
        except asyncio.CancelledError:
            # Its reply is still coming; left alive, the next job would read
            # this one's answer. Kill it and let start() replace it.
            self.kill()
            raise
        if not line:
            self.kill()
            raise ChartRenderError("a render worker crashed")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise ChartRenderError(reply.get("error") or "a render worker failed")
        return int(reply["frames"])

    def kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
        self.proc = None


_workers: list[_Worker] = []


async def start_workers() -> None:
    """Start every worker now rather than on the first render - each one
    importing numpy and skia is most of a cold render's overhead."""
    if not renderer_available():
        return
    while len(_workers) < worker_count():
        _workers.append(_Worker())
    await asyncio.gather(*(w.start() for w in _workers))


async def stop_workers() -> None:
    for w in _workers:
        if w.proc is not None and w.proc.returncode is None:
            try:
                w.proc.stdin.close()  # the worker exits at EOF
                await asyncio.wait_for(w.proc.wait(), timeout=5)
            except (asyncio.TimeoutError, OSError):
                w.kill()
        w.proc = None
    _workers.clear()


# -- public entry point ---------------------------------------------------------


def _bitrate_for(duration_s: float, size_budget_bytes: int | None) -> int:
    if not size_budget_bytes or duration_s <= 0:
        return _DEFAULT_BITRATE
    # Leave room for the audio track and container overhead.
    budget = int(size_budget_bytes * 8 / (duration_s * 1.08)) - 96_000
    return max(_MIN_BITRATE, min(_MAX_BITRATE, budget))


def _sfx_hits(chart: Chart, t_start: float, t_end: float) -> list[SfxHit]:
    """One answer sound per moment a note is hit; the "each" sample when
    several land together."""
    counts: dict[int, int] = {}
    for n in [*chart.taps, *chart.holds, *chart.touches]:
        if t_start <= n.time < t_end:
            key = round(n.time * 1000)
            counts[key] = counts.get(key, 0) + 1
    return [
        SfxHit(time_ms=key - t_start * 1000.0, is_each=count > 1)
        for key, count in sorted(counts.items())
    ]


ProgressCallback = Callable[[float, float | None, float], None]


async def render_chart(
    chart_text: str,
    out_path: Path,
    *,
    ffmpeg: str,
    hi_speed: float = HI_SPEED_DEFAULT,
    from_measure: int | None = None,
    to_measure: int | None = None,
    size_budget_bytes: int | None = None,
    progress: ProgressCallback | None = None,
) -> CaptureResult:
    """Renders simai text to a raw Annex-B H.264 file at `out_path` and
    returns a CaptureResult ready for `chart_video.encode_capture`."""
    if not renderer_available():
        raise ChartRenderUnavailable("skia-python isn't installed - run `pip install skia-python`")

    try:
        chart = parse_chart(chart_text)
    except ValueError as e:  # SimaiError, or a float() on a mangled number
        raise ChartRenderError(f"couldn't read the chart ({e})") from e
    if chart.note_count == 0:
        raise ChartRenderError("the chart has no notes in it")

    total_measures = chart.total_measures
    lead = travel_time(hi_speed) * (1 + GROW_FRACTION)
    if from_measure:
        start_measure = min(from_measure, total_measures - 1)
        t_start = chart.measure_time(start_measure)
    else:
        start_measure = 0
        t_start = min(0.0, chart.first_time - lead)
    if to_measure is not None and to_measure < total_measures:
        end_measure = to_measure
        t_end = chart.measure_time(to_measure)
    else:
        end_measure = total_measures
        t_end = chart.end_time + 1.0
    if t_end <= t_start:
        raise ChartRenderError("that measure range is empty")

    truncated = False
    if t_end - t_start > _MAX_SECONDS:
        t_end = t_start + _MAX_SECONDS
        truncated = True

    total_frames = int(math.ceil((t_end - t_start) * FPS))
    duration = total_frames / FPS
    bitrate = _bitrate_for(duration, size_budget_bytes)

    workers = worker_count()
    chunk = max(_MIN_CHUNK_FRAMES, math.ceil(total_frames / (workers * 3)))
    ranges = [(a, min(a + chunk, total_frames)) for a in range(0, total_frames, chunk)]
    parts = [out_path.with_name(f"{out_path.stem}.part{k:03d}.h264") for k in range(len(ranges))]
    jobs = [
        _ChunkJob(
            chart_text=chart_text, hi_speed=hi_speed, t0=t_start,
            frame_start=a, frame_end=b, fps=FPS, size=SIZE, bitrate=bitrate,
            out_path=str(part), ffmpeg=ffmpeg,
        )
        for (a, b), part in zip(ranges, parts)
    ]

    while len(_workers) < workers:
        _workers.append(_Worker())
    queue: asyncio.Queue[_ChunkJob] = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    done_frames = 0

    async def drain(worker: _Worker) -> None:
        nonlocal done_frames
        while True:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            # Await first, then add: `done_frames += await ...` reads the
            # total before suspending, so concurrent drains lose updates.
            frames = await worker.run(job)
            done_frames += frames
            if progress is not None:
                progress(done_frames / FPS, duration, done_frames / total_frames)

    tasks = [asyncio.create_task(drain(w)) for w in _workers[:workers]]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # One chunk failed (or the command was cancelled): stop handing out
        # work. Workers cancelled mid-chunk are killed and respawn on demand.
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    if done_frames != total_frames:
        raise ChartRenderError(f"rendered {done_frames} of {total_frames} frames")

    def _join() -> None:
        with open(out_path, "wb") as out:
            for part in parts:
                with open(part, "rb") as fh:
                    while block := fh.read(1 << 20):
                        out.write(block)
                part.unlink(missing_ok=True)

    try:
        await asyncio.to_thread(_join)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)

    return CaptureResult(
        video_path=out_path,
        frame_count=total_frames,
        fps=FPS,
        width=SIZE,
        height=SIZE,
        sfx=_sfx_hits(chart, t_start, t_end),
        hi_speed=hi_speed,
        start_measure=start_measure,
        end_measure=end_measure,
        total_measures=total_measures,
        truncated=truncated,
    )
