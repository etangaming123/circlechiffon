"""
Best-50 bucketing: the best 15 rated scores among charts from the current
game version OR the one before it (B15 - "new"), plus the best 35 rated
scores among everything else (B35 - "old"). Version resolution comes from
dxdata.json itself (SongCatalog.current_version/previous_version - the last
two entries in its chronologically-ordered top-level "versions" list).
Bucket sizing follows gekichumai/dxrating's calculateBest50
(packages/maimai-domain/src/best50.ts, MIT); the current+previous version
window for B15 eligibility is this bot's own definition, not dxrating's.
"""

from dataclasses import dataclass

from circlechiffon.ratingcalc.calculator import calculate_rating
from circlechiffon.songdata.catalog import SongCatalog
from circlechiffon.types import ChartType, Score, Sheet


@dataclass(slots=True, frozen=True)
class RatedEntry:
    score: Score
    sheet: Sheet
    rating: int
    rank: str


@dataclass(slots=True, frozen=True)
class Best50Result:
    b15: list[RatedEntry | None]  # always length 15, padded with None
    b35: list[RatedEntry | None]  # always length 35, padded with None
    b15_total: int
    b35_total: int

    @property
    def total_rating(self) -> int:
        return self.b15_total + self.b35_total


def calculate_best50(scores: list[Score], catalog: SongCatalog, *, next_update_preview: bool = False) -> Best50Result:
    # next_update_preview simulates the current+previous B15 window
    # narrowing to just current_version, as if previous_version had just
    # aged out on the next game update - anything that drops out of the B15
    # set below still gets bucketed into older_entries (B35) as usual,
    # rather than being dropped, matching the real "ages out" mechanic. It
    # can't predict actual unreleased charts since dxdata.json has none.
    if next_update_preview:
        new_versions = {v for v in (catalog.current_version,) if v is not None}
    else:
        new_versions = {v for v in (catalog.current_version, catalog.previous_version) if v is not None}

    current_entries: list[RatedEntry] = []
    older_entries: list[RatedEntry] = []

    for score in scores:
        if score.chart_type == ChartType.utage:
            # UTAGE charts don't count toward rating on real maimai DX NET -
            # dxdata.json does carry sheet entries for them (with their own
            # internal_level_value), so without this they'd otherwise match
            # a sheet and get bucketed in same as any other chart.
            continue
        sheet = catalog.find_sheet(score.title, score.chart_type, score.difficulty)
        if sheet is None or sheet.internal_level_value is None:
            continue
        award = calculate_rating(sheet.internal_level_value, score.achievement, score.combo_flag)
        entry = RatedEntry(score=score, sheet=sheet, rating=award.rating, rank=award.rank)
        if sheet.version in new_versions:
            current_entries.append(entry)
        else:
            older_entries.append(entry)

    current_entries.sort(key=lambda e: e.rating, reverse=True)
    older_entries.sort(key=lambda e: e.rating, reverse=True)

    b15 = current_entries[:15]
    b35 = older_entries[:35]

    return Best50Result(
        b15=b15 + [None] * (15 - len(b15)),
        b35=b35 + [None] * (35 - len(b35)),
        b15_total=sum(e.rating for e in b15),
        b35_total=sum(e.rating for e in b35),
    )
