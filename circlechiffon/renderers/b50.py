import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from circlechiffon.ratingcalc.best50 import Best50Result, RatedEntry
from circlechiffon.ratingcalc.calculator import rank_tag_for_achievement
from circlechiffon.renderers.layout import LAYOUT_VERSION, Box, Layout, RenderTemplate, apply_top, font, make_base
from circlechiffon.renderers.layout import opacity as _opacity
from circlechiffon.types import Difficulty

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
FONT_DIR = ASSETS_DIR / "fonts"

_JP_REGULAR = str(FONT_DIR / "NotoSansJP-Regular.ttf")
_JP_MEDIUM = str(FONT_DIR / "NotoSansJP-Medium.ttf")
_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_INTER_REGULAR = str(FONT_DIR / "Inter_28pt-Regular.ttf")
_INTER_BOLD = str(FONT_DIR / "Inter_28pt-Bold.ttf")

# Every pixel constant and drawing-offset literal in this module is defined
# at this 1x logical size, then run through S() wherever it's used. Bumping
# SCALE renders the same layout onto a proportionally larger canvas with
# proportionally larger fonts/icons - a supersample for a crisper image
# rather than a naive post-hoc upscale of a small render.
SCALE = 2


def S(value: int | float) -> int:
    return round(value * SCALE)


FONT_RATING = ImageFont.truetype(_JP_MEDIUM, S(20))
FONT_FOOTER = ImageFont.truetype(_INTER_REGULAR, S(13))

# Landscape layout: the two sections sit side by side rather than stacked,
# and 35 / 15 both factor into 5 rows (7 wide and 3 wide respectively), so
# the grids line up top and bottom with no partial row in either.
COL_WIDTH = S(300)
ROW_HEIGHT = S(215)
CELL_PADDING = S(4)
CARD_WIDTH = COL_WIDTH - CELL_PADDING * 2
CARD_HEIGHT = ROW_HEIGHT - CELL_PADDING * 2
JACKET_SIZE = S(90)

B35_COLS, B35_ROWS = 7, 5  # 35 cells
B15_COLS, B15_ROWS = 3, 5  # 15 cells
GRID_ROWS = B35_ROWS  # both sections are the same height

SIDE_MARGIN = S(18)
SECTION_GAP = S(36)  # between the two grid blocks; holds the vertical divider
HEADER_HEIGHT = S(120)  # one row: [icon] [name] [rating badge] ... stats, logo
SECTION_HEADER_H = S(32)  # label + accent bar above each B35/B15 grid
FOOTER_HEIGHT = S(27)
LOGO_HEIGHT = S(78)  # current-version title logo, header top-right

# exact sums of every band - keeps the footer flush against the bottom of
# the grids and the outer margins even, with no leftover slop.
CANVAS_WIDTH = SIDE_MARGIN + B35_COLS * COL_WIDTH + SECTION_GAP + B15_COLS * COL_WIDTH + SIDE_MARGIN
CANVAS_HEIGHT = HEADER_HEIGHT + SECTION_HEADER_H + GRID_ROWS * ROW_HEIGHT + FOOTER_HEIGHT

BACKGROUND_COLOR = (24, 24, 32)
TEMPLATE_PATH = ASSETS_DIR / "b50" / "template.png"

# shared with renderers/display.py - the gold used for the equipped
# rating badge's digit overlay there, reused here so the standalone
# per-chart rating value (see _render_cell) reads as "the same kind of
# number" across both renderers instead of blending into the achievement
# % text above it.
RATING_ACCENT_COLOR = (255, 221, 51)
_RATING_SLOTS = 5  # split-flap digit cells on the rating_base_*.png strip
# rating_base_*.png (296x86) landmarks in source px, for the widened badge
# /cc-display draws (see _paste_rating_badge's `width`). The opaque plate
# is the alpha bbox; the logo and the digit strip are the two pieces of art
# that must keep their aspect, and the plain background columns between
# them are the only thing stretched.
_BADGE_PLATE_BOX = (3, 6, 292, 79)
_BADGE_LOGO_X = (15, 113)
_BADGE_LOGO_INK_H = 43  # y 21..64
_BADGE_LOGO_BOX = (12, 18, 116, 67)  # the ink plus a few px of background to feather
_BADGE_STRIP_BOX = (122, 17, 282, 69)
_BADGE_EDGE_W = 6  # the frame's own left/right border, kept at scale
_BADGE_STRIP_RADIUS = 8
# where the reference card (170x32) centres the logo and the strip, and
# how tall it prints the strip - as fractions of the badge box.
_WIDE_LOGO_CX = 39 / 170
_WIDE_LOGO_CY = 16 / 32
_WIDE_LOGO_H = 23.5 / 32  # ink height
_WIDE_STRIP_CX = 120.5 / 170
_WIDE_STRIP_CY = 15.5 / 32
_WIDE_STRIP_H = 24 / 32

_SECTION_OLD_COLOR = (140, 150, 210)  # B35 - older-version bests
_SECTION_NEW_COLOR = (255, 176, 64)  # B15 - current-version bests, called out more
_DIVIDER_COLOR = (70, 72, 88)  # vertical rule between the two grid blocks


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb

# shared with renderers/display.py and renderers/profile.py - the
# title/trophy banner has no image asset anywhere on the real site (it's
# CSS-styled text), so every renderer that shows it draws a gradient
# capsule colored by this tier lookup instead.
_TIER_COLORS = {
    "Gold": ("#fff6d9", "#f0b93d", "#a8650f"),
    "Silver": ("#f7f7f7", "#c3c3c3", "#7a7a7a"),
    "Bronze": ("#f3ddb8", "#cd8a3f", "#7a4a1e"),
    "Rainbow": ("#ffe3fb", "#b7c9ff", "#7b5ff7"),
    "Normal": ("#eef2ff", "#c9d3f5", "#7c8bbd"),
}


