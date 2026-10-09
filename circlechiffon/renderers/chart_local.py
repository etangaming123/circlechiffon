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

`Scene` owns timing and the render mode; drawing goes through a painter.
The default "simple" mode always uses `chart_vector.VectorPainter` (vector
notes in mai-notes' style - roughly twice as fast). The "game" modes use
`chart_skin.SkinPainter` when a MajdataPlay skin has been imported
(`import_chart_skin.py`), falling back to the vector painter. Both take
MajdataPlay's Unity-unit geometry (ring radius 4.8, y up).

Note speed follows mai-notes' scale (see `approach_seconds`), whichever
painter draws.
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
# Only reached when a chart needs more than MAX_PARTS videos (or splitting is
# off): anything else is split to stay above _QUALITY_BITRATE. A floor above
# the budget would force chart_video's corrective re-encode, which measured
# slower than the whole render.
_MIN_BITRATE = 150_000
_MAX_BITRATE = 2_500_000

RING_PX = 0.43 * SIZE  # judgement ring radius on the canvas
U = geometry.UNIT_RING  # ...which is 4.8 MajdataPlay units

# -- note speed: mai-notes' scale -----------------------------------------------
#
# mai-notes' player (public bundle, read 2026-10-07): the slider value becomes
# `hiSpeed = slider * (2/3) * 0.9`, and a note is on screen for
# `BASE_APPROACH_TIME_MS / hiSpeed` with BASE_APPROACH_TIME_MS = 2250 - half
# of it growing at the spawn point, half travelling to the ring. So 7.5 on
# our option is 500ms on screen, exactly as 7.5 on their slider. Touches use
# 0.9 of the window.

HI_SPEED_DEFAULT = 7.5
HI_SPEED_MIN = 1.0
HI_SPEED_MAX = 10.0
HI_SPEED_STEP = 0.25
_BASE_APPROACH_SECONDS = 2.25


def clamp_hi_speed(value: float) -> float:
    snapped = round(value / HI_SPEED_STEP) * HI_SPEED_STEP
    return min(HI_SPEED_MAX, max(HI_SPEED_MIN, snapped))


def approach_seconds(hi_speed: float) -> float:
    """How long a tap is on screen before it's hit, on mai-notes' scale."""
    return _BASE_APPROACH_SECONDS / (hi_speed * (2 / 3) * 0.9)


# -- render modes ---------------------------------------------------------------

MODE_SIMPLE = "simple"  # mai-notes-style vectors, notes are hit, nothing written
MODE_GAME = "game"  # notes are hit, every one CRITICAL PERFECT
MODE_MISS = "game_miss"  # nothing is hit, every one MISS
RENDER_MODES = (MODE_SIMPLE, MODE_GAME, MODE_MISS)

# MajdataPlay's "too late" points: a tap/star is a miss 9 frames (150ms)
# after its time, a touch 18 (300ms), a slide 36 (600ms) after it ends.
_TAP_MISS_AFTER = 0.15
_TOUCH_MISS_AFTER = 0.30
_SLIDE_MISS_AFTER = 0.60

# Unity-unit placements (MajdataPlay TapDrop / TapEffectDisplayer /
# TouchEffectDisplayer): notes spawn at r=1.225; judgement text sits at 3.8 on
# a lane, 0.66 inside a touch sensor (1.0 for a touch hold).
_SPAWN = 1.225
_JUDGE_R = 3.8
_GUIDE_MIN_SCALE = 0.3

BACKGROUND_SKIN = (8, 8, 14)
BACKGROUND_VECTOR = (26, 27, 46)
COLOR_TEXT = (200, 200, 215)


class ChartRenderError(RuntimeError):
    pass


class ChartRenderUnavailable(RuntimeError):
    """skia isn't installed on this host."""


def _rgba_surface(skia, arr: np.ndarray):
    """A canvas drawing into `arr` as RGBA - the byte order ffmpeg is told
    (`-pix_fmt rgba`). Never skia's default "N32": that's the platform's
    native order, RGBA on macOS but BGRA on Windows, where it swapped red
    and blue in every frame."""
    return skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType)


def renderer_available() -> bool:
    try:
        import skia  # noqa: F401
    except ImportError:
        return False
    return True


# -- the scene: everything precomputed once per worker --------------------------


def _kind(note) -> str:
    if note.is_break:
        return "break"
    return "each" if note.is_each else "normal"


def _lane_dir(position: int) -> np.ndarray:
    return geometry.key_point_u(position, 1.0)


