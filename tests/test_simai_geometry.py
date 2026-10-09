"""simai/geometry.py - where buttons sit and the path each slide shape takes.

Coordinates: centre (0, 0), ring radius 1, y points DOWN (screen convention),
angles clockwise from 12 o'clock, button 1 at 22.5 degrees.
"""

import math

import numpy as np
import pytest

from circlechiffon.simai.geometry import (
    SlidePath,
    build_slide_path,
    button_angle,
    button_point,
    segment_path,
    touch_point,
)
from circlechiffon.simai.parser import SLIDE_SHAPES, Slide, SlideSegment


def test_button_angles_are_45_degrees_apart():
    assert button_angle(1) == pytest.approx(math.radians(22.5))
    assert button_angle(8) == pytest.approx(math.radians(337.5))
    for k in range(1, 8):
        assert button_angle(k + 1) - button_angle(k) == pytest.approx(math.radians(45))


def test_buttons_sit_on_the_ring():
    for k in range(1, 9):
        assert np.linalg.norm(button_point(k)) == pytest.approx(1.0)


def test_button_one_is_top_right_and_five_bottom_left():
    x1, y1 = button_point(1)
    assert x1 > 0 and y1 < 0          # y is down, so negative = up
    x5, y5 = button_point(5)
    assert x5 < 0 and y5 > 0


def test_buttons_mirror_left_to_right():
    for k in range(1, 9):
        mirrored = button_point(9 - k)
        assert button_point(k)[0] == pytest.approx(-mirrored[0])
        assert button_point(k)[1] == pytest.approx(mirrored[1])


def test_touch_sensors():
    assert np.allclose(touch_point("C", 0), (0, 0))
    assert np.allclose(touch_point("D", 1), (0, -0.8))                 # D1 is at 12 o'clock
    assert np.allclose(touch_point("A", 3), button_point(3) * 0.8)     # A lines up with the buttons
    # B is nearer the middle than A
    assert np.linalg.norm(touch_point("B", 3)) < np.linalg.norm(touch_point("A", 3))


def seg(shape, start, end, via=None):
    return SlideSegment(shape=shape, start=start, end=end, via=via)


@pytest.mark.parametrize("shape", [s for s in SLIDE_SHAPES if s != "V"])
@pytest.mark.parametrize("start, end", [(1, 5), (2, 6), (3, 4), (8, 3), (6, 1)])
def test_every_shape_runs_from_its_start_to_its_end(shape, start, end):
    path = segment_path(seg(shape, start, end))
    assert path.ndim == 2 and path.shape[1] == 2 and len(path) >= 2
    assert np.all(np.isfinite(path))
    assert np.allclose(path[0], button_point(start), atol=1e-6)
    assert np.allclose(path[-1], button_point(end), atol=1e-6)


def test_v_slide_passes_through_its_turn_point():
    path = segment_path(seg("V", 1, 5, via=3))
    assert np.allclose(path[0], button_point(1))
    assert np.allclose(path[-1], button_point(5))
    assert np.min(np.linalg.norm(path - button_point(3), axis=1)) < 0.02


def test_straight_slide_is_a_straight_line():
    path = segment_path(seg("-", 1, 5))
    length = float(np.sum(np.linalg.norm(np.diff(path, axis=0), axis=1)))
    assert length == pytest.approx(np.linalg.norm(button_point(5) - button_point(1)), rel=1e-3)


def test_v_shape_through_the_centre():
    path = segment_path(seg("v", 1, 3))
    assert np.min(np.linalg.norm(path, axis=1)) < 0.02


def slide(*segments, launch=0.5, duration=1.0):
    return Slide(head_time=0.0, launch_time=launch, duration=duration, segments=list(segments))


def test_slide_path_progress_runs_from_zero_to_full_length():
    path = build_slide_path(slide(seg("-", 1, 5), launch=0.5, duration=1.0))
    assert isinstance(path, SlidePath)
    assert path.progress_at(0.0) == 0.0                       # before launch: clamped
    assert path.progress_at(0.5) == pytest.approx(0.0)
    assert path.progress_at(1.5) == pytest.approx(path.length)
    assert path.progress_at(99.0) == pytest.approx(path.length)
    assert 0 < path.progress_at(1.0) < path.length


def test_slide_path_progress_never_goes_backwards():
    path = build_slide_path(slide(seg("-", 1, 4), seg("q", 4, 7)))
    samples = [path.progress_at(t) for t in np.linspace(0.0, 2.0, 50)]
    assert samples == sorted(samples)


def test_constant_speed_for_a_single_timing():
    path = build_slide_path(slide(seg("-", 1, 5), launch=0.0, duration=2.0))
    assert path.progress_at(1.0) == pytest.approx(path.length / 2, rel=1e-3)


def test_per_segment_durations_change_speed():
    fast_then_slow = slide(seg("-", 1, 5), seg("-", 5, 1), launch=0.0, duration=3.0)
    fast_then_slow.segments[0].duration = 1.0
    fast_then_slow.segments[1].duration = 2.0
    path = build_slide_path(fast_then_slow)
    # both legs are the same length, so half way in distance is 1 s in, not 1.5 s
    assert path.progress_at(1.0) == pytest.approx(path.length / 2, rel=1e-2)


def test_point_at_ends_and_clamping():
    path = build_slide_path(slide(seg("-", 1, 5)))
    x, y, _heading = path.point_at(0.0)
    assert (x, y) == pytest.approx(tuple(button_point(1)), abs=1e-6)
    x, y, _heading = path.point_at(path.length)
    assert (x, y) == pytest.approx(tuple(button_point(5)), abs=1e-6)
    assert path.point_at(-5.0)[:2] == path.point_at(0.0)[:2]
    assert path.point_at(path.length + 5.0)[:2] == pytest.approx(path.point_at(path.length)[:2])


def test_wifi_slide_flags_its_three_ends():
    path = build_slide_path(slide(seg("w", 1, 1)))
    assert path.is_wifi and path.wifi_ends == (8, 1, 2)         # wraps round the ring
    assert build_slide_path(slide(seg("w", 3, 5))).wifi_ends == (4, 5, 6)


def test_normal_slide_is_not_wifi():
    path = build_slide_path(slide(seg("-", 1, 5)))
    assert not path.is_wifi and path.wifi_ends is None
