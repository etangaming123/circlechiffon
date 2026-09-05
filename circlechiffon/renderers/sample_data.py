"""
A single fixed, entirely fake "sample profile" fixture - shared by
/cc-template-preview (circlechiffon/cogs/templates.py) and the local
generate_previews.py tool, so there is exactly one definition of what
"the sample profile" looks like. Nothing here talks to the network, the
DB, or a real maimai DX NET account - every image is generated on the fly
with Pillow, so both callers can run fully offline.
"""

import io

from PIL import Image, ImageDraw, ImageFont

from circlechiffon.ratingcalc.best50 import Best50Result, RatedEntry
from circlechiffon.ratingcalc.calculator import rank_tag_for_achievement
from circlechiffon.types import ChartType, ComboFlag, Difficulty, MusicCountEntry, Profile, Score, Sheet, SyncFlag

_COMBO_CYCLE = [None, None, ComboFlag.fc, ComboFlag.fcp, ComboFlag.ap, ComboFlag.app]
_SYNC_CYCLE = [None, None, None, SyncFlag.fs, SyncFlag.fsp, SyncFlag.sync]

# deterministic, made-up song titles - real dxdata.json titles aren't
# needed since sample_data never touches the song catalog, only the
# renderer's own drawing code.
_SAMPLE_TITLES = [
    "Neon Skyline", "Starlit Cascade", "Velvet Horizon", "Chrono Break", "Iris Bloom",
    "Paradox Waltz", "Aurora Drive", "Glasswing", "Midnight Parade", "Zenith Run",
    "Echo Chamber", "Prism Rain", "Solstice", "Voltage", "Afterglow",
]

_SAMPLE_COLORS = [
    (219, 88, 92), (88, 150, 219), (98, 201, 145), (219, 176, 60), (150, 98, 219),
    (219, 122, 60), (60, 180, 180), (200, 90, 160), (120, 140, 60), (90, 90, 200),
]

_DIFFICULTIES = [Difficulty.master, Difficulty.remaster, Difficulty.expert]


def _placeholder_png(size: tuple[int, int], color: tuple[int, int, int], label: str = "") -> bytes:
    image = Image.new("RGB", size, color)
    if label:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size[0] - text_w) / 2 - bbox[0], (size[1] - text_h) / 2 - bbox[1]),
            label, font=font, fill=(255, 255, 255),
        )
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _sample_jacket(index: int) -> bytes:
    color = _SAMPLE_COLORS[index % len(_SAMPLE_COLORS)]
    return _placeholder_png((300, 300), color, label=f"#{index + 1}")


def build_sample_badge_icons() -> dict[str, bytes]:
    """Every key the renderers look up out of a live `badge_icons` dict
    (see adapters/maimai_net/badge_icons.py's 'category:tag' convention) -
    a small colored circle labeled with its own tag, standing in for the
    real network-fetched icon."""
    icons: dict[str, bytes] = {}
    for tag in ("app", "ap", "fcp", "fc"):
        icons[f"combo:{tag}"] = _placeholder_png((64, 64), (219, 176, 60), tag.upper())
    for tag in ("sync", "fs", "fsp", "fsd", "fsdp"):
        icons[f"sync:{tag}"] = _placeholder_png((64, 64), (88, 150, 219), tag.upper())
    # aliases so the Player's Data grid's "fdx"/"fdxp" filename-stem tags
    # (see MusicCountEntry) resolve too - mirrors get_all_badge_icons()'s
    # own real aliasing of the same bytes under both names.
    icons["sync:fdx"] = icons["sync:fsd"]
    icons["sync:fdxp"] = icons["sync:fsdp"]
    for tag in ("d", "c", "b", "bb", "bbb", "a", "aa", "aaa", "s", "sp", "ss", "ssp", "sss", "sssp"):
        icons[f"rank:{tag}"] = _placeholder_png((64, 64), (150, 98, 219), tag.upper())
    icons["clear:clear"] = _placeholder_png((120, 40), (98, 201, 145), "CLEAR")
    for n in range(1, 6):
        icons[f"dxstar:{n}"] = _placeholder_png((64, 64), (219, 122, 60), f"★{n}")
    icons["misc:star"] = _placeholder_png((32, 32), (255, 221, 51), "★")
    return icons