def _key(t: float) -> int:
    return round(t * 1000)


@dataclass(slots=True)
class _StarHead:
    double: bool
    spin: float  # deg/s, MajdataPlay convention (negative = clockwise)


@dataclass(slots=True)
class _SlideDraw:
    slide: object
    track: geometry.SlideTrack
    kind: str
    show: float
    full: float
    hide: float
    has_head: bool


@dataclass(slots=True)
class _Event:
    time: float
    world: np.ndarray
    grade: str  # "just" | "miss"
    is_break: bool


class Scene:
    """Everything one worker needs to draw any frame of a chart: notes sorted
    with their on-screen windows, slide tracks, and the judgement/effect
    events the render mode calls for. `draw` is a pure function of t."""

    def __init__(self, chart: Chart, hi_speed: float, mode: str = MODE_SIMPLE, size: int = SIZE,
                 skin_path: Path | None = None, use_skin: bool = True):
        import skia

        from circlechiffon.renderers import chart_skin, chart_vector

        if mode not in RENDER_MODES:
            raise ValueError(f"unknown render mode {mode!r}")
        self.skia = skia
        self.chart = chart
        self.mode = mode
        self.hit = mode != MODE_MISS
        self.size = size
        ring_px = RING_PX * size / SIZE

        # "simple" always draws mai-notes-style vectors: it's the default and
        # the fast path (measured 71x realtime median against 38x skinned).
        # The game modes use the MajdataPlay skin when one is installed.
        skinned = use_skin and mode != MODE_SIMPLE
        loaded = chart_skin.load_skin(ring_px / U, skin_path) if skinned else None
        if loaded is not None:
            skin, self.table = loaded
            self.painter = chart_skin.SkinPainter(skin, size, ring_px)
            self.majdata_alpha = True
            background = BACKGROUND_SKIN
        else:
            self.table = None
            self.painter = chart_vector.VectorPainter(size, ring_px)
            self.majdata_alpha = False
            background = BACKGROUND_VECTOR

        self.window = approach_seconds(hi_speed)
        self.half = self.window / 2
        self.touch_window = self.window * 0.9

        typeface = skia.Typeface.MakeFromName("Arial", skia.FontStyle.Normal()) or skia.Typeface.MakeDefault()
        self._font = skia.Font(typeface, 15 * size / SIZE)
        self._hud_paint = skia.Paint(AntiAlias=True, Color=skia.Color(*COLOR_TEXT, 230))

        self._build()
        self.background = self._draw_background(background)

    @property
    def skinned(self) -> bool:
        return self.table is not None

    # -- precompute -------------------------------------------------------------

    def _draw_background(self, rgb) -> np.ndarray:
        skia = self.skia
        arr = np.zeros((self.size, self.size, 4), np.uint8)
        surface = _rgba_surface(skia, arr)
        canvas = surface.getCanvas()
        canvas.clear(skia.Color(*rgb))
        self.painter.background(canvas)
        del canvas, surface
        return arr

    def _build(self) -> None:
        chart, W, Wt = self.chart, self.window, self.touch_window

        # Slides first: star heads need their slide's speed and count.
        star_keys = {(_key(t.time), t.position) for t in chart.taps if t.is_star}
        self.slides: list[_SlideDraw] = []
        by_head: dict[tuple[int, int], list] = {}
        # In time order: _draw_slides relies on it to put older slides on top.
        for s in sorted(chart.slides, key=lambda s: (s.head_time, s.launch_time)):
            track = geometry.build_track(s, self.table)
            start = s.segments[0].start
            by_head.setdefault((_key(s.head_time), start), []).append((s, track))
            has_head = (_key(s.head_time), start) in star_keys
            show = s.launch_time if s.sudden else s.head_time - self.half
            hide = s.end_time + (0.0 if self.hit else _SLIDE_MISS_AFTER)
            self.slides.append(_SlideDraw(slide=s, track=track, kind=_kind(s), show=show, full=s.head_time,
                                          hide=hide, has_head=has_head))
        self.slide_show = np.array([d.show for d in self.slides])
        self.slide_hide = np.array([d.hide for d in self.slides])

        self.taps = sorted(chart.taps, key=lambda n: n.time)
        self.star_heads: dict[int, _StarHead] = {}
        for i, n in enumerate(self.taps):
            if not n.is_star:
                continue
            group = by_head.get((_key(n.time), n.position), [])
            spin = 0.0
            if group:
                s, track = group[0]
                bars = 20 if track.wifi is not None else track.arrow_count + 1
                spin = max(-9.257 * bars / max(s.duration, 1e-3), -1080.0)
            self.star_heads[i] = _StarHead(double=len(group) >= 2, spin=spin)
        tap_life = 0.0 if self.hit else _TAP_MISS_AFTER
        self.tap_show = np.array([n.time - W for n in self.taps])
        self.tap_hide = np.array([n.time + tap_life for n in self.taps])

        self.holds = sorted(chart.holds, key=lambda n: n.time)
        self.hold_show = np.array([n.time - W for n in self.holds])
        self.hold_hide = np.array([n.time + n.duration for n in self.holds])

        self.touches = sorted(chart.touches, key=lambda n: n.time)
        touch_life = 0.0 if self.hit else _TOUCH_MISS_AFTER
        self.touch_show = np.array([n.time - Wt for n in self.touches])
        self.touch_hide = np.array([
            n.time + n.duration if n.duration is not None else n.time + touch_life for n in self.touches
        ])

        # Each-lines join consecutive simultaneous button notes, short way round.
        groups: dict[int, list[int]] = {}
        for n in [*self.taps, *self.holds]:
            if n.is_each:
                groups.setdefault(_key(n.time), []).append(n.position)
        self.each_lines: list[tuple[float, int, int]] = []
        for k, lanes in groups.items():
            lanes = sorted(set(lanes))
            for a, b in zip(lanes, lanes[1:]):
                steps = (b - a) % 8
                start = a
                if steps > 4:
                    steps, start = 8 - steps, b
                if 1 <= steps <= 4:
                    self.each_lines.append((k / 1000.0, steps, start))
        self.each_line_times = np.array([e[0] for e in self.each_lines])

        self._build_events()

    def _lane_frame(self, position: int, radius: float) -> np.ndarray:
        rot = geometry.lane_rotation(position)
        p = _lane_dir(position) * radius
        return geometry.mat_t(*p) @ geometry.mat_r(rot)

    def _touch_text_frame(self, n) -> np.ndarray:
        inset = 1.0 if n.duration is not None else 0.66
        if n.area == "C":
            # Centre sensor: just below the middle (touch hold: 0.99 down).
            return geometry.mat_t(0.0, -0.99 if n.duration is not None else -0.66)
        rot = geometry.touch_rotation(n.area, n.index)
        dist = geometry.UNIT_TOUCH_RADIUS[n.area] - inset
        a = math.radians(rot + 90.0)
        return geometry.mat_t(math.cos(a) * dist, math.sin(a) * dist) @ geometry.mat_r(rot)

    def _build_events(self) -> None:
        judge: list[_Event] = []
        banners: list[tuple[float, _SlideDraw, str]] = []
        hits: list[tuple[float, np.ndarray, float, bool]] = []
        fireworks: list[tuple[float, np.ndarray]] = []
        grade = "just" if self.hit else "miss"
        written = self.mode != MODE_SIMPLE

        for n in self.taps:
            when = n.time if self.hit else n.time + _TAP_MISS_AFTER
            if written:
                judge.append(_Event(when, self._lane_frame(n.position, _JUDGE_R), grade, n.is_break))
            if self.hit:
                hits.append((n.time, geometry.key_point_u(n.position), geometry.lane_rotation(n.position), False))
        for n in self.holds:
            end = n.time + n.duration
            if written:
                judge.append(_Event(end, self._lane_frame(n.position, _JUDGE_R), grade, n.is_break))
            if self.hit:
                for t in (n.time, end):
                    hits.append((t, geometry.key_point_u(n.position), geometry.lane_rotation(n.position), False))
        for n in self.touches:
            pos = geometry.touch_point_u(n.area, n.index)
            end = n.time + (n.duration or 0.0)
            when = end if (self.hit or n.duration is not None) else n.time + _TOUCH_MISS_AFTER
            if written:
                judge.append(_Event(when, self._touch_text_frame(n), grade, n.is_break))
            if self.hit:
                hits.append((n.time, pos, 0.0, True))
                if n.duration is not None:
                    hits.append((end, pos, 0.0, True))
                if n.firework:
                    fireworks.append((end, pos))
        if written:
            for d in self.slides:
                when = d.slide.end_time + (0.0 if self.hit else _SLIDE_MISS_AFTER)
                banners.append((when, d, grade))

        judge.sort(key=lambda e: e.time)
        self.judge_events = judge
        self.judge_times = np.array([e.time for e in judge])
        banners.sort(key=lambda b: b[0])
        self.banners = banners
        self.banner_times = np.array([b[0] for b in banners])
        hits.sort(key=lambda h: h[0])
        self.hits = hits
        self.hit_times = np.array([h[0] for h in hits])
        # One burst per button/sensor: a newer hit in the same spot cuts the
        # previous one off rather than drawing over it (as MajdataPlay's one
        # effect object per button does).
        self.hit_until = np.full(len(hits), np.inf)
        last: dict[tuple, int] = {}
        for k, (when, pos, _, touch) in enumerate(hits):
            spot = (touch, round(float(pos[0]), 3), round(float(pos[1]), 3))
            if spot in last:
                self.hit_until[last[spot]] = when
            last[spot] = k
        self.fireworks = fireworks

        # The hold effect runs while a hold or touch hold is held, so only
        # when notes are hit.
        held = []
        if self.hit:
            held += [(n.time, n.time + n.duration, geometry.key_point_u(n.position)) for n in self.holds]
            held += [(n.time, n.time + n.duration, geometry.touch_point_u(n.area, n.index))
                     for n in self.touches if n.duration is not None]
        held.sort(key=lambda h: h[0])
        self.hold_fx = held
        self.hold_fx_start = np.array([h[0] for h in held])
        self.hold_fx_end = np.array([h[1] for h in held])

    # -- per frame --------------------------------------------------------------

    def _approach(self, note_time: float, t: float) -> tuple[float, float]:
        """(radius in units, scale) of a button note due at `note_time`.
        First half of the window: grow at the spawn point. Second half: travel
        to the ring (and on past it, for a missed note)."""
        dt = note_time - t
        if dt > self.half:
            return _SPAWN, max(0.0, min(1.0, 1.0 - (dt - self.half) / self.half))
        return _SPAWN + (U - _SPAWN) * (1.0 - dt / self.half), 1.0

    @staticmethod
    def _active(show: np.ndarray, hide: np.ndarray, t: float, inclusive_end: bool = False):
        if len(show) == 0:
            return ()
        if inclusive_end:
            return np.flatnonzero((show <= t) & (hide >= t))
        return np.flatnonzero((show <= t) & (hide > t))

    @staticmethod
    def _recent(times: np.ndarray, t: float, span: float):
        if len(times) == 0:
            return range(0)
        lo = int(np.searchsorted(times, t - span))
        hi = int(np.searchsorted(times, t, side="right"))
        return range(lo, hi)

    def _break_brightness(self, t: float) -> float:
        return 0.95 + max(0.65 * math.sin(12.5 * t), 0.0)

    def draw(self, canvas, frame: np.ndarray, t: float) -> None:
        frame[:] = self.background
        self._draw_slides(canvas, t)
        self._draw_guides(canvas, t)
        self._draw_holds(canvas, t)
        self._draw_slide_stars(canvas, t)
        self._draw_taps(canvas, t)
        self._draw_touches(canvas, t)
        self._draw_effects(canvas, t)
        self._draw_judgements(canvas, t)
        self._draw_hud(canvas, t)

    def _slide_alpha(self, d: _SlideDraw, t: float) -> float:
        if d.slide.sudden:
            return 1.0
        if not self.majdata_alpha:
            # mai-notes: fade in over the half-window before the head.
            return max(0.0, min(1.0, (t - d.show) / max(d.full - d.show, 1e-6)))
        # MajdataPlay SlideBase: up to 0.5 within <=0.2s, 0.5 until 50ms
        # before the head, then solid.
        if t >= d.full - 0.05:
            return 1.0
        span = max(min((d.full - 0.05) - d.show, 0.2), 1e-6)
        return 0.5 * max(0.0, min(1.0, (t - d.show) / span))

    def _draw_slides(self, canvas, t: float) -> None:
        painter = self.painter
        shine = self._break_brightness(t)
        # Oldest on top: newer slides are drawn first, and within a slide
        # the end first, so a slide's start covers its end where they cross.
        for i in reversed(self._active(self.slide_show, self.slide_hide, t)):
            d = self.slides[i]
            alpha = self._slide_alpha(d, t)
            b = shine if d.kind == "break" and t >= d.full else 1.0
            if d.track.wifi is not None:
                w = d.track.wifi
                first = 0
                if self.hit and t > w.t0:
                    first = int(11 * min((t - w.t0) / max(w.t1 - w.t0, 1e-6), 1.0))
                painter.wifi_bars(canvas, w, first, alpha, d.kind, b)
                continue
            for seg in reversed(d.track.segments):
                first = 0
                if self.hit and t >= seg.t1:
                    continue
                if self.hit and t > seg.t0:
                    _, _, f = seg.star_at(t)
                    first = int(f)
                painter.slide_arrows(canvas, seg.arrows, first, alpha, d.kind, b)

    def _draw_slide_stars(self, canvas, t: float) -> None:
        painter = self.painter
        shine = self._break_brightness(t)
        for i in self._active(self.slide_show, self.slide_hide, t):
            d = self.slides[i]
            s = d.slide
            if t < s.head_time:
                continue
            b = shine if d.kind == "break" else 1.0
            if t < s.launch_time:
                if not d.has_head or s.sudden:
                    continue
                alpha = (t - s.head_time) / max(s.launch_time - s.head_time, 1e-6)
                scale = alpha + 0.5
            else:
                alpha, scale = 1.0, 1.5
            if self.hit and t >= s.end_time:
                continue
            w = d.track.wifi
            if w is not None:
                p = min(max((t - w.t0) / max(w.t1 - w.t0, 1e-6), 0.0), 1.0)
                for k in range(3):
                    pos = w.starts[k] + (w.ends[k] - w.starts[k]) * p
                    painter.slide_star(canvas, pos, w.star_angles[k], scale, d.kind, alpha, b)
                continue
            segs = d.track.segments
            seg = next((x for x in segs if t < x.t1), segs[-1])
            pos, rot, _ = seg.star_at(t)
            painter.slide_star(canvas, pos, rot, scale, d.kind, alpha, b)

    def _draw_guides(self, canvas, t: float) -> None:
        if not self.painter.uses_guides:
            return
        painter = self.painter
        for i in self._active(self.tap_show, self.tap_hide, t):
            n = self.taps[i]
            r, scale = self._approach(n.time, t)
            if scale <= _GUIDE_MIN_SCALE:
                continue
            kind = "break" if n.is_break else "each" if n.is_each else ("slide" if n.is_star else "normal")
            painter.guide(canvas, kind, geometry.lane_rotation(n.position), r / U)
        for i in self._active(self.hold_show, self.hold_hide, t):
            n = self.holds[i]
            if t >= n.time:
                continue
            r, scale = self._approach(n.time, t)
            if scale > _GUIDE_MIN_SCALE:
                painter.guide(canvas, _kind(n), geometry.lane_rotation(n.position), r / U)
        for k in self._recent(self.each_line_times, t + self.window, self.window):
            when, steps, start = self.each_lines[k]
            if not (when - self.window <= t < when + (0.0 if self.hit else _TAP_MISS_AFTER)):
                continue
            r, scale = self._approach(when, t)
            if scale > _GUIDE_MIN_SCALE:
                painter.each_line(canvas, steps, start, r / U)

    def _draw_taps(self, canvas, t: float) -> None:
        painter = self.painter
        shine = self._break_brightness(t)
        for i in self._active(self.tap_show, self.tap_hide, t):
            n = self.taps[i]
            r, scale = self._approach(n.time, t)
            if scale <= 0:
                continue
            rot = geometry.lane_rotation(n.position)
            pos = _lane_dir(n.position) * r
            kind = _kind(n)
            b = shine if n.is_break else 1.0
            if n.is_star:
                head = self.star_heads.get(i, _StarHead(False, 0.0))
                spin = head.spin * max(0.0, t - (n.time - self.window))
                painter.star(canvas, pos, rot + spin, scale, kind, n.is_ex, head.double, b)
            else:
                painter.tap(canvas, pos, rot, scale, kind, n.is_ex, b)

    def _draw_holds(self, canvas, t: float) -> None:
        painter = self.painter
        shine = self._break_brightness(t)
        held = 0.95 + 0.5 * abs(math.sin(12.5 * t))
        for i in self._active(self.hold_show, self.hold_hide, t):
            n = self.holds[i]
            head_r, scale = self._approach(n.time, t)
            if scale <= 0:
                continue
            end = n.time + n.duration
            if t >= n.time:
                head_r = U
            tail_r = max(_SPAWN, min(self._approach(end, t)[0], head_r))
            if scale < 1.0:
                head_r = tail_r = _SPAWN
            if self.hit:
                state = "on" if t >= n.time else "idle"
            else:
                # A missed hold keeps its colour until MajdataPlay would
                # judge the head a miss, then turns grey.
                state = "off" if t >= n.time + _TAP_MISS_AFTER else "idle"
            b = shine if n.is_break else 1.0
            if state == "on":
                b *= held
            show_end = end - t <= self.half
            painter.hold(canvas, geometry.lane_rotation(n.position), head_r, tail_r, scale, _kind(n), n.is_ex,
                         state, show_end, b)

    def _touch_d(self, rel: float) -> tuple[float, float]:
        """How far a touch's arrowheads still are from closing, as (d, alpha):
        d runs 0.4 (spawn) -> 0 (hit), MajdataPlay's spread in units."""
        whole = self.touch_window
        if rel >= 0:
            return 0.0, 1.0
        if not self.majdata_alpha:
            # mai-notes: a quartic ease over the whole window, fading in
            # over its first 150ms.
            frac = max(0.0, min(1.0, 1.0 + rel / whole))
            return 0.4 * (1.0 - frac ** 4), max(0.0, min(1.0, (whole + rel) / 0.15))
        # MajdataPlay's curve, stretched over mai-notes' window.
        move, fade = 0.8 * whole, 0.2 * whole
        alpha = max(0.0, min(1.0, (whole + rel) / fade))
        d = 0.42 - math.exp(8.0 * (rel * 0.43 / move) - 0.85)
        return max(0.0, min(0.4, d)), alpha

    def _draw_touches(self, canvas, t: float) -> None:
        painter = self.painter
        shine = self._break_brightness(t)
        active = list(self._active(self.touch_show, self.touch_hide, t))
        queues: dict[tuple[str, int], list[int]] = {}
        for i in active:
            n = self.touches[i]
            if n.duration is None:
                queues.setdefault((n.area, n.index), []).append(i)
        for i in active:
            n = self.touches[i]
            pos = geometry.touch_point_u(n.area, n.index)
            d, alpha = self._touch_d(t - n.time)
            kind = _kind(n)
            b = shine if n.is_break else 1.0
            if n.duration is not None:
                progress = (t - n.time) / n.duration if (t >= n.time and n.duration > 0) else None
                missed = not self.hit and t >= n.time + _TOUCH_MISS_AFTER
                painter.touch_hold(canvas, pos, d, alpha, kind, progress, missed, b, n.firework)
                continue
            borders = []
            queue = queues.get((n.area, n.index), [])
            if queue and queue[0] == i:
                for depth in (2, 3):
                    if len(queue) >= depth:
                        borders.append((depth, _kind(self.touches[queue[depth - 1]])))
            painter.touch(canvas, pos, d, alpha, kind, t >= n.time, borders, b, n.firework)

    def _draw_effects(self, canvas, t: float) -> None:
        painter = self.painter
        for k in self._active(self.hold_fx_start, self.hold_fx_end, t):
            start, _, pos = self.hold_fx[k]
            painter.hold_effect(canvas, pos, t - start)
        for k in self._recent(self.hit_times, t, 0.45):
            if t >= self.hit_until[k]:
                continue
            when, pos, rot, touch = self.hits[k]
            painter.hit(canvas, pos, rot, t - when, touch)
        for when, pos in self.fireworks:
            if 0.0 <= t - when < 0.6:
                painter.firework(canvas, pos, t - when)

    def _draw_judgements(self, canvas, t: float) -> None:
        painter = self.painter
        for k in self._recent(self.judge_times, t, 0.27):
            e = self.judge_events[k]
            painter.judge(canvas, e.world, e.grade, e.is_break, t - e.time)
        for k in self._recent(self.banner_times, t, 0.42):
            when, d, grade = self.banners[k]
            tr = d.track
            painter.banner(canvas, tr.banner, tr.banner_kind, tr.banner_r, grade, d.kind == "break", t - when)

    def _draw_hud(self, canvas, t: float) -> None:
        measure = self.chart.measure_at(max(t, 0.0))
        bpm = self.chart.bpms[0].bpm
        for p in self.chart.bpms:
            if p.time > t:
                break
            bpm = p.bpm
        s = self.size / SIZE
        canvas.drawString(f"Measure {measure}", 10 * s, 22 * s, self._font, self._hud_paint)
        canvas.drawString(f"BPM {bpm:g}", 10 * s, 42 * s, self._font, self._hud_paint)


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
    render_mode: str = MODE_SIMPLE
    skin_dir: str | None = None


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


