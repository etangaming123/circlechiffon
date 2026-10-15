"""
Best-50 bucketing: the best 15 rated scores among charts from the current
game version OR the one before it (B15 - "new"), plus the best 35 rated
scores among everything else (B35 - "old"). Version resolution comes from
dxdata.json itself (SongCatalog.current_version/previous_version - the latest
version released on INTL and the one before it).
Bucket sizing follows gekichumai/dxrating's calculateBest50
(packages/maimai-domain/src/best50.ts, MIT); the current+previous version
window for B15 eligibility is this bot's own definition, not dxrating's.
"""

from dataclasses import dataclass, replace

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


def calculate_best50(
    scores: list[Score], catalog: SongCatalog, *, next_update_preview: bool = False, worst: bool = False
) -> Best50Result:
    # worst=True is /cc-worst: the same B15/B35 buckets, but the LOWEST rated
    # plays, ascending. Unplayed charts (score.played False - the scores list
    # carries them with achievement 0.0, same as a real 0% play) are skipped, otherwise they'd always be the worst.
    # next_update_preview simulates the current+previous B15 window
    # narrowing to just current_version, as if previous_version had just
    # aged out on the next game update - anything that drops out of the B15
    # set below still gets bucketed into older_entries (B35) as usual,
    # rather than being dropped, matching the real "ages out" mechanic. It
    # can't predict actual unreleased charts: JP-only charts have no INTL score
    # to bucket. It does apply JP's already-announced constant changes
    # (Sheet.jp_internal_level_value) to INTL charts.
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
        if worst and not score.played:
            continue
        sheet = catalog.find_sheet(score.title, score.chart_type, score.difficulty)
        if sheet is None or sheet.internal_level_value is None:
            continue
        if next_update_preview and sheet.intl and sheet.jp_internal_level_value is not None:
            # JP already moved this chart's constant; INTL most likely follows
            sheet = replace(
                sheet,
                internal_level_value=sheet.jp_internal_level_value,
                level=sheet.jp_level or sheet.level,
            )
        award = calculate_rating(sheet.internal_level_value, score.achievement, score.combo_flag)
        entry = RatedEntry(score=score, sheet=sheet, rating=award.rating, rank=award.rank)
        if sheet.version in new_versions:
            current_entries.append(entry)
        else:
            older_entries.append(entry)

    # Rating first, then achievement as the tiebreak - the rating formula
    # floors to an int, so plenty of distinct achievements collapse onto the
    # same rating, and without the second key their order falls out of
    # whatever order client.get_music_scores() happened to return. Ties also
    # decide which entries make the cut at the 15/35 boundary, not just how
    # they're displayed.
    current_entries.sort(key=lambda e: (e.rating, e.score.achievement), reverse=not worst)
    older_entries.sort(key=lambda e: (e.rating, e.score.achievement), reverse=not worst)

    b15 = current_entries[:15]
    b35 = older_entries[:35]

    return Best50Result(
        b15=b15 + [None] * (15 - len(b15)),
        b35=b35 + [None] * (35 - len(b35)),
        b15_total=sum(e.rating for e in b15),
        b35_total=sum(e.rating for e in b35),
    )


@dataclass(slots=True, frozen=True)
class Best50Stats:
    """Summary numbers shown under a rendered b50. `edge_*` is the bucket's last
    entry (the floor of a best list, the ceiling of a worst list) and `top_*` its
    first (the max of a best list, the min of a worst list)."""

    avg_overall: float
    avg_new: float
    avg_old: float
    edge_new: int | None
    edge_old: int | None
    top_new: int | None
    top_old: int | None
    new_full: bool
    old_full: bool


def summarize(result: Best50Result) -> Best50Stats:
    new_entries = [e for e in result.b15 if e is not None]
    old_entries = [e for e in result.b35 if e is not None]
    total_count = len(new_entries) + len(old_entries)
    return Best50Stats(
        avg_overall=result.total_rating / total_count if total_count else 0,
        avg_new=result.b15_total / len(new_entries) if new_entries else 0,
        avg_old=result.b35_total / len(old_entries) if old_entries else 0,
        edge_new=new_entries[-1].rating if new_entries else None,
        edge_old=old_entries[-1].rating if old_entries else None,
        top_new=new_entries[0].rating if new_entries else None,
        top_old=old_entries[0].rating if old_entries else None,
        new_full=len(new_entries) == len(result.b15),
        old_full=len(old_entries) == len(result.b35),
    )


def format_stats(stats: Best50Stats, *, worst: bool = False) -> str:
    """The Average / Floor / Max lines (Ceiling / Min for a worst list), Old before New like the b50 image."""

    def fmt(value: int | None) -> str:
        return str(value) if value is not None else "N/A"

    edge_label, top_label = ("Ceiling", "Min") if worst else ("Floor", "Max")
    return (
        f"Average rating: Overall **{stats.avg_overall:.2f}**, Old **{stats.avg_old:.2f}**, New **{stats.avg_new:.2f}**\n"
        f"{edge_label}: Old: **{fmt(stats.edge_old)}**, New: **{fmt(stats.edge_new)}**\n"
        f"{top_label}: Old: **{fmt(stats.top_old)}**, New: **{fmt(stats.top_new)}**\n"
    )
