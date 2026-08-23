import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from circlechiffon.ratingcalc.best50 import Best50Result, RatedEntry
from circlechiffon.ratingcalc.calculator import rank_tag_for_achievement
from circlechiffon.types import Difficulty

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
FONT_DIR = ASSETS_DIR / "fonts"

_JP_REGULAR = str(FONT_DIR / "NotoSansJP-Regular.ttf")
_JP_MEDIUM = str(FONT_DIR / "NotoSansJP-Medium.ttf")
_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_INTER_REGULAR = str(FONT_DIR / "Inter_28pt-Regular.ttf")
_INTER_BOLD = str(FONT_DIR / "Inter_28pt-Bold.ttf")

FONT_TITLE = ImageFont.truetype(_JP_BOLD, 15)
FONT_SUBTEXT = ImageFont.truetype(_JP_REGULAR, 13)
FONT_RATING = ImageFont.truetype(_JP_MEDIUM, 14)
FONT_RATING_VALUE = ImageFont.truetype(_INTER_BOLD, 20)
FONT_RANK_BADGE = ImageFont.truetype(_INTER_REGULAR, 12)
FONT_HEADER_NAME = ImageFont.truetype(_JP_BOLD, 34)
FONT_HEADER_STAT_VALUE = ImageFont.truetype(_INTER_BOLD, 30)
FONT_HEADER_STAT_LABEL = ImageFont.truetype(_INTER_REGULAR, 14)
FONT_SECTION_LABEL = ImageFont.truetype(_INTER_BOLD, 16)
FONT_FOOTER = ImageFont.truetype(_INTER_REGULAR, 13)

CANVAS_WIDTH = 1500
GRID_COLS = 5
ROW_HEIGHT = 124
COL_WIDTH = CANVAS_WIDTH // GRID_COLS
CELL_PADDING = 4
CARD_WIDTH = COL_WIDTH - CELL_PADDING * 2
CARD_HEIGHT = ROW_HEIGHT - CELL_PADDING * 2
JACKET_SIZE = 84
B35_ROWS = 7
B15_ROWS = 3

HEADER_HEIGHT = 140
SECTION_HEADER_H = 32  # label + accent bar above each B35/B15 grid
GRID_GAP = 10  # gap between the B35 grid and the B15 section label
FOOTER_HEIGHT = 27

# exact sum of every band below - keeps the footer flush against the
# bottom of the B15 grid with no leftover slop.
CANVAS_HEIGHT = HEADER_HEIGHT + SECTION_HEADER_H + B35_ROWS * ROW_HEIGHT + GRID_GAP + SECTION_HEADER_H + B15_ROWS * ROW_HEIGHT + FOOTER_HEIGHT

BACKGROUND_COLOR = (24, 24, 32)
TEMPLATE_PATH = ASSETS_DIR / "b50" / "template.png"

# shared with renderers/display.py - the gold used for the equipped
# rating badge's digit overlay there, reused here so the standalone
# per-chart rating value (see _render_cell) reads as "the same kind of
# number" across both renderers instead of blending into the achievement
# % text above it.
RATING_ACCENT_COLOR = (255, 221, 51)

_SECTION_OLD_COLOR = (140, 150, 210)  # B35 - older-version bests
_SECTION_NEW_COLOR = (255, 176, 64)  # B15 - current-version bests, called out more

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


_GUIDE_LABEL_FONT = ImageFont.truetype(str(FONT_DIR / "Inter_28pt-Regular.ttf"), 11)


