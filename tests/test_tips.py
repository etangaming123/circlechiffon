"""ratingcalc/tips.py - which chart constants beat a bucket's floor.

Expected numbers are worked out from rating = floor(coef * constant * achievement / 100),
using the lowest achievement of each grade (S 97, S+ 98, SS 99, SS+ 99.5, SSS 100, SSS+ 100.5).
"""

from circlechiffon.ratingcalc.tips import TipRow, format_tips, tip_rows


# floor 300:
#   13.3 SSS+: 22.4 * 13.3 * 1.005 = 299.4 -> 299, not > 300
#   13.4 SSS+: 22.4 * 13.4 * 1.005 = 301.7 -> 301  (first constant that works)
#   13.9 SSS : 21.6 * 13.9         = 300.24 -> 300, still needs SSS+;  14.0 SSS: 302.4 -> 302
#   14.3 SS+ : 21.1 * 14.3 * .995  = 300.2 -> 300, still needs SSS;   14.4 SS+: 302.3 -> 302
def test_rows_for_a_300_floor():
    rows = tip_rows(300)
    assert rows[0] == TipRow(13.4, 13.9, "SSS+")
    assert rows[1] == TipRow(14.0, 14.3, "SSS")
    assert rows[2].low == 14.4 and rows[2].grade == "SS+"
    assert rows[-1].high == 15.0


def test_constants_that_cannot_beat_the_floor_are_left_out():
    assert tip_rows(400) == []
    assert all(r.low >= 13.4 for r in tip_rows(300))


def test_grade_only_gets_easier_as_the_constant_rises():
    order = ["SSS+", "SSS", "SS+", "SS", "S+", "S"]
    grades = [order.index(r.grade) for r in tip_rows(270)]
    assert grades == sorted(grades)


def test_format_marks_open_ended_and_unreachable_columns():
    text = format_tips(None, 400)      # new bucket not full, old unreachable
    row = text.split("```")[1].strip("\n").splitlines()[1]
    assert row.startswith("none") and row.endswith("| any chart")


def test_format_fits_a_phone_screen():
    text = format_tips(285, 270)
    block = text.split("```")[1].strip("\n").splitlines()
    assert block[0].startswith("OLD") and "| NEW" in block[0]
    assert all(len(line) <= 34 for line in block)
    assert "Old 270 | New 285" in text


def test_range_reaching_the_top_constant_uses_a_plus():
    # floor 300: SS (20.8 * 15.0 * .99 = 308.9) still clears it at 15.0, so the last row runs to the cap
    text = format_tips(300, None)
    assert "14.7+ SS" in text
