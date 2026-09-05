from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
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


class TemplateAccess(Base):
    """Whitelist of Discord users allowed to upload custom b50/profile
    render templates (see circlechiffon/user_templates.py). The owner
    always has access implicitly (access.is_owner()), independent of
    whether they have a row here."""

    __tablename__ = "template_access"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserTemplate(Base):
    """One uploaded background template for one (user, render type) pair.
    The image itself lives on disk at a path derived from these two key
    columns (see user_templates.py) - no path column needed."""

    __tablename__ = "user_templates"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    render_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
