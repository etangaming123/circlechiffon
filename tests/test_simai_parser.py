"""simai/parser.py - chart text -> timed notes.

Timing used throughout: at (120) a whole note is 2 s, so `{4}` makes each comma
0.5 s and `{8}` 0.25 s. A slide's star waits one beat (0.5 s at 120) before it
leaves.
"""

import pytest

from circlechiffon.simai.parser import (
    SimaiError,
    parse_chart,
    parse_hold_length,
    parse_slide_timing,
)


def test_taps_land_on_the_comma_grid():
    chart = parse_chart("(120){4}1,2,E")
    assert [(t.position, t.time) for t in chart.taps] == [(1, 0.0), (2, 0.5)]


def test_divisor_changes_step_size():
    chart = parse_chart("(120){8}1,2,3,E")
    assert [t.time for t in chart.taps] == pytest.approx([0.0, 0.25, 0.5])


def test_fixed_seconds_step():
    chart = parse_chart("(120){#0.1}1,2,E")
    assert [t.time for t in chart.taps] == pytest.approx([0.0, 0.1])


def test_empty_commas_are_rests():
    chart = parse_chart("(120){4}1,,,2,E")
    assert [t.time for t in chart.taps] == pytest.approx([0.0, 1.5])


def test_default_bpm_used_when_chart_sets_none():
    assert parse_chart("{4}1,2,E").taps[1].time == pytest.approx(0.5)
    assert parse_chart("{4}1,2,E", default_bpm=60).taps[1].time == pytest.approx(1.0)


def test_bpm_change_mid_chart():
    chart = parse_chart("(120){4}1,2,(60)3,4,E")
    assert [t.time for t in chart.taps] == pytest.approx([0.0, 0.5, 1.0, 2.0])   # 60 bpm -> 1 s steps
    assert [(p.bpm, p.beat, p.time) for p in chart.bpms] == [(120, 0, 0), (60, 2, 1.0)]


def test_modifiers_on_taps():
    chart = parse_chart("(120){4}1b,2x,3$,4,E")
    flags = [(t.position, t.is_break, t.is_ex, t.is_star) for t in chart.taps]
    assert flags == [(1, True, False, False), (2, False, True, False), (3, False, False, True), (4, False, False, False)]


def test_hold_length_from_fraction_and_seconds():
    chart = parse_chart("(120){4}1h[4:1],2h[#2],E")
    assert [h.duration for h in chart.holds] == pytest.approx([0.5, 2.0])
    assert chart.taps == []


def test_touch_notes():
    chart = parse_chart("(120){4}A1,C,B3f,D8h[4:1],E")
    assert [(t.area, t.index, t.firework, t.duration) for t in chart.touches] == [
        ("A", 1, False, None), ("C", 0, False, None), ("B", 3, True, None), ("D", 8, False, 0.5),
    ]


def test_sensor_e_with_a_number_is_a_note_not_the_end_marker():
    chart = parse_chart("(120){4}E1,2,E")
    assert [t.area for t in chart.touches] == ["E"]
    assert len(chart.taps) == 1


def test_everything_after_the_end_marker_is_ignored():
    assert parse_chart("(120){4}1,E,2,3").note_count == 1


def test_comments_are_stripped_to_end_of_line():
    chart = parse_chart("(120){4}1,||2,3\n4,E")
    assert [t.position for t in chart.taps] == [1, 4]


def test_bare_digit_run_is_many_taps_at_once():
    chart = parse_chart("(120){4}18,E")
    assert [(t.position, t.time) for t in chart.taps] == [(1, 0.0), (8, 0.0)]
    assert all(t.is_each for t in chart.taps)


def test_slash_makes_notes_simultaneous_and_each():
    chart = parse_chart("(120){4}1/5,3,E")
    each = {t.position: t.is_each for t in chart.taps}
    assert each == {1: True, 5: True, 3: False}


def test_zero_note_marks_a_lone_note_as_each():
    chart = parse_chart("(120){4}3/0,E")
    assert chart.taps[0].is_each


def test_backtick_notes_are_a_hair_apart():
    chart = parse_chart("(120){4}1`2,E")
    assert chart.taps[1].time - chart.taps[0].time == pytest.approx(0.010)


def test_simple_slide():
    chart = parse_chart("(120){4}1-5[4:1],E")
    (slide,) = chart.slides
    assert (slide.head_time, slide.launch_time, slide.duration) == pytest.approx((0.0, 0.5, 0.5))
    assert [(s.shape, s.start, s.end) for s in slide.segments] == [("-", 1, 5)]
    assert slide.end_time == pytest.approx(1.0)
    # a slide with a visible head also gets a star-shaped tap
    assert len(chart.taps) == 1 and chart.taps[0].is_star


