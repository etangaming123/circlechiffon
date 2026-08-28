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
    # "trophy_Gold"). The banner itself IS an image asset after all -
    # .trophy_block carries it as a CSS `background-image`
    # (img/trophy_<tier>.png, 268x25) rather than an <img>, which is why
    # an earlier DOM sweep for <img> tags concluded there wasn't one.
    title_tier: str | None = None
    # img/trophy_<tier>.png, derived from title_tier - the real banner
    # graphic, so /cc-display doesn't have to hand-draw a plaque whose
    # colors would drift as SEGA changes tiers.
    title_plate_url: str | None = None
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
class CircleChallengeMember:
    name: str
    rating: int | None = None
    title: str | None = None
    title_tier: str | None = None
    title_plate_url: str | None = None
    icon_url: str | None = None


@dataclass(slots=True, kw_only=True)
class CircleChallenge:
    song_title: str
    category: str | None = None
    note_designer: str | None = None
    jacket_url: str | None = None
    gauge_percent: float | None = None
    achievement_percent: float | None = None
    member: CircleChallengeMember | None = None


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
    # .circle_profile_class's img - the circle's rank-colored name banner
    # (img/circle/profile/circle_profile_color_<color>.png, 300x44). The
    # color varies with the circle's class, so this is scraped rather than
    # assumed.
    color_url: str | None = None


@dataclass(slots=True, kw_only=True)
class Photo:
    """One entry from playerData/photo/ (the in-game "Album" feature) -
    confirmed live this session: the site keeps only the 10 most recent
    photos across the whole account, newest first, with no further
    pagination of its own."""

    image_url: str  # per-account - fetch via client.get_image_bytes
    title: str | None = None
    difficulty: Difficulty | None = None
    chart_type: ChartType | None = None
    played_at: datetime | None = None
    venue: str | None = None


@dataclass(slots=True, kw_only=True)
class CollectionItem:
    """One entry on a collection/{,nameplate,frame,trophy} page.

    `key` is the only part stable enough to store: `idx` is a single-use
    nonce (confirmed live - two fetches of the same page share none of their
    idx values), so a saved preset holds `key` and re-resolves idx at load
    time."""

    key: str
    label: str
    image_url: str | None = None
    idx: str | None = None
