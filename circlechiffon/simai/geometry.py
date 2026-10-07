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


# =============================================================================
# Unity-space slide tracks
#
# Everything below works in MajdataPlay's coordinates - y up, angles in
# degrees counter-clockwise, judgement ring at radius 4.8 units - because the
# imported slide data (`assets/chart_skin/slides.json`, see
# import_chart_skin.py) is in those units. Renderers convert with one matrix.
#
# A SlideTrack is what a renderer needs for one slide: per-segment arrow
# transforms, the points/angles the star steps through, and where the
# completion banner sits. It's built from MajdataPlay's prefab layouts when
# they're installed, else procedurally from the shapes above.
# =============================================================================

UNIT_RING = 4.8
# Prefab arrows are ~0.47 units apart; procedural ones match that spacing.
UNIT_ARROW_SPACING = 0.47
# Sensor distances from the centre, in units (MajdataPlay NoteHelper).
UNIT_TOUCH_RADIUS = {"A": 4.0, "B": 2.2, "C": 0.0, "D": 4.1, "E": 3.1}


def mat_t(x: float, y: float) -> np.ndarray:
    return np.array([[1.0, 0.0, x], [0.0, 1.0, y], [0.0, 0.0, 1.0]])


def mat_r(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def mat_s(sx: float, sy: float) -> np.ndarray:
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])


def mat_angle(m: np.ndarray) -> float:
    """Rotation of a (mirror-free) transform, in degrees."""
    return math.degrees(math.atan2(m[1, 0], m[0, 0]))


def key_point_u(position: float, radius: float = UNIT_RING) -> np.ndarray:
    a = math.radians((5.0 - 2.0 * position) * 22.5)
    return np.array([math.cos(a) * radius, math.sin(a) * radius])


def lane_rotation(position: int) -> float:
    """Rotation that turns a sprite's +y outward along a button's lane."""
    return -22.5 - 45.0 * (position - 1)


def touch_point_u(area: str, index: int) -> np.ndarray:
    if area == "C":
        return np.zeros(2)
    base = 5.0 if area in "AB" else 6.0
    a = math.pi * base / 8 - index * math.pi / 4
    r = UNIT_TOUCH_RADIUS[area]
    return np.array([math.cos(a) * r, math.sin(a) * r])


def touch_rotation(area: str, index: int) -> float:
    """Rotation that points a frame's +y from the centre at the sensor."""
    if area == "C":
        return 0.0
    p = touch_point_u(area, index)
    return math.degrees(math.atan2(p[1], p[0])) - 90.0


def _rel(start: int, end: int) -> int:
    return (end - start) % 8 + 1


def _mirror_key(key: int) -> int:
    return {1: 1, 2: 8, 3: 7, 4: 6, 5: 5, 6: 4, 7: 3, 8: 2}[key]


def _upper_half(key: int) -> bool:
    return key in (7, 8, 1, 2)


def _right_half(key: int) -> bool:
    return key in (1, 2, 3, 4)


def majdata_shape(seg: SlideSegment) -> tuple[str, bool]:
    """Which prefab draws this segment, and whether it's mirrored.
    Same rules as MajdataPlay's NoteLoader.DetectShapeFromText."""
    rel = _rel(seg.start, seg.end)
    shape = seg.shape
    if shape == "-":
        return f"line{rel}", False
    if shape in (">", "<"):
        clockwise = (shape == ">") == _upper_half(seg.start)
        return (f"circle{rel}", False) if clockwise else (f"circle{_mirror_key(rel)}", True)
    if shape == "^":
        return (f"circle{rel}", False) if rel < 5 else (f"circle{_mirror_key(rel)}", True)
    if shape == "v":
        return f"v{rel}", False
    if shape == "pp":
        return f"ppqq{rel}", False
    if shape == "qq":
        return f"ppqq{_mirror_key(rel)}", True
    if shape == "p":
        return f"pq{rel}", False
    if shape == "q":
        return f"pq{_mirror_key(rel)}", True
    if shape == "s":
        return "s", False
    if shape == "z":
        return "s", True
    if shape == "V":
        turn = _rel(seg.start, seg.via or seg.start)
        if turn == 7:
            return f"L{rel}", False
        return f"L{_mirror_key(rel)}", True
    if shape == "w":
        return "wifi", False
    raise ValueError(f"unknown slide shape {shape!r}")


def banner_is_r(seg: SlideSegment) -> bool:
    """Which way the completion banner faces (MajdataPlay DetectJustType)."""
    shape = seg.shape
    if shape == ">":
        return _upper_half(seg.start)
    if shape == "<":
        return not _upper_half(seg.start)
    if shape == "^":
        return (seg.end - seg.start) % 8 < 4
    if shape == "w":
        return _upper_half(seg.end)
    return _right_half(seg.end)


