"""
Per-SEGA-ID request queue: at most one DX NET operation in flight per linked
account at a time, everything else waits its turn.

DX NET allows a single live session per account, and leech mode lets several
people read through one host's session, so unbounded concurrency means evicted
sessions and a flood of requests from one SEGA ID. The queue is keyed by the
account's (host's) Discord id.

The account owner jumps ahead of anyone waiting (owners are FIFO among
themselves, then everyone else FIFO) but never interrupts the operation that is
already running - a slot is only ever handed over when it is released.
"""

import asyncio
from collections import deque
from contextlib import asynccontextmanager

from circlechiffon.adapters.maimai_net.errors import MaimaiNetError

# Non-owners waiting behind one SEGA ID beyond this are turned away rather than
# queued indefinitely. Owners are never turned away.
MAX_WAITING = 5


class AccountBusy(MaimaiNetError):
    """The account's queue is full. Subclasses MaimaiNetError so every
    command's existing handler shows the message."""


class _Slot:
    __slots__ = ("busy", "owners", "others")

    def __init__(self) -> None:
        self.busy = False
        self.owners: deque[asyncio.Future] = deque()
        self.others: deque[asyncio.Future] = deque()


_slots: dict[int, _Slot] = {}


def waiting(key: int) -> int:
    """How many operations are queued behind the running one for this account."""
    slot = _slots.get(key)
    return 0 if slot is None else len(slot.owners) + len(slot.others)


def _release(key: int) -> None:
    slot = _slots[key]
    for q in (slot.owners, slot.others):
        while q:
            fut = q.popleft()
            if not fut.done():
                fut.set_result(None)  # hand the slot over; busy stays True
                return
    del _slots[key]


async def acquire(key: int, is_owner: bool) -> None:
    slot = _slots.get(key)
    if slot is None:
        slot = _slots[key] = _Slot()
    if not slot.busy:
        slot.busy = True
        return
    if not is_owner and len(slot.others) >= MAX_WAITING:
        raise AccountBusy(
            "That SEGA ID has too many requests queued right now. Try again in a moment."
        )
    fut = asyncio.get_running_loop().create_future()
    (slot.owners if is_owner else slot.others).append(fut)
    try:
        await fut
    except BaseException:
        if fut.done() and not fut.cancelled():
            _release(key)  # slot was handed to us just as we were cancelled
        else:
            fut.cancel()  # skipped by _release; pruned lazily
            _prune(key)
        raise


def _prune(key: int) -> None:
    slot = _slots.get(key)
    if slot is not None and not slot.busy:
        _slots.pop(key, None)


@asynccontextmanager
async def slot(key: int, is_owner: bool = True):
    """Hold the account's single in-flight slot for the duration of the block."""
    await acquire(key, is_owner)
    try:
        yield
    finally:
        _release(key)
