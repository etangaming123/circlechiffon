"""
Placeholder render inputs for /cc-template-preview - lets a user see their
uploaded template drawn over real Pillow output without a linked account or
a single DX NET request.

Nothing here touches the song catalog, the network or the jacket cache: the
best-50 is built straight from a small pool of placeholder titles, with
random levels, achievements and flags, and each chart gets a generated
jacket. Values are re-rolled on every call (pass `seed` for a repeatable
roll, e.g. when comparing two renders). The pool deliberately includes the
awkward cases a layout has to survive: a very long title, fullwidth and
Japanese text, 100.5000%, a flagless chart.
"""

import colorsys
import functools
import io
import random

from PIL import Image

from circlechiffon.ratingcalc.best50 import Best50Result, RatedEntry
from circlechiffon.ratingcalc.calculator import calculate_rating
from circlechiffon.types import (
    ChartType,
    ComboFlag,
    Difficulty,
    MissionEntry,
    MusicCountEntry,
    Profile,
    ProfileExtras,
    Score,
    Sheet,
    SyncFlag,
    TicketEntry,
)

SAMPLE_NAME = "ＣｉＲＣＬＥ"
LONG_TITLE = "A really long chart name powered by CiRCLE Chiffon"

_TITLES = [
    LONG_TITLE,
    "Placeholder Song",
    "ＣｉＲＣＬＥ Ｃｈｉｆｆｏｎ",
    "ダミーの曲名 -placeholder-",
    "Re:Placeholder",
    "999,999",
    "Sample Title ~Extended Mix~",
    "A Very Normal Song",
    "Tiny",
    "Neon Lights & Chiffon Skies",
]
_DIFFICULTIES = [Difficulty.master, Difficulty.master, Difficulty.remaster, Difficulty.expert]
_COMBOS = [ComboFlag.app, ComboFlag.ap, ComboFlag.fcp, ComboFlag.fc, None, None]
_SYNCS = [SyncFlag.fsdp, SyncFlag.fsd, SyncFlag.fsp, SyncFlag.fs, SyncFlag.sync, None, None]
_VERSIONS = ("old", "new")  # only compared against each other; the renderer never shows them


def _rng(seed: int | None) -> random.Random:
    return random.Random(seed)


def _entry(rng: random.Random, title: str, level_lo: float, level_hi: float, version: str) -> RatedEntry:
    level = round(rng.uniform(level_lo, level_hi), 1)
    # skewed high, with 100.5000% showing up often enough to check it fits
    achievement = 100.5 if rng.random() < 0.15 else round(rng.uniform(97.0, 100.4999), 4)
    combo = rng.choice(_COMBOS)
    difficulty = rng.choice(_DIFFICULTIES)
    chart_type = rng.choice((ChartType.dx, ChartType.dx, ChartType.std))
    award = calculate_rating(level, achievement, combo)
    return RatedEntry(
        score=Score(
            title=title,
            difficulty=difficulty,
            chart_type=chart_type,
            achievement=achievement,
            combo_flag=combo,
            sync_flag=rng.choice(_SYNCS),
        ),
        sheet=Sheet(type=chart_type, difficulty=difficulty, level=f"{int(level)}{'+' if level % 1 >= 0.6 else ''}", internal_level_value=level, version=version),
        rating=award.rating,
        rank=award.rank,
    )


