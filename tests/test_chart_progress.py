"""cogs/chart.py's _ProgressReporter: whatever the render says last is what
ends up on screen, even when it's said while a slow edit is in flight."""

import asyncio

import discord

from circlechiffon.cogs import chart
from circlechiffon.types import Difficulty


class FakeInteraction:
    def __init__(self, edit_seconds=0.0, fail=False):
        self.contents = []
        self.edit_seconds = edit_seconds
        self.fail = fail

    async def edit_original_response(self, *, content):
        await asyncio.sleep(self.edit_seconds)
        if self.fail:
            raise discord.HTTPException(type("R", (), {"status": 500, "reason": "x"})(), "boom")
        self.contents.append(content)


def reporter(interaction, monkeypatch):
    monkeypatch.setattr(chart, "_MIN_EDIT_INTERVAL", 0.01)
    return chart._ProgressReporter(interaction, "Song", Difficulty.master)


def run(coro):
    return asyncio.run(coro)


def test_bar_then_phase_shows_the_phase_last(monkeypatch):
    async def go():
        inter = FakeInteraction(edit_seconds=0.05)               # slow edit: the phase arrives mid-flight
        r = reporter(inter, monkeypatch)
        r.update(30.0, 120.0, 0.75)
        await asyncio.sleep(0.01)
        r.phase("encoding", 0, 2)
        await asyncio.sleep(0.3)
        await r.stop()
        return inter.contents

    contents = run(go())
    assert "75%" in contents[0]
    assert contents[-1].endswith("Encoding video (1/2)...")


def test_only_the_newest_of_a_burst_is_shown_after_the_inflight_edit(monkeypatch):
    async def go():
        inter = FakeInteraction(edit_seconds=0.05)
        r = reporter(inter, monkeypatch)
        for fraction in (0.1, 0.2, 0.3, 0.4):
            r.update(1.0, 10.0, fraction)
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.3)
        await r.stop()
        return inter.contents

    contents = run(go())
    assert "40%" in contents[-1]
    assert len(contents) < 4


def test_notice_stays_above_everything_and_shows_before_any_bar(monkeypatch):
    async def go():
        inter = FakeInteraction()
        r = reporter(inter, monkeypatch)
        r.set_notice("NOTICE")
        await asyncio.sleep(0.05)
        r.update(5.0, 50.0, 0.1)
        await asyncio.sleep(0.05)
        r.phase("downloading")
        await asyncio.sleep(0.05)
        await r.stop()
        return inter.contents

    contents = run(go())
    assert contents[0].startswith("NOTICE\nRendering **Song** [")
    assert all(c.startswith("NOTICE\n") for c in contents)
    assert contents[-1].endswith("Downloading video...")


def test_reset_drops_the_old_bar(monkeypatch):
    async def go():
        inter = FakeInteraction()
        r = reporter(inter, monkeypatch)
        r.update(5.0, 50.0, 0.8)
        await asyncio.sleep(0.05)
        r.set_notice("DOWN")
        r.reset()
        await asyncio.sleep(0.05)
        await r.stop()
        return inter.contents

    contents = run(go())
    assert "80%" in contents[0]
    assert "%" not in contents[-1] and contents[-1].startswith("DOWN")


def test_nothing_is_edited_before_there_is_anything_to_say(monkeypatch):
    async def go():
        inter = FakeInteraction()
        r = reporter(inter, monkeypatch)
        r.reset()
        await asyncio.sleep(0.05)
        await r.stop()
        return inter.contents

    assert run(go()) == []


def test_stop_prevents_later_edits_and_failed_edits_are_ignored(monkeypatch):
    async def go():
        inter = FakeInteraction()
        r = reporter(inter, monkeypatch)
        r.update(1.0, 10.0, 0.1)
        await asyncio.sleep(0.05)
        await r.stop()
        r.update(2.0, 10.0, 0.2)
        await asyncio.sleep(0.05)
        failing = FakeInteraction(fail=True)
        r2 = reporter(failing, monkeypatch)
        r2.update(1.0, 10.0, 0.1)
        await asyncio.sleep(0.05)
        await r2.stop()
        return inter.contents

    assert len(run(go())) == 1


def test_phase_text():
    r = chart._ProgressReporter(FakeInteraction(), "S", Difficulty.master)
    r._kick = lambda: None
    r.phase("encoding", 0, 1)
    assert r._status == "Encoding video..."
    r.phase("encoding", 1, 3)
    assert r._status == "Encoding video (2/3)..."
    r.phase("encoding", 0, 0)                                    # server that predates the counts
    assert r._status == "Encoding video..."