def test_slide_head_variants():
    chart = parse_chart("(120){4}1?-5[4:1],2!-6[4:1],3@-7[4:1],E")
    assert len(chart.slides) == 3
    assert [s.sudden for s in chart.slides] == [False, True, False]
    # ? and ! have no head; @ has a plain (non-star) tap head
    assert [(t.position, t.is_star) for t in chart.taps] == [(3, False)]


def test_chained_slide_segments():
    chart = parse_chart("(120){4}1-4q7[4:1],E")
    (slide,) = chart.slides
    assert [(s.shape, s.start, s.end) for s in slide.segments] == [("-", 1, 4), ("q", 4, 7)]


def test_per_segment_timing_sums():
    chart = parse_chart("(120){4}1-4[4:1]-7[4:2],E")
    (slide,) = chart.slides
    assert [s.duration for s in slide.segments] == pytest.approx([0.5, 1.0])
    assert slide.duration == pytest.approx(1.5)


def test_v_slide_keeps_its_turn_point():
    chart = parse_chart("(120){4}1V35[4:1],E")
    seg = chart.slides[0].segments[0]
    assert (seg.shape, seg.start, seg.via, seg.end) == ("V", 1, 3, 5)


def test_two_character_shapes():
    chart = parse_chart("(120){4}1pp5[4:1],2qq6[4:1],E")
    assert [s.segments[0].shape for s in chart.slides] == ["pp", "qq"]


def test_star_launches_several_slides():
    chart = parse_chart("(120){4}1-5[4:1]*-3[4:1],E")
    assert len(chart.slides) == 2
    assert [s.segments[0].end for s in chart.slides] == [5, 3]
    assert all(s.is_each for s in chart.slides)       # same launch moment


def test_malformed_note_is_skipped_with_a_warning():
    chart = parse_chart("(120){4}1,9,2,E")
    assert [t.position for t in chart.taps] == [1, 2]
    assert len(chart.warnings) == 1 and "'9'" in chart.warnings[0]


@pytest.mark.parametrize("text", ["(120", "{4", "(120){4}1h[4:1"])
def test_unclosed_brackets_are_fatal(text):
    with pytest.raises(SimaiError):
        parse_chart(text)


@pytest.mark.parametrize("text", ["(0)1,E", "(-5)1,E", "{0}1,E"])
def test_nonsense_tempo_or_divisor_is_fatal(text):
    with pytest.raises(SimaiError):
        parse_chart(text)


def test_measure_arithmetic():
    chart = parse_chart("(120){4}1,2,3,4,5,E")
    assert chart.total_beats == pytest.approx(5.0)
    assert chart.total_measures == 2
    assert chart.measure_time(1) == pytest.approx(2.0)      # 4 beats at 120 bpm
    assert chart.measure_at(2.0) == 1
    assert chart.measure_at(1.99) == 0
    assert parse_chart("(120){4}1,2,3,4,E").total_measures == 1


def test_measure_time_across_a_bpm_change():
    chart = parse_chart("(120){4}1,2,(60)3,4,E")
    assert chart.time_at_beat(4) == pytest.approx(1.0 + 2 * 1.0)   # 2 beats at 120, then 2 at 60


def test_summary_properties():
    chart = parse_chart("(120){4}1,2h[4:2],A1,E")
    assert chart.note_count == 3
    assert chart.first_time == 0.0
    assert chart.end_time == pytest.approx(1.5)                # the hold: 0.5 + 1.0


def test_empty_chart():
    chart = parse_chart("E")
    assert chart.note_count == 0
    assert chart.first_time == 0.0 and chart.end_time == 0.0
    assert chart.total_measures == 1


# -- the bracket helpers, directly -------------------------------------------------


@pytest.mark.parametrize(
    "inner, expected",
    [("4:1", 0.5), ("8:3", 0.75), ("180#4:1", 240 / 180 / 4), ("#2", 2.0), ("4:0", 0.0)],
)
def test_parse_hold_length(inner, expected):
    assert parse_hold_length(inner, 120) == pytest.approx(expected)


@pytest.mark.parametrize(
    "inner, delay, travel",
    [
        ("4:1", 0.5, 0.5),
        ("#1.5", 0.5, 1.5),
        ("3##1.5", 3.0, 1.5),
        ("3##4:1", 3.0, 0.5),
        ("240#4:1", 0.25, 0.25),
        ("0:0", 0.5, 0.0),
    ],
)
def test_parse_slide_timing(inner, delay, travel):
    assert parse_slide_timing(inner, 120) == pytest.approx((delay, travel))


def test_nonsense_length_is_rejected():
    with pytest.raises(SimaiError):
        parse_hold_length("0:1", 120)