def sample_best50(seed: int | None = None) -> Best50Result:
    """15 + 35 placeholder entries, best first, with the totals filled in
    the way calculate_best50 would. No catalog involved."""
    rng = _rng(seed)
    titles = _TITLES + [f"Placeholder Song {i}" for i in range(2, 52)]
    rng.shuffle(titles)
    # the long title always lands on a card, whatever the shuffle did
    titles.remove(LONG_TITLE)
    titles.insert(rng.randrange(50), LONG_TITLE)

    def bucket(count: int, start: int, lo: float, hi: float, version: str) -> list[RatedEntry]:
        entries = [_entry(rng, titles[start + i], lo, hi, version) for i in range(count)]
        entries.sort(key=lambda e: (e.rating, e.score.achievement), reverse=True)
        return entries

    b15 = bucket(15, 0, 13.2, 14.9, "new")
    b35 = bucket(35, 15, 12.5, 14.6, "old")
    return Best50Result(
        b15=list(b15),
        b35=list(b35),
        b15_total=sum(e.rating for e in b15),
        b35_total=sum(e.rating for e in b35),
    )


@functools.lru_cache(maxsize=16)
def _jacket(hue: int) -> bytes:
    """A small flat-gradient stand-in jacket; the renderer resizes it."""
    size = 96
    img = Image.new("RGB", (size, size))
    r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue / 16, 0.5, 0.95))
    for y in range(size):
        shade = 0.55 + 0.45 * (1 - y / size)
        img.paste((int(r * shade), int(g * shade), int(b * shade)), (0, y, size, y + 1))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def sample_jackets(result: Best50Result) -> dict[str, bytes]:
    """title -> generated jacket for every entry, so nothing is downloaded."""
    jackets: dict[str, bytes] = {}
    for entry in [*result.b15, *result.b35]:
        if entry is not None:
            title = entry.score.title
            jackets[title] = _jacket(sum(map(ord, title)) % 16)
    return jackets


def sample_profile(seed: int | None = None) -> Profile:
    rng = _rng(seed)
    total = 4104
    counts: list[MusicCountEntry] = []
    for category, tags, top in (
        ("rank", ("sssp", "sss", "ssp", "ss", "sp", "s"), total),
        ("dxstar", ("5", "4", "3", "2", "1"), total),
        ("combo", ("app", "ap", "fcp", "fc"), total),
        ("sync", ("fdxp", "fdx", "fsp", "fs", "sync"), total),
    ):
        # the first tag is the hardest tier, so it has the fewest earned
        values = sorted(rng.randint(top // 40, top) for _ in tags)
        counts += [MusicCountEntry(category=category, tag=tag, earned=v, total=total) for tag, v in zip(tags, values)]
    counts.append(MusicCountEntry(category="clear", tag="clear", earned=rng.randint(total // 2, total), total=total))
    # pin one to the widest realistic value so the pill sizing gets tested
    counts[0] = MusicCountEntry(category="rank", tag="sssp", earned=rng.choice((999, 4104)), total=total)
    return Profile(
        display_name=SAMPLE_NAME,
        rating=rng.randint(14000, 16999),
        title="CiRCLE Chiffon Sample Title",
        title_tier="Rainbow",
        current_version_plays=rng.randint(100, 999),
        total_plays=rng.randint(1000, 99999),
        star_count=rng.randint(100, 9999),
        music_counts=counts,
    )


def sample_profile_extras(mission_count: int = 5, ticket_count: int = 3, seed: int | None = None) -> ProfileExtras:
    rng = _rng(seed)
    cleared = rng.randint(0, mission_count)
    missions = [
        MissionEntry(
            text=LONG_TITLE if i == 0 else f"Play {rng.randint(3, 20)} tracks at MASTER or above",
            mile_reward=rng.choice((50, 100, 200, 500)),
            cleared=i < cleared,
        )
        for i in range(mission_count)
    ]
    tickets = [TicketEntry(name=f"Sample Ticket {i + 1}", count=rng.randint(1, 99)) for i in range(ticket_count)]
    return ProfileExtras(
        cp_current=rng.randint(1, 99),
        cp_required=10,
        mile_count=rng.randint(1000, 999999),
        mission_deadline_text="Until 2026/10/01 04:59",
        mission_clear_count=cleared,
        mission_total_count=mission_count,
        missions=missions,
        tickets=tickets,
        intimate_count=rng.randint(0, 99),
    )
