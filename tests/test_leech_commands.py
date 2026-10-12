"""A leech user (accepted link, no account of their own) must get through the
account-gated commands instead of "You haven't linked...". Runs the real
command callbacks with fake interactions; DX NET is a fake client."""

import asyncio
import types

import pytest

from circlechiffon import accounts, leech
from circlechiffon.cogs.friends import FriendsCog
from circlechiffon.cogs.profile import ProfileCog
from circlechiffon.cogs.records import RecordsCog
from circlechiffon.cogs.score import ScoreCog, build_score_view
from circlechiffon.types import ChartType, Difficulty, FriendEntry, Profile, Score, Sheet, Song

HOST, LEECHER = 100, 200
NOT_LINKED = "haven't linked"


class FakeInteraction:
    def __init__(self, user_id, cogs=None):
        cogs = cogs or {}
        self.user = types.SimpleNamespace(id=user_id)
        self.contents = []
        self.response = types.SimpleNamespace(
            defer=self._noop, is_done=lambda: False, send_message=self._noop
        )
        self.client = types.SimpleNamespace(get_cog=lambda name: cogs.get(name))

    async def _noop(self, *a, **k):
        pass

    async def edit_original_response(self, **kwargs):
        if kwargs.get("content") is not None:
            self.contents.append(kwargs["content"])
        return types.SimpleNamespace()


class FakeClient:
    def __init__(self):
        self.friend = FriendEntry(profile=Profile(display_name="Ethan"), idx="idx1")

    async def get_friend_profile(self, idx):
        return self.friend if idx == "idx1" else None

    async def get_friend_list(self):
        return [self.friend]

    async def get_friend_scores(self, idx, on_progress=None, difficulties=None):
        return [Score(title="Song", chart_type=ChartType.dx, difficulty=Difficulty.master, achievement=100.5)]


@pytest.fixture
def leeching(database, monkeypatch):
    """LEECHER has an accepted link to HOST; HOST has the only account."""
    async def setup():
        from circlechiffon.database import engine as db_engine
        from circlechiffon.database.models import Account

        async with db_engine.session() as session:
            session.add(Account(discord_id=HOST, encrypted_cookie="x"))
            await session.commit()
        await leech.offer(HOST, LEECHER, "idx1", "Ethan")
        await leech.accept(LEECHER, HOST)

    asyncio.run(setup())

    async def fake_with_client(discord_id, operation, on_retry=None, is_owner=True):
        if discord_id != HOST:
            raise accounts.NotLinked()
        return await operation(FakeClient())

    monkeypatch.setattr(accounts, "with_client", fake_with_client)
    monkeypatch.setattr("circlechiffon.access.handle_command_access", _allow)


async def _allow(*a, **k):
    return True


def call(command, interaction, *args, **kwargs):
    asyncio.run(command.callback(command.binding if hasattr(command, "binding") else None, interaction, *args, **kwargs))


def test_best_reaches_the_friend_renderer(leeching):
    rendered = []

    class SpyFriends(FriendsCog):
        async def _render_friend_best(self, interaction, entry, *, session_id=None, note=None):
            rendered.append((entry.idx, session_id, note))

    friends = SpyFriends(None)
    inter = FakeInteraction(LEECHER, {"FriendsCog": friends})
    cog = RecordsCog(None)
    asyncio.run(RecordsCog.best.callback(cog, inter))
    assert not any(NOT_LINKED in c for c in inter.contents), inter.contents
    assert rendered and rendered[0][:2] == ("idx1", HOST)


def test_profile_core_reaches_the_friend_renderer_and_extra_is_refused(leeching):
    rendered = []

    class SpyFriends(FriendsCog):
        async def _send_friend_profile(self, interaction, entry, *, session_id=None, note=None):
            rendered.append((entry.idx, session_id))

    friends = SpyFriends(None)
    cog = ProfileCog(None)
    inter = FakeInteraction(LEECHER, {"FriendsCog": friends})
    asyncio.run(ProfileCog.profile.callback(cog, inter, None))
    assert rendered == [("idx1", HOST)] and not any(NOT_LINKED in c for c in inter.contents)

    inter = FakeInteraction(LEECHER, {"FriendsCog": friends})
    view = types.SimpleNamespace(value="extra")
    asyncio.run(ProfileCog.profile.callback(cog, inter, view))
    assert any("leech mode" in c for c in inter.contents) and not any(NOT_LINKED in c for c in inter.contents)


def test_scores_view_is_built_from_the_leechers_friend_entry(leeching, fake_catalog):
    song = Song(
        song_id="1", title="Song",
        sheets=[Sheet(type=ChartType.dx, difficulty=Difficulty.master, level="13", internal_level_value=13.0)],
    )
    view = asyncio.run(build_score_view(LEECHER, song))
    assert view is not None


def test_unsupported_command_gets_the_leech_message_not_the_login_one(leeching):
    assert asyncio.run(leech.not_linked_text(LEECHER)) == leech.LEECH_UNSUPPORTED_MESSAGE
