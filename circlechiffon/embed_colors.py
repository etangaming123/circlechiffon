"""Embed color coding, keyed off the same rank_tag strings produced by
ratingcalc/calculator.py's rank_tag_for_achievement and the Difficulty enum
in types.py."""

from circlechiffon.types import Difficulty

GENERIC = 0x808080  # generic song info, "no play recorded"
INFO = 0xE67E22  # cc-profile / cc-circle (amber, distinct from S-rank yellow)

_RANK_COLORS = {
    "d": 0x808080, "c": 0x808080,  # D, C - grey
    "b": 0x3498DB, "bb": 0x3498DB, "bbb": 0x3498DB,  # B, BB, BBB - blue
    "a": 0xE74C3C, "aa": 0xE74C3C, "aaa": 0xE74C3C,  # A, AA, AAA - red
    "s": 0xF1C40F, "sp": 0xF1C40F, "ss": 0xF1C40F, "ssp": 0xF1C40F,  # S/S+/SS/SS+ - yellow
    "sss": 0x2ECC71, "sssp": 0x2ECC71,  # SSS, SSS+ - green
}

_DIFFICULTY_COLORS = {
    Difficulty.basic: 0x2ECC71,  # green
    Difficulty.advanced: 0xF1C40F,  # yellow
    Difficulty.expert: 0xE74C3C,  # red
    Difficulty.master: 0x9B59B6,  # purple
    Difficulty.remaster: 0x9B59B6,  # purple
}
_UTAGE_COLOR = 0x9B59B6  # purple, same bucket as Master/Re:MASTER


def rank_color(rank_tag: str) -> int:
    return _RANK_COLORS.get(rank_tag, GENERIC)


def difficulty_color(difficulty: Difficulty | None) -> int:
    if difficulty is None:
        return _UTAGE_COLOR
    return _DIFFICULTY_COLORS.get(difficulty, GENERIC)
