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
    "RECORD_RECENT_PAGE": "https://maimaidx-eng.com/maimai-mobile/record",
    "RECORD_MUSICS_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/musicGenre/search/",
    "RECORD_DETAIL_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/playlogDetail/",
    "MUSIC_DETAIL_PAGE": "https://maimaidx-eng.com/maimai-mobile/record/musicDetail/",
    "CIRCLE_PAGE": "https://maimaidx-eng.com/maimai-mobile/circle/",
    "CIRCLE_MEMBER_PAGE": "https://maimaidx-eng.com/maimai-mobile/circle/circleMember/",
    "NAMEPLATE_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/nameplate/",
    "FRAME_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/frame/",
    "TOUR_MEMBER_PAGE": "https://maimaidx-eng.com/maimai-mobile/collection/character/",
}

ERROR_PAGES = [
    "https://maimaidx-eng.com/maimai-mobile/error/",
]

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
