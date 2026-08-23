from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from circlechiffon.database.models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(db_path: str):
    global _engine, _session_factory
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _ensure_accounts_schema(sync_conn):
    # Base.metadata.create_all only creates missing tables, it never alters an
    # existing one - so a DB created before encrypted_credentials was added to
    # the Account model needs it added by hand, or every query against that
    # column fails with "no such column" on an existing install.
    cols = {row[1] for row in sync_conn.execute(text("PRAGMA table_info(accounts)")).fetchall()}
    if cols and "encrypted_credentials" not in cols:
        sync_conn.execute(text("ALTER TABLE accounts ADD COLUMN encrypted_credentials TEXT"))


async def create_all():
    if _engine is None:
        raise RuntimeError("init_engine() must be called before create_all()")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_accounts_schema)


def session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("init_engine() must be called before session()")
    return _session_factory()
