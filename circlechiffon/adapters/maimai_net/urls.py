# we're only doing intl, no jp version
INTL = {
    "LOGIN_PAGE": (
        "https://lng-tgk-aime-gw.am-all.net/common_auth/login"
        "?site_id=maimaidxex"
        "&redirect_url=https://maimaidx-eng.com/maimai-mobile/"
        "&back_url=https://maimai.sega.com/"
    ),
    "LOGIN_ENDPOINT": "https://lng-tgk-aime-gw.am-all.net/common_auth/login/sid",
    "LOGIN_OTP_ENDPOINT": "https://lng-tgk-aime-gw.am-all.net/common_auth/login/otpauth",
    "HOME_PAGE": "https://maimaidx-eng.com/maimai-mobile/home/",
    "PLAYER_DATA_PAGE": "https://maimaidx-eng.com/maimai-mobile/playerData/",
    "PHOTO_PAGE": "https://maimaidx-eng.com/maimai-mobile/playerData/photo/",
    "RECORD_RECENT_PAGE": "https://maimaidx-eng.com/maimai-mobile/record",
    "RECORD_MUSICS_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/musicGenre/search/",
    "RECORD_DETAIL_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/playlogDetail/",
    "MUSIC_DETAIL_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/musicDetail/",
    "CIRCLE_PAGE": "https://maimaidx-eng.com/maimai-mobile/circle/",
    "CIRCLE_MEMBER_PAGE": "https://maimaidx-eng.com/maimai-mobile/circle/circleMember/",
    "NAMEPLATE_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/nameplate/",
    "FRAME_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/frame/",
    "TOUR_MEMBER_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/character/",
    "FRIEND_LIST_PAGE": "https://maimaidx-eng.com/maimai-mobile/friend/",
    # confirmed live: paginates at 10/page - caller appends
    # "?type=next&idx={page-1}" to fetch `page` directly (stateless from URL
    # params alone, no need to crawl pages sequentially).
    "FRIEND_LIST_PAGES_ENDPOINT": "https://maimaidx-eng.com/maimai-mobile/friend/pages/",
    "FRIEND_DETAIL_PAGE": "https://maimaidx-eng.com/maimai-mobile/friend/friendDetail/",
    # confirmed live: scoreType=2 is the achievement view (scoreType=1 shows
    # a DX-star icon instead but never the raw DX score number - not useful
    # here); genre=99 is "All genre" in one page; diff is appended by caller
    # from FRIEND_SCORE_DIFF_VALUES.
    "FRIEND_SCORE_PAGE": "https://maimaidx-eng.com/maimai-mobile/friend/friendGenreVs/battleStart/?scoreType=2&genre=99",
}

IMG_BASE = "https://maimaidx-eng.com/maimai-mobile/img/"

# .trophy_block's title banner is a stylesheet-level `background-image`
# rather than an <img>, so it can't be scraped out of the page markup -
# but its filename is a pure function of the tier modifier class on that
# block. Verified live: normal/bronze/silver/gold/rainbow all resolve to
# 268x25 PNGs.
_TROPHY_TIERS = {"normal", "bronze", "silver", "gold", "rainbow"}


def trophy_plate_url(title_tier: str | None) -> str | None:
    if not title_tier:
        return None
    tier = title_tier.strip().lower()
    if tier not in _TROPHY_TIERS:
        return None
    return f"{IMG_BASE}trophy_{tier}.png"


# Matched on the *path* of a resolved Location header rather than by exact
# equality against a single absolute URL. Confirmed live: a stale/garbage `userId`
# cookie on /playerData/ 302s to exactly
# "https://maimaidx-eng.com/maimai-mobile/error/" with no query string, so
# the old exact-match did work for plain expiry - but it silently missed any
# variant (a "?errorCode=..." query, a host-relative "/maimai-mobile/error/",
# an http:// scheme), and a miss doesn't raise: _get_page just hands the
# empty 302 body to a parser that never raises, so the command returns an
# empty list instead of an error.
ERROR_PAGE_PATH = "/maimai-mobile/error"

# Confirmed live: a request carrying no session cookie at all 302s to the
# bare landing page, NOT to /error/ - so this needs its own check or it falls
# into the same silent-empty-parse hole described above.
LANDING_PAGE_PATHS = ("/maimai-mobile", "/maimai-mobile/")

