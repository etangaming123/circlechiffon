import asyncio

import pytest

from circlechiffon import session_queue as sq


@pytest.fixture(autouse=True)
def _clean():
    sq._slots.clear()
    yield
    sq._slots.clear()


async def _job(order, name, key, is_owner, gate=None):
    async with sq.slot(key, is_owner):
        order.append(name)
        if gate is not None:
            await gate.wait()


def test_one_at_a_time():
    async def go():
        running = peak = 0

        async def job():
            nonlocal running, peak
            async with sq.slot(1, False):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0)
                running -= 1

        await asyncio.gather(*(job() for _ in range(4)))
        assert peak == 1 and not sq._slots

    asyncio.run(go())


def test_different_accounts_independent():
    async def go():
        order, gate = [], asyncio.Event()
        a = asyncio.create_task(_job(order, "a", 1, False, gate))
        await asyncio.sleep(0)
        await asyncio.wait_for(_job(order, "b", 2, False), 1)
        assert order == ["a", "b"]
        gate.set()
        await a

    asyncio.run(go())


def test_owner_skips_queue_without_interrupting():
    async def go():
        order, gate = [], asyncio.Event()
        running = asyncio.create_task(_job(order, "running", 1, False, gate))
        await asyncio.sleep(0)
        l1 = asyncio.create_task(_job(order, "leech1", 1, False))
        l2 = asyncio.create_task(_job(order, "leech2", 1, False))
        await asyncio.sleep(0)
        o = asyncio.create_task(_job(order, "owner", 1, True))
        await asyncio.sleep(0)
        assert order == ["running"]  # owner did not interrupt
        gate.set()
        await asyncio.gather(running, l1, l2, o)
        assert order == ["running", "owner", "leech1", "leech2"]

    asyncio.run(go())


def test_queue_full_rejects_non_owner_only():
    async def go():
        gate, order = asyncio.Event(), []
        tasks = [asyncio.create_task(_job(order, "r", 1, False, gate))]
        await asyncio.sleep(0)
        for i in range(sq.MAX_WAITING):
            tasks.append(asyncio.create_task(_job(order, i, 1, False)))
        await asyncio.sleep(0)
        with pytest.raises(sq.AccountBusy):
            await sq.acquire(1, False)
        tasks.append(asyncio.create_task(_job(order, "owner", 1, True)))
        gate.set()
        await asyncio.gather(*tasks)
        assert order[1] == "owner"

    asyncio.run(go())


def test_cancelled_waiter_does_not_wedge_queue():
    async def go():
        gate, order = asyncio.Event(), []
        r = asyncio.create_task(_job(order, "r", 1, False, gate))
        await asyncio.sleep(0)
        w = asyncio.create_task(_job(order, "w", 1, False))
        n = asyncio.create_task(_job(order, "n", 1, False))
        await asyncio.sleep(0)
        w.cancel()
        gate.set()
        await asyncio.gather(r, n)
        assert order == ["r", "n"] and not sq._slots

    asyncio.run(go())


def test_released_on_error():
    async def go():
        with pytest.raises(RuntimeError):
            async with sq.slot(1):
                raise RuntimeError
        assert not sq._slots

    asyncio.run(go())