_GUIDE_LABEL_FONT = ImageFont.truetype(str(FONT_DIR / "Inter_28pt-Regular.ttf"), S(11))


def _draw_guide_box(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str, color: tuple[int, int, int] = (255, 0, 255)
) -> None:
    """Shared by every renderer's `render_*_template()` - a colored outline
    plus a small labeled chip at its top-left corner, marking where a real
    render places some piece of content. Used to generate guide-only PNGs
    (transparent background, no real Pillow-drawn content) for designing a
    matching background/decoration in an external image editor."""
    x0, y0, x1, y1 = box
    draw.rectangle([(x0, y0), (x1, y1)], outline=color, width=S(2))
    if label:
        label_w = draw.textlength(label, font=_GUIDE_LABEL_FONT)
        draw.rectangle([(x0, y0), (x0 + label_w + S(6), y0 + S(14))], fill=(0, 0, 0, 200))
        draw.text((x0 + S(3), y0 + S(1)), label, font=_GUIDE_LABEL_FONT, fill=color)


def _load_base_image() -> Image.Image:
    try:
        with Image.open(TEMPLATE_PATH) as template:
            return template.convert("RGB").resize((CANVAS_WIDTH, CANVAS_HEIGHT))
    except (FileNotFoundError, OSError):
        return Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_COLOR)

_DIFFICULTY_COLOR = {
    Difficulty.basic: "#22bb5b",
    Difficulty.advanced: "#fb9c2d",
    Difficulty.expert: "#f64861",
    Difficulty.master: "#9e45e2",
}
_REMASTER_BG = "#EBCFFF"

# chart-type header pill (DX/STD) - loosely matches the real maimai NET
# card's red "でらっくす" (DX) pill vs. the plain "STANDARD" one.
_TYPE_TAG_COLORS = {"dx": (219, 50, 88), "std": (50, 130, 219)}

_RANK_ICON_DIR = ASSETS_DIR / "badge_icons"
_rank_icon_cache: dict[str, bytes | None] = {}


def _load_rank_icon(rank_tag: str) -> bytes | None:
    """rank_tag_for_achievement's lowercase tags ("ss", "sssp", ...) match
    assets/badge_icons/rank_{tag}.png exactly. Cached in-process since the
    same handful of files get re-read across every card in a render."""
    if rank_tag not in _rank_icon_cache:
        try:
            _rank_icon_cache[rank_tag] = (_RANK_ICON_DIR / f"rank_{rank_tag}.png").read_bytes()
        except OSError:
            _rank_icon_cache[rank_tag] = None
    return _rank_icon_cache[rank_tag]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _card_palette(difficulty: Difficulty | None) -> dict:
    if difficulty == Difficulty.remaster:
        bg = _hex_to_rgb(_REMASTER_BG)
        # solid black rather than a difficulty-tint purple - the light
        # lavender background makes any tinted text low-contrast, and a
        # muted grey (not pure black) for the secondary difficulty-name line
        # so it doesn't compete with the main title.
        return {"bg1": bg, "bg2": _shade(bg, 0.92), "fg": (0, 0, 0), "sub_fg": (55, 55, 60)}
    base = _hex_to_rgb(_DIFFICULTY_COLOR.get(difficulty, "#666666"))
    return {"bg1": base, "bg2": _shade(base, 0.72), "fg": (255, 255, 255), "sub_fg": (225, 225, 230)}


def _vertical_gradient(size: tuple[int, int], color1: tuple[int, int, int], color2: tuple[int, int, int]) -> Image.Image:
    """Plain top-to-bottom gradient, color1 to color2. Previously rotated
    45deg and resized to the card's (wide, non-square) size, which produced
    a visible chevron/diamond artifact rather than a smooth diagonal - a
    straight vertical gradient reads as a slight, solid-ish shade instead."""
    w, h = size
    ramp = Image.linear_gradient("L").resize((w, h))
    img1 = Image.new("RGB", size, color1)
    img2 = Image.new("RGB", size, color2)
    return Image.composite(img2, img1, ramp)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text + "..." if text else "..."


def _scale_to_height(img: Image.Image, target_h: int) -> Image.Image:
    """Moved here from renderers/display.py so both renderers share one
    copy - b50.py is already the base module display.py imports its other
    low-level helpers from."""
    w = max(1, round(img.width * target_h / img.height))
    return img.resize((w, target_h), Image.Resampling.LANCZOS)


def _paste_scaled(base: Image.Image, icon_bytes: bytes | None, pos: tuple[int, int], target_height: int) -> int:
    """Pastes an image scaled to `target_height`, preserving aspect ratio,
    top-left anchored at `pos`. Returns the width used (0 if no image).
    Unlike `_paste_icon` below, this doesn't crop to the image's opaque
    bbox first - suited to profile icons/frames rather than badge plaques
    with lots of transparent padding."""
    if not icon_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(icon_bytes)) as img:
            img = _scale_to_height(img.convert("RGBA"), target_height)
            base.paste(img, pos, img)
            return img.width
    except Exception:
        return 0


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: str, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    """Picks the largest font size (from font_path) whose rendered bbox for
    `text` fits within (max_w, max_h) - guarantees text never overflows its
    box regardless of content length or box size."""
    size = max_h
    while size > 6:
        font = ImageFont.truetype(font_path, size)
        w = draw.textlength(text, font=font)
        bbox = draw.textbbox((0, 0), text, font=font)
        h = bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            return font
        size -= 1
    return ImageFont.truetype(font_path, 6)


