"""Shared test setup, loaded automatically by pytest.

Three guards so a test run can never touch live state:

* `config` is replaced by a stub. The real config.py reads config.json (and,
  on a fresh checkout, calls input() waiting for a bot token) the moment it's
  imported - nothing under test should depend on it.
* dxrating.net is never contacted: SongCatalog() fetches aliases over HTTP on
  construction, so that fetch is replaced with an empty result.
* Fake catalog helpers below let best-50 tests run without data/dxdata.json.
"""

import sys
import types

import pytest

from circlechiffon.types import ChartType, Difficulty, Sheet

_stub_config = types.ModuleType("config")
_stub_config.config = types.SimpleNamespace(
    token="test-token", owner_id="1", db_path=":memory:",
    chart_render="owner", chart_render_server="", chart_render_key="",
)
sys.modules["config"] = _stub_config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("circlechiffon.songdata.catalog.fetch_aliases_by_song_id", lambda: {})


class FakeCatalog:
    """Just the three things calculate_best50() asks a SongCatalog for."""

    def __init__(self, current_version="NEW", previous_version="PREV"):
        self.current_version = current_version
        self.previous_version = previous_version
        self._sheets: dict[tuple, Sheet] = {}

    def add(self, title, chart_type=ChartType.dx, difficulty=Difficulty.master, level=13.0, version="OLD"):
        self._sheets[(title, chart_type, difficulty)] = Sheet(
            type=chart_type, difficulty=difficulty, level=str(int(level)),
            internal_level_value=level, version=version,
        )

    def find_sheet(self, title, chart_type, difficulty):
        return self._sheets.get((title, chart_type, difficulty))


@pytest.fixture
def fake_catalog():
    return FakeCatalog()


@pytest.fixture
def database(tmp_path):
    """A fresh, empty SQLite database wired into circlechiffon.database.engine
    for one test (a temp file - never circlechiffon.db). Use with asyncio.run()."""
    import asyncio

    from circlechiffon.database import engine

    previous = (engine._engine, engine._session_factory)
    engine.init_engine(str(tmp_path / "test.db"))
    asyncio.run(engine.create_all())
    yield engine
    asyncio.run(engine._engine.dispose())
    engine._engine, engine._session_factory = previous
