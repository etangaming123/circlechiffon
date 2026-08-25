from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Difficulty(str, Enum):
    basic = "basic"
    advanced = "advanced"
    expert = "expert"
    master = "master"
    remaster = "remaster"

    @property
    def display_name(self) -> str:
        return {
            Difficulty.basic: "BASIC",
            Difficulty.advanced: "ADVANCED",
            Difficulty.expert: "EXPERT",
            Difficulty.master: "MASTER",
            Difficulty.remaster: "Re:MASTER",
        }[self]


class ChartType(str, Enum):
    dx = "dx"
    std = "std"
    utage = "utage"


@dataclass(slots=True, kw_only=True)
class NoteCounts:
    tap: int | None = None
    hold: int | None = None
    slide: int | None = None
    touch: int | None = None
    brk: int | None = None  # "break" is a keyword; dxdata.json calls this field "break"
    total: int | None = None


@dataclass(slots=True, kw_only=True)
class Sheet:
    type: ChartType
    difficulty: Difficulty | None
    level: str | None
    internal_level_value: float | None
    version: str | None = None
    is_special: bool = False
    note_designer: str | None = None
    release_date: str | None = None
    note_counts: NoteCounts | None = None


@dataclass(slots=True, kw_only=True)
class Song:
    song_id: str
    title: str
    artist: str | None = None
    category: str | None = None
    bpm: float | None = None
    image_name: str | None = None
    sheets: list[Sheet] = field(default_factory=list)
    search_acronyms: list[str] = field(default_factory=list)


class ComboFlag(str, Enum):
    fc = "fc"
    fcp = "fcp"
    ap = "ap"
    app = "app"


class SyncFlag(str, Enum):
    sync = "sync"
    fs = "fs"
    fsp = "fsp"
    fsd = "fsd"
    fsdp = "fsdp"

    @property
    def display_name(self) -> str:
        return {
            SyncFlag.sync: "SYNC",
            SyncFlag.fs: "FS",
            SyncFlag.fsp: "FS+",
            SyncFlag.fsd: "FDX",
            SyncFlag.fsdp: "FDX+",
        }[self]


@dataclass(slots=True, kw_only=True)
class NoteTypeJudgement:
    critical_perfect: int | None = None
    perfect: int | None = None
    great: int | None = None
    good: int | None = None
    miss: int | None = None


@dataclass(slots=True, kw_only=True)
class Judgements:
    """Per-note-type judgment breakdown from a play's detail page - confirmed
    live: maimai DX NET's own table has one row per note type (tap/hold/
    slide/touch/break), not a single set of tier totals."""

    tap: NoteTypeJudgement | None = None
    hold: NoteTypeJudgement | None = None
    slide: NoteTypeJudgement | None = None
    touch: NoteTypeJudgement | None = None
    brk: NoteTypeJudgement | None = None  # "break" is a keyword; matches NoteCounts.brk's naming
    fast: int | None = None
    late: int | None = None


@dataclass(slots=True, kw_only=True)
class Score:
    title: str
    difficulty: Difficulty | None
    chart_type: ChartType | None
    achievement: float  # percentage, e.g. 100.5
    dx_score: int | None = None  # achieved
    dx_score_total: int | None = None  # max possible for this chart
    combo_flag: ComboFlag | None = None
    sync_flag: SyncFlag | None = None
    rating: int | None = None
    internal_level_value: float | None = None
    max_combo: int | None = None
    # hidden form value needed to fetch this song/difficulty's own detail
    # page - on Score this is the musicDetail page (record/musicGenre's
    # rows), on RecentScore it's the playlogDetail page for that one play
    idx: str | None = None


@dataclass(slots=True, kw_only=True)
class RecentScore(Score):
    played_at: datetime | None = None
    track_no: int | None = None


@dataclass(slots=True, kw_only=True)
class SongPlayStats:
    """Per-difficulty enrichment from a song's musicDetail page - play count
    and the timestamp of the most recent play on that difficulty."""

    play_count: int | None = None
    last_played: datetime | None = None


@dataclass(slots=True, kw_only=True)
class Profile:
    display_name: str
    rating: int | None = None
    title: str | None = None
    # the tier modifier class on .trophy_block (e.g. "Gold" from
    # "trophy_Gold") - there's no image asset for the title/trophy banner
    # at all (confirmed live via collection/trophy/'s DOM), so this is
    # only used to pick a color scheme for a hand-drawn plaque.
    title_tier: str | None = None
    icon_url: str | None = None
    # course-rank (dan, e.g. 七段) and class-rank (e.g. "A4") badges are pure
    # images on maimai DX NET with hash-coded filenames - no accompanying
    # text/alt anywhere on the page, so there is no string form to parse.
    course_rank_url: str | None = None
    class_rank_url: str | None = None
    rating_badge_url: str | None = None
    current_version_plays: int | None = None
    total_plays: int | None = None
    star_count: int | None = None
    music_counts: list["MusicCountEntry"] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class MusicCountEntry:
    """One row of the Player's Data page's clear-count grid (e.g. "SSS+:
    108/5,999"). `category`/`tag` together form the badge_icons.py lookup
    key f"{category}:{tag}" for the tier's circular icon."""

    category: str  # "rank" | "combo" | "sync" | "clear" | "dxstar"
    tag: str  # e.g. "sssp", "app", "fdx", "clear", "5"
    earned: int | None = None
    total: int | None = None


@dataclass(slots=True, kw_only=True)
class MissionEntry:
    text: str | None
    mile_reward: int | None = None
    cleared: bool = False


@dataclass(slots=True, kw_only=True)
class TicketEntry:
    name: str
    count: int | None = None
    image_url: str | None = None  # per-account - fetch via client.get_image_bytes


@dataclass(slots=True, kw_only=True)
class ProfileExtras:
    """The Player's Data page's CP/mile/mission/ticket/intimate-item
    section - deliberately separate from Profile so /cc-best and
    /cc-display (which only ever need core Profile fields) never pay for
    parsing this."""

    cp_current: int | None = None
    cp_required: int | None = None
    mile_count: int | None = None
    # kept verbatim, not datetime-parsed - includes a localized day-of-week
    # in full-width parens (e.g. "Until Aug 23, 2026（Sun.）")
    mission_deadline_text: str | None = None
    mission_clear_count: int | None = None
    mission_total_count: int | None = None
    missions: list[MissionEntry] = field(default_factory=list)
    tickets: list[TicketEntry] = field(default_factory=list)
    intimate_count: int | None = None


@dataclass(slots=True, kw_only=True)
class FriendEntry:
    """A friend row from /friend/ or /friend/friendDetail/ - confirmed live
    to be the same profile-card markup as Player's Data, so it wraps a
    (partial) Profile rather than duplicating its fields. `idx` is the
    hidden form value every friend sub-page (detail, friendGenreVs) is
    addressed by; SEGA never exposes it as a visible "friend code" anywhere
    on the page, it's just baked into these URLs."""

    profile: Profile
    idx: str
    comment: str | None = None


@dataclass(slots=True, kw_only=True)
class CircleMember:
    name: str
    points: int | None = None


@dataclass(slots=True, kw_only=True)
class Circle:
    name: str
    code: str
    leader_name: str
    comment: str | None = None
    tags: list[str] = field(default_factory=list)
    points_this_month: int | None = None
    rank_this_month: int | None = None
    members: list[CircleMember] = field(default_factory=list)
