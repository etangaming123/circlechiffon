"""cogs/friends.py - how a typed name is matched to a friend.

Shared by /cc-friend-profile, /cc-friend-best and /cc-scores' `friend` option,
so a change to it changes all three.
"""

import asyncio

import pytest

from circlechiffon.cogs.friends import _normalize_name, _resolve_friend_entry
from circlechiffon.types import FriendEntry, Profile


def friend(name, idx):
    return FriendEntry(profile=Profile(display_name=name), idx=idx)


class FakeClient:
    def __init__(self, *friends):
        self.friends = list(friends)
        self.profile_lookups = []

    async def get_friend_list(self):
        return self.friends

    async def get_friend_profile(self, idx):
        self.profile_lookups.append(idx)
        return next((f for f in self.friends if f.idx == idx), None)


def resolve(client, query):
    return asyncio.run(_resolve_friend_entry(client, query))


# -- _normalize_name ----------------------------------------------------------------


def test_fullwidth_latin_folds_to_plain():
    assert _normalize_name("Ｅｔｈａｎ") == "ethan"


def test_casefold_and_strip():
    assert _normalize_name("  MaImAi  ") == "maimai"
    assert _normalize_name("Straße") == "strasse"


def test_circled_digits_and_ligatures_fold():
    assert _normalize_name("①②") == "12"


def test_halfwidth_katakana_folds_to_fullwidth_katakana_not_romaji():
    assert _normalize_name("ｲｰｻﾝ") == "イーサン"
    assert _normalize_name("ｲｰｻﾝ") != "isan"            # deliberate: no katakana -> romaji transliteration


# -- _resolve_friend_entry ----------------------------------------------------------


def test_blank_query_matches_nothing():
    client = FakeClient(friend("Ethan", "1"))
    assert resolve(client, "") is None and resolve(client, "   ") is None


def test_unique_substring_match():
    only = friend("Ethan", "1")
    assert resolve(FakeClient(only, friend("Sora", "2")), "eth") is only


def test_match_ignores_width_and_case():
    only = friend("Ｅｔｈａｎ", "1")
    assert resolve(FakeClient(only, friend("Sora", "2")), "ETHAN") is only


def test_several_matches_come_back_as_a_list():
    a, b = friend("Ethan A", "1"), friend("Ethan B", "2")
    assert resolve(FakeClient(a, b, friend("Sora", "3")), "ethan") == [a, b]


def test_typo_falls_back_to_fuzzy_candidates():
    ethan = friend("Ethan", "1")
    result = resolve(FakeClient(ethan, friend("Sora", "2")), "Ethna")
    assert result is ethan or result == [ethan]


def test_nothing_close_returns_none():
    assert resolve(FakeClient(friend("Ethan", "1")), "zzzzzzzz") is None


def test_all_digit_query_tries_the_friend_id_first():
    wanted = friend("Whoever", "1234567")
    client = FakeClient(wanted, friend("1234567 fan", "9"))
    assert resolve(client, "1234567") is wanted
    assert client.profile_lookups == ["1234567"]


def test_unknown_id_falls_back_to_name_matching():
    named = friend("Player 42", "7")
    client = FakeClient(named)
    assert resolve(client, "42") is named
    assert client.profile_lookups == ["42"]
