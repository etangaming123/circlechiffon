"""
Glue between the Account DB row (encrypted session cookie, and optionally
encrypted SEGA ID credentials) and a live MaimaiNetClient, used by every cog
that needs a linked session.
"""

import json
from typing import Awaitable, Callable

import discord
from sqlalchemy import delete

import crypto_utils
from circlechiffon.adapters.maimai_net.client import MaimaiNetClient
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import Account, CollectionPreset


_FAILED = object()  # sentinel: `operation` may legitimately return None


class NotLinked(Exception):
    """Raised by with_client() when the Discord user has no linked account."""


async def get_client(discord_id: int) -> MaimaiNetClient | None:
    """Returns a MaimaiNetClient built from the user's stored session cookie,
    or None if they haven't linked an account."""
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        if account is None:
            return None
        cookies_json = crypto_utils.decrypt_value(account.encrypted_cookie)
        return MaimaiNetClient.from_cookies_json(cookies_json)


async def save_session(discord_id: int, client: MaimaiNetClient, region: str = "intl", display_name: str | None = None) -> None:
    encrypted = crypto_utils.encrypt_value(client.cookies_json())
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        if account is None:
            account = Account(discord_id=discord_id, encrypted_cookie=encrypted, region=region, display_name=display_name)
            session.add(account)
        else:
            account.encrypted_cookie = encrypted
            account.region = region
            if display_name is not None:
                account.display_name = display_name
        await session.commit()


async def save_credentials(discord_id: int, sega_id: str, password: str) -> None:
    """Store a SEGA ID username/password, encrypted at rest, so reauth() can
    silently re-login when the session cookie expires. Only call this after
    the user has explicitly opted in and been warned - the account must
    already exist (i.e. call save_session() first)."""
    encrypted = crypto_utils.encrypt_value(json.dumps({"sega_id": sega_id, "password": password}))
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        if account is None:
            raise NotLinked()
        account.encrypted_credentials = encrypted
        await session.commit()


async def has_stored_credentials(discord_id: int) -> bool:
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        return account is not None and account.encrypted_credentials is not None


async def _get_credentials(discord_id: int) -> tuple[str, str] | None:
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        if account is None or account.encrypted_credentials is None:
            return None
        raw = crypto_utils.decrypt_value(account.encrypted_credentials)
        data = json.loads(raw)
        return data["sega_id"], data["password"]


async def refresh_session(discord_id: int) -> MaimaiNetClient | None:
    """Mint a fresh maimai DX NET session from the stored SEGA Aime `clal`
    cookie, with no password involved, and persist it.

    This is the cheap recovery path, and the one that covers the common case:
    DX NET allows a single live session per account, so the user opening the
    site in their own browser silently evicts the bot. `clal` outlives the
    session by decades, so the gateway hop can just be replayed - see
    MaimaiNetClient.refresh_session(). Unlike reauth() this needs no stored
    password, so it works for every linked account, not just the ones that
    opted into remember_password.

    Returns None if there's no linked account, no usable `clal`, or the
    gateway declined - callers should fall back to reauth()."""
    client = await get_client(discord_id)
    if client is None:
        return None
    if not await client.refresh_session():
        await client.close()
        return None
    await save_session(discord_id, client)
    return client


async def reauth(discord_id: int) -> MaimaiNetClient | None:
    """Attempt to silently re-login using stored SEGA ID credentials (only
    present if the user opted into remember_password), saving the refreshed
    session cookie. Returns None if no credentials are stored or re-login
    itself fails (e.g. the password was since changed) - callers should fall
    back to telling the user to /cc-login again in that case."""
    creds = await _get_credentials(discord_id)
    if creds is None:
        return None
    sega_id, password = creds

    client = MaimaiNetClient()
    try:
        await client.login(sega_id, password)
    except MaimaiNetError:
        await client.close()
        return None

    await save_session(discord_id, client)
    return client


async def with_client(
    discord_id: int,
    operation,
    on_retry: Callable[[], Awaitable[None]] | None = None,
):
    """Run `operation(client)` against the user's linked account, recovering
    from an expired session without troubling the user where possible.

    Recovery is tried in cost order. The client itself re-mints from `clal`
    mid-command when it can (see MaimaiNetClient.refresh_session); if the
    operation still comes back expired, refresh_session() mints a fresh
    session from `clal` and retries - no password, so this works for every
    linked account. Only when `clal` is spent does it fall back to reauth(),
    a full password re-login, which needs remember_password.

    If `on_retry` is given it's awaited just before that password re-login -
    but only when credentials are actually stored, so users about to
    hard-fail never see a "retrying" message. The `clal` path is silent,
    since it's fast and usually invisible.

    Raises NotLinked if there's no linked account, or SessionExpired if every
    recovery path failed."""
    client = await get_client(discord_id)
    if client is None:
        raise NotLinked()

    try:
        result = await operation(client)
    except SessionExpired:
        result = _FAILED
    finally:
        # The client re-mints from `clal` on its own when DX NET evicts the
        # session mid-command. That new userId only lives in this client's
        # jar, so persist it or the next command starts from a cookie we
        # already know is dead. Only written when a re-mint actually
        # happened - a normal command still costs no DB write.
        if client.session_refreshed:
            await save_session(discord_id, client)
        await client.close()

    if result is not _FAILED:
        return result

    # Cheapest recovery first: a new session straight from `clal`, no
    # password needed and available to every linked account. Only if that
    # fails (clal spent - password changed, sessions revoked) do we fall
    # back to a full re-login, which needs remember_password.
    refreshed = await refresh_session(discord_id)
    if refreshed is not None:
        try:
            return await operation(refreshed)
        except SessionExpired:
            pass
        finally:
            await refreshed.close()

    if on_retry is not None and await has_stored_credentials(discord_id):
        await on_retry()

    reauthed = await reauth(discord_id)
    if reauthed is None:
        raise SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")
    try:
        return await operation(reauthed)
    finally:
        await reauthed.close()


def default_retry_notice(interaction: discord.Interaction) -> Callable[[], Awaitable[None]]:
    """Standard on_retry callback for with_client(): edits the command's
    already-deferred response to let the user know a silent re-login is
    underway. Every account-gated cog command uses this same wording."""

    async def notice() -> None:
        await interaction.edit_original_response(content="Retrying login, please wait...")

    return notice


async def delete_account(discord_id: int) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(delete(Account).where(Account.discord_id == discord_id))
        await session.execute(delete(CollectionPreset).where(CollectionPreset.discord_id == discord_id))
        await session.commit()
        return result.rowcount > 0


async def is_linked(discord_id: int) -> bool:
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        return account is not None


async def get_display_name(discord_id: int) -> str | None:
    """The maimai DX NET display name recorded for this account at login
    (cogs/auth.py), or None if it was never captured - login stores it
    best-effort and swallows a failed profile fetch. Reading it here is free
    (no request to SEGA), which is the whole point: commands that just want
    to label an embed with the player's name shouldn't cost a page fetch."""
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        return account.display_name if account is not None else None


async def set_display_name(discord_id: int, name: str) -> None:
    """Backfills the display name for an account that never got one, so the
    profile fetch that recovered it only has to happen once. Deliberately not
    routed through save_session(), which would re-encrypt the session cookie
    for no reason."""
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        if account is not None:
            account.display_name = name
            await session.commit()