def build_sample_jackets_by_title() -> dict[str, bytes]:
    return {title: _sample_jacket(i) for i, title in enumerate(_SAMPLE_TITLES)}


def build_sample_icon_bytes() -> bytes:
    return _placeholder_png((256, 256), (72, 191, 238), "ICON")


def build_sample_rating_badge_bytes() -> bytes:
    # roughly the real asset's own proportions (296x86) - _paste_rating_badge
    # positions the achieved-rating digits as a fraction of the pasted
    # image's own size, so any placeholder at a similar aspect ratio reads
    # sensibly once the real rating number is drawn on top of it.
    image = Image.new("RGBA", (296, 86), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([(0, 0), (295, 85)], radius=20, fill=(45, 45, 60, 255))
    draw.rounded_rectangle([(122, 17), (281, 67)], radius=8, fill=(20, 20, 28, 255))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _sample_entry(index: int, *, rating: int, achievement: float) -> RatedEntry:
    title = _SAMPLE_TITLES[index % len(_SAMPLE_TITLES)]
    difficulty = _DIFFICULTIES[index % len(_DIFFICULTIES)]
    score = Score(
        title=title,
        difficulty=difficulty,
        chart_type=ChartType.dx if index % 2 == 0 else ChartType.std,
        achievement=achievement,
        combo_flag=_COMBO_CYCLE[index % len(_COMBO_CYCLE)],
        sync_flag=_SYNC_CYCLE[index % len(_SYNC_CYCLE)],
        rating=rating,
    )
    sheet = Sheet(
        type=score.chart_type,
        difficulty=difficulty,
        level="14+",
        internal_level_value=14.5,
        version="SAMPLE",
    )
    return RatedEntry(score=score, sheet=sheet, rating=rating, rank=rank_tag_for_achievement(achievement))


def build_sample_best50() -> Best50Result:
    """A full 50-entry grid with a couple of entries left as None in each
    bucket, to also show how a not-yet-filled slot renders."""
    b35 = [
        _sample_entry(i, rating=max(280 - i * 4, 180), achievement=100.5 - i * 0.4)
        for i in range(33)
    ] + [None, None]
    b15 = [
        _sample_entry(i + 100, rating=max(320 - i * 5, 220), achievement=100.5 - i * 0.5)
        for i in range(13)
    ] + [None, None]
    return Best50Result(
        b15=b15,
        b35=b35,
        b15_total=sum(e.rating for e in b15 if e is not None),
        b35_total=sum(e.rating for e in b35 if e is not None),
    )


def build_sample_profile() -> Profile:
    music_counts = [
        MusicCountEntry(category="rank", tag=tag, earned=n, total=n + 20)
        for n, tag in enumerate(("sssp", "sss", "ssp", "ss", "sp", "s"), start=40)
    ] + [
        MusicCountEntry(category="clear", tag="clear", earned=612, total=650),
    ] + [
        MusicCountEntry(category="dxstar", tag=str(n), earned=n * 30, total=n * 40)
        for n in range(5, 0, -1)
    ] + [
        MusicCountEntry(category="combo", tag=tag, earned=n, total=n + 30)
        for n, tag in enumerate(("app", "ap", "fcp", "fc"), start=20)
    ] + [
        MusicCountEntry(category="sync", tag=tag, earned=n, total=n + 15)
        for n, tag in enumerate(("fdxp", "fdx", "fsp", "fs", "sync"), start=10)
    ]
    return Profile(
        display_name="ＳＡＭＰＬＥ",
        rating=15234,
        title="Sample Title",
        title_tier="Rainbow",
        current_version_plays=482,
        total_plays=3190,
        star_count=57,
        music_counts=music_counts,
    )
