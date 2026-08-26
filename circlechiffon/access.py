"""
Per-command access control: ban checks and tiered per-user cooldowns, gated
behind a single `handle_command_access()` call at the top of every command.
"""

import time
from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import delete

from config import config
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import BannedUser

MAIMAI_NET_COOLDOWN = 15  # commands that talk to maimai DX NET
# Fan-out commands that issue one request per friend rather than a fixed
# handful - on a ~50-friend account that's ~50 requests against a
# process-wide 10/s rate limiter, so these get their own much longer tier.
MAIMAI_NET_HEAVY_COOLDOWN = 120
DEFAULT_COOLDOWN = 5  # everything else

_cooldowns: dict[int, dict[str, float]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """SQLite/aiosqlite drops tzinfo on round-trip, so a value read back from
    the DB comes back naive even though it was stored as UTC - reattach it."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def is_owner(discord_id: int) -> bool:
    try:
        return config.owner_id is not None and discord_id == int(config.owner_id)
    except (TypeError, ValueError):
        return False


def _check_cooldown(discord_id: int, command_name: str) -> float | None:
    """Returns the timestamp the user can use the command again, or None if
    they're clear to use it now."""
    user_cooldowns = _cooldowns.get(discord_id)
    if not user_cooldowns or command_name not in user_cooldowns:
        return None
    expires_at = user_cooldowns[command_name]
    if time.time() < expires_at:
        return expires_at
    del user_cooldowns[command_name]
    return None


def _set_cooldown(discord_id: int, command_name: str, seconds: int) -> None:
    _cooldowns.setdefault(discord_id, {})[command_name] = time.time() + seconds


def clear_cooldown(discord_id: int, command_name: str) -> None:
    """Releases a cooldown that handle_command_access already recorded.

    That call sets the cooldown up front, before the command does any work -
    which is wrong for a command that then asks the user to confirm before
    starting, since declining would otherwise cost them the full cooldown
    for a command that never ran. Call this on the decline/timeout path."""
    _cooldowns.get(discord_id, {}).pop(command_name, None)


async def get_ban(discord_id: int) -> BannedUser | None:
    """Returns the active ban for a user, auto-clearing (and returning None
    for) a timed ban that has already expired."""
    async with db_engine.session() as session:
        ban = await session.get(BannedUser, discord_id)
        if ban is None:
            return None
        if not ban.ncmd and ban.expires_at is not None and _utcnow() > _as_utc(ban.expires_at):
            await session.delete(ban)
            await session.commit()
            return None
        return ban


async def ban_user(discord_id: int, *, duration_seconds: int | None = None, ncmd: bool = False, reason: str | None = None) -> None:
    """Bans a user. `ncmd=True` lifts the ban after their next command
    attempt; otherwise `duration_seconds=None` is a permanent ban and a given
    value expires that many seconds from now."""
    expires_at = None
    if not ncmd and duration_seconds is not None:
        expires_at = _utcnow() + timedelta(seconds=duration_seconds)

    async with db_engine.session() as session:
        ban = await session.get(BannedUser, discord_id)
        if ban is None:
            ban = BannedUser(discord_id=discord_id)
            session.add(ban)
        ban.expires_at = expires_at
        ban.ncmd = ncmd
        ban.reason = reason
        await session.commit()


async def unban_user(discord_id: int) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(delete(BannedUser).where(BannedUser.discord_id == discord_id))
        await session.commit()
        return result.rowcount > 0


async def handle_command_access(interaction: discord.Interaction, discord_id: int, command_name: str, cooldown_seconds: int | None = None) -> bool:
    """Call at the top of every command. Returns True if the command should
    proceed. On False, an ephemeral message explaining why has already been
    sent - the caller should just `return`."""
    ban = await get_ban(discord_id)
    if ban is not None:
        reason = ban.reason or "No reason provided."
        if ban.ncmd:
            ban_until = "your next command attempt (this one)"
            await unban_user(discord_id)
        elif ban.expires_at is not None:
            ban_until = f"<t:{round(_as_utc(ban.expires_at).timestamp())}:F>"
        else:
            ban_until = "further notice (permanent)"
        await interaction.response.send_message(
            content=f"You are banned from using circlechiffon until {ban_until}.\n\n{reason}", ephemeral=True
        )
        return False

    if cooldown_seconds is not None and not is_owner(discord_id):
        retry_at = _check_cooldown(discord_id, command_name)
        if retry_at is not None:
            await interaction.response.send_message(
                content=f"Slow down! You can use this command again <t:{round(retry_at)}:R>.", ephemeral=True
            )
            return False
        _set_cooldown(discord_id, command_name, cooldown_seconds)

    return True
