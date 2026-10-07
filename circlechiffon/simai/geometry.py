"""
Where things sit on the judgement ring, and the path each slide shape takes.

Coordinates are normalised: centre (0, 0), judgement ring radius 1, and
**y points down** (screen convention), so increasing angle is clockwise on
screen. Angles are measured clockwise from 12 o'clock, which is how maimai
numbers its buttons: button 1 is at 22.5deg, button 8 at 337.5deg.

Each slide segment becomes a dense polyline; the renderer walks it by arc
length to place arrows and move the star. The loop sizes below are first
approximations of the cabinet's shapes - tuned by eye, not measured - and
live as module constants so the aesthetics pass can adjust them in one place.
"""

import math
from dataclasses import dataclass

import numpy as np

from circlechiffon.simai.parser import Slide, SlideSegment

# p/q wrap a circle around the centre of about this radius.
PQ_RADIUS = 0.45
# pp/qq run straight from the start to the centre, then loop round a circle
# that touches the centre tangentially (so the join is smooth) before heading
# out to the end. This is that circle's radius; its centre sits the same
# distance off the middle, square to the incoming line.
PPQQ_RADIUS = 0.40
# s/z turn at two points this far either side of the centre.
SZ_TURN_DIST = 0.42

_SAMPLE_STEP = 0.01  # polyline resolution, in ring radii

# Sensor geometry (A/D sit just inside the ring, B/E further in).
TOUCH_RADIUS = {"A": 0.80, "B": 0.46, "C": 0.0, "D": 0.80, "E": 0.60}


def button_angle(position: int) -> float:
    return math.radians(22.5 + 45.0 * (position - 1))


def polar(angle: float, radius: float = 1.0) -> np.ndarray:
    return np.array([math.sin(angle) * radius, -math.cos(angle) * radius])


def button_point(position: int, radius: float = 1.0) -> np.ndarray:
    return polar(button_angle(position), radius)


def touch_point(area: str, index: int) -> np.ndarray:
    if area == "C":
        return np.zeros(2)
    # A/B line up with the buttons; D/E sit between them (D1 at 12 o'clock).
    angle = button_angle(index) if area in "AB" else math.radians(45.0 * (index - 1))
    return polar(angle, TOUCH_RADIUS[area])


def _wrap(position: int) -> int:
    return (position - 1) % 8 + 1


# -- primitive paths ------------------------------------------------------------


def _line(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = max(2, int(np.linalg.norm(b - a) / _SAMPLE_STEP) + 1)
    t = np.linspace(0.0, 1.0, n)[:, None]
    return a + (b - a) * t


def _arc(center: np.ndarray, radius: float, a0: float, span: float) -> np.ndarray:
    """Arc from screen-math angle `a0` (atan2 convention, which in y-down
    coordinates turns clockwise as it grows) through signed `span`."""
    n = max(2, int(abs(span) * radius / _SAMPLE_STEP) + 1)
    a = a0 + np.linspace(0.0, span, n)
    return center + np.stack([np.cos(a), np.sin(a)], axis=1) * radius


def _atan2(v: np.ndarray) -> float:
    return math.atan2(v[1], v[0])


def _join(*parts: np.ndarray) -> np.ndarray:
    out = [parts[0]]
    for p in parts[1:]:
        out.append(p[1:] if np.allclose(out[-1][-1], p[0]) else p)
    return np.concatenate(out)


def _ring_arc(start: int, end: int, clockwise: bool) -> np.ndarray:
    a0 = _atan2(button_point(start))
    a1 = _atan2(button_point(end))
    if clockwise:
        span = (a1 - a0) % (2 * math.pi)
    else:
        span = -((a0 - a1) % (2 * math.pi))
    if abs(span) < 1e-6:
        span = 2 * math.pi if clockwise else -2 * math.pi
    return _arc(np.zeros(2), 1.0, a0, span)


def _tangent_point(p: np.ndarray, center: np.ndarray, radius: float, clockwise: bool, entering: bool) -> float:
    """Angle on the circle where a straight line from/to `p` meets it
    tangentially, travelling in the given rotational direction."""
    d = p - center
    dist = float(np.linalg.norm(d))
    if dist <= radius:
        return _atan2(d)
    base = _atan2(d)
    beta = math.acos(radius / dist)
    best = base
    for a in (base + beta, base - beta):
        t = center + radius * np.array([math.cos(a), math.sin(a)])
        # Direction of travel round the circle at `a`.
        vel = np.array([-math.sin(a), math.cos(a)]) * (1 if clockwise else -1)
        line = (t - p) if entering else (p - t)
        if float(np.dot(line, vel)) > 0:
            best = a
    return best


def _around(start: np.ndarray, end: np.ndarray, center: np.ndarray, radius: float, clockwise: bool) -> np.ndarray:
    a_in = _tangent_point(start, center, radius, clockwise, entering=True)
    a_out = _tangent_point(end, center, radius, clockwise, entering=False)
    if clockwise:
        span = (a_out - a_in) % (2 * math.pi)
    else:
        span = -((a_in - a_out) % (2 * math.pi))
    if abs(span) < 0.05:
        span += 2 * math.pi if clockwise else -2 * math.pi
    t_in = center + radius * np.array([math.cos(a_in), math.sin(a_in)])
    t_out = center + radius * np.array([math.cos(a_out), math.sin(a_out)])
    return _join(_line(start, t_in), _arc(center, radius, a_in, span), _line(t_out, end))


# -- shapes ---------------------------------------------------------------------


def segment_path(seg: SlideSegment) -> np.ndarray:
    s, e = button_point(seg.start), button_point(seg.end)
    shape = seg.shape

    if shape in ("-", "w"):
        return _line(s, e)
    if shape == "v":
        return _join(_line(s, np.zeros(2)), _line(np.zeros(2), e))
    if shape == "V":
        via = button_point(seg.via or seg.start)
        return _join(_line(s, via), _line(via, e))
    if shape == "^":
        diff = (seg.end - seg.start) % 8
        return _ring_arc(seg.start, seg.end, clockwise=diff <= 4)
    if shape in ("<", ">"):
        # `>` points right: clockwise along the top half, anticlockwise
        # along the bottom, where "right" runs the other way round.
        upper = seg.start in (1, 2, 7, 8)
        clockwise = (shape == ">") == upper
        return _ring_arc(seg.start, seg.end, clockwise)
    if shape in ("p", "q"):
        return _around(s, e, np.zeros(2), PQ_RADIUS, clockwise=shape == "q")
    if shape in ("pp", "qq"):
        clockwise = shape == "qq"
        # Heading in from the start, a clockwise loop bends right (adds
        # angle in this convention) and an anticlockwise one bends left.
        swing = math.pi / 2 * (1 if clockwise else -1)
        center = polar(button_angle(seg.start) + math.pi + swing, PPQQ_RADIUS)
        return _around(s, e, center, PPQQ_RADIUS, clockwise)
    if shape in ("s", "z"):
        side = -1 if shape == "z" else 1
        turn = polar(button_angle(seg.start) + side * math.pi / 2, SZ_TURN_DIST)
        return _join(_line(s, turn), _line(turn, -turn), _line(-turn, e))
    raise ValueError(f"unknown slide shape {shape!r}")


def _cumulative(points: np.ndarray) -> np.ndarray:
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)])


