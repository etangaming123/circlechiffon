"""
Port of maimai DX's rating formula, straight from
gekichumai/dxrating (MIT) `packages/maimai-domain/src/best50.ts`:
SCORE_COEFFICIENT_TABLE + calculateRatingAward().

rating = floor(coefficient * chart_constant * min(100.5, achievement%) / 100) + ap_bonus
"""

import math
from dataclasses import dataclass

RANK_NAMES = {
    "d": "D",
    "c": "C",
    "b": "B",
    "bb": "BB",
    "bbb": "BBB",
    "a": "A",
    "aa": "AA",
    "aaa": "AAA",
    "s": "S",
    "sp": "S+",
    "ss": "SS",
    "ssp": "SS+",
    "sss": "SSS",
    "sssp": "SSS+",
}

# (achievement% breakpoint, coefficient, rank tag)
SCORE_COEFFICIENT_TABLE: list[tuple[float, float, str]] = [
    (0, 0, "d"),
    (10, 1.6, "d"),
    (20, 3.2, "d"),
    (30, 4.8, "d"),
    (40, 6.4, "d"),
    (50, 8, "c"),
    (60, 9.6, "b"),
    (70, 11.2, "bb"),
    (75, 12.0, "bbb"),
    (79.9999, 12.8, "bbb"),
    (80, 13.6, "a"),
    (90, 15.2, "aa"),
    (94, 16.8, "aaa"),
    (96.9999, 17.6, "aaa"),
    (97, 20, "s"),
    (98, 20.3, "sp"),
    (98.9999, 20.6, "sp"),
    (99, 20.8, "ss"),
    (99.5, 21.1, "ssp"),
    (99.9999, 21.4, "ssp"),
    (100, 21.6, "sss"),
    (100.4999, 22.2, "sss"),
    (100.5, 22.4, "sssp"),
]

ComboFlag = str | None  # one of "fc", "fcp", "ap", "app", or None


@dataclass(slots=True, frozen=True)
class RatingAward:
    rating: int
    coefficient: float
    rank_tag: str
    rank: str


def calculate_rating(
    internal_level: float,
    achievement: float,
    combo_flag: ComboFlag = None,
) -> RatingAward:
    """internal_level: chart constant (e.g. 13.9). achievement: percentage as a
    float, e.g. 100.5 for AP+ (not a 0-1 fraction)."""
    table = SCORE_COEFFICIENT_TABLE
    for i, (_, coefficient, rank_tag) in enumerate(table):
        is_last = i == len(table) - 1
        next_breakpoint = table[i + 1][0] if not is_last else None
        if is_last or achievement < next_breakpoint:
            ap_bonus = 1 if combo_flag in ("ap", "app") else 0
            rating = math.floor(coefficient * internal_level * min(100.5, achievement) / 100) + ap_bonus
            return RatingAward(
                rating=rating,
                coefficient=coefficient,
                rank_tag=rank_tag,
                rank=RANK_NAMES[rank_tag],
            )
    # unreachable: table always has a final catch-all row
    return RatingAward(rating=0, coefficient=0, rank_tag="d", rank=RANK_NAMES["d"])


def rank_for_achievement(achievement: float) -> str:
    return calculate_rating(0, achievement).rank


def rank_tag_for_achievement(achievement: float) -> str:
    return calculate_rating(0, achievement).rank_tag