# A worker usually gets several chunks of the same render in a row; building
# the Scene (parse, slide tracks) once per render rather than per chunk.
_scene_cache: tuple[tuple, Scene] | None = None


def _scene_for(job: _ChunkJob) -> Scene:
    global _scene_cache
    key = (hash(job.chart_text), len(job.chart_text), job.hi_speed, job.render_mode, job.size, job.skin_dir)
    if _scene_cache is not None and _scene_cache[0] == key:
        return _scene_cache[1]
    skin = Path(job.skin_dir) if job.skin_dir else None
    scene = Scene(parse_chart(job.chart_text), job.hi_speed, job.render_mode, job.size, skin_path=skin)
    _scene_cache = (key, scene)
    return scene


def _render_chunk(job: _ChunkJob) -> int:
    """Runs in a worker process. Returns the number of frames written."""
    import skia

    scene = _scene_for(job)
    frame = np.zeros((job.size, job.size, 4), np.uint8)
    surface = _rgba_surface(skia, frame)
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
#
# A long chart squeezed into one upload-sized file came out smeared (a 4-minute
# chart got ~200kbps). Instead a chart is split into overlapping videos, each
# short enough to keep at least its mode's quality bitrate. Measured on a
# dense MASTER: flat vector notes hold up at 350kbps; the skin's glows and
# hit bursts block up there but not at 450kbps; 200kbps is bad for both. At a
# 9.2MB budget that's one video up to ~2:40 (simple) / ~2:10 (game modes).