def banner_kind(shape_name: str) -> str:
    if shape_name.startswith("circle"):
        return "curv"
    if shape_name == "wifi":
        return "wifi"
    return "str"


@dataclass(slots=True)
class SegTrack:
    arrows: list[np.ndarray]  # Unity world transform per arrow (sprite-local -> world)
    points: np.ndarray  # (n+2, 2): start key, each arrow, end key
    angles: np.ndarray  # (n+2,) travelling-star rotation at each point, degrees
    t0: float
    t1: float

    def star_at(self, t: float) -> tuple[np.ndarray, float, float]:
        """(position, rotation, progress in point-steps) at time t; equal
        time per step, as MajdataPlay moves its star."""
        span = max(self.t1 - self.t0, 1e-6)
        p = min(max((t - self.t0) / span, 0.0), 1.0)
        f = (len(self.points) - 1) * p
        i = min(int(f), len(self.points) - 2)
        u = f - i
        pos = self.points[i] + (self.points[i + 1] - self.points[i]) * u
        a0, a1 = self.angles[i], self.angles[i + 1]
        d = (a1 - a0 + 180.0) % 360.0 - 180.0
        return pos, a0 + d * u, f


@dataclass(slots=True)
class WifiTrack:
    bars: list[np.ndarray]  # transforms for wifi_0..wifi_10 (empty if procedural)
    starts: np.ndarray  # (3, 2)
    ends: np.ndarray  # (3, 2): end-1, end, end+1
    star_angles: tuple[float, float, float]
    t0: float
    t1: float


@dataclass(slots=True)
class SlideTrack:
    segments: list[SegTrack]
    wifi: WifiTrack | None
    banner: np.ndarray  # Unity transform of the completion banner
    banner_kind: str  # "str" | "curv" | "wifi"
    banner_r: bool
    arrow_count: int
    from_prefab: bool


def _segment_times(slide: Slide, weights: list[float]) -> list[tuple[float, float]]:
    if all(seg.duration is not None for seg in slide.segments):
        durations = [seg.duration for seg in slide.segments]
    else:
        total = sum(weights) or 1.0
        durations = [slide.duration * w / total for w in weights]
    out, t = [], slide.launch_time
    for d in durations:
        out.append((t, t + max(d, 1e-6)))
        t += max(d, 1e-6)
    return out


def _star_angles(arrow_angles: list[float]) -> np.ndarray:
    """MajdataPlay's star rotation list: the first arrow's angle twice, each
    arrow's, then one extrapolated past the last - all +18deg."""
    if not arrow_angles:
        return np.array([18.0, 18.0])
    if len(arrow_angles) == 1:
        last = arrow_angles[0]
    else:
        last = 2 * arrow_angles[-1] - arrow_angles[-2]
    return np.array([arrow_angles[0], *arrow_angles, last]) + 18.0


def _prefab_segment(seg: SlideSegment, table: dict, t0: float, t1: float) -> tuple[SegTrack, dict]:
    name, mirrored = majdata_shape(seg)
    data = table["shapes"][name]
    if mirrored:
        root_angle = -45.0 * seg.start
        root = mat_r(root_angle) @ mat_s(-1.0, 1.0)
    else:
        root_angle = -45.0 * (seg.start - 1)
        root = mat_r(root_angle)

    arrows, points, angles = [], [key_point_u(seg.start)], []
    for x, y, angle, sx in data["arrows"]:
        w = root @ mat_t(x, y) @ mat_r(angle) @ mat_s(sx, 1.0)
        arrows.append(w)
        points.append(w[:2, 2].copy())
        angles.append(root_angle + angle + (180.0 if mirrored else 0.0))
    points.append(key_point_u(seg.end))
    track = SegTrack(arrows=arrows, points=np.array(points), angles=_star_angles(angles), t0=t0, t1=t1)
    return track, {"name": name, "mirrored": mirrored, "root": root, "data": data}


def _prefab_banner(info: dict, seg: SlideSegment) -> tuple[np.ndarray, str, bool]:
    text = info["data"]["text"]
    w = info["root"] @ mat_t(text["x"], text["y"]) @ mat_r(text["angle"])
    if info["mirrored"]:
        w = w @ mat_s(-1.0, 1.0)  # the banner itself is never drawn mirrored
    kind = banner_kind(info["name"])
    is_r = banner_is_r(seg)
    if kind == "str" and (is_r == info["mirrored"]):
        # SlideDrop.LoadSkin: flip the straight banner and nudge it 0.27u.
        w = w @ mat_r(180.0)
        a = math.radians(mat_angle(w))
        w = mat_t(math.sin(a) * 0.27, -math.cos(a) * 0.27) @ w
    return w, kind, is_r


