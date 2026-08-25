import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser, LexborNode

from circlechiffon.adapters.maimai_net import urls
from circlechiffon.types import (
    ChartType,
    Circle,
    CircleMember,
    ComboFlag,
    Difficulty,
    FriendEntry,
    Judgements,
    MissionEntry,
    MusicCountEntry,
    NoteTypeJudgement,
    Photo,
    Profile,
    ProfileExtras,
    RecentScore,
    Score,
    SongPlayStats,
    SyncFlag,
    TicketEntry,
)

_PLAYED_AT_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")

_TYPE_ICON_RE = re.compile(r"music_(standard|dx)\.png")
_DIFF_ICON_RE = re.compile(r"diff_(.*)\.png")

_TYPE_MAP = {"standard": ChartType.std, "dx": ChartType.dx}

_DIFFICULTY_MAP = {
    "basic": Difficulty.basic,
    "advanced": Difficulty.advanced,
    "expert": Difficulty.expert,
    "master": Difficulty.master,
    "remaster": Difficulty.remaster,
}

# markers confirmed live against real maimai DX NET score-list markup
# (music_icon_<tier>.png) - a prior version of this list guessed wrong
# filenames for the "+" tiers (e.g. "applus.png"/"fcplus.png") that never
# matched anything on the real site, silently dropping AP+/FC+ combo badges
# and FS+/FDX/FDX+ sync badges (only the un-plussed tiers happened to match,
# since e.g. "fc.png" is coincidentally a substring of "music_icon_fc.png").
_FLAG_MATCHERS: list[tuple[str, ComboFlag]] = [
    ("app.png", ComboFlag.app),
    ("ap.png", ComboFlag.ap),
    ("fcp.png", ComboFlag.fcp),
    ("fc.png", ComboFlag.fc),
]

_SYNC_MATCHERS: list[tuple[str, SyncFlag]] = [
    ("fdxp.png", SyncFlag.fsdp),
    ("fdx.png", SyncFlag.fsd),
    ("fsp.png", SyncFlag.fsp),
    ("fs.png", SyncFlag.fs),
    ("sync.png", SyncFlag.sync),
]


def _parse_achievement(text: str | None) -> float:
    if not text:
        return 0.0
    cleaned = text.strip().replace("%", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _classify_type_and_difficulty(type_src: str | None, diff_src: str | None) -> tuple[ChartType | None, Difficulty | None]:
    chart_type = None
    if type_src:
        m = _TYPE_ICON_RE.search(type_src)
        if m:
            chart_type = _TYPE_MAP.get(m.group(1))

    difficulty = None
    if diff_src:
        m = _DIFF_ICON_RE.search(diff_src)
        if m:
            diff_key = m.group(1)
            if diff_key == "utage":
                chart_type = ChartType.utage
            else:
                difficulty = _DIFFICULTY_MAP.get(diff_key)

    return chart_type, difficulty


def _parse_dx_score(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    parts = text.strip().split(" / ")
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0].replace(",", "")), int(parts[1].replace(",", ""))
    except ValueError:
        return None, None


def _parse_played_at(text: str | None) -> datetime | None:
    if not text:
        return None
    m = _PLAYED_AT_RE.search(text)
    if not m:
        return None
    year, month, day, hour, minute = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _extract_combo_flag(node: LexborNode, img_selector: str) -> tuple[ComboFlag | None, SyncFlag | None]:
    combo_flag = None
    sync_flag = None
    for img in node.css(img_selector):
        src = img.attributes.get("src") or ""
        if sync_flag is None:
            for marker, flag in _SYNC_MATCHERS:
                if marker in src:
                    sync_flag = flag
                    break
        for marker, flag in _FLAG_MATCHERS:
            if marker in src:
                combo_flag = flag
                break
    return combo_flag, sync_flag


