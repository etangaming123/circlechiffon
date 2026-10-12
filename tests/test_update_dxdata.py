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
