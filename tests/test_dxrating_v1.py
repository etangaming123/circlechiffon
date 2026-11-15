"""dxrating v1 catalog sync (update_dxdata.py), the bundled-extras readers and
the maimai NET status rule. Expected values follow from the rules in each
docstring, not from running the code."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import update_dxdata as u
from circlechiffon.adapters.dxrating import aliases, extras, status, tags


def v1_sheet(**extra):
    base = {"id": "dsht_1", "type": "dx", "difficulty": "master", "level": "13", "internalLevelValue": 13.0,
            "version": "CiRCLE", "isSpecial": False, "noteDesigner": "x", "releaseDate": "2025-01-01",
            "noteCounts": {"tap": 1, "total": 1}, "serverIds": ["jp", "intl"]}
    return base | extra


def v1_song(id_, title, image="img", sheets=None):
    return {"id": id_, "title": title, "imageName": image, "category": "maimai", "sheets": sheets or [v1_sheet()]}


def existing_song(song_id, title, image="img", id_=None, level="13"):
    s = {"songId": song_id, "title": title, "imageName": image,
         "sheets": [{"type": "dx", "difficulty": "master", "level": level, "noteCounts": {"total": 1}}]}
    return s | ({"id": id_} if id_ else {})


# -- legacy songId mapping ----------------------------------------------------------

def test_legacy_ids_prefers_stored_id_over_title():
    m = u.legacy_ids([v1_song("dsng_a", "Renamed")], [existing_song("Old Name", "Old Name", id_="dsng_a")])
    assert m == {"dsng_a": "Old Name"}


def test_legacy_ids_first_run_matches_title_and_jacket():
    m = u.legacy_ids([v1_song("dsng_a", "Link", "j2"), v1_song("dsng_b", "Link", "j1")],
                     [existing_song("Link", "Link", "j1"), existing_song("Link (2)", "Link", "j2")])
    assert m == {"dsng_a": "Link (2)", "dsng_b": "Link"}


def test_legacy_ids_separates_same_title_and_jacket_by_chart_level():
    songs = [v1_song("dsng_b", "U", sheets=[v1_sheet(level="11?")]), v1_song("dsng_a", "U", sheets=[v1_sheet(level="10?")])]
    m = u.legacy_ids(songs, [existing_song("U (BASIC)", "U", level="10?"), existing_song("U (ADVANCED)", "U", level="11?")])
    assert m == {"dsng_a": "U (BASIC)", "dsng_b": "U (ADVANCED)"}


def test_legacy_ids_title_fallback_when_jacket_changed_upstream():
    m = u.legacy_ids([v1_song("dsng_a", "Can Do", "new_jacket")], [existing_song("Can Do", "Can Do", "old_jacket")])
    assert m == {"dsng_a": "Can Do"}


def test_legacy_ids_new_song_gets_title_or_suffixed_when_taken():
    existing = [existing_song("Song", "Song", "j1", id_="dsng_old")]
    m = u.legacy_ids([v1_song("dsng_old", "Song", "j1"), v1_song("dsng_new", "Song", "j9"), v1_song("dsng_x", "Fresh")], existing)
    assert m == {"dsng_old": "Song", "dsng_new": "Song (dsng_new)", "dsng_x": "Fresh"}


# -- normalising ----------------------------------------------------------------------

def test_normalize_maps_servers_overrides_and_fills_note_counts():
    sh = v1_sheet(serverIds=["intl"], serverOverrides={"intl": {"version": "Splash"}}, noteCounts={"tap": 5})
    up = u.normalize_v1({"updatedAt": "t", "versions": [], "songs": [v1_song("dsng_a", "A", sheets=[sh])]}, {"dsng_a": "A"})
    (song,) = up["songs"]
    assert (up["updateTime"], song["songId"], song["id"]) == ("t", "A", "dsng_a")
    out = song["sheets"][0]
    assert out["regions"] == {"jp": False, "intl": True, "usa": False, "cn": False}
    assert out["regionOverrides"] == {"intl": {"version": "Splash"}}
    assert out["noteCounts"] == {"tap": 5, "hold": None, "slide": None, "touch": None, "break": None, "total": None}
    assert u.resolve_sheet(out)["version"] == "Splash" and u.resolve_sheet(out)["intl"] is True


def test_normalize_empty_note_counts_become_all_null():
    up = u.normalize_v1({"songs": [v1_song("dsng_a", "A", sheets=[v1_sheet(noteCounts={})])]}, {"dsng_a": "A"})
    assert set(up["songs"][0]["sheets"][0]["noteCounts"].values()) == {None}


def test_resolve_song_stores_the_v1_id_only_when_present():
    assert u.resolve_song({"songId": "A", "id": "dsng_a", "title": "A"})["id"] == "dsng_a"
    assert "id" not in u.resolve_song({"songId": "A", "title": "A"})


def test_extras_rekeyed_to_song_id_and_unknown_songs_dropped():
    v1 = {"aliases": [{"song_id": "dsng_a", "name": "x"}, {"song_id": "dsng_a", "name": "x"},
                      {"song_id": "dsng_gone", "name": "y"}],
          "tags": [{"id": 1}], "tagGroups": [{"id": 2}],
          "tagSongs": [{"song_id": "dsng_a", "sheet_type": "dx", "sheet_difficulty": "master", "tag_id": 1},
                       {"song_id": "dsng_gone", "tag_id": 1}]}
    out = u.extras_from_v1(v1, {"dsng_a": "A"})
    assert out["aliases"] == {"A": ["x"]}
    assert out["tagSongs"] == [{"song_id": "A", "sheet_type": "dx", "sheet_difficulty": "master", "tag_id": 1}]
    assert (out["tags"], out["tagGroups"]) == ([{"id": 1}], [{"id": 2}])


def test_is_v1_detects_shape():
    assert u.is_v1({"schemaVersion": 1, "songs": []})
    assert u.is_v1({"songs": [{"id": "dsng_a"}]})
    assert not u.is_v1({"songs": [{"songId": "A"}]})


# -- sync: ETag + extras file -----------------------------------------------------------

@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "DATA_PATH", tmp_path / "dxdata.json")
    monkeypatch.setattr(extras, "EXTRAS_PATH", tmp_path / "extras.json")
    monkeypatch.setattr(extras.load, "__defaults__", (tmp_path / "extras.json",))
    monkeypatch.setattr(extras.save, "__defaults__", (tmp_path / "extras.json",))
    monkeypatch.setattr(extras.mtime, "__defaults__", (tmp_path / "extras.json",))
    return tmp_path


def v1_doc(updated="t1", alias="x"):
    return {"schemaVersion": 1, "updatedAt": updated, "versions": [{"version": "CiRCLE"}],
            "songs": [v1_song("dsng_a", "A")], "aliases": [{"song_id": "dsng_a", "name": alias}],
            "tags": [], "tagGroups": [], "tagSongs": []}


def test_sync_revalidates_with_stored_etag_and_304_changes_nothing(files, monkeypatch):
    calls = []

    def fake_fetch(etag=None, timeout=60):
        calls.append(etag)
        return (v1_doc(), 'W/"e1"') if etag is None else (None, etag)

    monkeypatch.setattr(u, "fetch_upstream", fake_fetch)
    changed, _ = u.sync()
    assert changed and calls == [None]
    saved = json.loads((files / "extras.json").read_text(encoding="utf-8"))
    assert saved["etag"] == 'W/"e1"' and saved["aliases"] == {"A": ["x"]}
    assert json.loads((files / "dxdata.json").read_text(encoding="utf-8"))["songs"][0]["id"] == "dsng_a"
    before = (files / "dxdata.json").read_text(encoding="utf-8")
    assert u.sync() == (False, "upstream unchanged (304)") and calls == [None, 'W/"e1"']
    assert (files / "dxdata.json").read_text(encoding="utf-8") == before


def test_sync_alias_only_change_counts_as_changed(files):
    assert u.sync(v1_doc())[0]
    assert u.sync(v1_doc())[0] is False
    assert u.sync(v1_doc(alias="new nickname"))[0] is True


def test_sync_without_data_file_does_not_send_etag(files, monkeypatch):
    extras.save({"etag": 'W/"old"', "aliases": {}})
    seen = []
    monkeypatch.setattr(u, "fetch_upstream", lambda etag=None, timeout=60: (seen.append(etag), (v1_doc(), None))[1])
    u.sync()
    assert seen == [None]


# -- readers ----------------------------------------------------------------------------

def test_aliases_come_from_extras_file_when_present(files, monkeypatch):
    extras.save({"aliases": {"A": ["nick"]}})
    monkeypatch.setattr(aliases.httpx, "get", lambda *a, **k: pytest.fail("network used"))
    assert aliases.fetch_aliases_by_song_id() == {"A": ["nick"]}


def test_tags_come_from_extras_file_without_network(files, monkeypatch):
    extras.save({"tags": [{"id": 1, "group_id": 2, "localized_name": {"en": "Stamina"}, "localized_description": {}}],
                 "tagGroups": [{"id": 2, "localized_name": {"en": "Skill"}}],
                 "tagSongs": [{"song_id": "A", "sheet_type": "dx", "sheet_difficulty": "master", "tag_id": 1}]})
    monkeypatch.setattr(tags.httpx, "AsyncClient", lambda *a, **k: pytest.fail("network used"))
    client = tags.DxRatingTagsClient()
    found = asyncio.run(client.find_tags("A", "dx", "master"))
    assert [(t.name, t.group_name) for t in found] == [("Stamina", "Skill")]


# -- maimai NET status ------------------------------------------------------------------

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def answer(state, minutes_ago=1):
    return {"intl": {"status": state, "checkedAt": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")}}


def test_status_only_fresh_explicit_maintenance_counts():
    assert status.is_maintenance_answer(answer("maintenance"), NOW)
    assert not status.is_maintenance_answer(answer("ok"), NOW)
    assert not status.is_maintenance_answer(answer("unknown"), NOW)
    assert not status.is_maintenance_answer(answer("maintenance", minutes_ago=16), NOW)   # stale checker
    assert not status.is_maintenance_answer({"jp": {"status": "maintenance"}}, NOW)        # JP only
    assert not status.is_maintenance_answer({"intl": {"status": "maintenance"}}, NOW)      # no timestamp