# Confirmed live against https://maimaidx-eng.com/maimai-mobile/error/ - the
# page is served as a 200 (not a redirect), and renders:
#     <img src=".../img/title_error.png" class="title m_10"/>
#     <div class="p_5 f_14 ">ERROR CODE：100001</div>
#     <div class="p_5 f_12 gray break">An error occured. <br>Please login again.</div>
# Both markers are needed: `title_error.png` catches the page even if SEGA
# reworded the body, "ERROR CODE" catches it even if the image were renamed.
# Keep this list narrow - it's scanned against every 200 body, so a loose
# token would false-positive on a real score page.
ERROR_PAGE_MARKERS = [
    "title_error.png",
    "ERROR CODE",
]

# Which error code the page renders is decided server-side from the cookie
# sent *with the request for the error page itself*; passing ?errorCode=NNNNNN
# does nothing (verified across 100001-100007 / 200001-200005 / 300001-300002,
# all of which rendered 100001).
#
# Confirmed live, two distinct codes:
#   100001  "An error occured. Please login again."
#           what a request carrying no session cookie at all gets. There is
#           nothing to recover, so this is raised immediately, unretried.
#   200002  "The connection time has been expired. Please try again later."
#           what a request carrying a cookie DX NET won't accept gets. This
#           is the one the retry path exists for - see
#           SESSION_SUSPECT_ERROR_CODES below for why it's ambiguous.
SESSION_EXPIRED_ERROR_CODES = {"100001"}

SESSION_EXPIRED_ERROR_STRINGS = [
    "Please login again",
    "please login again",
    "log in again",
]

# 200002 is genuinely ambiguous and can't be classified from the page alone:
# a live session that DX NET has wedged (the case a browser user clears by
# refreshing, or backing out and re-entering) reports it, and so does a
# session that is simply dead. The two are only distinguishable by outcome -
# so it's retried like any transient error, and if it still hasn't cleared
# after the last attempt, _get_page converts it to SessionExpired. That way a
# wedged session recovers silently, while a dead one still reaches the
# /cc-login path (or with_client's silent re-login) instead of being reported
# as "busy" forever.
SESSION_SUSPECT_ERROR_CODES = {"200002"}

# Matched against the error page body. "try again later" is the load-bearing
# one - it catches SEGA's actual wording, "The connection time has been
# expired. Please try again later." (note "has been", not "has"). The other
# spellings are kept as cheap insurance against a rewording.
TRANSIENT_ERROR_STRINGS = [
    "connection time has been expired",
    "Connection time has expired",
    "try again later",
]


def is_error_page_url(url) -> bool:
    """True if a resolved redirect target points at maimai DX NET's error
    page. Takes an httpx.URL (or anything with a `.path`)."""
    return str(getattr(url, "path", url)).rstrip("/").endswith(ERROR_PAGE_PATH)


def is_landing_page_url(url) -> bool:
    """True if a resolved redirect target is the bare /maimai-mobile/ landing
    page, which is where a session-less request gets sent."""
    return str(getattr(url, "path", url)) in LANDING_PAGE_PATHS

MAINTENANCE_STRINGS = [
    "Sorry, servers are under maintenance.",
]

# genre=99 (all) & diff=0..4 for BASIC..Re:MASTER, 10 for UTAGE.
# Listed highest-to-lowest (Re:MASTER first) so concurrent fetches are
# issued - and therefore tend to complete - in that same order.
UTAGE_DIFF_VALUE = 10
MUSIC_RECORD_DIFF_VALUES = [4, 3, 2, 1, 0, UTAGE_DIFF_VALUE]

MUSIC_RECORD_DIFF_LABELS = {
    0: "BASIC",
    1: "ADVANCED",
    2: "EXPERT",
    3: "MASTER",
    4: "Re:MASTER",
    UTAGE_DIFF_VALUE: "UTAGE",
}

# friend/friendGenreVs uses the same 0..4 diff values as record/musicGenre
# (confirmed live), but UTAGE isn't fetched for friends - matches
# get_music_scores(include_utage=False), the same default /cc-best uses.
FRIEND_SCORE_DIFF_VALUES = [4, 3, 2, 1, 0]