def _wifi_track(slide: Slide, table: dict | None) -> tuple[WifiTrack, np.ndarray, bool]:
    seg = slide.segments[0]
    start, end = seg.start, seg.end
    root = mat_r(-45.0 * (start - 1))
    ends = np.array([key_point_u(_wrap(end - 1)), key_point_u(end), key_point_u(_wrap(end + 1))])
    starts = np.array([key_point_u(start)] * 3)
    angles = tuple(-22.5 * (8 + i + 2 * (start - 1)) for i in range(3))
    bars = []
    is_r = banner_is_r(seg)
    if table is not None:
        data = table["shapes"]["wifi"]
        bars = [root @ mat_t(x, y) @ mat_r(a) @ mat_s(sx, 1.0) for x, y, a, sx in data["arrows"]]
        text = data["text"]
        banner = root @ mat_t(text["x"], text["y"]) @ mat_r(text["angle"])
    else:
        mid = key_point_u(end) * 0.55
        banner = mat_t(*mid) @ mat_r(math.degrees(math.atan2(mid[1], mid[0])) - 90.0)
    if not is_r:
        banner = banner @ mat_r(180.0)
    track = WifiTrack(bars=bars, starts=starts, ends=ends, star_angles=angles,
                      t0=slide.launch_time, t1=slide.end_time)
    return track, banner, is_r


def _procedural_segment(seg: SlideSegment, t0: float, t1: float) -> SegTrack:
    """Arrows along this module's own shapes, as Unity transforms whose
    local -x (the chevron's point) faces the direction of travel."""
    pts = segment_path(seg) * np.array([UNIT_RING, -UNIT_RING])  # y-down norm -> Unity
    cum = _cumulative(pts)
    n = max(1, int(cum[-1] / UNIT_ARROW_SPACING))
    arrows, points, angles = [], [key_point_u(seg.start)], []
    for k in range(1, n + 1):
        d = k * cum[-1] / (n + 1)
        i = min(int(np.searchsorted(cum, d, side="right")) - 1, len(pts) - 2)
        a, b = pts[i], pts[i + 1]
        seg_len = cum[i + 1] - cum[i]
        f = 0.0 if seg_len <= 0 else (d - cum[i]) / seg_len
        pos = a + (b - a) * f
        heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        w = mat_t(*pos) @ mat_r(heading + 180.0)
        arrows.append(w)
        points.append(pos)
        angles.append(heading + 180.0)
    points.append(key_point_u(seg.end))
    return SegTrack(arrows=arrows, points=np.array(points), angles=_star_angles(angles), t0=t0, t1=t1)


def build_track(slide: Slide, table: dict | None) -> SlideTrack:
    """`table` is the parsed slides.json, or None for procedural shapes."""
    if len(slide.segments) == 1 and slide.segments[0].shape == "w":
        wifi, banner, is_r = _wifi_track(slide, table)
        return SlideTrack(segments=[], wifi=wifi, banner=banner, banner_kind="wifi", banner_r=is_r,
                          arrow_count=19, from_prefab=table is not None)

    if table is not None:
        try:
            weights = [len(table["shapes"][majdata_shape(s)[0]]["arrows"]) + 1.0 for s in slide.segments]
        except KeyError:
            table = None  # a shape the prefab map doesn't have - draw it procedurally
    if table is None:
        weights = [float(_cumulative(segment_path(s))[-1]) for s in slide.segments]

    times = _segment_times(slide, weights)
    segments, info = [], None
    for seg, (t0, t1) in zip(slide.segments, times):
        if table is not None:
            track, info = _prefab_segment(seg, table, t0, t1)
        else:
            track = _procedural_segment(seg, t0, t1)
        segments.append(track)

    last = slide.segments[-1]
    if info is not None:
        banner, kind, is_r = _prefab_banner(info, last)
    else:
        end = segments[-1].points[-1]
        prev = segments[-1].points[-2]
        heading = math.degrees(math.atan2(end[1] - prev[1], end[0] - prev[0]))
        pos = end - (end - prev) / max(np.linalg.norm(end - prev), 1e-6) * 1.2
        banner, kind, is_r = mat_t(*pos) @ mat_r(heading), "str", banner_is_r(last)
    return SlideTrack(
        segments=segments, wifi=None, banner=banner, banner_kind=kind, banner_r=is_r,
        arrow_count=sum(len(s.arrows) for s in segments), from_prefab=table is not None,
    )


def load_slide_table(path) -> dict | None:
    import json
    from pathlib import Path

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("shapes") else None
