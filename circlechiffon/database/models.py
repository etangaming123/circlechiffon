from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    """A Discord user's linked maimai DX NET account.

    `encrypted_cookie` holds a Fernet-encrypted serialized session (obtained
    once via /cc-login and refreshed in place as it's used).

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
