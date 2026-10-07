"""
Fixed, fake-but-realistic render inputs for /cc-template-preview - lets a
user see their uploaded template drawn over real Pillow output without a
linked account or a single DX NET request. Also handy for render tests
(see temporary/).

Everything here is deterministic: the same catalog always yields the same
best-50, so two previews of the same template are directly comparable.
"""

from circlechiffon.ratingcalc.best50 import Best50Result, calculate_best50
from circlechiffon.songdata.catalog import SongCatalog
from circlechiffon.types import (
    ChartType,
    ComboFlag,
    Difficulty,
    MissionEntry,
    MusicCountEntry,
    Profile,
    ProfileExtras,
    Score,
    SyncFlag,
    TicketEntry,
)

SAMPLE_NAME = "ＣｉＲＣＬＥ"
SAMPLE_RATING = 15432

_ACHIEVEMENTS = [100.6512, 100.5, 100.4213, 100.2871, 100.0512, 99.8765, 99.5123, 99.0021, 98.7654, 97.5]
_COMBOS = [ComboFlag.app, ComboFlag.ap, ComboFlag.fcp, ComboFlag.fc, None, None]
_SYNCS = [SyncFlag.fsdp, SyncFlag.fsd, SyncFlag.fsp, None, SyncFlag.sync, None]


def sample_best50(catalog: SongCatalog) -> Best50Result:
    """Picks 60 MASTER/Re:MASTER charts (a mix of current and older
    versions, so both buckets fill) and scores them with a spread of
    achievements and combo/sync flags."""
    new_versions = {v for v in (catalog.current_version, catalog.previous_version) if v is not None}
    new_scores: list[Score] = []
    old_scores: list[Score] = []
    for song in catalog.songs:
        for sheet in song.sheets:
            if sheet.type == ChartType.utage or sheet.difficulty not in (Difficulty.master, Difficulty.remaster):
                continue
            if sheet.internal_level_value is None or not 13.0 <= sheet.internal_level_value <= 14.9:
                continue
            bucket = new_scores if sheet.version in new_versions else old_scores
            if len(bucket) >= (20 if bucket is new_scores else 40):
                continue
            i = len(new_scores) + len(old_scores)
            bucket.append(
                Score(
                    title=song.title,
                    difficulty=sheet.difficulty,
                    chart_type=sheet.type,
                    achievement=_ACHIEVEMENTS[i % len(_ACHIEVEMENTS)],
                    combo_flag=_COMBOS[i % len(_COMBOS)],
                    sync_flag=_SYNCS[i % len(_SYNCS)],
                )
            )
            break  # one chart per song
    return calculate_best50(new_scores + old_scores, catalog)


def sample_profile() -> Profile:
    counts = []
    for tag, earned in (("sssp", 312), ("sss", 845), ("ssp", 1203), ("ss", 1411), ("sp", 1502), ("s", 1580)):
        counts.append(MusicCountEntry(category="rank", tag=tag, earned=earned, total=4104))
    counts.append(MusicCountEntry(category="clear", tag="clear", earned=1702, total=4104))
    for tag, earned in (("5", 21), ("4", 143), ("3", 402), ("2", 788), ("1", 1110)):
        counts.append(MusicCountEntry(category="dxstar", tag=tag, earned=earned, total=4104))
    for tag, earned in (("app", 88), ("ap", 301), ("fcp", 690), ("fc", 1022)):
        counts.append(MusicCountEntry(category="combo", tag=tag, earned=earned, total=4104))
    for tag, earned in (("fdxp", 12), ("fdx", 40), ("fsp", 130), ("fs", 260), ("sync", 1300)):
        counts.append(MusicCountEntry(category="sync", tag=tag, earned=earned, total=4104))
    return Profile(
        display_name=SAMPLE_NAME,
        rating=SAMPLE_RATING,
        title="CiRCLE Chiffon Sample Title",
        title_tier="Rainbow",
        current_version_plays=512,
        total_plays=3456,
        star_count=1234,
        music_counts=counts,
    )


def sample_profile_extras(mission_count: int = 5, ticket_count: int = 3) -> ProfileExtras:
    missions = [
        MissionEntry(text=f"Play {i + 3} tracks at MASTER or above", mile_reward=100 * (i + 1), cleared=i < 2)
        for i in range(mission_count)
    ]
    tickets = [TicketEntry(name=f"Sample Ticket {i + 1}", count=i + 1) for i in range(ticket_count)]
    return ProfileExtras(
        cp_current=27,
        cp_required=10,
        mile_count=12345,
        mission_deadline_text="Until 2026/10/01 04:59",
        mission_clear_count=2,
        mission_total_count=mission_count,
        missions=missions,
        tickets=tickets,
        intimate_count=42,
    )