def parse_recent_records(html: str) -> list[RecentScore]:
    tree = LexborHTMLParser(html)
    results: list[RecentScore] = []

    for record in tree.css(".wrapper > div.p_10"):
        title_node = record.css_first(".basic_block.break")
        title = title_node.text(deep=False, strip=True) if title_node is not None else None

        achievement_node = record.css_first(".playlog_achievement_txt")
        achievement = _parse_achievement(achievement_node.text() if achievement_node else None)

        score_block_node = record.css_first(".playlog_score_block")
        dx_score, dx_score_total = _parse_dx_score(score_block_node.text() if score_block_node else None)

        # unverified for maimai DX NET specifically, degrades to None.
        idx_node = record.css_first("form input[name=idx]")
        idx = idx_node.attributes.get("value") if idx_node is not None else None

        type_icon = record.css_first(".playlog_music_kind_icon")
        diff_icon = record.css_first(".playlog_diff")
        chart_type, difficulty = _classify_type_and_difficulty(
            type_icon.attributes.get("src") if type_icon else None,
            diff_icon.attributes.get("src") if diff_icon else None,
        )

        combo_flag, sync_flag = _extract_combo_flag(record, ".playlog_result_innerblock img.f_l")

        track_no = None
        played_at = None
        subtitles = record.css(".sub_title .v_b")
        if subtitles:
            track_text = subtitles[0].text().replace("TRACK", "").strip()
            try:
                track_no = int(track_text)
            except ValueError:
                track_no = None
        if len(subtitles) > 1:
            played_at = _parse_played_at(subtitles[1].text())

        if not title:
            continue

        results.append(
            RecentScore(
                title=title,
                difficulty=difficulty,
                chart_type=chart_type,
                achievement=achievement,
                dx_score=dx_score,
                dx_score_total=dx_score_total,
                combo_flag=combo_flag,
                sync_flag=sync_flag,
                track_no=track_no,
                played_at=played_at,
                idx=idx,
            )
        )

    return results


def parse_music_records(html: str) -> list[Score]:
    tree = LexborHTMLParser(html)
    results: list[Score] = []

    for record in tree.css(".w_450.m_15.p_r.f_0"):
        title_node = record.css_first(".music_name_block")
        title = title_node.text(strip=True) if title_node is not None else None

        achievement_node = record.css_first(".music_score_block.w_112")
        achievement = _parse_achievement(achievement_node.text() if achievement_node else None)

        score_block_node = record.css_first(".music_score_block.w_190")
        dx_score, dx_score_total = _parse_dx_score(score_block_node.text() if score_block_node else None)

        type_icon = record.css_first(".music_kind_icon")
        diff_icon = record.css_first(".h_20.f_l")
        chart_type, difficulty = _classify_type_and_difficulty(
            type_icon.attributes.get("src") if type_icon else None,
            diff_icon.attributes.get("src") if diff_icon else None,
        )

        combo_flag, sync_flag = _extract_combo_flag(record, "form img.f_r")

        # each row's form submits to musicDetail with this idx - same
        # per-song detail page shown when you click the song on the site.
        idx_node = record.css_first("form input[name=idx]")
        idx = idx_node.attributes.get("value") if idx_node is not None else None

        if not title:
            continue

        results.append(
            Score(
                title=title,
                difficulty=difficulty,
                chart_type=chart_type,
                achievement=achievement,
                dx_score=dx_score,
                dx_score_total=dx_score_total,
                combo_flag=combo_flag,
                sync_flag=sync_flag,
                idx=idx,
            )
        )

    return results


def parse_photos(html: str) -> list[Photo]:
    """Parses playerData/photo/ (the in-game "Album") - confirmed live
    this session. Each photo is one `div.m_10.p_5.f_0` block: title in
    `.black_block`, the "YYYY/MM/DD HH:MM" timestamp in `.block_info`
    (same format `_parse_played_at` already handles), chart type/
    difficulty via the same icon-filename convention as
    `parse_music_records` (`.music_kind_icon` / a diff icon - `h_16.f_l`
    here rather than `h_20.f_l`, but `_classify_type_and_difficulty`
    only cares about the filename), and the venue name in the second
    `.col2`'s `.see_through_block`. The photo itself is `img.w_430`'s
    `src` - skip the block entirely if that's missing, since without an
    image there's nothing to show; every other field degrades to None."""
    tree = LexborHTMLParser(html)
    results: list[Photo] = []

    for block in tree.css(".m_10.p_5.f_0"):
        image_node = block.css_first("img.w_430")
        image_url = image_node.attributes.get("src") if image_node is not None else None
        if not image_url:
            continue

        title_node = block.css_first(".black_block")
        title = title_node.text(strip=True) if title_node is not None else None

        date_node = block.css_first(".block_info")
        played_at = _parse_played_at(date_node.text() if date_node is not None else None)

        type_icon = block.css_first(".music_kind_icon")
        diff_icon = block.css_first(".h_16.f_l")
        chart_type, difficulty = _classify_type_and_difficulty(
            type_icon.attributes.get("src") if type_icon else None,
            diff_icon.attributes.get("src") if diff_icon else None,
        )

        venue_node = block.css_first(".col2.f_r .see_through_block")
        venue = venue_node.text(strip=True) if venue_node is not None else None

        results.append(
            Photo(
                image_url=image_url,
                title=title,
                difficulty=difficulty,
                chart_type=chart_type,
                played_at=played_at,
                venue=venue,
            )
        )

    return results