def _wide_rating_badge(
    src: Image.Image, width: int, height: int
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """Rebuilds a rating_base_*.png plate at `width` x `height` - far wider
    than its own aspect - without flattening any of its art. Returns the
    image and the digit strip's (x, y, w, h) inside it.

    The plate is cut into columns: frame edge, background, logo,
    background, strip, background, frame edge. Every column is scaled to
    `height` uniformly, and the three background bands alone take up the
    extra width, which puts the logo and the strip where the reference
    card has them. The card also prints the strip a touch larger than the
    plate's own scale would, so an aspect-true copy of it is laid over the
    top at that size."""
    k = src.width / 296
    plate = src.crop(tuple(round(v * k) for v in _BADGE_PLATE_BOX))
    s = height / plate.height
    px0 = round(_BADGE_PLATE_BOX[0] * k)
    edge = round(_BADGE_EDGE_W * k)
    logo0, logo1 = (round(v * k) - px0 for v in _BADGE_LOGO_X)
    strip0, strip1 = round(_BADGE_STRIP_BOX[0] * k) - px0, round(_BADGE_STRIP_BOX[2] * k) - px0

    edge_w = max(1, round(edge * s))
    logo_w = round((logo1 - logo0) * s)
    logo_x = round(width * _WIDE_LOGO_CX - logo_w / 2)
    stripcol_w = round((strip1 - strip0) * s)
    stripcol_x = round(width * _WIDE_STRIP_CX - stripcol_w / 2)
    src_cuts = [0, edge, logo0, logo1, strip0, strip1, plate.width - edge, plate.width]
    dst_cuts = [0, edge_w, logo_x, logo_x + logo_w, stripcol_x, stripcol_x + stripcol_w, width - edge_w, width]
    if any(b <= a for a, b in zip(dst_cuts, dst_cuts[1:])):
        # too narrow for the layout - plain aspect-kept plate instead
        pill = _scale_to_height(plate, height)
        return pill, (
            strip0 * pill.width / plate.width,
            (round(_BADGE_STRIP_BOX[1] * k) - round(_BADGE_PLATE_BOX[1] * k)) * s,
            (strip1 - strip0) * pill.width / plate.width,
            (_BADGE_STRIP_BOX[3] - _BADGE_STRIP_BOX[1]) * k * s,
        )

    pill = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for (s0, s1), (d0, d1) in zip(zip(src_cuts, src_cuts[1:]), zip(dst_cuts, dst_cuts[1:])):
        piece = plate.crop((s0, 0, s1, plate.height)).resize((d1 - d0, height), Image.Resampling.LANCZOS)
        pill.paste(piece, (d0, 0))

    # the logo, likewise: aspect-true and card-sized, its background
    # margin feathered into the stretched background underneath.
    logo_src = src.crop(tuple(round(v * k) for v in _BADGE_LOGO_BOX))
    ls = height * _WIDE_LOGO_H / (_BADGE_LOGO_INK_H * k)
    logo = logo_src.resize((round(logo_src.width * ls), round(logo_src.height * ls)), Image.Resampling.LANCZOS)
    feather = max(1, round(3 * k * ls))
    logo_mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(logo_mask).rectangle([(feather, feather), (logo.width - 1 - feather, logo.height - 1 - feather)], fill=255)
    logo_mask = logo_mask.filter(ImageFilter.GaussianBlur(feather / 2))
    pill.paste(
        logo,
        (round(width * _WIDE_LOGO_CX - logo.width / 2), round(height * _WIDE_LOGO_CY - logo.height / 2)),
        logo_mask,
    )

    strip_src = src.crop(tuple(round(v * k) for v in _BADGE_STRIP_BOX))
    strip_h = round(height * _WIDE_STRIP_H)
    strip_w = round(strip_src.width * strip_h / strip_src.height)
    strip = strip_src.resize((strip_w, strip_h), Image.Resampling.LANCZOS)
    strip_x = round(width * _WIDE_STRIP_CX - strip_w / 2)
    strip_y = round(height * _WIDE_STRIP_CY - strip_h / 2)
    mask = _rounded_mask((strip_w, strip_h), max(1, round(_BADGE_STRIP_RADIUS * k * strip_h / strip_src.height)))
    pill.paste(strip, (strip_x, strip_y), mask)
    return pill, (strip_x, strip_y, strip_w, strip_h)


def _paste_rating_badge(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    rating_badge_bytes: bytes | None,
    rating_text: str,
    pos: tuple[int, int],
    height: int,
    text_color: tuple[int, int, int],
    width: int | None = None,
) -> int:
    """Pastes the account's equipped rating badge/frame image scaled to
    `height`, then draws `rating_text` into the badge's own dark digit
    strip. Returns the pasted badge width (0 if no badge image).

    The strip is five split-flap slots - measured off the real
    rating_base_*.png assets (296x86): x 123..281, y 17..68, separators at
    ~155/186/217/249, i.e. five equal cells. Every digit gets one cell,
    right-aligned so a 4-digit rating leaves the first slot empty the way
    the game does, and all digits share one font size so a narrow "1"
    doesn't render bigger than its neighbours. Measured as fractions of
    the *actual* pasted size, so it holds at any scale. Anything that
    isn't 1-5 digits (e.g. "?") falls back to one centred run across the
    whole strip.

    With `width`, the badge's *opaque plate* fills exactly `width` x
    `height` instead - /cc-display's reference card prints it much wider
    than its native aspect. Nothing is squashed to get there: the logo and the digit strip keep their
    aspect and only the plain background between them is stretched (see
    _wide_rating_badge)."""
    x, y = pos
    badge_w = 0
    num_box = (0.0, 0.0, 0.0, 0.0)  # digit strip x, y, w, h relative to pos
    if rating_badge_bytes:
        try:
            with Image.open(io.BytesIO(rating_badge_bytes)) as pill_src:
                pill_src = pill_src.convert("RGBA")
                if width is not None:
                    pill, num_box = _wide_rating_badge(pill_src, width, height)
                else:
                    pill = _scale_to_height(pill_src, height)
                    num_box = (
                        pill.width * (123 / 296),
                        height * (17 / 86),
                        pill.width * (158 / 296),
                        height * (51 / 86),
                    )
                image.paste(pill, (x, y), pill)
                badge_w = pill.width
        except Exception:
            badge_w = 0
    if badge_w:
        num_x, num_y, num_w, num_h = x + num_box[0], y + num_box[1], num_box[2], num_box[3]
        if rating_text.isdigit() and len(rating_text) <= _RATING_SLOTS:
            cell_w = num_w / _RATING_SLOTS
            first_slot = _RATING_SLOTS - len(rating_text)
            # sized by the digit's *ink*, not the font size - _fit_font caps
            # the size at max_h, and a digit's ink is only ~3/4 of that.
            max_ink_w, max_ink_h = cell_w * 0.8, num_h * 0.78
            size = max(6, round(num_h * 1.4))
            while size > 6:
                font = ImageFont.truetype(_INTER_BOLD, size)
                ink = draw.textbbox((0, 0), "0", font=font)
                if ink[2] - ink[0] <= max_ink_w and ink[3] - ink[1] <= max_ink_h:
                    break
                size -= 1
            font = ImageFont.truetype(_INTER_BOLD, size)
            for i, ch in enumerate(rating_text):
                ink = draw.textbbox((0, 0), ch, font=font)
                cell_x = num_x + cell_w * (first_slot + i)
                draw.text(
                    (
                        round(cell_x + (cell_w - (ink[2] - ink[0])) / 2 - ink[0]),
                        round(num_y + (num_h - (ink[3] - ink[1])) / 2 - ink[1]),
                    ),
                    ch,
                    font=font,
                    fill=text_color,
                )
        else:
            font = _fit_font(draw, rating_text, _INTER_BOLD, round(num_w * 0.9), round(num_h * 0.7))
            bbox = draw.textbbox((0, 0), rating_text, font=font)
            draw.text(
                (
                    round(num_x + (num_w - (bbox[2] - bbox[0])) / 2 - bbox[0]),
                    round(num_y + (num_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                ),
                rating_text,
                font=font,
                fill=text_color,
            )
    return badge_w


def _paste_icon(base: Image.Image, icon_bytes: bytes | None, pos: tuple[int, int], target_height: int) -> int:
    """Pastes a badge icon scaled to `target_height`, preserving aspect
    ratio, at `pos` (top-left). Returns the width used (0 if no icon), so
    callers can lay out several icons in a row without hardcoding widths -
    maimai's own rank/combo/sync plaques aren't uniformly sized."""
    if not icon_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(icon_bytes)) as icon:
            icon = icon.convert("RGBA")
            bbox = icon.getbbox()
            if bbox:
                icon = icon.crop(bbox)
            w = round(icon.width * target_height / icon.height)
            icon = icon.resize((w, target_height), Image.Resampling.LANCZOS)
            base.paste(icon, pos, icon)
            return w
    except Exception:
        return 0


def _pad_box(b: Box, pad: int) -> tuple[int, int, int, int]:
    return (b.x - pad, b.y - pad, b.right + pad, b.bottom + pad)


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _draw_text(
    draw: ImageDraw.ImageDraw,
    style: dict,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] = (0, 0, 0),
    anchor: str | None = None,
) -> None:
    """draw.text with the element's `style` (Layout.style) applied over the
    renderer's defaults: the user's colour replaces `fill`, and their
    outline colour/width replace the default stroke (an element with no
    default stroke gets a black one only if the user sets a width)."""
    if style["color"] is not None:
        fill = style["color"]
    if style["outline_color"] is not None:
        stroke_fill = style["outline_color"]
    else:
        # a default stroke that matches the fill erases the text (black
        # text on a remaster card + the default black stroke = a blob), so
        # flip it to the opposite extreme
        if abs(_luma(fill) - _luma(stroke_fill)) < 90:
            stroke_fill = (255, 255, 255) if _luma(fill) < 128 else (0, 0, 0)
    if style["outline_width"] is not None:
        # capped so a shrunk (fit-to-box) font can't be swallowed whole
        stroke_width = max(0, min(round(style["outline_width"]), round(font.size / 6)))
    kwargs = {"stroke_width": stroke_width, "stroke_fill": stroke_fill} if stroke_width > 0 else {}
    draw.text(xy, text, font=font, fill=fill, anchor=anchor, **kwargs)


def default_layout() -> dict:
    """render_b50's stock geometry as a layout dict - see
    renderers/layout.py. `card` is a second, card-relative layout shared by
    all 50 cards; the two grids themselves only move (their size is fixed
    by the column/row constants)."""
    header_pad = S(24)
    icon_size = S(96)
    icon_y = (HEADER_HEIGHT - icon_size) // 2
    name_x = header_pad + icon_size + S(20)
    name_h = S(56)
    badge_h = S(60)
    logo_w = round(LOGO_HEIGHT * 352 / 154)  # live asset is 352x154
    logo_x = CANVAS_WIDTH - header_pad - logo_w
    stat_w, stat_h = S(110), S(64)
    stat_y = (HEADER_HEIGHT - stat_h) // 2
    stat_right = logo_x - S(32)
    badge_w = round(badge_h * 296 / 86)  # rating badge asset is 296x86
    # DX NET display names are at most 8 (often fullwidth) characters, so
    # this fits any of them at full size.
    name_w = S(440)

    label_top = HEADER_HEIGHT
    grid_top = label_top + SECTION_HEADER_H
    b35_x = SIDE_MARGIN
    b15_x = SIDE_MARGIN + B35_COLS * COL_WIDTH + SECTION_GAP
    divider_x = b35_x + B35_COLS * COL_WIDTH + SECTION_GAP // 2
    elements = {
        "icon": {"x": header_pad, "y": icon_y, "w": icon_size, "h": icon_size},
        "name": {"x": name_x, "y": (HEADER_HEIGHT - name_h) // 2, "w": name_w, "h": name_h},
        "rating_badge": {
            "x": name_x + name_w + S(24),
            "y": (HEADER_HEIGHT - badge_h) // 2,
            "w": badge_w,
            "h": badge_h,
            "follow": "name",
            "gap": S(24),
        },
        "stat_total": {"x": stat_right - 3 * stat_w, "y": stat_y, "w": stat_w, "h": stat_h},
        "stat_b15": {"x": stat_right - 2 * stat_w, "y": stat_y, "w": stat_w, "h": stat_h},
        "stat_b35": {"x": stat_right - stat_w, "y": stat_y, "w": stat_w, "h": stat_h},
        "version_logo": {"x": logo_x, "y": (HEADER_HEIGHT - LOGO_HEIGHT) // 2, "w": logo_w, "h": LOGO_HEIGHT},
        "section_b35": {
            "x": b35_x + CELL_PADDING,
            "y": label_top,
            "w": B35_COLS * COL_WIDTH - CELL_PADDING * 2,
            "h": SECTION_HEADER_H,
        },
        "section_b15": {
            "x": b15_x + CELL_PADDING,
            "y": label_top,
            "w": B15_COLS * COL_WIDTH - CELL_PADDING * 2,
            "h": SECTION_HEADER_H,
        },
        "divider": {
            "x": divider_x - S(1),
            "y": label_top,
            "w": S(2),
            "h": SECTION_HEADER_H + GRID_ROWS * ROW_HEIGHT - CELL_PADDING,
        },
        "grid_b35": {"x": b35_x, "y": grid_top, "w": B35_COLS * COL_WIDTH, "h": GRID_ROWS * ROW_HEIGHT},
        "grid_b15": {"x": b15_x, "y": grid_top, "w": B15_COLS * COL_WIDTH, "h": GRID_ROWS * ROW_HEIGHT},
    }

    # card-relative: (0, 0) is the card's own top-left corner.
    left = S(8)
    right = CARD_WIDTH - S(8)
    body_top = S(84)
    mid_x = left + JACKET_SIZE + S(10)
    icon = S(30)
    card = {
        "type_tag": {"x": left, "y": S(6), "w": S(60), "h": S(20)},
        "level_badge": {"x": right - S(80), "y": S(4), "w": S(80), "h": S(26)},
        "title": {"x": left, "y": S(34), "w": right - left, "h": S(20)},
        "card_divider": {"x": left, "y": S(76), "w": right - left, "h": S(2)},
        "jacket": {"x": left, "y": body_top, "w": JACKET_SIZE, "h": JACKET_SIZE},
        "rank_icon": {"x": mid_x, "y": body_top, "w": S(110), "h": S(38)},
        "achievement": {"x": mid_x, "y": body_top + S(46), "w": S(150), "h": S(26)},
        "difficulty_name": {"x": mid_x, "y": body_top + S(74), "w": S(110), "h": S(20)},
        "sync_icon": {"x": right - icon, "y": body_top, "w": icon, "h": icon},
        "combo_icon": {"x": right - icon * 2 - S(6), "y": body_top, "w": icon, "h": icon},
        "rating_value": {"x": right - S(90), "y": CARD_HEIGHT - S(42), "w": S(90), "h": S(40)},
        "rank_number": {"x": left, "y": CARD_HEIGHT - S(20), "w": S(50), "h": S(18)},
        # the gradient behind the card - only its opacity/visibility are
        # used; it always fills the whole card
        "card_bg": {"x": 0, "y": 0, "w": CARD_WIDTH, "h": CARD_HEIGHT},
    }
    for group in (elements, card):
        for element in group.values():
            element.setdefault("visible", True)
    return {
        "version": LAYOUT_VERSION,
        "kind": "b50",
        "canvas": [CANVAS_WIDTH, CANVAS_HEIGHT],
        "elements": elements,
        "card": {"canvas": [CARD_WIDTH, CARD_HEIGHT], "elements": card},
        "colors": {
            "section_old": _rgb_hex(_SECTION_OLD_COLOR),
            "section_new": _rgb_hex(_SECTION_NEW_COLOR),
            "divider": _rgb_hex(_DIVIDER_COLOR),
        },
        "options": {
            # the difficulty-coloured gradient behind each card - turn off
            # to let template art show through.
            "card_background": True,
            # combo badge slides into the sync badge's slot when a chart
            # has no sync flag, instead of leaving a gap.
            "collapse_badges": True,
        },
    }


LABELS = {
    "icon": "Icon",
    "name": "Player name",
    "rating_badge": "Rating badge",
    "stat_total": "Total rating",
    "stat_b15": "B15 total",
    "stat_b35": "B35 total",
    "version_logo": "Version logo",
    "section_b35": "B35 heading",
    "section_b15": "B15 heading",
    "divider": "Divider",
    "grid_b35": "B35 grid",
    "grid_b15": "B15 grid",
}
COLOR_LABELS = {
    "section_old": "Older-versions heading (B35)",
    "section_new": "Current-version heading (B15)",
    "divider": "Divider line",
}

CARD_LABELS = {
    "type_tag": "DX/STD tag",
    "level_badge": "Level",
    "title": "Title",
    "card_divider": "Divider",
    "jacket": "Jacket",
    "rank_icon": "Rank",
    "achievement": "Achievement",
    "difficulty_name": "Difficulty",
    "sync_icon": "Sync badge",
    "combo_icon": "Combo badge",
    "rating_value": "Rating",
    "rank_number": "#",
    "card_bg": "Card background",
}

# elements that draw text, so take color / outline_color / outline_width
# (anything may take opacity). Imported by customisation/validate.py.
TEXT_ELEMENTS = frozenset({"name", "stat_total", "stat_b15", "stat_b35", "section_b35", "section_b15"})
CARD_TEXT_ELEMENTS = frozenset(
    {"type_tag", "level_badge", "title", "difficulty_name", "achievement", "rating_value", "rank_number"}
)


def _render_cell(
    base: Image.Image,
    entry: RatedEntry | None,
    x: int,
    y: int,
    jacket_bytes: bytes | None,
    rank_in_section: int,
    badge_icons: dict[str, bytes],
    layout: Layout,
) -> None:
    card_pos = (x + CELL_PADDING, y + CELL_PADDING)
    card_layout = layout.card
    card_region = (card_pos[0], card_pos[1], card_pos[0] + CARD_WIDTH, card_pos[1] + CARD_HEIGHT)
    draw_background = layout.options.get("card_background", True) and card_layout.visible("card_bg")
    bg_opacity = card_layout.style("card_bg")["opacity"]

    def box(name: str):
        b = card_layout.box(name)
        return None if b is None else Box(card_pos[0] + b.x, card_pos[1] + b.y, b.w, b.h)

    def op(name: str):
        return _opacity(base, card_layout.style(name)["opacity"], card_region)

    def paste_card_bg(card: Image.Image) -> None:
        mask = _rounded_mask((CARD_WIDTH, CARD_HEIGHT), S(10))
        if bg_opacity < 1:
            mask = mask.point(lambda v: round(v * bg_opacity))
        base.paste(card, card_pos, mask)

    if entry is None or entry.score.achievement == 0:
        # None is an unfilled slot; 0% achievement is a padding entry with
        # no real play data - grey the card out and drop title/jacket
        # rather than showing a difficulty-colored card with a fake-looking
        # song.
        if draw_background:
            paste_card_bg(Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (40, 40, 50)))
        if entry is not None:
            draw = ImageDraw.Draw(base)
            draw.text((card_pos[0] + S(8), card_pos[1] + S(8)), "No Chart", font=FONT_RATING, fill=(150, 150, 155))
        return

    palette = _card_palette(entry.sheet.difficulty)
    if draw_background:
        paste_card_bg(_vertical_gradient((CARD_WIDTH, CARD_HEIGHT), palette["bg1"], palette["bg2"]).convert("RGBA"))

    draw = ImageDraw.Draw(base)
    fg = palette["fg"]

    # header row: chart-type pill top-left, internal-level badge top-right -
    # mirrors the maimai NET score card's own header band.
    if b := box("type_tag"):
        with op("type_tag"):
            k = card_layout.scale("type_tag")
            type_name = entry.sheet.type.value.upper() if entry.sheet.type else "?"
            tag_color = _TYPE_TAG_COLORS.get(entry.sheet.type.value if entry.sheet.type else "", (90, 90, 100))
            tag_font = font(_INTER_BOLD, round(S(13) * k))
            tag_pad = round(S(7) * k)
            tag_w = draw.textlength(type_name, font=tag_font) + tag_pad * 2
            draw.rounded_rectangle([(b.x, b.y), (b.x + tag_w, b.y + b.h)], radius=round(S(10) * k), fill=tag_color)
            _draw_text(
                draw, card_layout.style("type_tag"), (b.x + tag_pad, b.y + round(S(3) * k)), type_name, tag_font, (255, 255, 255)
            )

    # right-aligned to its box's right edge, growing leftward with the text
    if b := box("level_badge"):
        with op("level_badge"):
            k = card_layout.scale("level_badge")
            level_value = entry.sheet.internal_level_value
            level_display = f"{level_value:.1f}" if level_value is not None else (entry.sheet.level or "?")
            level_font = font(_INTER_BOLD, round(S(18) * k))
            level_pad = round(S(9) * k)
            level_w = draw.textlength(level_display, font=level_font) + level_pad * 2
            level_left = b.right - level_w
            draw.rounded_rectangle(
                [(level_left, b.y), (b.right, b.bottom)], radius=round(S(6) * k), fill=_shade(palette["bg1"], 0.5)
            )
            _draw_text(
                draw, card_layout.style("level_badge"), (level_left + level_pad, b.y + round(S(4) * k)), level_display, level_font, fg
            )

    # title - sized to fit rather than truncated: _fit_font shrinks the font
    # (down to its own 6px floor) until even a long title fits on one line,
    # instead of ellipsis-cutting it at a fixed size.
    if b := box("title"):
        with op("title"):
            title_font = _fit_font(draw, entry.score.title, _JP_BOLD, b.w, b.h)
            _draw_text(draw, card_layout.style("title"), (b.x, b.y), entry.score.title, title_font, fg)

    if b := box("card_divider"):
        with op("card_divider"):
            draw.line([(b.x, b.y), (b.right, b.y)], fill=_shade(palette["bg1"], 1.3), width=b.h)

    if b := box("jacket"):
        with op("jacket"):
            size = (b.w, b.h)
            jmask = _rounded_mask(size, S(6))
            pasted = False
            if jacket_bytes:
                try:
                    with Image.open(io.BytesIO(jacket_bytes)) as jacket:
                        base.paste(jacket.convert("RGB").resize(size, Image.Resampling.LANCZOS), (b.x, b.y), jmask)
                        pasted = True
                except Exception:
                    pass
            if not pasted:
                base.paste(Image.new("RGB", size, (15, 15, 20)), (b.x, b.y), jmask)

    if b := box("rank_icon"):
        with op("rank_icon"):
            rank_tag = rank_tag_for_achievement(entry.score.achievement)
            _paste_icon(base, _load_rank_icon(rank_tag), (b.x, b.y), b.h)

    # achievement rate, plain fg (white on normal cards, black on remaster's
    # light background via the same palette-driven color the rest of the
    # card's text uses) rather than a separate accent color.
    if b := box("achievement"):
        with op("achievement"):
            achievement_font = card_layout.font(_JP_MEDIUM, S(20), "achievement")
            _draw_text(draw, card_layout.style("achievement"), (b.x, b.y), f"{entry.score.achievement:.4f}%", achievement_font, fg)

    if b := box("difficulty_name"):
        with op("difficulty_name"):
            diff_name = entry.sheet.difficulty.display_name if entry.sheet.difficulty else "?"
            diff_font = card_layout.font(_JP_REGULAR, S(16), "difficulty_name")
            _draw_text(draw, card_layout.style("difficulty_name"), (b.x, b.y), diff_name, diff_font, palette["sub_fg"])

    # combo/sync badges. With collapse_badges, a chart with no sync flag
    # draws its combo badge in the sync slot instead of leaving a gap.
    sync_box, combo_box = box("sync_icon"), box("combo_icon")
    if entry.score.sync_flag is not None and sync_box:
        with op("sync_icon"):
            _paste_icon(base, badge_icons.get(f"sync:{entry.score.sync_flag.value}"), (sync_box.x, sync_box.y), sync_box.h)
    if entry.score.combo_flag is not None and combo_box:
        combo_name = "combo_icon"
        if entry.score.sync_flag is None and sync_box and layout.options.get("collapse_badges", True):
            combo_box = sync_box
        with op(combo_name):
            _paste_icon(base, badge_icons.get(f"combo:{entry.score.combo_flag.value}"), (combo_box.x, combo_box.y), combo_box.h)

    # chart rating value - the card's actual contribution to the b50 total,
    # right-aligned in its box. Bigger/bolder than the achievement % with a
    # thin dark stroke so it stays legible against every card's gradient
    # (including the light remaster palette).
    if b := box("rating_value"):
        with op("rating_value"):
            k = card_layout.scale("rating_value")
            rating_font = font(_INTER_BOLD, round(S(32) * k))
            rating_text = f"{entry.rating}"
            rating_w = draw.textlength(rating_text, font=rating_font)
            _draw_text(
                draw,
                card_layout.style("rating_value"),
                (b.right - rating_w, b.y),
                rating_text,
                rating_font,
                RATING_ACCENT_COLOR,
                stroke_width=max(1, round(S(1) * k)),
                stroke_fill=(20, 20, 20),
            )

    if b := box("rank_number"):
        with op("rank_number"):
            _draw_text(
                draw,
                card_layout.style("rank_number"),
                (b.x, b.y),
                f"#{rank_in_section}",
                card_layout.font(_INTER_REGULAR, S(14), "rank_number"),
                fg,
            )


def _render_grid(
    base: Image.Image,
    entries: list[RatedEntry | None],
    jackets_by_title: dict[str, bytes],
    origin_x: int,
    top_y: int,
    cols: int,
    badge_icons: dict[str, bytes],
    layout: Layout,
) -> None:
    for i, entry in enumerate(entries):
        col = i % cols
        row = i // cols
        x = origin_x + col * COL_WIDTH
        y = top_y + row * ROW_HEIGHT
        jacket_bytes = jackets_by_title.get(entry.score.title) if entry is not None else None
        _render_cell(base, entry, x, y, jacket_bytes, i + 1, badge_icons, layout)


def _render_section_header(
    image: Image.Image, draw: ImageDraw.ImageDraw, layout: Layout, name: str, label: str, color: tuple[int, int, int]
) -> None:
    """Colored accent bar + label above a grid, so the B35/older vs
    B15/current split reads clearly at a glance instead of just as
    whitespace. Each bar sits over its own grid block."""
    box = layout.box(name)
    if box is None:
        return
    style = layout.style(name)
    if style["color"] is not None:
        color = style["color"]
    with _opacity(image, style["opacity"], _pad_box(box, S(20))):
        draw.rectangle([(box.x, box.y), (box.right, box.y + layout.s(S(4), name))], fill=color)
        _draw_text(
            draw, style, (box.x, box.y + layout.s(S(10), name)), label, layout.font(_INTER_BOLD, S(16), name), color
        )


def render_b50(
    *,
    player_name: str,
    rating: int | None = None,
    icon_bytes: bytes | None = None,
    rating_badge_bytes: bytes | None = None,
    result: Best50Result,
    b15_version_label: str = "CURRENT VERSION",
    jackets_by_title: dict[str, bytes],
    badge_icons: dict[str, bytes] | None = None,
    version_logo_bytes: bytes | None = None,
    output,
    template: RenderTemplate | None = None,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().
    `badge_icons` (rank/combo/sync PNGs, see adapters/maimai_net/badge_icons.py)
    is optional - missing keys just skip that icon, card still renders.
    `icon_bytes`/`rating_badge_bytes` are optional too and degrade to a
    placeholder / plain text respectively, same as `/cc-display`.
    `version_logo_bytes` is the current game version's title logo (see
    adapters/maimai_site/version_logo.py) - omitted entirely when None.
    `template` is the caller's custom base/top/layout, if they have one."""
    badge_icons = badge_icons or {}
    layout = Layout.build(default_layout(), template.layout if template else None, CANVAS_WIDTH)
    if template and template.base:
        image = make_base(template, (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_COLOR, stretch=True)
    else:
        image = _load_base_image()
    draw = ImageDraw.Draw(image)

    # header, one row: [icon] [name] [rating badge] on the left, Total/B15/
    # B35 stat blocks and the current-version title logo on the right.
    if box := layout.box("version_logo"):
        if version_logo_bytes:
            # right-aligned in its box, so a logo narrower than the stock
            # 352x154 asset still hugs the canvas edge.
            try:
                with Image.open(io.BytesIO(version_logo_bytes)) as logo_src:
                    logo = _scale_to_height(logo_src.convert("RGBA"), box.h)
                with _opacity(image, layout.style("version_logo")["opacity"], _pad_box(box, logo.width)):
                    image.paste(logo, (box.right - logo.width, box.y), logo)
            except Exception:
                pass

    stats = {"stat_total": ("Total", result.total_rating), "stat_b15": ("B15", result.b15_total), "stat_b35": ("B35", result.b35_total)}
    for name, (label, value) in stats.items():
        box = layout.box(name)
        if box is None:
            continue
        style = layout.style(name)
        with _opacity(image, style["opacity"], _pad_box(box, S(20))):
            value_font = layout.font(_INTER_BOLD, S(30), name)
            label_font = layout.font(_INTER_REGULAR, S(14), name)
            value_text = str(value)
            value_w = draw.textlength(value_text, font=value_font)
            label_w = draw.textlength(label, font=label_font)
            _draw_text(draw, style, (box.x + (box.w - value_w) / 2, box.y), value_text, value_font, (255, 255, 255))
            _draw_text(
                draw, style, (box.x + (box.w - label_w) / 2, box.y + layout.s(S(40), name)), label, label_font, (180, 180, 190)
            )

    # slight round, not a full circle - same convention as /cc-display's icon.
    if box := layout.box("icon"):
        icon_size = min(box.w, box.h)
        with _opacity(image, layout.style("icon")["opacity"], _pad_box(box, 0)):
            icon_mask = _rounded_mask((icon_size, icon_size), S(10))
            pasted_icon = False
            if icon_bytes:
                try:
                    with Image.open(io.BytesIO(icon_bytes)) as src:
                        scaled = _scale_to_height(src.convert("RGBA"), icon_size)
                        left = max(0, (scaled.width - icon_size) // 2)
                        cropped = scaled.crop((left, 0, left + icon_size, icon_size))
                        image.paste(cropped, (box.x, box.y), icon_mask)
                        pasted_icon = True
                except Exception:
                    pasted_icon = False
            if not pasted_icon:
                placeholder = Image.new("RGB", (icon_size, icon_size), (200, 200, 205))
                image.paste(placeholder, (box.x, box.y), icon_mask)

    # name vertically centred in its box, truncated to the box width - the
    # rating badge follows wherever the name actually ends.
    if box := layout.box("name"):
        style = layout.style("name")
        with _opacity(image, style["opacity"], _pad_box(box, S(20))):
            name_font = layout.font(_JP_BOLD, S(40), "name")
            name = _truncate_to_width(draw, player_name, name_font, max(S(40), box.w))
            _draw_text(draw, style, (box.x, box.y + box.h / 2), name, name_font, (255, 255, 255), anchor="lm")
        layout.drawn("name", box.x + round(draw.textlength(name, font=name_font)))
    else:
        layout.drawn("name", None)

    if box := layout.box("rating_badge"):
        with _opacity(image, layout.style("rating_badge")["opacity"], _pad_box(box, max(box.w, 4 * box.h))):
            rating_text = str(rating) if rating is not None else "?"
            rating_w = _paste_rating_badge(image, draw, rating_badge_bytes, rating_text, (box.x, box.y), box.h, RATING_ACCENT_COLOR)
            if rating_w == 0:
                fallback_font = layout.font(_INTER_BOLD, S(26), "rating_badge")
                draw.text(
                    (box.x, box.y + box.h / 2), f"Rating {rating_text}", font=fallback_font, fill=RATING_ACCENT_COLOR, anchor="lm"
                )

    # B35 grid (older-version bests) and B15 grid (current-version bests),
    # each under its own accent-colored label band, with a vertical rule
    # between them by default.
    _render_section_header(image, draw, layout, "section_b35", "BEST 35 · OLDER VERSIONS", layout.color("section_old"))
    _render_section_header(image, draw, layout, "section_b15", f"BEST 15 · {b15_version_label}", layout.color("section_new"))
    if box := layout.box("divider"):
        with _opacity(image, layout.style("divider")["opacity"], _pad_box(box, 0)):
            draw.rectangle([(box.x, box.y), (box.right, box.bottom)], fill=layout.color("divider"))
    for name, entries, cols in (("grid_b35", result.b35, B35_COLS), ("grid_b15", result.b15, B15_COLS)):
        if box := layout.box(name):
            _render_grid(image, entries, jackets_by_title, box.x, box.y, cols, badge_icons, layout)

    # footer - fixed, not part of the layout (it carries the credits)
    footer_y = CANVAS_HEIGHT - FOOTER_HEIGHT
    draw.rectangle([(0, footer_y), (CANVAS_WIDTH, CANVAS_HEIGHT)], fill=(0, 0, 0, 120))
    draw.text((S(24), footer_y + S(6)), "Generated by CiRCLE Chiffon - data from maimai DX NET & dxrating.net // cc.etangaming.xyz // etan • etangaming123 • etangamingxyz", font=FONT_FOOTER, fill=(200, 200, 205))

    image = apply_top(image, template, stretch=True)
    image.save(output, "PNG", compress_level=3)
    output.seek(0)
