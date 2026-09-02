"""
Achievement-loss-percentage breakdown - how much of the possible 101%
achievement (100% + BREAK notes' up-to-1% bonus) each judgement/note type
cost you. Port of the scoring math from SpiritsUnite/maimai-score-details's
score-details.js (a DX-NET-page userscript, MIT-style) - only the math is
ported here, not its DOM manipulation.

Note weights: TAP=1, HOLD=2, SLIDE=3, TOUCH=1, BREAK=5. Within a weighted
note, Critical Perfect and Perfect keep full value, Great keeps 4/5, Good
keeps 1/2, Miss keeps 0. BREAK notes additionally carry a shared bonus pool
(up to 1% total, split across every BREAK note) that Perfect/Great BREAK
hits partially forfeit - and DX NET's own table only reports BREAK's
Perfect/Great as raw counts, not their exact bonus-timing split, so that
split has to be reconstructed by brute-force search against the displayed
achievement% (see _reconstruct_break_split).

All returned values are positive magnitudes ("you lost 2.93%") - callers
prepend "-" when formatting for display.
"""

from dataclasses import dataclass

from circlechiffon.types import Judgements, NoteTypeJudgement

LossValue = float | tuple[float, float]  # single magnitude, or (low, high) when BREAK's
# perfect/great split can't be disambiguated from counts + displayed achievement alone


@dataclass(slots=True, frozen=True)
class RowLoss:
    label: str  # "TAP" / "HOLD" / "SLIDE" / "TOUCH" / "BREAK"
    attr: str  # matches Judgements' field name: "tap" / "hold" / "slide" / "touch" / "brk"
    loss_percent: float  # this row's total lost %, as a positive magnitude
    row_total_percent: float  # this row's share of the 101% pie
    cells: dict[str, LossValue]  # keyed by NoteTypeJudgement attr name; a missing key means
    # this cell always loses 0% (critical_perfect everywhere, and perfect on non-BREAK rows)


@dataclass(slots=True, frozen=True)
class JudgementLoss:
    total_lost_percent: float  # 101.0 - achievement, exact by construction
    rows: list[RowLoss]  # present note types only, TAP/HOLD/SLIDE/TOUCH/BREAK order


_ROW_WEIGHTS: list[tuple[str, str, int]] = [
    ("TAP", "tap", 1),
    ("HOLD", "hold", 2),
    ("SLIDE", "slide", 3),
    ("TOUCH", "touch", 1),  # row-index 4th but weight 1, not 4 - matches the note type's real weight
    ("BREAK", "brk", 5),
]


def _row_sum(nt: NoteTypeJudgement | None) -> int:
    if nt is None:
        return 0
    return sum((getattr(nt, f) or 0) for f in ("critical_perfect", "perfect", "great", "good", "miss"))


def _c(nt: NoteTypeJudgement | None, attr: str) -> int:
    return (getattr(nt, attr) or 0) if nt is not None else 0