def parse_song_play_stats(html: str) -> dict[Difficulty, SongPlayStats]:
    """Parses maimai DX NET's musicDetail page (confirmed live: reached via
    the idx captured off a score row in parse_music_records) - one fetch
    covers every difficulty the song has, each in a `div#<difficulty>`
    block with class `music_<difficulty>_score_back`, containing a
    `table.collapse.f_11` with "Last played date：" and "PLAY COUNT：" rows."""
    tree = LexborHTMLParser(html)
    stats: dict[Difficulty, SongPlayStats] = {}
    for difficulty in Difficulty:
        block = tree.css_first(f"#{difficulty.value}")
        if block is None:
            continue
        entry = SongPlayStats()
        for row in block.css("table.collapse.f_11 tr"):
            cells = row.css("td")
            if len(cells) != 2:
                continue
            label = cells[0].text()
            if "PLAY COUNT" in label:
                entry.play_count = _parse_int(cells[1].text())
            elif "Last played date" in label:
                entry.last_played = _parse_played_at(cells[1].text())
        if entry.play_count is not None or entry.last_played is not None:
            stats[difficulty] = entry
    return stats


_PLAY_COUNT_RE = re.compile(r"play count of current version[：:]\s*([\d,]+)")
_TOTAL_PLAY_COUNT_RE = re.compile(r"maimaiDX total play count[：:]\s*([\d,]+)")
_STAR_COUNT_RE = re.compile(r"×\s*([\d,]+)")
_FRACTION_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)")
_CP_RE = re.compile(r"([\d,]+)\s*CP\s*/\s*([\d,]+)\s*CP")
_CLEAR_PROGRESS_RE = re.compile(r"CLEAR\s*(\d+)\s*/\s*(\d+)")
_OWN_COUNT_RE = re.compile(r"You own\s*([\d,]+)")

