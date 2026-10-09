"""leech.py - offers, acceptance, who gets served through whom, and the
friend-entry lookup (with its by-name recovery when an idx stops resolving).

Runs against the temp-DB `database` fixture; DX NET is replaced by a fake client.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from circlechiffon import accounts, leech
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import Account, LeechLink
from circlechiffon.types import FriendEntry, Profile

HOST, LEECHER, OTHER = 100, 200, 300


def friend(name, idx):
    return FriendEntry(profile=Profile(display_name=name), idx=idx)


async def link_account(discord_id):
    async with db_engine.session() as session:
        session.add(Account(discord_id=discord_id, encrypted_cookie="x"))
        await session.commit()


async def age_offer(host_id, leecher_id, days):
    async with db_engine.session() as session:
        row = await session.get(LeechLink, (host_id, leecher_id))
        row.created_at = datetime.now(timezone.utc) - timedelta(days=days)
        await session.commit()


def test_accepted_offer_serves_the_leecher(database):
    async def go():
        await leech.offer(HOST, LEECHER, "idx1", "Ethan")
        assert await leech.get_target(LEECHER) is None          # not accepted yet
        link = await leech.accept(LEECHER, HOST)
        assert (link.host_id, link.friend_idx, link.friend_name) == (HOST, "idx1", "Ethan")
        target = await leech.get_target(LEECHER)
        assert target.host_id == HOST and target.friend_idx == "idx1"

    asyncio.run(go())


def test_accept_needs_a_pending_offer_from_that_host(database):
    async def go():
        assert await leech.accept(LEECHER, HOST) is None        # never offered
        await leech.offer(HOST, LEECHER, "idx1", "Ethan")
        assert await leech.accept(LEECHER, OTHER) is None       # offered by someone else
        assert await leech.accept(LEECHER, HOST) is not None
        assert await leech.accept(LEECHER, HOST) is None        # already accepted

    asyncio.run(go())


def test_offers_expire_after_a_week(database):
    async def go():
        await leech.offer(HOST, LEECHER, "idx1", "Ethan")
        await age_offer(HOST, LEECHER, 8)
        assert await leech.accept(LEECHER, HOST) is None
        await age_offer(HOST, LEECHER, 6)
        assert await leech.accept(LEECHER, HOST) is not None

    asyncio.run(go())


def test_reoffering_resets_an_accepted_link_to_pending(database):
    async def go():
        await leech.offer(HOST, LEECHER, "idx1", "Ethan")
        await leech.accept(LEECHER, HOST)
        await leech.offer(HOST, LEECHER, "idx2", "Ethan2")
        assert await leech.get_target(LEECHER) is None
        link = await leech.accept(LEECHER, HOST)
        assert link.friend_idx == "idx2"

    asyncio.run(go())


def test_accepting_a_second_host_replaces_the_first(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        await leech.offer(OTHER, LEECHER, "b", "Ethan")
        await leech.accept(LEECHER, OTHER)
        assert (await leech.get_target(LEECHER)).host_id == OTHER
        async with db_engine.session() as session:
            assert await session.get(LeechLink, (HOST, LEECHER)) is None

    asyncio.run(go())


def test_a_pending_second_offer_does_not_replace_the_active_link(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        await leech.offer(OTHER, LEECHER, "b", "Ethan")          # offered, not accepted
        assert (await leech.get_target(LEECHER)).host_id == HOST

    asyncio.run(go())


def test_own_account_beats_leech(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        await link_account(LEECHER)
        assert await leech.get_target(LEECHER) is None

    asyncio.run(go())


def test_unlinking_the_host_drops_their_links(database):
    async def go():
        await link_account(HOST)
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        assert await accounts.delete_account(HOST) is True
        assert await leech.get_target(LEECHER) is None

    asyncio.run(go())


def test_remove_for_clears_both_roles(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.offer(LEECHER, OTHER, "b", "Sam")
        assert await leech.remove_for(LEECHER) is True           # as leecher of one, host of the other
        async with db_engine.session() as session:
            assert await session.get(LeechLink, (HOST, LEECHER)) is None
            assert await session.get(LeechLink, (LEECHER, OTHER)) is None
        assert await leech.remove_for(LEECHER) is False

    asyncio.run(go())


def test_not_linked_text_mentions_leech_only_for_leechers(database):
    async def go():
        assert await leech.not_linked_text(LEECHER) == leech.NOT_LINKED_MESSAGE
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        assert await leech.not_linked_text(LEECHER) == leech.LEECH_UNSUPPORTED_MESSAGE

    asyncio.run(go())


# -- fetch_entry --------------------------------------------------------------------


class FakeClient:
    def __init__(self, *friends):
        self.friends = list(friends)

    async def get_friend_list(self):
        return self.friends

    async def get_friend_profile(self, idx):
        return next((f for f in self.friends if f.idx == idx), None)


@pytest.fixture
def host_client(monkeypatch):
    """Route the host's session to a FakeClient; returns a setter for it."""
    state = {}

    async def fake_with_client(discord_id, operation, on_retry=None):
        assert discord_id == HOST
        if "error" in state:
            raise state["error"]
        return await operation(state["client"])

    monkeypatch.setattr(accounts, "with_client", fake_with_client)
    return state


