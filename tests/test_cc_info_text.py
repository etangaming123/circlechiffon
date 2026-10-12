"""cogs/songs.py - how /cc-info words a chart constant."""

from circlechiffon.cogs.songs import _constant_text
from circlechiffon.types import ChartType, Difficulty, Sheet


def sheet(value=13.4, jp=None, intl=True):
    return Sheet(type=ChartType.dx, difficulty=Difficulty.master, level="13", internal_level_value=value,
                 intl=intl, jp_internal_level_value=jp)


def test_plain_constant():
    assert _constant_text(sheet()) == "13.4"


def test_flags_the_jp_constant_when_it_differs():
    assert _constant_text(sheet(13.4, jp=13.6)) == "13.4 → 13.6 next upd"
    assert _constant_text(sheet(13.6, jp=13.4)) == "13.6 → 13.4 next upd"    # can go down too


def test_same_constant_has_no_flag():
    assert _constant_text(sheet(13.4, jp=13.4)) == "13.4"


def test_jp_only_chart_shows_its_one_constant():
    assert _constant_text(sheet(13.4, jp=13.6, intl=False)) == "13.4"