# on-page filename stem (from .musiccount_img_block img's src, confirmed
# live against a real account's Player's Data page) -> (category, tag) for
# MusicCountEntry - tag matches the on-page filename, not always the same
# spelling as this codebase's own enums (see badge_icons.py's fdx/fdxp note).
_MUSIC_COUNT_STEM_MAP: dict[str, tuple[str, str]] = {
    "music_icon_sssp": ("rank", "sssp"), "music_icon_sss": ("rank", "sss"),
    "music_icon_ssp": ("rank", "ssp"), "music_icon_ss": ("rank", "ss"),
    "music_icon_sp": ("rank", "sp"), "music_icon_s": ("rank", "s"),
    "music_icon_app": ("combo", "app"), "music_icon_ap": ("combo", "ap"),
    "music_icon_fcp": ("combo", "fcp"), "music_icon_fc": ("combo", "fc"),
    "music_icon_fdxp": ("sync", "fdxp"), "music_icon_fdx": ("sync", "fdx"),
    "music_icon_fsp": ("sync", "fsp"), "music_icon_fs": ("sync", "fs"),
    "music_icon_sync": ("sync", "sync"),
    "music_icon_clear": ("clear", "clear"),
    "music_icon_dxstar_1": ("dxstar", "1"), "music_icon_dxstar_2": ("dxstar", "2"),
    "music_icon_dxstar_3": ("dxstar", "3"), "music_icon_dxstar_4": ("dxstar", "4"),
    "music_icon_dxstar_5": ("dxstar", "5"),
}


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _extract_profile_fields(scope: LexborHTMLParser | LexborNode) -> dict:
    """Extracts the profile-card fields shared by every page that renders
    this markup: Player's Data (own profile), and - confirmed live this
    session - the friend list row and friend detail page too (SEGA reuses
    the exact same card partial for all three). `scope` is whatever node
    contains one such card - the whole tree for the own-profile page, or a
    single row/container node for a friend."""
    name_node = scope.css_first(".name_block")
    display_name = name_node.text(strip=True) if name_node is not None else "(unknown)"

    rating = None
    rating_badge_url = None
    rating_node = scope.css_first(".rating_block")
    if rating_node is not None:
        digits = re.sub(r"\D", "", rating_node.text(strip=True))
        if digits:
            rating = int(digits)
        # the rating pill's background (e.g. rating_base_purple.png) is a
        # sibling img right before .rating_block, not baked into any text -
        # its filename already reflects the correct rating-tier color SEGA's
        # own page computed, so no need to re-derive the tier ourselves.
        if rating_node.parent is not None:
            bg_node = rating_node.parent.css_first("img")
            rating_badge_url = bg_node.attributes.get("src") if bg_node is not None else None

    title_node = scope.css_first(".trophy_block .trophy_inner_block")
    title = title_node.text(strip=True) if title_node is not None else None

    title_tier = None
    trophy_block_node = scope.css_first(".trophy_block")
    if trophy_block_node is not None:
        for cls in (trophy_block_node.attributes.get("class") or "").split():
            if cls.startswith("trophy_") and cls != "trophy_block" and cls != "trophy_inner_block":
                title_tier = cls.removeprefix("trophy_")
                break

    # the banner graphic itself is a stylesheet-level `background-image` on
    # .trophy_block (not an inline style, and not an <img>), so it can't be
    # read out of the page markup - but the filename is a pure function of
    # the tier class above, verified live for Normal/Bronze/Silver/Gold/
    # Rainbow (all 268x25 PNGs).
    title_plate_url = urls.trophy_plate_url(title_tier)

    # confirmed live: the player's icon is the first img.w_112.f_l directly
    # inside .basic_block - the previously-guessed .friend_block_icon class
    # doesn't exist anywhere in the real markup.
    icon_node = scope.css_first(".basic_block > img.w_112.f_l")
    icon_url = icon_node.attributes.get("src") if icon_node is not None else None

    # dan (course-rank) and class-rank badges are pure images with
    # hash-coded filenames on the real page - no text/alt anywhere - so
    # these are the only representation available at all.
    course_rank_node = scope.css_first("img.h_35.f_l")
    course_rank_url = course_rank_node.attributes.get("src") if course_rank_node is not None else None
    class_rank_node = scope.css_first("img.p_l_10.h_35.f_l")
    class_rank_url = class_rank_node.attributes.get("src") if class_rank_node is not None else None

    # the star-count div (confirmed live) contains only the icon and its
    # "×NNN" text - safe to read the whole parent's text.
    star_count = None
    star_icon_node = scope.css_first('img[src*="icon_star"]')
    if star_icon_node is not None and star_icon_node.parent is not None:
        m = _STAR_COUNT_RE.search(star_icon_node.parent.text())
        if m:
            star_count = _parse_int(m.group(1))

    return {
        "display_name": display_name,
        "rating": rating,
        "title": title,
        "title_tier": title_tier,
        "title_plate_url": title_plate_url,
        "icon_url": icon_url,
        "course_rank_url": course_rank_url,
        "class_rank_url": class_rank_url,
        "rating_badge_url": rating_badge_url,
        "star_count": star_count,
    }

def parse_profile(html: str) -> Profile:
    """Parses maimai DX NET's Player's Data page (confirmed live against a
    real account this session - same header markup as the home page, plus
    play-count stats home doesn't have)."""
    tree = LexborHTMLParser(html)
    fields = _extract_profile_fields(tree)

    page_text = tree.body.text() if tree.body is not None else ""
    current_version_plays = None
    m = _PLAY_COUNT_RE.search(page_text)
    if m:
        current_version_plays = _parse_int(m.group(1))
    total_plays = None
    m = _TOTAL_PLAY_COUNT_RE.search(page_text)
    if m:
        total_plays = _parse_int(m.group(1))

    music_counts: list[MusicCountEntry] = []
    for block in tree.css(".musiccount_block"):
        img = block.css_first(".musiccount_img_block img")
        stem = _icon_stem(img)
        mapping = _MUSIC_COUNT_STEM_MAP.get(stem or "")
        if mapping is None:
            continue
        category, tag = mapping
        counter_node = block.css_first(".musiccount_counter_block")
        frac = _FRACTION_RE.search(counter_node.text()) if counter_node is not None else None
        earned = _parse_int(frac.group(1)) if frac else None
        total = _parse_int(frac.group(2)) if frac else None
        music_counts.append(MusicCountEntry(category=category, tag=tag, earned=earned, total=total))

    return Profile(
        **fields,
        current_version_plays=current_version_plays,
        total_plays=total_plays,
        music_counts=music_counts,
    )

# confirmed live: every friend row shares this exact class set regardless of
# favorite status - img.friend_favorite_icon (the previous row anchor) only
# exists on rows for friends marked as a SEGA "Favorite", so anchoring on it
# silently dropped every non-favorited friend from the list.
_FRIEND_ROW_CLASSES = {"see_through_block", "p_r", "m_15", "m_t_5", "p_10", "t_l", "f_0"}