@dataclass(slots=True)
class SlidePath:
    """A whole slide (all chained segments) as one walkable polyline.

    `progress_at(t)` maps a time to the fraction of arc length the star has
    covered - piecewise linear, because chained segments with their own
    timings move at different speeds.
    """
    points: np.ndarray  # (N, 2)
    cum: np.ndarray  # (N,) arc length at each point
    knot_times: np.ndarray  # times at segment boundaries, launch..end
    knot_lengths: np.ndarray  # arc lengths at the same boundaries
    is_wifi: bool = False
    wifi_ends: tuple[int, int, int] | None = None

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    def progress_at(self, t: float) -> float:
        """Arc length covered at time t (clamped)."""
        return float(np.interp(t, self.knot_times, self.knot_lengths))

    def point_at(self, dist: float) -> tuple[float, float, float]:
        """(x, y, heading) at arc length `dist`."""
        dist = min(max(dist, 0.0), self.length)
        i = int(np.searchsorted(self.cum, dist, side="right")) - 1
        i = min(max(i, 0), len(self.points) - 2)
        a, b = self.points[i], self.points[i + 1]
        seg = self.cum[i + 1] - self.cum[i]
        f = 0.0 if seg <= 0 else (dist - self.cum[i]) / seg
        x, y = a + (b - a) * f
        return float(x), float(y), math.atan2(b[1] - a[1], b[0] - a[0])


def build_slide_path(slide: Slide) -> SlidePath:
    parts = [segment_path(seg) for seg in slide.segments]
    lengths = [float(_cumulative(p)[-1]) for p in parts]
    points = _join(*parts)
    cum = _cumulative(points)

    total_len = float(cum[-1]) or 1.0
    if all(seg.duration is not None for seg in slide.segments):
        durations = [seg.duration for seg in slide.segments]
    else:
        # One timing for the whole chain - constant speed throughout.
        durations = [slide.duration * (l / total_len) for l in lengths]

    knot_times = [slide.launch_time]
    knot_lengths = [0.0]
    for dur, l in zip(durations, lengths):
        knot_times.append(knot_times[-1] + max(dur, 1e-6))
        knot_lengths.append(knot_lengths[-1] + l)
    # The per-part lengths and the joined polyline can differ by a sample;
    # pin the last knot to the real end so the star always arrives.
    knot_lengths[-1] = float(cum[-1])

    wifi = len(slide.segments) == 1 and slide.segments[0].shape == "w"
    wifi_ends = None
    if wifi:
        e = slide.segments[0].end
        wifi_ends = (_wrap(e - 1), e, _wrap(e + 1))
    return SlidePath(
        points=points, cum=cum,
        knot_times=np.array(knot_times), knot_lengths=np.array(knot_lengths),
        is_wifi=wifi, wifi_ends=wifi_ends,
    )
