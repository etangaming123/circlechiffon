"""songdata/catalog.py - loading dxdata.json and searching it.

Each test builds a tiny dxdata-shaped JSON file in a temp folder, so results
don't shift when the real catalog is updated. One smoke test at the end loads
the real data/dxdata.json.
"""

import json

import pytest

from circlechiffon.songdata import catalog as catalog_module
from circlechiffon.songdata.catalog import DATA_PATH, SongCatalog
from circlechiffon.types import ChartType, Difficulty


def sheet(type="dx", difficulty="master", level=13.0, version="CiRCLE", **extra):
    return {"type": type, "difficulty": difficulty, "level": str(int(level)), "internalLevelValue": level,
            "version": version, **extra}


def song(title, sheets=None, acronyms=None, song_id=None):
    return {"songId": song_id or title, "title": title, "artist": "artist", "category": "cat", "bpm": 150.0,
            "imageName": "img", "searchAcronyms": acronyms or [], "sheets": sheets if sheets is not None else [sheet()]}


@pytest.fixture
def make_catalog(tmp_path):
    def build(songs, versions=(("OLD",), ("PREV",), ("NEW",))):
        path = tmp_path / "dxdata.json"
        path.write_text(json.dumps({
            "updateTime": "2026-01-01", "songs": songs,
            "versions": [{"version": v[0]} for v in versions],
        }), encoding="utf-8")
        return SongCatalog(path)
    return build


# -- loading ----------------------------------------------------------------------


def test_blank_titled_songs_are_dropped(make_catalog):
    cat = make_catalog([song("real"), song("   ", song_id="blank"), song("", song_id="empty")])
    assert [s.title for s in cat.songs] == ["real"]


def test_sheets_are_parsed(make_catalog):
    raw = song("x", [sheet(noteCounts={"tap": 10, "hold": 2, "slide": 3, "touch": 4, "break": 5, "total": 24},
                           isSpecial=True, noteDesigner="someone", releaseDate="2025-01-01")])
    parsed = make_catalog([raw]).get("x").sheets[0]
    assert (parsed.type, parsed.difficulty, parsed.internal_level_value) == (ChartType.dx, Difficulty.master, 13.0)
    assert parsed.is_special and parsed.note_designer == "someone"
    assert parsed.note_counts.brk == 5 and parsed.note_counts.total == 24     # "break" -> brk


def test_utage_free_text_difficulty_becomes_none(make_catalog):
    cat = make_catalog([song("party", [sheet(type="utage", difficulty="【宴】")])])
    parsed = cat.get("party").sheets[0]
    assert parsed.type == ChartType.utage and parsed.difficulty is None


def test_versions_are_the_last_two(make_catalog):
    cat = make_catalog([song("x")])
    assert (cat.current_version, cat.previous_version) == ("NEW", "PREV")


def test_single_or_missing_versions(make_catalog):
    one = make_catalog([song("x")], versions=[("ONLY",)])
    assert (one.current_version, one.previous_version) == ("ONLY", None)
    none = make_catalog([song("x")], versions=[])
    assert (none.current_version, none.previous_version) == (None, None)


def test_remote_aliases_are_merged_without_duplicates(make_catalog, monkeypatch):
    monkeypatch.setattr(catalog_module, "fetch_aliases_by_song_id", lambda: {"x": ["alias", "own"], "other": ["nope"]})
    cat = make_catalog([song("x", acronyms=["own"])])
    assert cat.get("x").search_acronyms == ["own", "alias"]


# -- lookups ----------------------------------------------------------------------


def test_get_and_get_by_title(make_catalog):
    cat = make_catalog([song("Hello World", song_id="id-1")])
    assert cat.get("id-1").title == "Hello World"
    assert cat.get("missing") is None
    assert cat.get_by_title("hello WORLD").song_id == "id-1"
    assert cat.get_by_title("nope") is None


def test_index_of_matches_position_in_songs(make_catalog):
    cat = make_catalog([song("a"), song(" ", song_id="blank"), song("b")])
    assert [cat.index_of(s.song_id) for s in cat.songs] == [0, 1]
    assert cat.index_of("blank") is None


def test_find_sheet_by_type_and_difficulty(make_catalog):
    cat = make_catalog([song("S", [
        sheet(type="std", difficulty="master", level=12.0),
        sheet(type="dx", difficulty="master", level=13.0),
        sheet(type="dx", difficulty="remaster", level=14.0),
    ])])
    assert cat.find_sheet("S", ChartType.dx, Difficulty.master).internal_level_value == 13.0
    assert cat.find_sheet("S", ChartType.std, Difficulty.master).internal_level_value == 12.0
    assert cat.find_sheet("s", ChartType.dx, Difficulty.remaster).internal_level_value == 14.0   # case-insensitive
    assert cat.find_sheet("S", ChartType.dx, Difficulty.basic) is None
    assert cat.find_sheet("nope", ChartType.dx, Difficulty.master) is None


def test_find_sheet_without_a_type_takes_the_first_match(make_catalog):
    cat = make_catalog([song("S", [sheet(type="std", level=12.0), sheet(type="dx", level=13.0)])])
    assert cat.find_sheet("S", None, Difficulty.master).internal_level_value == 12.0