def parse_friend_list(html: str) -> list[FriendEntry]:
    """Parses one page of /friend/ (confirmed live this session, including
    against a non-favorited friend's row). Each friend is a row matching
    _FRIEND_ROW_CLASSES, reusing the same profile-card partial
    _extract_profile_fields already handles, plus a hidden idx (the id
    every friend sub-page is addressed by - never shown to the user
    anywhere on SEGA's own UI) and an optional comment. The friend list
    paginates at 10/page - see parse_friend_list_page_count and
    urls.FRIEND_LIST_PAGES_ENDPOINT for fetching the rest."""
    tree = LexborHTMLParser(html)
    entries: list[FriendEntry] = []
    for row in tree.css(".see_through_block"):
        classes = set((row.attributes.get("class") or "").split())
        if not _FRIEND_ROW_CLASSES.issubset(classes):
            continue
        idx_node = row.css_first("input[name=idx]")
        idx = idx_node.attributes.get("value") if idx_node is not None else None
        if not idx:
            continue
        comment_node = row.css_first(".friend_comment_block")
        comment = comment_node.text(strip=True) if comment_node is not None else None
        entries.append(FriendEntry(profile=Profile(**_extract_profile_fields(row)), idx=idx, comment=comment or None))
    return entries


def parse_friend_list_page_count(html: str) -> int:
    """Confirmed live: the friend list's pager is a GET form at
    /friend/pages/ - form[action*="/friend/pages/"] .d_ib.m_5.p_t_10.v_t
    holds the total page count as plain text (e.g. "/5"). Defaults to 1 if
    the pager isn't present at all (an account with a single page of
    friends may not render one - unconfirmed, but "no pager" safely means
    "nothing more to fetch" either way)."""
    tree = LexborHTMLParser(html)
    total_node = tree.css_first('form[action*="/friend/pages/"] .d_ib.m_5.p_t_10.v_t')
    if total_node is None:
        return 1
    return _parse_int(total_node.text(strip=True)) or 1


def parse_friend_detail(html: str, idx: str) -> FriendEntry | None:
    """Parses /friend/friendDetail/?idx=... (confirmed live this session) -
    same profile-card partial as parse_friend_list's rows, rooted at
    .see_through_block, plus a .comment_block (note: different class than
    the list page's .friend_comment_block)."""
    tree = LexborHTMLParser(html)
    scope = tree.css_first(".see_through_block")
    if scope is None:
        return None
    comment_node = scope.css_first(".comment_block")
    comment = comment_node.text(strip=True) if comment_node is not None else None
    return FriendEntry(profile=Profile(**_extract_profile_fields(scope)), idx=idx, comment=comment or None)


# confirmed live this session against the friendGenreVs/battleStart page:
# the friend-side <td> of a score row's second <tr> has 3 <img>s - sync
# status, combo status, achievement-rank letter (the letter isn't parsed,
# Score has no field for it) - using the exact same music_icon_* filename
# stems as _MUSIC_COUNT_STEM_MAP, so that map is reused here rather than
# duplicated; only the tag->enum spelling differs (badge_icons.py's
# fdx/fdxp note). Classified by _icon_stem()+category, not img position,
# since "which slot is which" turned out less reliable to hardcode than
# just reusing the existing stem->category map for every img in the cell.
_SYNC_TAG_TO_FLAG = {
    "sync": SyncFlag.sync,
    "fs": SyncFlag.fs,
    "fsp": SyncFlag.fsp,
    "fdx": SyncFlag.fsd,
    "fdxp": SyncFlag.fsdp,
}
_COMBO_TAG_TO_FLAG = {
    "fc": ComboFlag.fc,
    "fcp": ComboFlag.fcp,
    "ap": ComboFlag.ap,
    "app": ComboFlag.app,
}


def _extract_friend_flags(cell: LexborNode | None) -> tuple[ComboFlag | None, SyncFlag | None]:
    if cell is None:
        return None, None
    combo_flag: ComboFlag | None = None
    sync_flag: SyncFlag | None = None
    for img in cell.css("img"):
        mapping = _MUSIC_COUNT_STEM_MAP.get(_icon_stem(img) or "")
        if mapping is None:
            continue
        category, tag = mapping
        if category == "sync" and sync_flag is None:
            sync_flag = _SYNC_TAG_TO_FLAG.get(tag)
        elif category == "combo" and combo_flag is None:
            combo_flag = _COMBO_TAG_TO_FLAG.get(tag)
    return combo_flag, sync_flag


