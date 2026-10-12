"""
`/cc-best tips:True`: which chart constants are worth playing to raise a rating.

A play only helps if its rating beats the bucket's current floor (the lowest
entry of B15 / B35). For each constant this finds the lowest S-and-above grade
that clears the floor, then merges neighbouring constants that need the same
grade into ranges. Pure logic, no Discord.
"""

from dataclasses import dataclass

from circlechiffon.ratingcalc.calculator import S_GRADE_BANDS, calculate_rating

MIN_CONSTANT_TENTHS = 10
MAX_CONSTANT_TENTHS = 150


@dataclass(slots=True, frozen=True)
class TipRow:
    low: float
    high: float
    grade: str  # lowest grade that beats the floor at these constants


def _required_grade(constant: float, cutoff: int) -> str | None:
    for rank, low_ach, _ in S_GRADE_BANDS:
        if calculate_rating(constant, low_ach).rating > cutoff:
            return rank
    return None


def tip_rows(cutoff: int) -> list[TipRow]:
    """Constant ranges (ascending) with the lowest grade that beats `cutoff`.
    Constants that can't beat it even at SSS+ are left out."""
    rows: list[TipRow] = []
    for tenths in range(MIN_CONSTANT_TENTHS, MAX_CONSTANT_TENTHS + 1):
        constant = tenths / 10
        grade = _required_grade(constant, cutoff)
        if grade is None:
            continue
        if rows and rows[-1].grade == grade and round(rows[-1].high + 0.1, 1) == constant:
            rows[-1] = TipRow(rows[-1].low, constant, grade)
        else:
            rows.append(TipRow(constant, constant, grade))
    return rows


def _cell(row: TipRow) -> str:
    if row.high >= MAX_CONSTANT_TENTHS / 10:
        span = f"{row.low:.1f}+"
    elif row.low == row.high:
        span = f"{row.low:.1f}"
    else:
        span = f"{row.low:.1f}-{row.high:.1f}"
    return f"{span} {row.grade}"


def _column(cutoff: int | None) -> list[str]:
    if cutoff is None:
        return ["any chart"]
    return [_cell(r) for r in tip_rows(cutoff)] or ["none"]


def format_tips(new_cutoff: int | None, old_cutoff: int | None) -> str:
    """Two-column block (OLD | NEW, matching the b50 image) sized for a phone
    screen. A cutoff of None means that bucket isn't full yet, so any play adds
    rating."""
    old_col, new_col = _column(old_cutoff), _column(new_cutoff)
    rows = max(len(old_col), len(new_col))
    old_col += [""] * (rows - len(old_col))
    new_col += [""] * (rows - len(new_col))
    width = max(len(c) for c in old_col + ["OLD"])
    lines = [f"{'OLD':<{width}} | NEW"]
    lines += [f"{o:<{width}} | {n}".rstrip() for o, n in zip(old_col, new_col)]
    floors = " | ".join(
        f"{label} {value}" for label, value in (("Old", old_cutoff), ("New", new_cutoff)) if value is not None
    )
    header = f"-# Lowest grade that beats your floor ({floors})\n" if floors else ""
    return header + "```\n" + "\n".join(lines) + "\n```"
