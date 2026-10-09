"""customisation/store.py - where uploaded templates live and who may use them.

Files go to a temp folder (USER_TEMPLATES_DIR is redirected), never the repo's
real user_templates/.
"""

import asyncio
import json

import pytest

from circlechiffon.customisation import store
from circlechiffon.renderers.guides import TEMPLATE_KINDS


@pytest.fixture(autouse=True)
def temp_templates(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "USER_TEMPLATES_DIR", tmp_path / "user_templates")
    monkeypatch.setattr(store.access.config, "owner_id", "999", raising=False)
    return tmp_path / "user_templates"


def layout_bytes(kind="b50") -> bytes:
    return json.dumps({"version": 1, "kind": kind, "elements": {}}).encode()


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        store.template_dir(1, "../../etc")


def test_paths_stay_inside_the_templates_folder(temp_templates):
    for kind in TEMPLATE_KINDS:
        assert temp_templates in store.template_dir(123, kind).parents


def test_save_read_delete_round_trip(temp_templates):
    assert store.existing_parts(1, "b50") == []
    assert store.read_part(1, "b50", "base") is None
    store.save_part(1, "b50", "base", b"PNGDATA")
    store.save_part(1, "b50", "layout", layout_bytes())
    assert store.existing_parts(1, "b50") == ["base", "layout"]
    assert store.read_part(1, "b50", "base") == b"PNGDATA"
    assert not list(store.template_dir(1, "b50").glob("*.tmp"))            # no temp file left behind

    assert store.delete_parts(1, "b50", ["base", "top"]) == ["base"]       # "top" never existed
    assert store.delete_parts(1, "b50", ["layout"]) == ["layout"]
    assert not store.template_dir(1, "b50").exists()                        # empty folders are tidied away
    assert not (temp_templates / "1").exists()


def test_overwriting_a_part_replaces_it():
    store.save_part(1, "b50", "base", b"one")
    store.save_part(1, "b50", "base", b"two")
    assert store.read_part(1, "b50", "base") == b"two"


def test_users_are_kept_apart():
    store.save_part(1, "b50", "base", b"mine")
    assert store.read_part(2, "b50", "base") is None


def test_load_template_from_dir(temp_templates):
    assert store.load_template_from_dir(temp_templates / "nothing", "b50") is None
    store.save_part(1, "b50", "base", b"BASE")
    store.save_part(1, "b50", "layout", layout_bytes("b50"))
    template = store.load_template_from_dir(store.template_dir(1, "b50"), "b50")
    assert template.base == b"BASE" and template.top is None and template.layout["kind"] == "b50"


def test_layout_for_the_wrong_kind_or_unreadable_is_ignored(capsys):
    store.save_part(1, "b50", "layout", layout_bytes("profile_core"))
    assert store.load_template_from_dir(store.template_dir(1, "b50"), "b50") is None
    store.save_part(2, "b50", "layout", b"{broken")
    assert store.load_template_from_dir(store.template_dir(2, "b50"), "b50") is None
    store.save_part(2, "b50", "base", b"BASE")                              # an image alone still loads
    assert store.load_template_from_dir(store.template_dir(2, "b50"), "b50").layout is None


def test_whitelist(database):
    async def go():
        assert await store.is_whitelisted(5) is False
        assert await store.add_to_whitelist(5) is True
        assert await store.add_to_whitelist(5) is False
        assert await store.is_whitelisted(5) is True
        assert await store.list_whitelist() == [5]
        assert await store.remove_from_whitelist(5) is True
        assert await store.remove_from_whitelist(5) is False
        assert await store.is_whitelisted(5) is False

    asyncio.run(go())


def test_owner_is_always_whitelisted(database):
    assert asyncio.run(store.is_whitelisted(999)) is True
    assert asyncio.run(store.list_whitelist()) == []                         # without needing a row


def test_get_user_template_requires_the_whitelist(database):
    async def go():
        store.save_part(5, "b50", "base", b"BASE")
        assert await store.get_user_template(5, "b50") is None               # has files, not whitelisted
        await store.add_to_whitelist(5)
        assert (await store.get_user_template(5, "b50")).base == b"BASE"
        await store.remove_from_whitelist(5)
        assert await store.get_user_template(5, "b50") is None               # files stay, just unused
        assert store.existing_parts(5, "b50") == ["base"]

    asyncio.run(go())


def test_get_user_template_never_raises(database):
    async def go():
        await store.add_to_whitelist(5)
        assert await store.get_user_template(5, "not a kind") is None

    asyncio.run(go())