def parse_friend_scores(html: str, difficulty: Difficulty) -> list[Score]:
    """Parses one difficulty's page of /friend/friendGenreVs/battleStart/
    (confirmed live this session, scoreType=2&genre=99). Rows share
    .main_wrapper.t_c .m_15 with the page's genre-header separator rows
    (class screw_block, skipped - genre itself isn't needed on Score) and
    aren't the same markup as parse_music_records' own-score rows (w_450
    m_15 p_3 f_0 here vs w_450 m_15 p_r f_0 there). Achievement of "0" or
    "― %" means unplayed, skipped. difficulty is passed in rather than
    derived from the row, since the caller already knows which of the 5
    per-difficulty pages it fetched."""
    tree = LexborHTMLParser(html)
    results: list[Score] = []

    for row in tree.css(".main_wrapper.t_c .m_15"):
        classes = set((row.attributes.get("class") or "").split())
        if not {"w_450", "m_15", "p_3", "f_0"}.issubset(classes):
            continue

        title_node = row.css_first(".music_name_block")
        title = title_node.text(strip=True) if title_node is not None else None
        if not title:
            continue

        achievement_node = row.css_first("td.w_120.f_b:last-child")
        achievement_text = achievement_node.text(strip=True) if achievement_node is not None else None
        if not achievement_text or achievement_text in ("0", "― %"):
            continue
        achievement = _parse_achievement(achievement_text)

        type_icon = row.css_first(".music_kind_icon")
        chart_type, _ = _classify_type_and_difficulty(
            type_icon.attributes.get("src") if type_icon else None, None
        )

        second_row_cells = row.css("table tbody tr:last-child td")
        friend_cell = second_row_cells[-1] if second_row_cells else None
        combo_flag, sync_flag = _extract_friend_flags(friend_cell)

        results.append(
            Score(
                title=title,
                difficulty=difficulty,
                chart_type=chart_type,
                achievement=achievement,
                combo_flag=combo_flag,
                sync_flag=sync_flag,
            )
        )

    return results


def parse_profile_extras(html: str) -> ProfileExtras:
    """Parses the Player's Data page's CP/mile/mission/ticket/intimate-item
    section - separate from parse_profile() so /cc-best and /cc-display
    never pay for parsing fields they don't use. Selectors confirmed live
    against a real account's Player's Data page."""
    tree = LexborHTMLParser(html)

    cp_current = cp_required = None
    cp_node = tree.css_first(".class_point_txt")
    if cp_node is not None:
        m = _CP_RE.search(cp_node.text())
        if m:
            cp_current, cp_required = _parse_int(m.group(1)), _parse_int(m.group(2))

    mile_node = tree.css_first(".mile_block")
    mile_count = _parse_int(mile_node.text()) if mile_node is not None else None

    # .mission_block is the whole mission section (not one per mission) -
    # .mission_block_text_date holds the deadline + "CLEAR n/m" spans;
    # individual mission rows are .mission_div, nested a few levels deeper.
    mission_deadline_text = None
    mission_clear_count = mission_total_count = None
    date_block = tree.css_first(".mission_block_text_date")
    if date_block is not None:
        spans = date_block.css("span")
        if spans:
            mission_deadline_text = spans[0].text(strip=True)
        m = _CLEAR_PROGRESS_RE.search(date_block.text())
        if m:
            mission_clear_count, mission_total_count = int(m.group(1)), int(m.group(2))

    missions: list[MissionEntry] = []
    for row in tree.css(".mission_div"):
        mile_reward_node = row.css_first(".mission_mile_text")
        mile_reward = _parse_int(mile_reward_node.text()) if mile_reward_node is not None else None
        # not every mission_div has a .mission_text - confirmed live, a
        # cleared row can be just the mile span + clear-overlay image.
        text_node = row.css_first(".mission_text span")
        # separator=" " because some mission descriptions contain <br> (e.g.
        # "category<br>with RANK SS...") which would otherwise concatenate
        # with no space.
        text = text_node.text(strip=True, separator=" ") if text_node is not None else None
        cleared = row.css_first('img[src*="mission_clear_0"]') is not None
        missions.append(MissionEntry(text=text, mile_reward=mile_reward, cleared=cleared))

    # ticket blocks have no dedicated container class - walk up from each
    # confirmed .ticket_title leaf instead of guessing a wrapper class.
    tickets: list[TicketEntry] = []
    for title_node in tree.css(".ticket_title"):
        container = title_node.parent
        if container is None:
            continue
        name = title_node.text(strip=True)
        img_node = container.css_first("img.ticket_img")
        image_url = img_node.attributes.get("src") if img_node is not None else None
        txt_node = container.css_first(".ticket_txt")
        m = _OWN_COUNT_RE.search(txt_node.text()) if txt_node is not None else None
        count = _parse_int(m.group(1)) if m else None
        tickets.append(TicketEntry(name=name, count=count, image_url=image_url))

    intimate_count = None
    intimate_node = tree.css_first(".intimateup_txt")
    if intimate_node is not None:
        m = _OWN_COUNT_RE.search(intimate_node.text())
        intimate_count = _parse_int(m.group(1)) if m else _parse_int(intimate_node.text())

    return ProfileExtras(
        cp_current=cp_current,
        cp_required=cp_required,
        mile_count=mile_count,
        mission_deadline_text=mission_deadline_text,
        mission_clear_count=mission_clear_count,
        mission_total_count=mission_total_count,
        missions=missions,
        tickets=tickets,
        intimate_count=intimate_count,
    )