def make_link(idx="idx1", name="Ethan"):
    return LeechLink(host_id=HOST, leecher_id=LEECHER, friend_idx=idx, friend_name=name, accepted=True)


def test_fetch_entry_uses_the_stored_idx(database, host_client):
    host_client["client"] = FakeClient(friend("Ethan", "idx1"), friend("Sam", "idx2"))
    entry = asyncio.run(leech.fetch_entry(make_link()))
    assert entry.idx == "idx1"


def test_fetch_entry_recovers_a_drifted_idx_by_name_and_saves_it(database, host_client):
    async def go():
        await leech.offer(HOST, LEECHER, "old", "Ethan")
        link = await leech.accept(LEECHER, HOST)
        host_client["client"] = FakeClient(friend("Ｅｔｈａｎ", "new"), friend("Sam", "idx2"))   # fullwidth name
        entry = await leech.fetch_entry(link)
        assert entry.idx == "new"
        assert (await leech.get_target(LEECHER)).friend_idx == "new"

    asyncio.run(go())


def test_fetch_entry_refuses_an_ambiguous_name(database, host_client):
    host_client["client"] = FakeClient(friend("Ethan", "a"), friend("ethan", "b"))
    with pytest.raises(leech.LeechBroken):
        asyncio.run(leech.fetch_entry(make_link(idx="gone")))


def test_fetch_entry_when_the_friend_is_gone(database, host_client):
    host_client["client"] = FakeClient(friend("Sam", "idx2"))
    with pytest.raises(leech.LeechBroken):
        asyncio.run(leech.fetch_entry(make_link()))


def test_host_failures_are_worded_for_the_leecher(database, host_client):
    host_client["client"] = FakeClient()
    host_client["error"] = accounts.NotLinked()
    with pytest.raises(leech.LeechBroken) as e:
        asyncio.run(leech.fetch_entry(make_link()))
    assert "host" in str(e.value).lower()

    host_client["error"] = SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")
    with pytest.raises(leech.LeechBroken) as e:
        asyncio.run(leech.fetch_entry(make_link()))
    assert "host" in str(e.value).lower()


def test_leech_broken_is_a_maimai_net_error():
    assert issubclass(leech.LeechBroken, MaimaiNetError)


# -- purge, removal, bans -----------------------------------------------------------


def test_purge_removes_only_stale_unaccepted_offers(database):
    async def go():
        await leech.offer(HOST, 1, "a", "Old pending")
        await age_offer(HOST, 1, 8)
        await leech.offer(HOST, 2, "b", "Fresh pending")
        await leech.offer(HOST, 3, "c", "Old accepted")
        await leech.accept(3, HOST)
        await age_offer(HOST, 3, 30)
        assert await leech.purge_expired() == 1
        async with db_engine.session() as session:
            assert await session.get(LeechLink, (HOST, 1)) is None
            assert await session.get(LeechLink, (HOST, 2)) is not None
            assert await session.get(LeechLink, (HOST, 3)) is not None
        assert await leech.purge_expired() == 0

    asyncio.run(go())


def test_remove_between_works_from_either_side_and_leaves_others(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.offer(HOST, OTHER, "b", "Sam")
        assert await leech.remove_between(LEECHER, HOST) is True       # leecher removes
        assert await leech.remove_between(LEECHER, HOST) is False
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        assert await leech.remove_between(HOST, LEECHER) is True       # host removes
        async with db_engine.session() as session:
            assert await session.get(LeechLink, (HOST, OTHER)) is not None

    asyncio.run(go())


def test_remove_between_drops_an_accepted_link(database):
    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        await leech.remove_between(LEECHER, HOST)
        assert await leech.get_target(LEECHER) is None

    asyncio.run(go())


def test_banning_a_host_unlinks_their_leechers_but_not_links_they_use(database):
    from circlechiffon import access

    async def go():
        await leech.offer(HOST, LEECHER, "a", "Ethan")
        await leech.accept(LEECHER, HOST)
        await leech.offer(OTHER, HOST, "b", "Host as leecher")
        await leech.accept(HOST, OTHER)
        await access.ban_user(HOST, duration_seconds=60)
        assert await leech.get_target(LEECHER) is None
        assert (await leech.get_target(HOST)).host_id == OTHER          # banned user's own use is blocked at the command gate instead

    asyncio.run(go())
