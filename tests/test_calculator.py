"""ratingcalc/calculator.py - the rating formula.

rating = floor(coefficient * chart_constant * min(100.5, achievement) / 100) (+1 for AP/AP+)

Expected numbers below are worked out by hand from that formula, using a
level-14.0 chart, so a failure means the code moved, not the test.
"""

import pytest

from circlechiffon.ratingcalc.calculator import (
    SCORE_COEFFICIENT_TABLE,
    calculate_rating,
    rank_for_achievement,
    rank_tag_for_achievement,
)
from circlechiffon.types import ComboFlag


# (achievement, rank shown, rating on a 14.0 chart)
#   e.g. 100.5: 22.4 * 14.0 = 313.6, * 1.005 = 315.168 -> 315
@pytest.mark.parametrize(
    "achievement, rank, rating",
    [
        (0, "D", 0),
        (50, "C", 56),           # 8 * 14 * 0.50
        (60, "B", 80),           # 9.6 * 14 * 0.60 = 80.64
        (70, "BB", 109),         # 11.2 * 14 * 0.70 = 109.76
        (75, "BBB", 126),        # 12.0 * 14 * 0.75
        (79.9999, "BBB", 143),   # 12.8 * 14 * 0.799999 = 143.36
        (80, "A", 152),          # 13.6 * 14 * 0.80 = 152.32
        (90, "AA", 191),         # 15.2 * 14 * 0.90 = 191.52
        (94, "AAA", 221),        # 16.8 * 14 * 0.94 = 221.08
        (96.9999, "AAA", 239),   # 17.6 * 14 * 0.969999 = 239.0077
        (97, "S", 271),          # 20.0 * 14 * 0.97 = 271.6
        (98, "S+", 278),         # 20.3 * 14 * 0.98 = 278.516
        (99, "SS", 288),         # 20.8 * 14 * 0.99 = 288.288
        (99.5, "SS+", 293),      # 21.1 * 14 * 0.995 = 293.923
        (99.9999, "SS+", 299),   # 21.4 * 14 * 0.999999 = 299.5997
        (100, "SSS", 302),       # 21.6 * 14 * 1.00 = 302.4
        (100.4999, "SSS", 312),  # 22.2 * 14 * 1.004999 = 312.35
        (100.5, "SSS+", 315),    # 22.4 * 14 * 1.005 = 315.168
    ],
)
def test_rating_at_each_rank_boundary(achievement, rank, rating):
    award = calculate_rating(14.0, achievement)
    assert award.rank == rank
    assert award.rating == rating


def test_achievement_above_100_5_is_capped():
    assert calculate_rating(14.0, 101.0).rating == calculate_rating(14.0, 100.5).rating == 315


@pytest.mark.parametrize("flag", ["ap", "app", ComboFlag.ap, ComboFlag.app])
def test_all_perfect_adds_one(flag):
    assert calculate_rating(14.0, 100.5, flag).rating == 316


@pytest.mark.parametrize("flag", [None, "fc", "fcp", ComboFlag.fc, ComboFlag.fcp])
def test_full_combo_or_nothing_adds_no_bonus(flag):
    assert calculate_rating(14.0, 100.5, flag).rating == 315


def test_award_carries_coefficient_and_tag():
    award = calculate_rating(14.0, 100.5)
    assert award.coefficient == 22.4
    assert award.rank_tag == "sssp"


def test_rating_scales_with_chart_constant():
    assert calculate_rating(15.0, 100.0).rating == 324   # 21.6 * 15
    assert calculate_rating(1.0, 100.0).rating == 21     # 21.6 * 1


def test_rank_helpers_ignore_chart_constant():
    assert rank_for_achievement(49.9) == "D"
    assert rank_for_achievement(74.9) == "BB"
    assert rank_for_achievement(96.5) == "AAA"
    assert rank_for_achievement(100.5) == "SSS+"
    assert rank_tag_for_achievement(98.5) == "sp"
    assert rank_tag_for_achievement(99.4999) == "ss"


def test_every_table_breakpoint_lands_in_its_own_row():
    # Feeding each row's own breakpoint back in must select that same row.
    for breakpoint, coefficient, tag in SCORE_COEFFICIENT_TABLE:
        award = calculate_rating(10.0, breakpoint)
        assert award.coefficient == coefficient, breakpoint
        assert award.rank_tag == tag, breakpoint


def test_table_is_sorted_and_coefficients_never_decrease():
    breakpoints = [row[0] for row in SCORE_COEFFICIENT_TABLE]
    coefficients = [row[1] for row in SCORE_COEFFICIENT_TABLE]
    assert breakpoints == sorted(breakpoints)
    assert coefficients == sorted(coefficients)