# confirmed live: table.playlog_notes_detail has zero text labels anywhere -
# every header is an <img>. Column 1 (top row, <td><img>) identifies the
# judgment tier; each subsequent row's <th><img> identifies the note type
# (tap/hold/slide/touch/break), with 5 <td> integer cells for that note
# type's count in each tier. Kept as a full per-note-type grid (not summed
# into tier totals) - see Judgements' docstring.
_TIER_FILE_TO_FIELD = {
    "criticalperfect": "critical_perfect",
    "perfect": "perfect",
    "great": "great",
    "good": "good",
    "miss": "miss",
}
_NOTE_TYPE_FILE_TO_FIELD = {
    "tap": "tap",
    "hold": "hold",
    "slide": "slide",
    "touch": "touch",
    "break": "brk",
}


def _icon_stem(img) -> str | None:
    src = img.attributes.get("src") if img is not None else None
    if not src:
        return None
    return src.rsplit("/", 1)[-1].split("?")[0].removesuffix(".png").lower()


def parse_recent_score_detail(html: str) -> Judgements | None:
    """Parses a play's detail page for its full per-note-type judgment
    breakdown, plus the Fast/Late timing counts from .playlog_fl_block
    (confirmed live: fast.png/late.png icon filenames identify which is
    which, not position). Returns None if the judgment table isn't found at
    all (e.g. a maimai DX NET markup change), so that surfaces as "no
    detail available" rather than a crash."""
    tree = LexborHTMLParser(html)
    table = tree.css_first("table.playlog_notes_detail")
    if table is None:
        return None

    rows = table.css("tr")
    if not rows:
        return None

    header_cells = rows[0].css("td")
    field_by_col = {}
    for col_index, cell in enumerate(header_cells):
        stem = _icon_stem(cell.css_first("img"))
        field = _TIER_FILE_TO_FIELD.get(stem) if stem else None
        if field:
            field_by_col[col_index] = field

    note_judgements: dict[str, NoteTypeJudgement] = {}
    for row in rows[1:]:
        header = row.css_first("th")
        stem = _icon_stem(header.css_first("img")) if header is not None else None
        note_field = _NOTE_TYPE_FILE_TO_FIELD.get(stem) if stem else None
        if note_field is None:
            continue
        values: dict[str, int] = {}
        for col_index, cell in enumerate(row.css("td")):
            field = field_by_col.get(col_index)
            if field is None:
                continue
            cleaned = cell.text(strip=True).replace(",", "")
            if cleaned.isdigit():
                values[field] = int(cleaned)
        if values:
            note_judgements[note_field] = NoteTypeJudgement(**values)

    if not note_judgements:
        return None

    fast = late = None
    for container in tree.css(".playlog_fl_block .w_96"):
        stem = _icon_stem(container.css_first("img"))
        value_node = container.css_first(".p_t_5")
        if value_node is None:
            continue
        cleaned = value_node.text(strip=True).replace(",", "")
        if not cleaned.isdigit():
            continue
        if stem == "fast":
            fast = int(cleaned)
        elif stem == "late":
            late = int(cleaned)

    return Judgements(
        tap=note_judgements.get("tap"),
        hold=note_judgements.get("hold"),
        slide=note_judgements.get("slide"),
        touch=note_judgements.get("touch"),
        brk=note_judgements.get("brk"),
        fast=fast,
        late=late,
    )


