"""
"Leech" mode: use the bot without a SEGA ID by reading your own data through
another user's (the host's) linked session, via the friend pages.

The host runs /cc-leech-send naming one of their DX NET friends and a Discord
user; that user runs /cc-leech-accept. From then on, the leecher's /cc-profile,
/cc-best and /cc-scores fetch the leecher's own friend entry using the host's
session. That is exactly the data `/cc-friend-*` can see, so every friend-data
limit in docs/limitations.md applies.

Only the one friend idx stored on the link is ever fetched for a leecher - the
host's friend list is never exposed to them.
"""

from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import delete, or_, select

from circlechiffon import accounts
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import Account, LeechLink
from circlechiffon.types import FriendEntry

# How long an unaccepted offer stays valid.
OFFER_TTL = timedelta(days=7)

NOT_LINKED_MESSAGE = "You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
LEECH_UNSUPPORTED_MESSAGE = (
    "You're using leech mode, which only covers `/cc-profile`, `/cc-best` and `/cc-scores` - "
    "SEGA doesn't expose that data for a friend. Run `/cc-login` to link your own SEGA ID for this command."
)

# What leech users can and can't see, shown on accept and quoted from the docs.
LIMITS_SUMMARY = (
    "Data comes from the host's friend view of you: achievement and combo/sync only - no DX score, "
    "play counts or last-played times, and your rating is computed locally. "
    "Commands that need your own account (`/cc-recent`, `/cc-album`, `/cc-circle`, `/cc-display`, "
    "`/cc-preset-*`, `/cc-friends`, the friend and leaderboard commands) don't work in leech mode."
)


class LeechBroken(MaimaiNetError):
    """The leech link can't currently be used (host unlinked or expired, or
    the host no longer has the leecher as a friend). Subclasses
    MaimaiNetError so every command's existing handler shows the message."""


def _as_utc(dt: datetime) -> datetime:
    """aiosqlite drops tzinfo on round-trip - see access._as_utc()."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def data_note(host_name: str | None = None) -> str:
    """One-line footer/caption for a render built from leech data."""
    via = f" via {host_name}'s link" if host_name else ""
    return (
        f"Leech mode{via}: achievement and combo/sync only, no DX score or play counts; "
        "rating is computed locally."
    )


async def offer(host_id: int, leecher_id: int, friend_idx: str, friend_name: str) -> None:
    """Create (or reset to pending) the host's offer to this leecher."""
    async with db_engine.session() as session:
        link = await session.get(LeechLink, (host_id, leecher_id))
        if link is None:
            link = LeechLink(host_id=host_id, leecher_id=leecher_id, friend_idx=friend_idx, friend_name=friend_name)
            session.add(link)
        else:
            link.friend_idx = friend_idx
            link.friend_name = friend_name
            link.accepted = False
            link.accepted_at = None
            link.created_at = datetime.now(timezone.utc)
        await session.commit()


async def accept(leecher_id: int, host_id: int) -> LeechLink | None:
    """Accept a pending, unexpired offer from `host_id`. The leecher keeps one
    active host at a time, so their other accepted links are dropped. Returns
    the accepted link, or None if there's no valid pending offer."""
    async with db_engine.session() as session:
        link = await session.get(LeechLink, (host_id, leecher_id))
        if link is None or link.accepted:
            return None
        if datetime.now(timezone.utc) - _as_utc(link.created_at) > OFFER_TTL:
            return None
        await session.execute(
            delete(LeechLink).where(
                LeechLink.leecher_id == leecher_id, LeechLink.accepted.is_(True), LeechLink.host_id != host_id
            )
        )
        link.accepted = True
        link.accepted_at = datetime.now(timezone.utc)
        await session.commit()
        session.expunge(link)
        return link


async def purge_expired() -> int:
    """Delete offers that were never accepted and are past their TTL (they
    can no longer be accepted anyway). Returns how many were removed."""
    cutoff = datetime.now(timezone.utc) - OFFER_TTL
    async with db_engine.session() as session:
        result = await session.execute(
            delete(LeechLink).where(LeechLink.accepted.is_(False), LeechLink.created_at < cutoff)
        )
        await session.commit()
        return result.rowcount