def _reconstruct_break_split(
    brk: NoteTypeJudgement, base: float, num_breaks: int, rem: float
) -> tuple[LossValue, LossValue]:
    """BREAK's Perfect/Great columns only report raw counts, not how many of
    each were hit at bonus-preserving ("critical") timing vs not - so this
    brute-forces every possible split and picks whichever predicted loss
    lands closest to `rem` (the actual unaccounted loss, derived from the
    real displayed achievement%). When multiple splits tie within the
    source script's own 0.00015 epsilon, returns a (low, high) range instead
    of guessing. Ported verbatim from score-details.js's tie-tracking loop -
    counts here are real note counts (small), so no optimization needed."""
    perfect_count, great_count = _c(brk, "perfect"), _c(brk, "great")

    closest: float | None = None
    next_closest: float | None = None
    next_perfect: float | None = None
    closest_break: tuple[int, int, int, int, int] | None = None

    for gp in range(perfect_count + 1):
        for gg in range(great_count + 1):
            for mg in range(great_count - gg + 1):
                bp = perfect_count - gp
                bg = great_count - gg - mg
                bloss = (
                    (gp / 4 + bp / 2) / num_breaks
                    + (5 * base / 5 + 0.6 / num_breaks) * gg
                    + (5 * base * 0.4 + 0.6 / num_breaks) * mg
                    + (5 * base / 2 + 0.6 / num_breaks) * bg
                )
                diff = abs(bloss - rem)
                if closest is None or diff < closest:
                    if closest is not None and closest_break[0] != gp:
                        next_perfect = closest
                    next_closest = closest
                    closest = diff
                    closest_break = (gp, bp, gg, mg, bg)
                else:
                    if next_closest is None or diff < next_closest:
                        next_closest = diff
                    if closest_break[0] != gp and (next_perfect is None or diff < next_perfect):
                        next_perfect = diff

    if next_perfect is None or next_perfect > 0.00015:
        gp, bp, _gg, _mg, _bg = closest_break
        perfect_loss = (gp / 4 + bp / 2) / num_breaks
        return perfect_loss, rem - perfect_loss

    # Ambiguous within epsilon - report a min~max range for both cells,
    # mirroring the source script's fallback bounds exactly.
    min_ploss, max_ploss = 0.25 / num_breaks * perfect_count, 0.5 / num_breaks * perfect_count
    min_gloss = (5 * base / 5 + 0.6 / num_breaks) * great_count
    max_gloss = (5 * base / 2 + 0.6 / num_breaks) * great_count

    p_lo, p_hi = max(min_ploss, rem - max_gloss), min(max_ploss, rem - min_gloss)
    perfect_loss: LossValue = p_lo if abs(p_hi - p_lo) < 1e-4 else (p_lo, p_hi)

    g_lo, g_hi = max(min_gloss, rem - max_ploss), min(max_gloss, rem - min_ploss)
    great_loss: LossValue = g_lo if abs(g_hi - g_lo) < 1e-4 else (g_lo, g_hi)

    return perfect_loss, great_loss


def calculate_judgement_loss(judgements: Judgements, achievement: float) -> JudgementLoss:
    """Caller must not pass a None Judgements - both call sites already
    branch on `judgements is not None` before doing anything detail-related,
    same convention as _format_judgement_table/_draw_table."""
    total = sum(_row_sum(getattr(judgements, attr)) * weight for _label, attr, weight in _ROW_WEIGHTS)
    if total == 0:
        return JudgementLoss(total_lost_percent=101.0 - achievement, rows=[])
    base = 100.0 / total

    losses: dict[str, dict[str, float]] = {}
    for _label, attr, weight in _ROW_WEIGHTS[:4]:  # TAP, HOLD, SLIDE, TOUCH
        nt = getattr(judgements, attr)
        if nt is None:
            continue
        losses[attr] = {
            "great": weight * _c(nt, "great") * base / 5,
            "good": weight * _c(nt, "good") * base / 2,
            "miss": weight * _c(nt, "miss") * base,
        }

    brk = judgements.brk
    num_breaks = _row_sum(brk) if brk is not None else 0
    if brk is not None:
        losses["brk"] = {}
        if num_breaks > 0:
            losses["brk"]["good"] = _c(brk, "good") * (3 * base + 0.7 / num_breaks)
            losses["brk"]["miss"] = _c(brk, "miss") * (5 * base + 1 / num_breaks)

    loss = sum(v for row in losses.values() for v in row.values())
    rem = (101.0 - loss - achievement) if num_breaks > 0 else 0.0

    rows_out: list[RowLoss] = []
    for label, attr, weight in _ROW_WEIGHTS:
        nt = getattr(judgements, attr)
        if nt is None:
            continue
        row_total = _row_sum(nt) * weight * base
        cells = dict(losses.get(attr, {}))
        row_loss = sum(cells.values())
        if attr == "brk" and num_breaks > 0:
            perfect_loss, great_loss = _reconstruct_break_split(nt, base, num_breaks, rem)
            cells["perfect"] = perfect_loss
            cells["great"] = great_loss
            row_loss += rem
            row_total += 1
        rows_out.append(RowLoss(label=label, attr=attr, loss_percent=row_loss, row_total_percent=row_total, cells=cells))

    return JudgementLoss(total_lost_percent=101.0 - achievement, rows=rows_out)
