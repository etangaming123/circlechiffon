"""update_dxdata.py - upstream dxrating sheet -> the INTL view stored in data/dxdata.json."""

import update_dxdata as u


def raw_sheet(**extra):
    base = {"type": "dx", "difficulty": "master", "level": "13", "internalLevelValue": 13.0, "version": "CiRCLE",
            "isSpecial": False, "noteDesigner": "x", "releaseDate": "2025-01-01", "noteCounts": {"tap": 1},
            "regions": {"jp": True, "intl": True, "usa": True, "cn": False}, "regionOverrides": {"intl": {}}}
    base.update(extra)
    return base


def test_plain_sheet_keeps_values_and_flag():
    out = u.resolve_sheet(raw_sheet())
    assert (out["level"], out["internalLevelValue"], out["version"], out["intl"]) == ("13", 13.0, "CiRCLE", True)


def test_intl_level_override_replaces_the_jp_constant():
    out = u.resolve_sheet(raw_sheet(regionOverrides={"intl": {"level": "12+", "levelValue": 12.7}}))
    assert (out["level"], out["internalLevelValue"]) == ("12+", 12.7)


def test_intl_version_override():
    out = u.resolve_sheet(raw_sheet(version="ORANGE PLUS", regionOverrides={"intl": {"version": "Splash"}}))
    assert out["version"] == "Splash"


def test_jp_only_sheet_is_flagged_and_keeps_jp_values():
    out = u.resolve_sheet(raw_sheet(regions={"jp": True, "intl": False, "usa": False, "cn": False}))
    assert out["intl"] is False and out["internalLevelValue"] == 13.0


def test_build_keeps_existing_song_order_and_appends_new():
    songs = [{"songId": s, "title": s, "sheets": []} for s in ("c", "a", "b")]
    out = u.build({"songs": songs, "versions": []}, existing_order=["a", "b"])
    assert [s["songId"] for s in out["songs"]] == ["a", "b", "c"]


def test_jp_constant_is_kept_only_when_intl_differs():
    differs = u.resolve_sheet(raw_sheet(regionOverrides={"intl": {"level": "12+", "levelValue": 12.7}}))
    assert differs["internalLevelValue"] == 12.7
    assert (differs["jpInternalLevelValue"], differs["jpLevel"]) == (13.0, "13")
    assert "jpInternalLevelValue" not in u.resolve_sheet(raw_sheet())
    jp_only = u.resolve_sheet(raw_sheet(regions={"jp": True, "intl": False}))
    assert "jpInternalLevelValue" not in jp_only        # JP-only charts already show the JP value


# -- per-version constants ----------------------------------------------------------

ORDER = ["PRiSM", "CiRCLE", "CiRCLE PLUS", "MAGiCAL"]


def test_multiver_constant_for_the_intl_version_beats_the_override():
    raw = raw_sheet(level="13+", internalLevelValue=13.6, regionOverrides={"intl": {"level": "13", "levelValue": 13.0}},
                    multiverInternalLevelValue={"CiRCLE": 13.4, "CiRCLE PLUS": 13.4})
    out = u.resolve_sheet(raw, "CiRCLE PLUS", ORDER)
    assert (out["internalLevelValue"], out["level"]) == (13.4, "13")
    assert (out["jpInternalLevelValue"], out["jpLevel"]) == (13.6, "13+")


def test_multiver_ignores_versions_newer_than_intl_and_falls_back_to_the_latest_earlier_one():
    raw = raw_sheet(multiverInternalLevelValue={"CiRCLE": 12.7, "MAGiCAL": 13.2})
    assert u.resolve_sheet(raw, "CiRCLE PLUS", ORDER)["internalLevelValue"] == 12.7
    assert u.resolve_sheet(raw, "MAGiCAL", ORDER)["internalLevelValue"] == 13.2


def test_multiver_only_in_newer_versions_leaves_the_override_alone():
    raw = raw_sheet(multiverInternalLevelValue={"MAGiCAL": 13.9})
    assert u.resolve_sheet(raw, "CiRCLE PLUS", ORDER)["internalLevelValue"] == 13.0
    assert u.resolve_sheet(raw_sheet(), None, ORDER)["internalLevelValue"] == 13.0


def test_level_label_plus_starts_at_point_six_from_level_seven():
    assert [u.level_label(v) for v in (12.5, 12.6, 14.0, 6.7, 7.6)] == ["12", "12+", "14", "6", "7+"]


def test_detect_intl_version_uses_the_catalog_threshold(monkeypatch):
    monkeypatch.setattr(u, "MIN_INTL_SHEETS", 2)
    sheets = lambda v, n: [raw_sheet(version=v) for _ in range(n)]
    up = {"versions": [{"version": v} for v in ORDER],
          "songs": [{"sheets": sheets("CiRCLE PLUS", 2) + sheets("MAGiCAL", 1)}]}
    assert u.detect_intl_version(up) == "CiRCLE PLUS"        # 1 early MAGiCAL chart isn't a release
    up["songs"][0]["sheets"] += sheets("MAGiCAL", 1)
    assert u.detect_intl_version(up) == "MAGiCAL"


# -- safeguards ---------------------------------------------------------------------

def good_data(n=10):
    return {"versions": [{"version": "A"}], "songs": [
        {"songId": str(i), "title": str(i), "sheets": [{"intl": True, "internalLevelValue": 12.0}]} for i in range(n)]}


def test_validate_accepts_normal_data_and_rejects_broken_upstreams():
    import pytest
    existing = good_data(10)
    u.validate(good_data(10), existing)
    u.validate(good_data(12), existing)
    for bad in (good_data(0), {**good_data(10), "versions": []}, good_data(5)):
        with pytest.raises(ValueError):
            u.validate(bad, existing)


def test_sync_writes_atomically_skips_when_unchanged_and_keeps_file_on_bad_data(tmp_path, monkeypatch):
    import json
    import pytest
    monkeypatch.setattr(u, "DATA_PATH", tmp_path / "dxdata.json")
    song = {"songId": "s", "title": "s", "sheets": [raw_sheet()]}
    up = {"updateTime": "t1", "versions": [{"version": "CiRCLE"}], "songs": [song]}
    changed, _ = u.sync(up)
    assert changed and json.loads(u.DATA_PATH.read_text(encoding="utf-8"))["updateTime"] == "t1"
    assert not (tmp_path / "dxdata.json.tmp").exists()
    assert u.sync(up)[0] is False
    before = u.DATA_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        u.sync({"updateTime": "t2", "versions": [{"version": "CiRCLE"}], "songs": []})
    assert u.DATA_PATH.read_text(encoding="utf-8") == before
