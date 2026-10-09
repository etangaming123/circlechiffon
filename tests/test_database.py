"""database/engine.py - schema creation and the hand-written migration.

SQLAlchemy's create_all() never alters an existing table, so a column added to
a model later needs a manual ALTER TABLE (engine._ensure_accounts_schema).
"""

import asyncio
import sqlite3

from sqlalchemy import text

from circlechiffon.database import engine


def columns(db_file, table):
    with sqlite3.connect(db_file) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_create_all_makes_the_expected_tables(database, tmp_path):
    with sqlite3.connect(tmp_path / "test.db") as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "banned_users", "template_whitelist"} <= tables


def test_session_before_init_is_an_error():
    saved = engine._engine, engine._session_factory
    engine._engine = engine._session_factory = None
    try:
        for call in (engine.session, lambda: asyncio.run(engine.create_all())):
            try:
                call()
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError")
    finally:
        engine._engine, engine._session_factory = saved


def test_old_accounts_table_gains_encrypted_credentials(tmp_path):
    db_file = tmp_path / "old.db"
    with sqlite3.connect(db_file) as conn:       # an install from before the column existed
        conn.execute(
            "CREATE TABLE accounts (discord_id BIGINT PRIMARY KEY, encrypted_cookie TEXT NOT NULL, "
            "region VARCHAR(8) NOT NULL, display_name VARCHAR(64), linked_at DATETIME)"
        )
        conn.execute("INSERT INTO accounts (discord_id, encrypted_cookie, region) VALUES (1, 'cookie', 'intl')")
    assert "encrypted_credentials" not in columns(db_file, "accounts")

    async def go():
        engine.init_engine(str(db_file))
        try:
            await engine.create_all()
            await engine.create_all()                                   # running it twice must be harmless
            async with engine.session() as s:
                row = (await s.execute(text("SELECT encrypted_cookie, encrypted_credentials FROM accounts"))).one()
            assert tuple(row) == ("cookie", None)                       # old row survives
        finally:
            await engine._engine.dispose()

    saved = engine._engine, engine._session_factory
    try:
        asyncio.run(go())
    finally:
        engine._engine, engine._session_factory = saved
    assert "encrypted_credentials" in columns(db_file, "accounts")
