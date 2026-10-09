"""access.py - bans and per-user cooldowns, the gate every command passes through.

handle_command_access() is exercised end to end against a temp database and a
fake Discord interaction, so no Discord connection is needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from circlechiffon import access


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    access._cooldowns.clear()
    monkeypatch.setattr(access.config, "owner_id", "999", raising=False)
    yield
    access._cooldowns.clear()


def interaction():
    return SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))


def sent_text(inter) -> str:
    return inter.response.send_message.await_args.kwargs["content"]


# -- small pure helpers ----------------------------------------------------------------


def test_as_utc_reattaches_dropped_timezone():
    naive = datetime(2026, 1, 2, 3, 4, 5)
    fixed = access._as_utc(naive)
    assert fixed.tzinfo is timezone.utc and fixed.replace(tzinfo=None) == naive
    assert fixed > datetime(2026, 1, 1, tzinfo=timezone.utc)         # comparing aware to aware must not raise


def test_as_utc_leaves_aware_values_alone():
    aware = datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=9)))
    assert access._as_utc(aware) is aware


@pytest.mark.parametrize("owner_id, user, expected", [
    ("999", 999, True), (999, 999, True), ("999", 1, False),
    (None, 999, False), ("not a number", 999, False), ("your discord user id here (optional)", 1, False),
])
def test_is_owner(monkeypatch, owner_id, user, expected):
    monkeypatch.setattr(access.config, "owner_id", owner_id)
    assert access.is_owner(user) is expected


def test_cooldown_lifecycle():
    assert access.cooldown_until(1, "cmd") is None
    access.set_cooldown(1, "cmd", 30)
    assert access.cooldown_until(1, "cmd") > datetime.now().timestamp()
    access.clear_cooldown(1, "cmd")
    assert access.cooldown_until(1, "cmd") is None


def test_clearing_a_cooldown_that_doesnt_exist_is_fine():
    access.clear_cooldown(1, "never set")


def test_expired_cooldown_is_forgotten():
    access.set_cooldown(1, "cmd", -1)
    assert access.cooldown_until(1, "cmd") is None
    assert "cmd" not in access._cooldowns[1]


def test_cooldowns_are_per_user_and_per_command():
    access.set_cooldown(1, "a", 30)
    assert access.cooldown_until(1, "b") is None
    assert access.cooldown_until(2, "a") is None


def test_cooldown_tiers_are_ordered_light_to_heavy():
    assert access.DEFAULT_COOLDOWN < access.MAIMAI_NET_COOLDOWN < access.COLLECTION_WRITE_COOLDOWN < access.MAIMAI_NET_HEAVY_COOLDOWN


# -- bans ---------------------------------------------------------------------------------


def test_permanent_ban_round_trip(database):
    async def go():
        assert await access.get_ban(5) is None
        await access.ban_user(5, reason="spam")
        ban = await access.get_ban(5)
        assert ban.reason == "spam" and ban.expires_at is None and not ban.ncmd
        assert await access.unban_user(5) is True
        assert await access.unban_user(5) is False
        assert await access.get_ban(5) is None

    asyncio.run(go())


def test_timed_ban_expires_and_is_cleaned_up(database):
    async def go():
        await access.ban_user(5, duration_seconds=-10)                # already over
        assert await access.get_ban(5) is None
        assert await access.unban_user(5) is False                    # row was auto-deleted
        await access.ban_user(6, duration_seconds=3600)
        assert await access.get_ban(6) is not None

    asyncio.run(go())


def test_rebanning_replaces_the_old_ban(database):
    async def go():
        await access.ban_user(5, reason="first")
        await access.ban_user(5, duration_seconds=3600, reason="second")
        ban = await access.get_ban(5)
        assert ban.reason == "second" and ban.expires_at is not None

    asyncio.run(go())


# -- the gate ---------------------------------------------------------------------------


def test_gate_lets_a_clean_user_through_and_starts_the_cooldown(database):
    inter = interaction()
    assert asyncio.run(access.handle_command_access(inter, 1, "cc-x", 10)) is True
    inter.response.send_message.assert_not_awaited()
    assert access.cooldown_until(1, "cc-x") is not None


def test_gate_blocks_a_repeat_inside_the_cooldown(database):
    async def go():
        assert await access.handle_command_access(interaction(), 1, "cc-x", 10) is True
        second = interaction()
        assert await access.handle_command_access(second, 1, "cc-x", 10) is False
        assert "Slow down" in sent_text(second)
        assert second.response.send_message.await_args.kwargs["ephemeral"] is True

    asyncio.run(go())


def test_gate_allows_again_after_clear_cooldown(database):
    async def go():
        await access.handle_command_access(interaction(), 1, "cc-x", 10)
        access.clear_cooldown(1, "cc-x")
        assert await access.handle_command_access(interaction(), 1, "cc-x", 10) is True

    asyncio.run(go())


def test_gate_with_no_cooldown_tier_never_throttles(database):
    async def go():
        for _ in range(3):
            assert await access.handle_command_access(interaction(), 1, "cc-x", None) is True
        assert access.cooldown_until(1, "cc-x") is None

    asyncio.run(go())


def test_owner_is_never_throttled(database):
    async def go():
        for _ in range(3):
            assert await access.handle_command_access(interaction(), 999, "cc-x", 120) is True
        assert access.cooldown_until(999, "cc-x") is None

    asyncio.run(go())


def test_gate_blocks_banned_users_with_the_reason(database):
    async def go():
        await access.ban_user(7, reason="abuse")
        inter = interaction()
        assert await access.handle_command_access(inter, 7, "cc-x", 10) is False
        assert "banned" in sent_text(inter) and "abuse" in sent_text(inter) and "permanent" in sent_text(inter)
        assert access.cooldown_until(7, "cc-x") is None                # a blocked command doesn't start a cooldown

    asyncio.run(go())


def test_next_command_ban_lifts_itself(database):
    async def go():
        await access.ban_user(7, ncmd=True, reason="warning")
        first = interaction()
        assert await access.handle_command_access(first, 7, "cc-x", 10) is False
        assert await access.get_ban(7) is None
        assert await access.handle_command_access(interaction(), 7, "cc-x", 10) is True

    asyncio.run(go())
