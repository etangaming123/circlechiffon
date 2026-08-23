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
from circlechiffon.database.models import Account


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
    """Run `operation(client)` against the user's linked account. If the
    stored session has expired and the user opted into remember_password,
    transparently re-logs in and retries `operation` once with the fresh
    session. If `on_retry` is given, it's awaited right before that
    re-login attempt actually starts - but only when we've confirmed
    credentials are stored, so callers never flash a "retrying" message
    for users who are about to hard-fail with no stored credentials at
    all. Raises NotLinked if the user has no linked account at all, or
    re-raises SessionExpired if the session expired and either no
    credentials are stored or the silent re-login failed."""
    client = await get_client(discord_id)
    if client is None:
        raise NotLinked()

    try:
        return await operation(client)
    except SessionExpired:
        pass
    finally:
        await client.close()

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
        await session.commit()
        return result.rowcount > 0


async def is_linked(discord_id: int) -> bool:
    async with db_engine.session() as session:
        account = await session.get(Account, discord_id)
        return account is not None
