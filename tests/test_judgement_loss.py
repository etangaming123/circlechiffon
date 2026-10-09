"""ratingcalc/judgement_loss.py - where the lost achievement% went.

The total available is 101% (100% + up to 1% BREAK bonus). Each note type has
a weight (TAP 1, HOLD 2, SLIDE 3, TOUCH 1, BREAK 5), so with no BREAKs one
weighted note is worth base = 100 / (sum of count * weight) percent.
"""

import pytest

from circlechiffon.ratingcalc.judgement_loss import calculate_judgement_loss
from circlechiffon.types import Judgements, NoteTypeJudgement


def nt(cp=0, perfect=0, great=0, good=0, miss=0):
    return NoteTypeJudgement(critical_perfect=cp, perfect=perfect, great=great, good=good, miss=miss)


def by_label(loss):
    return {row.label: row for row in loss.rows}


def test_no_notes_at_all_gives_no_rows():
    loss = calculate_judgement_loss(Judgements(), 100.0)
    assert loss.rows == []
    assert loss.total_lost_percent == pytest.approx(1.0)


def test_total_lost_is_101_minus_achievement():
    j = Judgements(tap=nt(cp=100))
    assert calculate_judgement_loss(j, 99.5).total_lost_percent == pytest.approx(1.5)


def test_without_breaks_cell_losses_follow_the_weights():
    # 633 tap + 87 hold + 141 slide notes: weighted total = 633 + 87*2 + 141*3 = 1230
    j = Judgements(
        tap=nt(cp=630, great=2, miss=1),
        hold=nt(cp=87),
        slide=nt(cp=141),
    )
    base = 100 / 1230
    row = by_label(calculate_judgement_loss(j, 99.0))["TAP"]
    assert row.cells["great"] == pytest.approx(2 * base / 5)    # a great keeps 4/5
    assert row.cells["miss"] == pytest.approx(1 * base)         # a miss loses it all
    assert row.cells["good"] == pytest.approx(0)
    assert row.loss_percent == pytest.approx(2 * base / 5 + base)


def test_without_breaks_rows_cover_exactly_100_percent():
    j = Judgements(tap=nt(cp=600, miss=3), hold=nt(cp=80), slide=nt(cp=140, great=1), touch=nt(cp=30))
    loss = calculate_judgement_loss(j, 99.0)
    assert sum(r.row_total_percent for r in loss.rows) == pytest.approx(100.0)


def test_row_order_and_absent_types_are_omitted():
    j = Judgements(tap=nt(cp=10), slide=nt(cp=10), touch=nt(cp=10))
    assert [r.label for r in calculate_judgement_loss(j, 100.0).rows] == ["TAP", "SLIDE", "TOUCH"]
    assert all(r.attr in {"tap", "slide", "touch"} for r in calculate_judgement_loss(j, 100.0).rows)


def test_good_loses_half_and_great_a_fifth_of_a_tap():
    j = Judgements(tap=nt(cp=98, great=1, good=1))
    base = 100 / 100
    cells = by_label(calculate_judgement_loss(j, 99.0))["TAP"].cells
    assert cells["great"] == pytest.approx(base / 5)
    assert cells["good"] == pytest.approx(base / 2)


def test_breaks_add_the_bonus_pool_to_the_pie():
    # 90 taps + 10 breaks: weighted total 90 + 50 = 140; breaks add up to 1% more.
    j = Judgements(tap=nt(cp=90), brk=nt(cp=10))
    loss = calculate_judgement_loss(j, 101.0)
    assert sum(r.row_total_percent for r in loss.rows) == pytest.approx(101.0)
    assert loss.total_lost_percent == pytest.approx(0.0)


def test_break_losses_account_for_the_whole_gap():
    # whatever the split between cells, row losses must add up to 101 - achievement
    j = Judgements(
        tap=nt(cp=600, perfect=20, great=3, good=1, miss=1),
        hold=nt(cp=80, perfect=2),
        slide=nt(cp=138, perfect=2, miss=1),
        touch=nt(cp=30),
        brk=nt(cp=15, perfect=3, great=1, miss=1),
    )
    loss = calculate_judgement_loss(j, 98.9123)
    assert sum(r.loss_percent for r in loss.rows) == pytest.approx(101.0 - 98.9123)


def test_break_perfect_split_is_reconstructed_when_unambiguous():
    # 9 CP breaks + 1 plain Perfect break; achievement says exactly 0.05% went
    # missing, which only fits "that perfect was a late one" (0.5/10), not an
    # early one (0.25/10) - so the whole 0.05 lands in the perfect cell.
    j = Judgements(tap=nt(cp=90), brk=nt(cp=9, perfect=1))
    brk = by_label(calculate_judgement_loss(j, 101.0 - 0.05))["BREAK"]
    assert brk.cells["perfect"] == pytest.approx(0.05)
    assert brk.cells["great"] == pytest.approx(0.0, abs=1e-9)


def test_ambiguous_break_split_reports_a_range():
    # Few BREAK perfects/greats: several splits explain the displayed
    # achievement equally well, so cells may come back as (low, high).
    j = Judgements(
        tap=nt(cp=650), hold=nt(cp=85), slide=nt(cp=141),
        brk=nt(cp=17, perfect=1, great=1),
    )
    brk = by_label(calculate_judgement_loss(j, 100.4321))["BREAK"]
    for cell in (brk.cells["perfect"], brk.cells["great"]):
        if isinstance(cell, tuple):
            low, high = cell
            assert low <= high
        else:
            assert cell >= 0


def test_all_zero_break_row_does_not_divide_by_zero():
    j = Judgements(tap=nt(cp=100), brk=nt())
    loss = calculate_judgement_loss(j, 100.0)
    assert [r.label for r in loss.rows] == ["TAP", "BREAK"]