def test_find_sheet_searches_every_song_sharing_a_title(make_catalog):
    cat = make_catalog([
        song("Same", [sheet(difficulty="basic")], song_id="one"),
        song("Same", [sheet(difficulty="master", level=14.0)], song_id="two"),
    ])
    assert cat.find_sheet("Same", ChartType.dx, Difficulty.master).internal_level_value == 14.0


# -- search -----------------------------------------------------------------------


def test_empty_query_returns_nothing(make_catalog):
    cat = make_catalog([song("abc")])
    assert cat.search("") == [] and cat.search("   ") == []


def test_exact_match_beats_substring(make_catalog):
    cat = make_catalog([song("Star Light"), song("Star"), song("Starlight Express")])
    assert [s.title for s in cat.search("star")][:1] == ["Star"]


def test_search_is_case_insensitive(make_catalog):
    cat = make_catalog([song("Supersonic Generation")])
    assert cat.search("SUPERSONIC")[0].title == "Supersonic Generation"


def test_alias_finds_the_song(make_catalog):
    cat = make_catalog([song("夜に駆ける", acronyms=["YOASOBI", "yoru ni kakeru"])])
    assert cat.search("yoasobi")[0].title == "夜に駆ける"
    assert cat.search("yoru ni kakeru")[0].title == "夜に駆ける"


def test_substring_ranks_before_fuzzy(make_catalog):
    cat = make_catalog([song("Generation Gap"), song("Supersonic Generation")])
    titles = [s.title for s in cat.search("generation")]
    assert set(titles) == {"Generation Gap", "Supersonic Generation"}


def test_typo_still_finds_via_fuzzy_fallback(make_catalog):
    cat = make_catalog([song("Supersonic Generation"), song("Completely Unrelated Tune")])
    assert cat.search("Supersonic Generatoin")[0].title == "Supersonic Generation"


def test_limit_is_respected(make_catalog):
    cat = make_catalog([song(f"Song {i}") for i in range(30)])
    assert len(cat.search("song", limit=5)) == 5
    assert len(cat.search("song")) == 10


def test_nonsense_query_matches_nothing(make_catalog):
    cat = make_catalog([song("Supersonic Generation")])
    assert cat.search("zzzzqqqq") == []


# -- real data ----------------------------------------------------------------------


@pytest.mark.skipif(not DATA_PATH.exists(), reason="data/dxdata.json not present")
def test_real_catalog_loads():
    cat = SongCatalog()
    assert len(cat.songs) > 500
    assert cat.current_version and cat.previous_version
    assert all(s.title.strip() for s in cat.songs)
    # a title that has existed since the first DX release
    assert cat.search("Supersonic Generation")


# -- INTL availability ------------------------------------------------------------


def test_sheets_default_to_intl_and_flag_is_parsed(make_catalog):
    cat = make_catalog([song("both", [sheet(intl=True), sheet(difficulty="expert", intl=False)]),
                        song("jp", [sheet(intl=False)]), song("old data")])
    assert [s.intl for s in cat.get("both").sheets] == [True, False]
    assert not cat.get("both").jp_only
    assert cat.get("jp").jp_only
    assert not cat.get("old data").jp_only        # no flag in the data = available


def test_current_version_is_the_latest_one_released_on_intl(make_catalog, monkeypatch):
    monkeypatch.setattr(catalog_module, "MIN_INTL_SHEETS", 3)
    songs = [song(f"new{i}", [sheet(version="NEW", intl=True)]) for i in range(3)]
    songs += [song(f"jp{i}", [sheet(version="JPNEXT", intl=False)]) for i in range(5)]
    # a JP-version song flagged INTL early is below the threshold, so it can't move the window
    songs.append(song("early", [sheet(version="JPNEXT", intl=True)]))
    cat = make_catalog(songs, versions=(("OLD",), ("PREV",), ("NEW",), ("JPNEXT",)))
    assert (cat.current_version, cat.previous_version) == ("NEW", "PREV")
    assert cat.get("early").sheets[0].version == "NEW"      # bucketed as current, not as a future version
    assert cat.get("jp0").sheets[0].version == "JPNEXT"     # JP-only charts keep their own version


def test_current_version_moves_when_intl_gets_the_next_version(make_catalog, monkeypatch):
    monkeypatch.setattr(catalog_module, "MIN_INTL_SHEETS", 3)
    songs = [song(f"s{i}", [sheet(version=v, intl=True)]) for v in ("PREV", "NEW", "JPNEXT") for i in range(3)]
    cat = make_catalog([song(f"{s['title']}-{i}", s["sheets"], song_id=f"{s['songId']}-{i}") for i, s in enumerate(songs)],
                       versions=(("OLD",), ("PREV",), ("NEW",), ("JPNEXT",)))
    assert (cat.current_version, cat.previous_version) == ("JPNEXT", "NEW")


def test_search_puts_intl_ahead_of_jp_only_on_ties(make_catalog):
    cat = make_catalog([
        song("abc", [sheet(intl=False)], song_id="jp-exact"),
        song("abc", [sheet(intl=True)], song_id="intl-exact"),
        song("xabc", [sheet(intl=False)], song_id="jp-sub"),
        song("yabc", [sheet(intl=True)], song_id="intl-sub"),
    ])
    ids = [s.song_id for s in cat.search("abc")]
    assert ids.index("intl-exact") < ids.index("jp-exact") < ids.index("intl-sub") < ids.index("jp-sub")