def _draw_guide_box(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str, color: tuple[int, int, int] = (255, 0, 255)
) -> None:
    """Shared by every renderer's `render_*_template()` - a colored outline
    plus a small labeled chip at its top-left corner, marking where a real
    render places some piece of content. Used to generate guide-only PNGs
    (transparent background, no real Pillow-drawn content) for designing a
    matching background/decoration in an external image editor."""
    x0, y0, x1, y1 = box
    draw.rectangle([(x0, y0), (x1, y1)], outline=color, width=2)
    if label:
        label_w = draw.textlength(label, font=_GUIDE_LABEL_FONT)
        draw.rectangle([(x0, y0), (x0 + label_w + 6, y0 + 14)], fill=(0, 0, 0, 200))
        draw.text((x0 + 3, y0 + 1), label, font=_GUIDE_LABEL_FONT, fill=color)


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
_REMASTER_FG = "#951BEF"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _card_palette(difficulty: Difficulty | None) -> dict:
    if difficulty == Difficulty.remaster:
        bg = _hex_to_rgb(_REMASTER_BG)
        return {"bg1": bg, "bg2": _shade(bg, 0.92), "fg": _hex_to_rgb(_REMASTER_FG), "sub_fg": _hex_to_rgb(_REMASTER_FG)}
    base = _hex_to_rgb(_DIFFICULTY_COLOR.get(difficulty, "#666666"))
    return {"bg1": base, "bg2": _shade(base, 0.72), "fg": (255, 255, 255), "sub_fg": (255, 255, 255, 210)}


def _diagonal_gradient(size: tuple[int, int], color1: tuple[int, int, int], color2: tuple[int, int, int]) -> Image.Image:
    w, h = size
    ramp = Image.linear_gradient("L").rotate(45, expand=True, resample=Image.BICUBIC)
    ramp = ramp.resize((w, h))
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