_QUALITY_BITRATE = {MODE_SIMPLE: 350_000, MODE_GAME: 450_000, MODE_MISS: 450_000}
_AUDIO_BITRATE = 96_000
_SPLIT_OVERLAP_MEASURES = 4
MAX_PARTS = 10  # Discord's attachments-per-message cap


def _bitrate_for(duration_s: float, size_budget_bytes: int | None) -> int:
    if not size_budget_bytes or duration_s <= 0:
        return _DEFAULT_BITRATE
    # Leave room for the audio track and container overhead.
    budget = int(size_budget_bytes * 8 / (duration_s * 1.08)) - _AUDIO_BITRATE
    return max(_MIN_BITRATE, min(_MAX_BITRATE, budget))


def _max_part_seconds(size_budget_bytes: int, render_mode: str) -> float:
    """Longest video that still gets the mode's quality bitrate inside the
    budget."""
    return size_budget_bytes * 8 / (1.08 * (_QUALITY_BITRATE[render_mode] + _AUDIO_BITRATE))


@dataclass(slots=True)
class _Part:
    t_start: float
    t_end: float
    start_measure: int
    end_measure: int


def _split(chart: Chart, whole: _Part, max_seconds: float | None) -> list[_Part]:
    """Cut `whole` into the fewest even, measure-aligned parts that each fit
    `max_seconds`, neighbours sharing _SPLIT_OVERLAP_MEASURES measures so
    nothing is lost at a cut. Gives up at MAX_PARTS (those parts then just
    get a lower bitrate)."""
    measures = whole.end_measure - whole.start_measure
    if max_seconds is None or whole.t_end - whole.t_start <= max_seconds or measures < 2:
        return [whole]
    lap = _SPLIT_OVERLAP_MEASURES
    parts = [whole]
    for n in range(2, MAX_PARTS + 1):
        if measures < n:
            break
        parts = []
        for i in range(n):
            a = whole.start_measure + round(i * measures / n)
            b = whole.start_measure + round((i + 1) * measures / n)
            if i > 0:
                a = max(whole.start_measure, a - lap // 2)
            if i < n - 1:
                b = min(whole.end_measure, b + lap - lap // 2)
            parts.append(_Part(
                t_start=whole.t_start if i == 0 else chart.measure_time(a),
                t_end=whole.t_end if i == n - 1 else chart.measure_time(b),
                start_measure=a, end_measure=b,
            ))
        if all(p.t_end - p.t_start <= max_seconds for p in parts):
            break
    return parts


def _sfx_hits(chart: Chart, t_start: float, t_end: float) -> list[SfxHit]:
    """One answer sound per moment a note is hit or a hold (or touch hold)
    is let go; the "each" sample when several land together."""
    times = [n.time for n in [*chart.taps, *chart.holds, *chart.touches]]
    times += [n.time + n.duration for n in chart.holds]
    times += [n.time + n.duration for n in chart.touches if n.duration is not None]
    counts: dict[int, int] = {}
    for when in times:
        if t_start <= when < t_end:
            key = round(when * 1000)
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
    render_mode: str = MODE_SIMPLE,
    split: bool = True,
) -> list[CaptureResult]:
    """Renders simai text to raw Annex-B H.264 and returns one CaptureResult
    per video, ready for `chart_video.encode_capture`. With a size budget
    and `split`, a long chart comes back as several overlapping videos
    (`<out_path stem>.1.h264`, `.2`, ...); otherwise it's one, at `out_path`."""
    if not renderer_available():
        raise ChartRenderUnavailable("skia-python isn't installed - run `pip install skia-python`")
    if render_mode not in RENDER_MODES:
        raise ChartRenderError(f"unknown render mode {render_mode!r}")

    try:
        chart = parse_chart(chart_text)
    except ValueError as e:  # SimaiError, or a float() on a mangled number
        raise ChartRenderError(f"couldn't read the chart ({e})") from e
    if chart.note_count == 0:
        raise ChartRenderError("the chart has no notes in it")

    total_measures = chart.total_measures
    lead = approach_seconds(hi_speed)
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
        # Room for the last judgement: a missed slide's banner lands 0.6s
        # after it ends and plays for ~0.4s.
        t_end = chart.end_time + (1.2 if render_mode == MODE_MISS else 1.0)
    if t_end <= t_start:
        raise ChartRenderError("that measure range is empty")

    truncated = False
    if t_end - t_start > _MAX_SECONDS:
        t_end = t_start + _MAX_SECONDS
        end_measure = min(end_measure, chart.measure_at(t_end) + 1)
        truncated = True

    whole = _Part(t_start, t_end, start_measure, end_measure)
    max_seconds = _max_part_seconds(size_budget_bytes, render_mode) if (split and size_budget_bytes) else None
    parts = _split(chart, whole, max_seconds)

    # Every part's chunks go into one queue, so the workers stay busy across
    # part boundaries rather than idling at the end of each.
    workers = worker_count()
    frame_counts = [int(math.ceil((p.t_end - p.t_start) * FPS)) for p in parts]
    total_frames = sum(frame_counts)
    chunk = max(_MIN_CHUNK_FRAMES, math.ceil(total_frames / (workers * 3)))
    if len(parts) == 1:
        videos = [out_path]
    else:
        videos = [out_path.with_name(f"{out_path.stem}.{k + 1}.h264") for k in range(len(parts))]
    jobs: list[_ChunkJob] = []
    chunk_files: list[list[Path]] = []
    for k, (part, frames, video) in enumerate(zip(parts, frame_counts, videos)):
        bitrate = _bitrate_for(frames / FPS, size_budget_bytes)
        files = []
        for a in range(0, frames, chunk):
            file = video.with_name(f"{video.stem}.chunk{a // chunk:03d}.h264")
            files.append(file)
            jobs.append(_ChunkJob(
                chart_text=chart_text, hi_speed=hi_speed, t0=part.t_start,
                frame_start=a, frame_end=min(a + chunk, frames), fps=FPS, size=SIZE, bitrate=bitrate,
                out_path=str(file), ffmpeg=ffmpeg, render_mode=render_mode,
                skin_dir=os.environ.get("CC_CHART_SKIN_DIR"),
            ))
        chunk_files.append(files)

    while len(_workers) < workers:
        _workers.append(_Worker())
    queue: asyncio.Queue[_ChunkJob] = asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    done_frames = 0
    shown_total = sum(frame_counts) / FPS

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
                progress(done_frames / FPS, shown_total, done_frames / total_frames)

    all_files = [f for files in chunk_files for f in files]
    tasks = [asyncio.create_task(drain(w)) for w in _workers[:workers]]
    try:
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # One chunk failed (or the command was cancelled): stop handing
            # out work. Workers cancelled mid-chunk are killed and respawn on
            # demand.
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        if done_frames != total_frames:
            raise ChartRenderError(f"rendered {done_frames} of {total_frames} frames")

        def _join() -> None:
            for video, files in zip(videos, chunk_files):
                with open(video, "wb") as out:
                    for file in files:
                        with open(file, "rb") as fh:
                            while block := fh.read(1 << 20):
                                out.write(block)
                        file.unlink(missing_ok=True)

        await asyncio.to_thread(_join)
    finally:
        for file in all_files:
            # On Windows a chunk can still be open in a killed worker's
            # ffmpeg for a moment; a leftover temp file mustn't replace the
            # error (or cancellation) that got us here.
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass

    return [
        CaptureResult(
            video_path=video,
            frame_count=frames,
            fps=FPS,
            width=SIZE,
            height=SIZE,
            sfx=_sfx_hits(chart, part.t_start, part.t_start + frames / FPS),
            hi_speed=hi_speed,
            start_measure=part.start_measure,
            end_measure=part.end_measure,
            total_measures=total_measures,
            truncated=truncated,
            start_seconds=part.t_start,
        )
        for part, frames, video in zip(parts, frame_counts, videos)
    ]