_POINTS_RE = re.compile(r"([\d,]+)\s*PT")
_RANK_RE = re.compile(r"Rank\s*([\d,]+)")


def parse_circle(html: str) -> Circle | None:
    """Parses maimai DX NET's circle profile page. The profile block's
    selectors (circle_profile_circle_name/circle_profile_circle_code/
    circle_profile_user_name/circle_profile_comment/circle_profile_tag*)
    are confirmed live against a real account this session. Total points
    live in .circle_totalpoint_point (e.g. "8,582" + a sibling span with
    "&nbsp;PT" - concatenated text still matches _POINTS_RE), a separate
    block from .circle_pointranking_block, which only holds the "Rank NNN"
    text matched by _RANK_RE - both confirmed live this session."""
    tree = LexborHTMLParser(html)

    name_node = tree.css_first(".circle_profile_circle_name span")
    if name_node is None:
        return None
    name = name_node.text(strip=True)

    code_node = tree.css_first(".circle_profile_circle_code span")
    code = code_node.text(strip=True) if code_node is not None else ""

    leader_node = tree.css_first(".circle_profile_user_name span")
    leader_name = leader_node.text(strip=True) if leader_node is not None else ""

    comment_node = tree.css_first(".circle_profile_comment span")
    comment = comment_node.text(strip=True) if comment_node is not None else None

    tags = []
    for i in (1, 2, 3):
        tag_node = tree.css_first(f".circle_profile_tag{i}.circle_profile_tag_text span")
        if tag_node is not None:
            text = tag_node.text(strip=True)
            if text:
                tags.append(text)

    points_this_month = None
    totalpoint_node = tree.css_first(".circle_totalpoint_point")
    if totalpoint_node is not None:
        m = _POINTS_RE.search(totalpoint_node.text())
        if m:
            points_this_month = _parse_int(m.group(1))

    rank_this_month = None
    pointranking_node = tree.css_first(".circle_pointranking_block")
    if pointranking_node is not None:
        m = _RANK_RE.search(pointranking_node.text())
        if m:
            rank_this_month = _parse_int(m.group(1))

    # .circle_profile_class holds the rank-colored banner the circle's name
    # is printed over on the real page - confirmed live this session
    # (img/circle/profile/circle_profile_color_bronze.png, 300x44).
    color_node = tree.css_first(".circle_profile_class img")
    color_url = color_node.attributes.get("src") if color_node is not None else None

    return Circle(
        name=name,
        code=code,
        leader_name=leader_name,
        comment=comment,
        tags=tags,
        points_this_month=points_this_month,
        rank_this_month=rank_this_month,
        color_url=color_url,
    )


def parse_circle_members(html: str) -> list[CircleMember]:
    """Parses the circle member roster page (circle/circleMember/).
    Confirmed live: each member (leader included) is one .see_through_block
    row containing a .name_block (player name) and a .circle_member_point_block
    (that member's monthly points, e.g. "1,024&nbsp;PT" - reuses the same
    _POINTS_RE the circle profile's own total is scraped with). Rows without
    a .name_block are skipped, and a row missing .circle_member_point_block
    just gets points=None instead of failing the whole page."""
    tree = LexborHTMLParser(html)
    members = []
    for row in tree.css(".see_through_block"):
        name_node = row.css_first(".name_block")
        if name_node is None:
            continue
        name = name_node.text(strip=True)
        if not name:
            continue
        points = None
        points_node = row.css_first(".circle_member_point_block")
        if points_node is not None:
            m = _POINTS_RE.search(points_node.text())
            if m:
                points = _parse_int(m.group(1))
        members.append(CircleMember(name=name, points=points))
    return members


def parse_equipped_collection_image(html: str, selector: str) -> str | None:
    """Parses one of the collection/{nameplate,frame,character} pages for
    the currently-equipped item's image src. Confirmed live: each page's
    "SETTING ..." box at the top shows the equipped item first, as the
    first element matching this selector on the page (nameplate/frame:
    `.w_396.m_r_10`; tour member: `.chara_cycle_img`, where the leader is
    always shown first in "TOUR MEMBER'S FORMATION")."""
    tree = LexborHTMLParser(html)
    node = tree.css_first(selector)
    return node.attributes.get("src") if node is not None else None


def is_maintenance(html: str) -> bool:
    from circlechiffon.adapters.maimai_net.urls import MAINTENANCE_STRINGS

    return any(marker in html for marker in MAINTENANCE_STRINGS)