def _paste_rating_badge(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    rating_badge_bytes: bytes | None,
    rating_text: str,
    pos: tuple[int, int],
    height: int,
    text_color: tuple[int, int, int],
) -> int:
    """Pastes the account's equipped rating badge/frame image scaled to
    `height`, then draws `rating_text` stretched to fill the badge's own
    dark digit-strip sub-box - measured pixel ratios of the real
    maimai-DX-NET-provided asset (x 122..281, y 17..67 of a 296x86 image),
    applied as fractions of the *actual* pasted size so the number always
    sits inside the strip regardless of scale. Originally written for
    renderers/display.py's `/cc-display` card; hoisted here so b50's
    header can draw the same rating-badge-with-number. Returns the pasted
    badge width (0 if no badge image)."""
    x, y = pos
    badge_w = 0
    if rating_badge_bytes:
        try:
            with Image.open(io.BytesIO(rating_badge_bytes)) as pill_src:
                pill = _scale_to_height(pill_src.convert("RGBA"), height)
                image.paste(pill, (x, y), pill)
                badge_w = pill.width
        except Exception:
            badge_w = 0
    if badge_w:
        num_x = x + round(badge_w * (122 / 296))
        num_y = y + round(height * (17 / 86))
        num_w = round(badge_w * (159 / 296))
        num_h = round(height * (50 / 86))
        font = _fit_font(draw, rating_text, _INTER_BOLD, 10_000, num_h)
        bbox = draw.textbbox((0, 0), rating_text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        layer = Image.new("RGBA", (max(text_w, 1), max(text_h, 1)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((-bbox[0], -bbox[1]), rating_text, font=font, fill=text_color)
        stretch_w = max(text_w, round(num_w * 0.95))
        layer = layer.resize((stretch_w, text_h), Image.Resampling.LANCZOS)
        image.paste(layer, (num_x + (num_w - stretch_w) // 2, num_y + (num_h - text_h) // 2), layer)
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


def _render_cell(
    base: Image.Image,
    entry: RatedEntry | None,
    x: int,
    y: int,
    jacket_bytes: bytes | None,
    rank_in_section: int,
    badge_icons: dict[str, bytes],
) -> None:
    card_pos = (x + CELL_PADDING, y + CELL_PADDING)

    if entry is None:
        mask = _rounded_mask((CARD_WIDTH, CARD_HEIGHT), 10)
        card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (40, 40, 50))
        base.paste(card, card_pos, mask)
        return

    palette = _card_palette(entry.sheet.difficulty)
    card = _diagonal_gradient((CARD_WIDTH, CARD_HEIGHT), palette["bg1"], palette["bg2"]).convert("RGBA")
    mask = _rounded_mask((CARD_WIDTH, CARD_HEIGHT), 10)
    base.paste(card, card_pos, mask)

    draw = ImageDraw.Draw(base)
    fg = palette["fg"]
    text_x = card_pos[0] + 8
    text_max_width = CARD_WIDTH - JACKET_SIZE - 20

    # jacket, top-right, falls back to a solid dark placeholder square
    jacket_pos = (card_pos[0] + CARD_WIDTH - JACKET_SIZE - 4, card_pos[1] + 4)
    if jacket_bytes:
        try:
            with Image.open(io.BytesIO(jacket_bytes)) as jacket:
                jacket = jacket.convert("RGB").resize((JACKET_SIZE, JACKET_SIZE), Image.Resampling.LANCZOS)
                jmask = _rounded_mask((JACKET_SIZE, JACKET_SIZE), 6)
                base.paste(jacket, jacket_pos, jmask)
        except Exception:
            jacket_bytes = None
    if not jacket_bytes:
        placeholder = Image.new("RGB", (JACKET_SIZE, JACKET_SIZE), (15, 15, 20))
        jmask = _rounded_mask((JACKET_SIZE, JACKET_SIZE), 6)
        base.paste(placeholder, jacket_pos, jmask)

    title = _truncate_to_width(draw, entry.score.title, FONT_TITLE, text_max_width)
    draw.text((text_x, card_pos[1] + 8), title, font=FONT_TITLE, fill=fg)

    diff_name = entry.sheet.difficulty.display_name if entry.sheet.difficulty else "?"
    type_name = entry.sheet.type.value.upper() if entry.sheet.type else "?"
    draw.text(
        (text_x, card_pos[1] + 28),
        f"{diff_name} [{type_name}] {entry.sheet.level or ''}",
        font=FONT_SUBTEXT,
        fill=fg,
    )

    draw.text(
        (text_x, card_pos[1] + 46),
        f"{entry.score.achievement:.4f}%",
        font=FONT_RATING,
        fill=fg,
    )

    # icon row - shifted a few px further from the achievement % line
    # above it (was flush at +62, only ~2px clearance from that text)
    icon_x = text_x
    icon_y = card_pos[1] + 66
    rank_tag = rank_tag_for_achievement(entry.score.achievement)
    used = _paste_icon(base, badge_icons.get(f"rank:{rank_tag}"), (icon_x, icon_y), 20)
    icon_x += used + (4 if used else 0)
    if entry.score.combo_flag is not None:
        used = _paste_icon(base, badge_icons.get(f"combo:{entry.score.combo_flag.value}"), (icon_x, icon_y + 2), 16)
        icon_x += used + (4 if used else 0)
    if entry.score.sync_flag is not None:
        _paste_icon(base, badge_icons.get(f"sync:{entry.score.sync_flag.value}"), (icon_x, icon_y + 2), 16)

    # chart rating value - the card's actual contribution to the b50
    # total. Bigger/bolder than the achievement % above it and drawn in
    # the same gold used for the equipped rating badge elsewhere, with a
    # thin dark stroke so it stays legible against every card's gradient
    # (including the light remaster palette) - meant to stand out as the
    # one number that matters most on each card, not blend in.
    draw.text(
        (text_x, card_pos[1] + 90),
        f"{entry.rating}",
        font=FONT_RATING_VALUE,
        fill=RATING_ACCENT_COLOR,
        stroke_width=1,
        stroke_fill=(20, 20, 20),
    )

    rank_badge = f"#{rank_in_section}"
    badge_width = draw.textlength(rank_badge, font=FONT_RANK_BADGE)
    draw.text(
        (card_pos[0] + CARD_WIDTH - badge_width - 6, card_pos[1] + CARD_HEIGHT - 18),
        rank_badge,
        font=FONT_RANK_BADGE,
        fill=fg,
    )


def _render_grid(
    base: Image.Image,
    entries: list[RatedEntry | None],
    jackets_by_title: dict[str, bytes],
    top_y: int,
    badge_icons: dict[str, bytes],
) -> None:
    for i, entry in enumerate(entries):
        col = i % GRID_COLS
        row = i // GRID_COLS
        x = col * COL_WIDTH
        y = top_y + row * ROW_HEIGHT
        jacket_bytes = jackets_by_title.get(entry.score.title) if entry is not None else None
        _render_cell(base, entry, x, y, jacket_bytes, i + 1, badge_icons)


def _render_section_header(draw: ImageDraw.ImageDraw, label: str, top_y: int, color: tuple[int, int, int]) -> None:
    """Colored accent bar + label above a grid - replaces the old single
    1px hairline divider so the B35/older vs B15/current split reads
    clearly at a glance instead of just as whitespace."""
    draw.rectangle([(24, top_y), (CANVAS_WIDTH - 24, top_y + 4)], fill=color)
    draw.text((24, top_y + 10), label, font=FONT_SECTION_LABEL, fill=color)


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
    output,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().
    `badge_icons` (rank/combo/sync PNGs, see adapters/maimai_net/badge_icons.py)
    is optional - missing keys just skip that icon, card still renders.
    `icon_bytes`/`rating_badge_bytes` are optional too and degrade to a
    placeholder / plain text respectively, same as `/cc-display`."""
    badge_icons = badge_icons or {}
    image = _load_base_image()
    draw = ImageDraw.Draw(image)

    # header: profile icon + player name + rating badge on the left,
    # Total/B15/B35 stat blocks right-aligned.
    header_pad = 24
    icon_size = 96
    icon_x, icon_y = header_pad, (HEADER_HEIGHT - icon_size) // 2

    stats = [("Total", result.total_rating), ("B15", result.b15_total), ("B35", result.b35_total)]
    stat_x = CANVAS_WIDTH - 24
    for label, value in reversed(stats):
        value_text = str(value)
        value_w = draw.textlength(value_text, font=FONT_HEADER_STAT_VALUE)
        label_w = draw.textlength(label, font=FONT_HEADER_STAT_LABEL)
        block_w = max(value_w, label_w) + 30
        stat_x -= block_w
        draw.text((stat_x + (block_w - value_w) / 2, 46), value_text, font=FONT_HEADER_STAT_VALUE, fill=(255, 255, 255))
        draw.text((stat_x + (block_w - label_w) / 2, 86), label, font=FONT_HEADER_STAT_LABEL, fill=(180, 180, 190))

    icon_mask = _rounded_mask((icon_size, icon_size), icon_size // 2)
    pasted_icon = False
    if icon_bytes:
        try:
            with Image.open(io.BytesIO(icon_bytes)) as src:
                scaled = _scale_to_height(src.convert("RGBA"), icon_size)
                left = max(0, (scaled.width - icon_size) // 2)
                cropped = scaled.crop((left, 0, left + icon_size, icon_size))
                image.paste(cropped, (icon_x, icon_y), icon_mask)
                pasted_icon = True
        except Exception:
            pasted_icon = False
    if not pasted_icon:
        placeholder = Image.new("RGB", (icon_size, icon_size), (200, 200, 205))
        image.paste(placeholder, (icon_x, icon_y), icon_mask)

    content_x = icon_x + icon_size + 16
    name_max_w = max(40, stat_x - content_x - 16)
    name = _truncate_to_width(draw, player_name, FONT_HEADER_NAME, name_max_w)
    draw.text((content_x, icon_y), name, font=FONT_HEADER_NAME, fill=(255, 255, 255))

    rating_text = str(rating) if rating is not None else "?"
    badge_h = 44
    badge_y = icon_y + 44
    rating_w = _paste_rating_badge(image, draw, rating_badge_bytes, rating_text, (content_x, badge_y), badge_h, RATING_ACCENT_COLOR)
    if rating_w == 0:
        draw.text((content_x, badge_y), f"Rating {rating_text}", font=FONT_HEADER_STAT_LABEL, fill=(200, 200, 205))

    # B35 grid (older-version bests), section-labeled divider, B15 grid
    # (current-version bests) - each grid gets its own accent-colored
    # label band instead of one thin shared hairline.
    b35_label_top = HEADER_HEIGHT
    _render_section_header(draw, "BEST 35 · OLDER VERSIONS", b35_label_top, _SECTION_OLD_COLOR)
    b35_top = b35_label_top + SECTION_HEADER_H
    _render_grid(image, result.b35, jackets_by_title, b35_top, badge_icons)

    b15_label_top = b35_top + B35_ROWS * ROW_HEIGHT + GRID_GAP
    _render_section_header(draw, f"BEST 15 · {b15_version_label}", b15_label_top, _SECTION_NEW_COLOR)
    b15_top = b15_label_top + SECTION_HEADER_H
    _render_grid(image, result.b15, jackets_by_title, b15_top, badge_icons)

    # footer
    footer_y = CANVAS_HEIGHT - FOOTER_HEIGHT
    draw.rectangle([(0, footer_y), (CANVAS_WIDTH, CANVAS_HEIGHT)], fill=(0, 0, 0, 120))
    draw.text((24, footer_y + 6), "Generated by CiRCLE Chiffon - data from maimai DX NET & dxrating.net // cc.etangaming.xyz // etan • etangaming123 • etangamingxyz", font=FONT_FOOTER, fill=(200, 200, 205))

    image.save(output, "PNG", compress_level=3)
    output.seek(0)


def render_b50_template(output) -> None:
    """Synchronous, no live data needed. Renders a transparent-background
    guide PNG at the exact /cc-best canvas size, with labeled outline boxes
    at every position render_b50() actually draws content - meant to be
    opened in an external image editor (Photoshop/GIMP/etc.) to design a
    replacement for assets/b50/template.png (currently just a flat color,
    see BACKGROUND_COLOR) around the real content instead of guessing at
    its layout. Stat/rating badge widths are data-dependent at real
    render time - drawn here at a representative fixed width."""
    image = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    header_pad = 24
    icon_size = 96
    icon_x, icon_y = header_pad, (HEADER_HEIGHT - icon_size) // 2
    _draw_guide_box(draw, (icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), "ICON 96x96")

    content_x = icon_x + icon_size + 16
    _draw_guide_box(draw, (content_x, icon_y, content_x + 350, icon_y + 34), "PLAYER NAME")

    badge_h = 44
    badge_y = icon_y + 44
    _draw_guide_box(draw, (content_x, badge_y, content_x + 150, badge_y + badge_h), "RATING BADGE")

    stat_x = CANVAS_WIDTH - 24
    for label in ("B35", "B15", "Total"):
        block_w = 90
        stat_x -= block_w
        _draw_guide_box(draw, (stat_x, 40, stat_x + block_w, 100), f"STAT: {label}")

    b35_label_top = HEADER_HEIGHT
    _draw_guide_box(draw, (24, b35_label_top, CANVAS_WIDTH - 24, b35_label_top + SECTION_HEADER_H), "SECTION HEADER: BEST 35")
    b35_top = b35_label_top + SECTION_HEADER_H

    b15_label_top = b35_top + B35_ROWS * ROW_HEIGHT + GRID_GAP
    _draw_guide_box(draw, (24, b15_label_top, CANVAS_WIDTH - 24, b15_label_top + SECTION_HEADER_H), "SECTION HEADER: BEST 15")
    b15_top = b15_label_top + SECTION_HEADER_H

    for section_label, top_y, rows in (("B35", b35_top, B35_ROWS), ("B15", b15_top, B15_ROWS)):
        for i in range(GRID_COLS * rows):
            col = i % GRID_COLS
            row = i // GRID_COLS
            x = col * COL_WIDTH
            y = top_y + row * ROW_HEIGHT
            card_pos = (x + CELL_PADDING, y + CELL_PADDING)
            _draw_guide_box(
                draw, (card_pos[0], card_pos[1], card_pos[0] + CARD_WIDTH, card_pos[1] + CARD_HEIGHT), f"{section_label} #{i + 1}"
            )
            # matches _render_cell's own geometry exactly: jacket top-right,
            # text/icons/rating on the left of it.
            jacket_pos = (card_pos[0] + CARD_WIDTH - JACKET_SIZE - 4, card_pos[1] + 4)
            _draw_guide_box(
                draw,
                (jacket_pos[0], jacket_pos[1], jacket_pos[0] + JACKET_SIZE, jacket_pos[1] + JACKET_SIZE),
                "JACKET",
                color=(0, 200, 255),
            )
            # unlabeled outline (the outer card box above already carries
            # the "{section} #{i}" label at this same corner - a second
            # label chip here would just draw over it).
            text_x = card_pos[0] + 8
            _draw_guide_box(draw, (text_x, card_pos[1], jacket_pos[0] - 4, card_pos[1] + CARD_HEIGHT), "", color=(0, 200, 255))

    footer_y = CANVAS_HEIGHT - FOOTER_HEIGHT
    _draw_guide_box(draw, (0, footer_y, CANVAS_WIDTH, CANVAS_HEIGHT), "FOOTER")

    image.save(output, "PNG", compress_level=3)
    output.seek(0)