async def get_target(user_id: int) -> LeechLink | None:
    """The accepted link this user should be served through, or None. A user
    with an account of their own is never leeched - their own data wins."""
    async with db_engine.session() as session:
        if await session.get(Account, user_id) is not None:
            return None
        result = await session.execute(
            select(LeechLink)
            .where(LeechLink.leecher_id == user_id, LeechLink.accepted.is_(True))
            .order_by(LeechLink.accepted_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def remove_for(user_id: int) -> bool:
    """Delete every link this user is part of, as host or as leecher."""
    async with db_engine.session() as session:
        result = await session.execute(
            delete(LeechLink).where(or_(LeechLink.host_id == user_id, LeechLink.leecher_id == user_id))
        )
        await session.commit()
        return result.rowcount > 0


async def remove_between(user_id: int, other_id: int) -> bool:
    """Delete the link between two users, whichever of them is the host, and
    whether or not it was accepted."""
    async with db_engine.session() as session:
        result = await session.execute(
            delete(LeechLink).where(
                or_(
                    (LeechLink.host_id == user_id) & (LeechLink.leecher_id == other_id),
                    (LeechLink.host_id == other_id) & (LeechLink.leecher_id == user_id),
                )
            )
        )
        await session.commit()
        return result.rowcount > 0


async def _update_idx(link: LeechLink, entry: FriendEntry) -> None:
    async with db_engine.session() as session:
        row = await session.get(LeechLink, (link.host_id, link.leecher_id))
        if row is not None:
            row.friend_idx = entry.idx
            row.friend_name = entry.profile.display_name[:64]
            await session.commit()
    link.friend_idx = entry.idx


async def with_host_client(host_id: int, operation, on_retry: Callable[[], Awaitable[None]] | None = None):
    """accounts.with_client() against the host's session, with failures worded
    for the leecher: they must never be told to /cc-login themselves."""
    try:
        return await accounts.with_client(host_id, operation, on_retry=on_retry)
    except accounts.NotLinked:
        raise LeechBroken("The host of your leech link has unlinked their account. Ask them to set it up again.")
    except SessionExpired:
        raise LeechBroken("The host's maimai DX NET session has expired. Ask them to `/cc-login` again.")


async def fetch_entry(link: LeechLink) -> FriendEntry:
    """The leecher's own friend entry, read through the host's session. If the
    stored idx no longer resolves, retry once by exact display name against the
    host's friend list (and persist the new idx)."""
    from circlechiffon.cogs.friends import _normalize_name

    async def fetch(client):
        entry = await client.get_friend_profile(link.friend_idx)
        if entry is not None:
            return entry
        wanted = _normalize_name(link.friend_name)
        matches = [e for e in await client.get_friend_list() if _normalize_name(e.profile.display_name) == wanted]
        return matches[0] if len(matches) == 1 else None

    entry = await with_host_client(link.host_id, fetch)
    if entry is None:
        raise LeechBroken(
            "Couldn't find you on the host's friend list any more. Ask them to check you're still "
            "friends on maimai DX NET and run `/cc-leech-send` again."
        )
    if entry.idx != link.friend_idx:
        await _update_idx(link, entry)
    return entry


async def not_linked_text(user_id: int) -> str:
    """The reply for a command that needs an account of the user's own."""
    if await get_target(user_id) is not None:
        return LEECH_UNSUPPORTED_MESSAGE
    return NOT_LINKED_MESSAGE


async def serve_via_friend_path(interaction, target: LeechLink, method: str, extra_note: str = "") -> None:
    """Run one of FriendsCog's single-friend renderers (`_send_friend_profile`
    or `_render_friend_best`) for the leecher's own friend entry, through the
    host's session. `interaction` must already be deferred."""
    try:
        entry = await fetch_entry(target)
    except MaimaiNetError as e:
        await interaction.edit_original_response(content=str(e))
        return
    except Exception as e:
        await interaction.edit_original_response(
            content=f"Couldn't fetch your data via leech mode: unexpected error ({type(e).__name__}: {e})"
        )
        return
    friends = interaction.client.get_cog("FriendsCog")
    await getattr(friends, method)(
        interaction, entry, session_id=target.host_id, note=f"{data_note()}{extra_note}"
    )
