from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    """A Discord user's linked maimai DX NET account.

    `encrypted_cookie` holds a Fernet-encrypted serialized session: the whole
    cookie jar, across both the SEGA Aime gateway and maimaidx-eng.com. It is
    written by /cc-login, and rewritten whenever the session is re-minted from
    the gateway's persistent `clal` token (see accounts.refresh_session) or a
    full re-login. Note that an ordinary command does NOT write it back, so
    cookies the server rotates mid-command are still discarded.

    `encrypted_credentials`, if present, holds a Fernet-encrypted JSON object
    of {"sega_id": ..., "password": ...} - only stored when the user
    explicitly opts into the `remember_password` option on /cc-login
    (after an explicit warning), so the bot can silently re-login when the
    session cookie expires instead of requiring /cc-login again. Null by
    default: the default login flow never stores a password.
    """

    __tablename__ = "accounts"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    encrypted_cookie: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(8), nullable=False, default="intl")
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BannedUser(Base):
    """A Discord user banned from using bot commands.

    Only a Discord ID plus what's needed to enforce/report the ban is kept -
    no username, no server info, nothing else identifying.

    `expires_at` is null for a permanent ban (unless `ncmd` is set).
    `ncmd` bans lift the moment the user's next command attempt is handled,
    regardless of `expires_at`.
    """

    __tablename__ = "banned_users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ncmd: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CollectionPreset(Base):
    """One saved set of equipped collection items (icon / name plate / frame /
    title) for a Discord user, in one of 5 numbered slots.

    `items` is a JSON object of {slot_name: {"key": ..., "label": ...}}. The
    key is the item's image filename for icon/nameplate/frame and
    "<tier>|<text>" for a title - never the page's `idx`, which is a single-use
    nonce and worthless a request later. Keeping all four in one JSON column
    means adding a fifth equippable slot later needs no migration.
    """

    __tablename__ = "collection_presets"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    items: Mapped[str] = mapped_column(Text, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TemplateWhitelist(Base):
    """A Discord user allowed to upload custom render templates (see
    customisation/store.py) and, unless `config.chart_render` is
    "everyone", to render /cc-chart videos. The bot owner is always allowed and never
    needs a row. The template files themselves live on disk under
    user_templates/, not in the DB; removing a row stops them being used
    but leaves the files in place."""

    __tablename__ = "template_whitelist"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LeechLink(Base):
    """A "leech" offer from a linked Discord user (the host) to another
    Discord user (the leecher): the leecher may use the bot without a SEGA ID
    of their own, reading their own data through the host's session via the
    friend pages (see leech.py).

    `friend_idx` is the leecher's id on the host's DX NET friend list - the
    hidden `idx` every friend sub-page is keyed on, not a user-facing friend
    code. `friend_name` is kept so a drifted idx can be re-resolved by name.
    `accepted` stays False until the leecher runs /cc-leech-accept.
    """

    __tablename__ = "leech_links"

    host_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leecher_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    friend_idx: Mapped[str] = mapped_column(String(64), nullable=False)
    friend_name: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
