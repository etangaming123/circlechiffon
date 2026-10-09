"""renderers/chart_local._sfx_hits - when the answer sound plays.

Charts are 120 BPM at {4}: one comma is 0.5s, and a [4:1] hold lasts one
beat, 0.5s.
"""

import pytest

from circlechiffon.renderers.chart_local import _sfx_hits
from circlechiffon.simai.parser import parse_chart


def _hits(text: str, t_start: float = 0.0, t_end: float = 60.0):
    return [(round(h.time_ms), h.is_each) for h in _sfx_hits(parse_chart(text), t_start, t_end)]


def test_hold_sounds_on_press_and_release():
    # Hold at 0.5s for 0.5s: pressed at 500ms, let go at 1000ms.
    assert _hits("(120){4},1h[4:1],,E") == [(500, False), (1000, False)]


def test_touch_hold_sounds_on_release_too():
    assert _hits("(120){4},Ch[4:1],,E") == [(500, False), (1000, False)]


def test_release_landing_on_a_tap_is_one_each_sound():
    # The tap on 2 lands at 1.0s, exactly when the hold on 1 ends.
    assert _hits("(120){4},1h[4:1],2,E") == [(500, False), (1000, True)]


def test_plain_taps_and_touches_are_unchanged():
    assert _hits("(120){4},1,2/3,B1,E") == [(500, False), (1000, True), (1500, False)]


def test_window_keeps_only_sounds_inside_it_and_shifts_them():
    # From 0.75s the hold's press (0.5s) is cut; its release lands 250ms in.
    assert _hits("(120){4},1h[4:1],,E", t_start=0.75) == [(250, False)]
    # The window end is exclusive: a release exactly at t_end is dropped.
    assert _hits("(120){4},1h[4:1],,E", t_end=1.0) == [(500, False)]


@pytest.mark.parametrize("text", ["(120){4},1h[4:1],,E", "(120){4},Ch[4:1],,E"])
def test_each_hold_adds_exactly_one_extra_sound(text):
    chart = parse_chart(text)
    assert len(_sfx_hits(chart, 0.0, 60.0)) == 2
