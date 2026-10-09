"""ratingcalc/best50.py - B15 (current + previous version) / B35 (the rest).

Uses FakeCatalog (tests/conftest.py) so no dxdata.json is needed. Versions in
these tests: "NEW" = current, "PREV" = previous, anything else = old.
"""

from circlechiffon.ratingcalc.best50 import calculate_best50
from circlechiffon.ratingcalc.calculator import calculate_rating
from circlechiffon.types import ChartType, ComboFlag, Difficulty, Score


def score(title, achievement=100.0, chart_type=ChartType.dx, difficulty=Difficulty.master, combo_flag=None):
    return Score(title=title, difficulty=difficulty, chart_type=chart_type, achievement=achievement, combo_flag=combo_flag)


def test_buckets_by_version(fake_catalog):
    fake_catalog.add("current song", version="NEW")
    fake_catalog.add("previous song", version="PREV")
    fake_catalog.add("old song", version="OLD")
    result = calculate_best50([score("current song"), score("previous song"), score("old song")], fake_catalog)

    assert {e.score.title for e in result.b15 if e} == {"current song", "previous song"}
    assert {e.score.title for e in result.b35 if e} == {"old song"}


def test_lists_are_padded_to_fixed_length(fake_catalog):
    fake_catalog.add("a", version="NEW")
    result = calculate_best50([score("a")], fake_catalog)
    assert len(result.b15) == 15 and len(result.b35) == 35
    assert result.b15[0] is not None and all(e is None for e in result.b15[1:])
    assert all(e is None for e in result.b35)


def test_empty_score_list(fake_catalog):
    result = calculate_best50([], fake_catalog)
    assert result.b15 == [None] * 15
    assert result.b35 == [None] * 35
    assert result.total_rating == 0


def test_buckets_are_capped_at_15_and_35(fake_catalog):
    scores = []
    for i in range(20):
        fake_catalog.add(f"new{i}", version="NEW", level=10.0 + i * 0.1)
        scores.append(score(f"new{i}"))
    for i in range(40):
        fake_catalog.add(f"old{i}", version="OLD", level=10.0 + i * 0.1)
        scores.append(score(f"old{i}"))
    result = calculate_best50(scores, fake_catalog)

    assert all(e is not None for e in result.b15)
    assert all(e is not None for e in result.b35)
    # the cut keeps the HIGHEST-rated: new0..new4 and old0..old4 (lowest constants) must be out
    kept_new = {e.score.title for e in result.b15}
    kept_old = {e.score.title for e in result.b35}
    assert {f"new{i}" for i in range(5)}.isdisjoint(kept_new)
    assert {f"old{i}" for i in range(5)}.isdisjoint(kept_old)
    assert "new19" in kept_new and "old39" in kept_old


def test_sorted_by_rating_descending(fake_catalog):
    fake_catalog.add("low", version="NEW", level=10.0)
    fake_catalog.add("high", version="NEW", level=14.0)
    fake_catalog.add("mid", version="NEW", level=12.0)
    result = calculate_best50([score("low"), score("high"), score("mid")], fake_catalog)
    assert [e.score.title for e in result.b15[:3]] == ["high", "mid", "low"]


def test_equal_ratings_are_ordered_by_achievement(fake_catalog):
    # 100.0% and 100.2% on a 10.0 chart both floor to 216, so only the
    # achievement tiebreak can order them.
    fake_catalog.add("exact", version="NEW", level=10.0)
    fake_catalog.add("better", version="NEW", level=10.0, difficulty=Difficulty.expert)
    result = calculate_best50(
        [score("exact", 100.0), score("better", 100.2, difficulty=Difficulty.expert)], fake_catalog
    )
    assert result.b15[0].rating == result.b15[1].rating == 216
    assert [e.score.achievement for e in result.b15[:2]] == [100.2, 100.0]


def test_totals_sum_each_bucket(fake_catalog):
    fake_catalog.add("n1", version="NEW", level=14.0)
    fake_catalog.add("o1", version="OLD", level=13.0)
    result = calculate_best50([score("n1", 100.5), score("o1", 100.0)], fake_catalog)
    assert result.b15_total == 315                    # 22.4 * 14.0 * 1.005
    assert result.b35_total == 280                    # 21.6 * 13.0 * 1.00 = 280.8
    assert result.total_rating == 315 + 280


def test_ap_bonus_flows_through(fake_catalog):
    fake_catalog.add("song", version="NEW", level=14.0)
    plain = calculate_best50([score("song", 100.5)], fake_catalog).b15_total
    ap = calculate_best50([score("song", 100.5, combo_flag=ComboFlag.app)], fake_catalog).b15_total
    assert ap == plain + 1


def test_entry_matches_calculate_rating(fake_catalog):
    fake_catalog.add("song", version="NEW", level=13.7)
    entry = calculate_best50([score("song", 99.1234)], fake_catalog).b15[0]
    award = calculate_rating(13.7, 99.1234)
    assert (entry.rating, entry.rank) == (award.rating, award.rank)


def test_utage_scores_never_count(fake_catalog):
    fake_catalog.add("utage song", chart_type=ChartType.utage, version="NEW", level=14.0)
    result = calculate_best50([score("utage song", chart_type=ChartType.utage)], fake_catalog)
    assert result.total_rating == 0
    assert all(e is None for e in result.b15 + result.b35)


def test_scores_with_no_catalog_match_are_skipped(fake_catalog):
    result = calculate_best50([score("not in catalog")], fake_catalog)
    assert result.total_rating == 0


def test_sheet_without_constant_is_skipped(fake_catalog):
    fake_catalog.add("no constant", version="NEW")
    fake_catalog._sheets[("no constant", ChartType.dx, Difficulty.master)].internal_level_value = None
    assert calculate_best50([score("no constant")], fake_catalog).total_rating == 0


def test_same_title_different_difficulty_are_separate_entries(fake_catalog):
    fake_catalog.add("song", difficulty=Difficulty.master, version="NEW", level=13.0)
    fake_catalog.add("song", difficulty=Difficulty.remaster, version="NEW", level=14.0)
    result = calculate_best50(
        [score("song", difficulty=Difficulty.master), score("song", difficulty=Difficulty.remaster)], fake_catalog
    )
    assert sum(1 for e in result.b15 if e) == 2


def test_next_update_preview_ages_previous_version_into_b35(fake_catalog):
    fake_catalog.add("current", version="NEW")
    fake_catalog.add("previous", version="PREV")
    scores = [score("current"), score("previous")]

    now = calculate_best50(scores, fake_catalog)
    preview = calculate_best50(scores, fake_catalog, next_update_preview=True)

    assert {e.score.title for e in now.b15 if e} == {"current", "previous"}
    assert {e.score.title for e in preview.b15 if e} == {"current"}
    # aged out, not dropped: it moves to B35, so the overall total is unchanged
    assert {e.score.title for e in preview.b35 if e} == {"previous"}
    assert preview.total_rating == now.total_rating


def test_catalog_with_no_versions_puts_everything_in_b35(fake_catalog):
    fake_catalog.current_version = None
    fake_catalog.previous_version = None
    fake_catalog.add("song", version=None)
    result = calculate_best50([score("song")], fake_catalog)
    assert all(e is None for e in result.b15)
    assert result.b35[0] is not None
