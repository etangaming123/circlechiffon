import io

from PIL import Image, ImageDraw, ImageFont

from circlechiffon.renderers.b50 import (
    FONT_DIR,
    RATING_ACCENT_COLOR,
    _TIER_COLORS,
    _fit_font,
    _hex_to_rgb,
    _paste_icon,
    _paste_rating_badge,
    _pad_box,
    _paste_scaled,
    _rounded_mask,
    _scale_to_height,
    _truncate_to_width,
)
from circlechiffon.renderers.layout import LAYOUT_VERSION, Layout, RenderTemplate, apply_top, font, make_base
from circlechiffon.renderers.layout import opacity as _opacity
from circlechiffon.types import Profile, ProfileExtras

_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_JP_MEDIUM = str(FONT_DIR / "NotoSansJP-Medium.ttf")
_INTER_REGULAR = str(FONT_DIR / "Inter_28pt-Regular.ttf")
_INTER_BOLD = str(FONT_DIR / "Inter_28pt-Bold.ttf")

FONT_FOOTER = ImageFont.truetype(_INTER_REGULAR, 13)
FONT_SECTION_TITLE = ImageFont.truetype(_JP_BOLD, 20)
FONT_BODY = ImageFont.truetype(_JP_MEDIUM, 16)
FONT_BODY_SMALL = ImageFont.truetype(_JP_MEDIUM, 14)
FONT_MILE = ImageFont.truetype(_INTER_BOLD, 16)

BACKGROUND_COLOR = (24, 24, 32)
FOOTER_HEIGHT = 27
FOOTER_TEXT = "CiRCLE Chiffon • cc.etangaming.xyz"

HEADER_HEIGHT_CORE = 174
ICON_SIZE = 84

# render_profile_core is a vertical rectangle, matching the real playerData
# page's own music-count list layout: two side-by-side columns, each a
# vertical stack of rows (colored tier badge + a rounded "pill" holding the
# earned/total count), rather than a flat icon-on-top grid.
CORE_CANVAS_WIDTH = 640
GRID_PAD = 20
GRID_COL_GAP = 16
GRID_ICON_BOX = 56
GRID_ICON_PILL_GAP = 8
GRID_ROW_H = 56
GRID_ROW_GAP = 8
GRID_COLUMN_WIDTH = (CORE_CANVAS_WIDTH - GRID_PAD * 2 - GRID_COL_GAP) // 2

# left column: rank tiers (highest first) + clear + dxstar (5 down to 1) -
# matches the real page's left-hand list top to bottom. Explicit order since
# dxstar tiers aren't contiguous in the page's own DOM order.
_LEFT_ORDER = [
    ("rank", "sssp"), ("rank", "sss"), ("rank", "ssp"), ("rank", "ss"), ("rank", "sp"), ("rank", "s"),
    ("clear", "clear"),
    ("dxstar", "5"), ("dxstar", "4"), ("dxstar", "3"), ("dxstar", "2"), ("dxstar", "1"),
]
# right column: combo tiers then sync tiers - matches the real page's
# right-hand list top to bottom (AP+, AP, FC+, FC, FDX+, FDX, FS+, FS, Sync Play).
_RIGHT_ORDER = [
    ("combo", "app"), ("combo", "ap"), ("combo", "fcp"), ("combo", "fc"),
    ("sync", "fdxp"), ("sync", "fdx"), ("sync", "fsp"), ("sync", "fs"), ("sync", "sync"),
]

GRID_ROWS = max(len(_LEFT_ORDER), len(_RIGHT_ORDER))
GRID_HEIGHT = GRID_ROWS * GRID_ROW_H + (GRID_ROWS - 1) * GRID_ROW_GAP

CANVAS_HEIGHT_CORE = HEADER_HEIGHT_CORE + GRID_HEIGHT + FOOTER_HEIGHT

_PILL_BORDER_COLOR = (14, 118, 178)
_PILL_FILL_COLOR = (72, 191, 238)
_PILL_TEXT_COLOR = (13, 55, 92)

# profile extra's stock colours - the layout-level colour slots default to
# these (see default_extras_layout)
_CP_BAR_FILL = (64, 200, 255)
_CP_BAR_TRACK = (50, 50, 62)
_CP_OVERFLOW_COLOR = (255, 140, 60)
_MISSION_ROW = (38, 38, 48)
_MISSION_ROW_CLEARED = (40, 60, 44)
_MISSION_BORDER = (55, 55, 68)
_MISSION_ACCENT = (255, 221, 51)  # maimille count, cleared check/text, rewards
_MISSION_TEXT = (150, 150, 158)  # pending mission text


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _paste_icon_centered(
    base: Image.Image, icon_bytes: bytes | None, center_x: int, y: int, target_height: int, max_width: int | None = None
) -> int:
    """Like b50._paste_icon, but centers the pasted icon horizontally on
    center_x instead of anchoring its left edge at a fixed position. Badge
    plaques aren't square, so anchoring at `center_x - target_height / 2`
    (assuming pasted width always equals target_height) puts non-square
    icons visibly off-center - this computes the real post-resize width
    first. If `max_width` is given and the height-based width would exceed
    it, the icon is scaled down further (preserving aspect) to fit - some
    badge plaques (e.g. CLEAR) are wide enough that a height-only target
    still overflows a narrow grid cell. Returns the width used (0 if no
    icon)."""
    if not icon_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(icon_bytes)) as icon:
            icon = icon.convert("RGBA")
            bbox = icon.getbbox()
            if bbox:
                icon = icon.crop(bbox)
            w = round(icon.width * target_height / icon.height)
            h = target_height
            if max_width is not None and w > max_width:
                h = round(h * max_width / w)
                w = max_width
            icon = icon.resize((w, h), Image.Resampling.LANCZOS)
            base.paste(icon, (round(center_x - w / 2), y), icon)
            return w
    except Exception:
        return 0


def _draw_footer(image: Image.Image, draw: ImageDraw.ImageDraw, canvas_h: int, canvas_w: int) -> None:
    footer_y = canvas_h - FOOTER_HEIGHT
    draw.rectangle([(0, footer_y), (canvas_w, canvas_h)], fill=(0, 0, 0, 120))
    footer_text = _truncate_to_width(draw, FOOTER_TEXT, FONT_FOOTER, canvas_w - 48)
    draw.text((24, footer_y + 6), footer_text, font=FONT_FOOTER, fill=(200, 200, 205))


def _draw_title_capsule(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    title: str | None,
    title_tier: str | None,
    pos: tuple[int, int],
    box_w: int,
    box_h: int,
) -> None:
    """Hand-drawn gradient capsule, same convention as /cc-display - the
    real site has no image asset for the title/trophy banner at all (it's
    CSS-styled text), so both renderers draw it instead of sourcing one."""
    x, y = pos
    _light_hex, mid_hex, dark_hex = _TIER_COLORS.get(title_tier or "", _TIER_COLORS["Gold"])
    radius = box_h // 2
    draw.rounded_rectangle([(x, y), (x + box_w, y + box_h)], radius=radius, fill=_hex_to_rgb(dark_hex))
    inset = 3
    draw.rounded_rectangle(
        [(x + inset, y + inset), (x + box_w - inset, y + box_h - inset)],
        radius=max(radius - inset, 2),
        fill=_hex_to_rgb(mid_hex),
    )
    if title:
        font = _fit_font(draw, title, _JP_BOLD, box_w - 24, box_h - 12)
        text_w = draw.textlength(title, font=font)
        text = title if text_w <= box_w - 24 else _truncate_to_width(draw, title, font, box_w - 24)
        text_w = draw.textlength(text, font=font)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_h = bbox[3] - bbox[1]
        draw.text(
            (x + (box_w - text_w) / 2, y + (box_h - text_h) / 2 - bbox[1]),
            text,
            font=font,
            fill=_hex_to_rgb(dark_hex),
        )


def _draw_count_pill(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    background: bool = True,
    border_color: tuple[int, int, int] = _PILL_BORDER_COLOR,
    fill_color: tuple[int, int, int] = _PILL_FILL_COLOR,
    text_color: tuple[int, int, int] = _PILL_TEXT_COLOR,
    background_opacity: float = 1.0,
    text_opacity: float = 1.0,
) -> None:
    """Light-blue rounded "pill" holding a right-aligned count, matching the
    real playerData page's own music-count rows (a colored tier badge next
    to a blue capsule, not an icon-over-text grid cell). With
    `background=False` (a custom template's `count_pill_background`
    option) only the count is drawn, white with a dark outline, so it
    reads on whatever the template's own art puts behind it."""
    if background:
        radius = h // 2
        with _opacity(image, background_opacity, (x, y, x + w + 1, y + h + 1)):
            draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=radius, fill=border_color)
            inset = 3
            draw.rounded_rectangle(
                [(x + inset, y + inset), (x + w - inset, y + h - inset)],
                radius=max(radius - inset, 2),
                fill=fill_color,
            )
    text_w = draw.textlength(text, font=font)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    pos = (x + w - 14 - text_w, y + (h - text_h) / 2 - bbox[1])
    text_region = (pos[0] - 3, y, x + w + 1, y + h + 1)
    with _opacity(image, text_opacity, text_region):
        if background:
            draw.text(pos, text, font=font, fill=text_color)
        else:
            draw.text(pos, text, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(20, 20, 20))


# element names for the 21 music-count rows, e.g. "row_combo_ap" - one
# draggable box per row, holding that row's tier icon + count pill.
def _row_name(category: str, tag: str) -> str:
    return f"row_{category}_{tag}"


def default_core_layout() -> dict:
    """render_profile_core's stock geometry as a layout dict - see
    renderers/layout.py. Reproduces the pre-layout render pixel for pixel."""
    pad = GRID_PAD
    right_edge = CORE_CANVAS_WIDTH - pad
    content_x = pad + ICON_SIZE + 16
    row_y = pad + ICON_SIZE + 10
    elements = {
        "icon": {"x": pad, "y": pad, "w": ICON_SIZE, "h": ICON_SIZE},
        "name": {"x": content_x, "y": pad, "w": right_edge - 140 - content_x, "h": 40},
        "play_stats": {"x": right_edge - 140, "y": pad, "w": 140, "h": 30},
        "title_capsule": {"x": content_x, "y": pad + 50, "w": min(300, right_edge - content_x), "h": 30},
        "rating_badge": {"x": pad, "y": row_y, "w": 138, "h": 40},
        "course_rank": {"x": pad + 148, "y": row_y, "w": 34, "h": 34, "follow": "rating_badge", "gap": 10},
        "class_rank": {"x": pad + 190, "y": row_y, "w": 34, "h": 34, "follow": "course_rank", "gap": 8},
        "star_count": {"x": pad + 232, "y": row_y, "w": 100, "h": 34, "follow": "class_rank", "gap": 8},
    }
    for col_x0, order in ((pad, _LEFT_ORDER), (pad + GRID_COLUMN_WIDTH + GRID_COL_GAP, _RIGHT_ORDER)):
        for i, (category, tag) in enumerate(order):
            elements[_row_name(category, tag)] = {
                "x": col_x0,
                "y": HEADER_HEIGHT_CORE + i * (GRID_ROW_H + GRID_ROW_GAP),
                "w": GRID_COLUMN_WIDTH,
                "h": GRID_ROW_H,
            }
    for element in elements.values():
        element.setdefault("visible", True)
    return {
        "version": LAYOUT_VERSION,
        "kind": "profile_core",
        "canvas": [CORE_CANVAS_WIDTH, CANVAS_HEIGHT_CORE],
        "elements": elements,
        "colors": {
            "pill_border": _rgb_hex(_PILL_BORDER_COLOR),
            "pill_fill": _rgb_hex(_PILL_FILL_COLOR),
            "pill_text": _rgb_hex(_PILL_TEXT_COLOR),
        },
        "opacities": {"pill_background": 1.0, "pill_icon": 1.0, "pill_text": 1.0},
        "options": {"count_pill_background": True},
    }


_ROW_LABELS = {
    ("rank", "sssp"): "SSS+", ("rank", "sss"): "SSS", ("rank", "ssp"): "SS+", ("rank", "ss"): "SS",
    ("rank", "sp"): "S+", ("rank", "s"): "S", ("clear", "clear"): "CLEAR",
    ("dxstar", "5"): "DX STAR 5", ("dxstar", "4"): "DX STAR 4", ("dxstar", "3"): "DX STAR 3", ("dxstar", "2"): "DX STAR 2", ("dxstar", "1"): "DX STAR 1",
    ("combo", "app"): "AP+", ("combo", "ap"): "AP", ("combo", "fcp"): "FC+", ("combo", "fc"): "FC",
    ("sync", "fdxp"): "FDX+", ("sync", "fdx"): "FDX", ("sync", "fsp"): "FS+", ("sync", "fs"): "FS", ("sync", "sync"): "SYNC PLAY",
}

CORE_COLOR_LABELS = {
    "pill_border": "Count pill border",
    "pill_fill": "Count pill fill",
    "pill_text": "Count pill text",
}

CORE_OPACITY_LABELS = {
    "pill_background": "Count pill background",
    "pill_icon": "Count tier icon",
    "pill_text": "Count text",
}

CORE_LABELS = {
    "icon": "Icon",
    "name": "Player name",
    "play_stats": "Play counts",
    "title_capsule": "Title",
    "rating_badge": "Rating badge",
    "course_rank": "Course rank",
    "class_rank": "Class rank",
    "star_count": "Star count",
} | {_row_name(c, t): f"Row: {_ROW_LABELS[c, t]}" for c, t in _LEFT_ORDER + _RIGHT_ORDER}


def _paste_square_icon(image: Image.Image, icon_bytes: bytes | None, box, radius: int) -> None:
    """Profile icon, center-cropped square with slightly rounded corners -
    same convention as /cc-display's icon. Grey placeholder if missing."""
    size = min(box.w, box.h)
    icon_mask = _rounded_mask((size, size), radius)
    if icon_bytes:
        try:
            with Image.open(io.BytesIO(icon_bytes)) as src:
                scaled = _scale_to_height(src.convert("RGBA"), size)
                left = max(0, (scaled.width - size) // 2)
                cropped = scaled.crop((left, 0, left + size, size))
                image.paste(cropped, (box.x, box.y), icon_mask)
                return
        except Exception:
            pass
    placeholder = Image.new("RGB", (size, size), (200, 200, 205))
    image.paste(placeholder, (box.x, box.y), icon_mask)


def _draw_core_header(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    layout: Layout,
    *,
    profile: Profile,
    icon_bytes: bytes | None,
    course_rank_bytes: bytes | None,
    class_rank_bytes: bytes | None,
    rating_badge_bytes: bytes | None,
    badge_icons: dict[str, bytes],
    show_play_stats: bool,
) -> None:
    """The header strip of /cc-profile's core view - icon, name, title
    capsule, and the rating/dan/class/star row - shared with
    /cc-friend-profile's card, which has the same fields bar the play
    counts (`show_play_stats=False`). Geometry comes from `layout`."""
    def op(name: str, box, pad: int):
        return _opacity(image, layout.style(name)["opacity"], _pad_box(box, pad))

    if box := layout.box("icon"):
        with op("icon", box, 0):
            _paste_square_icon(image, icon_bytes, box, 10)

    # small right-aligned play-count stats, stacked in their box. Friends
    # don't expose these.
    if show_play_stats and (box := layout.box("play_stats")):
        with op("play_stats", box, 20):
            stats_font = layout.font(_INTER_REGULAR, 11, "play_stats")
            stats = [("Total Plays", profile.total_plays), ("This Version", profile.current_version_plays)]
            stat_y = box.y
            for label, value in stats:
                text = f"{label}: {value:,}" if value is not None else f"{label}: -"
                text_w = draw.textlength(text, font=stats_font)
                draw.text((box.right - text_w, stat_y), text, font=stats_font, fill=(180, 180, 190))
                stat_y += layout.s(15, "play_stats")

    if box := layout.box("name"):
        with op("name", box, 20):
            name_font = layout.font(_JP_BOLD, 34, "name")
            name = _truncate_to_width(draw, profile.display_name, name_font, max(40, box.w))
            draw.text((box.x, box.y), name, font=name_font, fill=(255, 255, 255))

    # title capsule. The name font is 34pt with real ink extending to
    # ~47px below its anchor, so the default capsule box sits 50px under
    # the name's rather than 34.
    if box := layout.box("title_capsule"):
        with op("title_capsule", box, 4):
            _draw_title_capsule(image, draw, profile.title, profile.title_tier, (box.x, box.y), box.w, box.h)

    # rating pill + rank badges + star count - each follows the one before
    # it by default, so a missing badge closes the gap instead of leaving it.
    rating_text = str(profile.rating) if profile.rating is not None else "?"
    if box := layout.box("rating_badge"):
        with op("rating_badge", box, max(box.w, 4 * box.h)):
            rating_w = _paste_rating_badge(image, draw, rating_badge_bytes, rating_text, (box.x, box.y), box.h, RATING_ACCENT_COLOR)
            if rating_w == 0:
                label_font = layout.font(_INTER_REGULAR, 14, "rating_badge")
                label = f"Rating {rating_text}"
                draw.text((box.x, box.y + layout.s(10, "rating_badge")), label, font=label_font, fill=(200, 200, 205))
                rating_w = round(draw.textlength(label, font=label_font))
        layout.drawn("rating_badge", box.x + rating_w)
    else:
        layout.drawn("rating_badge", None)
    for name, rank_bytes in (("course_rank", course_rank_bytes), ("class_rank", class_rank_bytes)):
        box = layout.box(name)
        if box:
            with op(name, box, max(box.w, 4 * box.h)):
                used = _paste_scaled(image, rank_bytes, (box.x, box.y), box.h)
        else:
            used = 0
        layout.drawn(name, box.x + used if used else None)
    if box := layout.box("star_count"):
        with op("star_count", box, max(box.w, 4 * box.h)):
            star_x = box.x
            used = _paste_icon(image, badge_icons.get("misc:star"), (star_x, box.y + layout.s(8, "star_count")), layout.s(22, "star_count"))
            star_x += used + (layout.s(4, "star_count") if used else 0)
            star_text = f"×{profile.star_count:,}" if profile.star_count is not None else "×-"
            draw.text(
                (star_x, box.y + layout.s(10, "star_count")),
                star_text,
                font=layout.font(_INTER_REGULAR, 14, "star_count"),
                fill=(220, 220, 225),
            )


def render_profile_core(
    *,
    profile: Profile,
    icon_bytes: bytes | None,
    course_rank_bytes: bytes | None,
    class_rank_bytes: bytes | None,
    rating_badge_bytes: bytes | None,
    badge_icons: dict[str, bytes],
    output,
    template: RenderTemplate | None = None,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().
    Matches the "core" stats shown on maimai DX NET's Player's Data page:
    name/title/rating/rank badges/star count/play counts, plus the full
    21-tier music clear-count grid. All image params are optional and
    degrade gracefully to a placeholder / text-only fallback. `template`
    is the caller's custom base/top/layout, if they have one."""
    badge_icons = badge_icons or {}
    layout = Layout.build(default_core_layout(), template.layout if template else None, CORE_CANVAS_WIDTH)
    image = make_base(template, (CORE_CANVAS_WIDTH, CANVAS_HEIGHT_CORE), BACKGROUND_COLOR, stretch=True)
    draw = ImageDraw.Draw(image)

    _draw_core_header(
        image,
        draw,
        layout,
        profile=profile,
        icon_bytes=icon_bytes,
        course_rank_bytes=course_rank_bytes,
        class_rank_bytes=class_rank_bytes,
        rating_badge_bytes=rating_badge_bytes,
        badge_icons=badge_icons,
        show_play_stats=True,
    )

    # music clear-count list - one row per tier (colored tier badge + a
    # blue count pill), each at its own layout box. Defaults stack them in
    # two columns, matching the real playerData page's own layout.
    pill_background = layout.options.get("count_pill_background", True)
    for entry in profile.music_counts:
        name = _row_name(entry.category, entry.tag)
        if name not in CORE_LABELS:
            continue  # a tier this renderer has no slot for
        box = layout.box(name)
        if box is None:
            continue
        with _opacity(image, layout.style(name)["opacity"], _pad_box(box, 4)):
            s = layout.scale(name)
            icon_box_w = round(GRID_ICON_BOX * s)
            icon_center_x = box.x + icon_box_w // 2
            # the CLEAR badge's source image is a much wider plaque than the
            # rank/combo/sync/dxstar icons, so pasting it at the same target
            # height as the rest makes it look oversized in its narrow box.
            icon_target_h = round((26 if entry.category == "clear" else 42) * s)
            icon_y = box.y + (box.h - icon_target_h) // 2
            with _opacity(image, layout.opacity_slot("pill_icon"), (box.x, box.y, box.x + icon_box_w, box.bottom)):
                used = _paste_icon_centered(
                    image, badge_icons.get(f"{entry.category}:{entry.tag}"), icon_center_x, icon_y, icon_target_h, icon_box_w - round(6 * s)
                )
            label_font = font(_INTER_REGULAR, round(11 * s))
            if used == 0:
                # no icon available (e.g. remote fetch failed) - fall back to
                # a plain text tag label so the row still identifies its tier.
                label = entry.tag.upper()
                label_w = draw.textlength(label, font=label_font)
                draw.text(
                    (box.x + (icon_box_w - label_w) / 2, box.y + (box.h - 11 * s) / 2),
                    label, font=label_font, fill=(200, 200, 205),
                )
            pill_x = box.x + icon_box_w + round(GRID_ICON_PILL_GAP * s)
            count_text = f"{entry.earned:,}/{entry.total:,}" if entry.earned is not None and entry.total is not None else "-"
            _draw_count_pill(
                image, draw, pill_x, box.y, box.right - pill_x, box.h, count_text, font(_INTER_BOLD, round(17 * s)),
                background=pill_background,
                border_color=layout.color("pill_border"), fill_color=layout.color("pill_fill"), text_color=layout.color("pill_text"),
                background_opacity=layout.opacity_slot("pill_background"), text_opacity=layout.opacity_slot("pill_text"),
            )

    _draw_footer(image, draw, CANVAS_HEIGHT_CORE, CORE_CANVAS_WIDTH)
    image = apply_top(image, template, stretch=True)
    image.save(output, "PNG", compress_level=3)
    output.seek(0)


_FRIEND_COMMENT_LINE_H = 22
_FRIEND_COMMENT_MAX_LINES = 3


def _wrap_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int) -> list[str]:
    """Greedy per-character wrap - friend comments are usually Japanese,
    with no spaces to break on. Anything past `max_lines` is cut, the last
    kept line ending in an ellipsis."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for ch in paragraph:
            if line and draw.textlength(line + ch, font=font) > max_w:
                lines.append(line)
                line = ch
            else:
                line += ch
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _truncate_to_width(draw, lines[-1] + "…", font, max_w)
    return lines


def render_friend_profile(
    *,
    profile: Profile,
    comment: str | None,
    icon_bytes: bytes | None,
    course_rank_bytes: bytes | None,
    class_rank_bytes: bytes | None,
    rating_badge_bytes: bytes | None,
    badge_icons: dict[str, bytes],
    output,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().
    /cc-friend-profile's card: the same header strip as render_profile_core
    (a friend's page exposes the same icon/name/title/rating/dan/class/star
    fields, just no play counts or clear-count grid), plus the friend's
    comment underneath if they've set one."""
    badge_icons = badge_icons or {}
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = CORE_CANVAS_WIDTH - GRID_PAD * 2
    comment_lines = (
        _wrap_to_width(probe, comment, FONT_BODY, text_w, _FRIEND_COMMENT_MAX_LINES) if comment else []
    )
    comment_h = len(comment_lines) * _FRIEND_COMMENT_LINE_H + (GRID_PAD // 2 if comment_lines else 0)
    canvas_h = HEADER_HEIGHT_CORE + comment_h + FOOTER_HEIGHT

    image = Image.new("RGB", (CORE_CANVAS_WIDTH, canvas_h), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    _draw_core_header(
        image,
        draw,
        Layout.build(default_core_layout(), None, CORE_CANVAS_WIDTH),
        profile=profile,
        icon_bytes=icon_bytes,
        course_rank_bytes=course_rank_bytes,
        class_rank_bytes=class_rank_bytes,
        rating_badge_bytes=rating_badge_bytes,
        badge_icons=badge_icons,
        show_play_stats=False,
    )
    y = HEADER_HEIGHT_CORE - GRID_PAD // 2
    for line in comment_lines:
        draw.text((GRID_PAD, y), line, font=FONT_BODY, fill=(220, 220, 225))
        y += _FRIEND_COMMENT_LINE_H

    _draw_footer(image, draw, canvas_h, CORE_CANVAS_WIDTH)
    image.save(output, "PNG", compress_level=3)
    output.seek(0)


# narrower than CANVAS_WIDTH on purpose: unlike render_profile_core's
# side-by-side grid, this view's content is a single vertical column, so a
# tall/thin canvas reads better than the shared 1500px-wide one.
EXTRAS_CANVAS_WIDTH = 640

_EXTRA_HEADER_H = 90
_EXTRA_ICON_SIZE = 64
_CP_SECTION_H = 90
_MILE_HEADER_H = 70
_MISSION_ROW_H = 46
_TICKETS_HEADER_H = 34
_TICKET_ROW_H = 76
_INTIMATE_H = 40
_SECTION_GAP = 14


_EXTRA_PAD = 24
# the default layout's list boxes are sized for this many rows - the same
# representative counts the old hand-written guide template used.
_DEFAULT_MISSION_ROWS = 5
_DEFAULT_TICKET_ROWS = 3


def default_extras_layout() -> dict:
    """render_profile_extras' stock geometry as a layout dict - see
    renderers/layout.py. Its lists (missions, tickets) vary in length per
    account: their boxes are sized for _DEFAULT_*_ROWS, and a longer list
    pushes everything below it down (see _list_shifts)."""
    pad = _EXTRA_PAD
    inner_w = EXTRAS_CANVAS_WIDTH - pad * 2
    icon_y = (_EXTRA_HEADER_H - _EXTRA_ICON_SIZE) // 2
    name_x = pad + _EXTRA_ICON_SIZE + 16
    y_cp = _EXTRA_HEADER_H
    y_mile = y_cp + _CP_SECTION_H
    y_missions = y_mile + _MILE_HEADER_H
    y_tickets_header = y_missions + _DEFAULT_MISSION_ROWS * _MISSION_ROW_H + _SECTION_GAP
    y_tickets = y_tickets_header + _TICKETS_HEADER_H
    y_presents = y_tickets + _DEFAULT_TICKET_ROWS * _TICKET_ROW_H + _SECTION_GAP
    elements = {
        "icon": {"x": pad, "y": icon_y, "w": _EXTRA_ICON_SIZE, "h": _EXTRA_ICON_SIZE},
        "name": {"x": name_x, "y": icon_y + 14, "w": EXTRAS_CANVAS_WIDTH - pad - name_x, "h": 40},
        "cp_block": {"x": pad, "y": y_cp, "w": inner_w, "h": _CP_SECTION_H},
        "mile_block": {"x": pad, "y": y_mile, "w": inner_w, "h": _MILE_HEADER_H},
        "mission_list": {"x": pad, "y": y_missions, "w": inner_w, "h": _DEFAULT_MISSION_ROWS * _MISSION_ROW_H},
        "tickets_header": {"x": pad, "y": y_tickets_header, "w": 200, "h": _TICKETS_HEADER_H},
        "ticket_list": {"x": pad, "y": y_tickets, "w": inner_w, "h": _DEFAULT_TICKET_ROWS * _TICKET_ROW_H},
        "presents": {"x": pad, "y": y_presents, "w": 300, "h": _INTIMATE_H},
    }
    for element in elements.values():
        element.setdefault("visible", True)
    return {
        "version": LAYOUT_VERSION,
        "kind": "profile_extra",
        "canvas": [EXTRAS_CANVAS_WIDTH, y_presents + _INTIMATE_H + FOOTER_HEIGHT],
        "elements": elements,
        "colors": {
            "cp_bar_fill": _rgb_hex(_CP_BAR_FILL),
            "cp_bar_overflow": _rgb_hex(_CP_OVERFLOW_COLOR),
            "cp_bar_track": _rgb_hex(_CP_BAR_TRACK),
            "mission_row": _rgb_hex(_MISSION_ROW),
            "mission_row_cleared": _rgb_hex(_MISSION_ROW_CLEARED),
            "mission_border": _rgb_hex(_MISSION_BORDER),
            "mission_accent": _rgb_hex(_MISSION_ACCENT),
            "mission_text": _rgb_hex(_MISSION_TEXT),
        },
        # on: a list shorter than its box pulls everything below it up
        # (the stock compact look). Turn off for template art that needs
        # elements to stay exactly where they were placed.
        "options": {"compact_lists": True},
    }


EXTRAS_LABELS = {
    "icon": "Icon",
    "name": "Player name",
    "cp_block": "Class point bar",
    "mile_block": "maimille + mission status",
    "mission_list": "Missions",
    "tickets_header": "TICKETS heading",
    "ticket_list": "Tickets",
    "presents": "Presents count",
}

EXTRAS_COLOR_LABELS = {
    "cp_bar_fill": "Class point bar",
    "cp_bar_overflow": "Class point overflow",
    "cp_bar_track": "Class point bar background",
    "mission_row": "Mission row",
    "mission_row_cleared": "Cleared mission row",
    "mission_border": "Mission row border",
    "mission_accent": "Mission accent (gold)",
    "mission_text": "Pending mission text",
}

# which elements are variable-length lists, and each one's row height
_EXTRA_LISTS = {"mission_list": _MISSION_ROW_H, "ticket_list": _TICKET_ROW_H}


def _list_shifts(layout: Layout, row_counts: dict[str, int]) -> tuple[dict[str, int], int]:
    """How far each element moves down because a list above it holds more
    (or, with compact_lists, fewer) rows than its box was sized for. An
    element counts as "below" a list if its top is at or under the list
    box's bottom, in the layout as placed. Returns (shift per element,
    total canvas growth)."""
    compact = layout.options.get("compact_lists", True)
    deltas = {}
    for list_name, row_h in _EXTRA_LISTS.items():
        box = layout.box(list_name)
        if box is None:
            continue
        delta = row_counts[list_name] * row_h - box.h
        deltas[list_name] = delta if compact else max(0, delta)
    shifts = {}
    for name in EXTRAS_LABELS:
        raw = layout.raw(name)
        shifts[name] = sum(
            delta
            for list_name, delta in deltas.items()
            if list_name != name and raw["y"] >= layout.raw(list_name)["y"] + layout.raw(list_name)["h"]
        )
    return shifts, sum(deltas.values())


def render_profile_extras(
    *,
    profile: Profile,
    extras: ProfileExtras,
    icon_bytes: bytes | None,
    ticket_image_bytes: list[bytes | None],
    output,
    template: RenderTemplate | None = None,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().
    Renders CP progress, maimile count, missions, tickets, and intimate
    item count - fully separate from render_profile_core's stats, per the
    "extra" /cc-profile view (mutually exclusive with "core", not
    additive). `template` is the caller's custom base/top/layout, if any."""
    defaults = default_extras_layout()
    layout = Layout.build(defaults, template.layout if template else None, EXTRAS_CANVAS_WIDTH)
    row_counts = {"mission_list": len(extras.missions), "ticket_list": max(len(extras.tickets), 1)}
    shifts, growth = _list_shifts(layout, row_counts)
    boxes = {name: layout.box(name, dy=shifts[name]) for name in EXTRAS_LABELS}

    # a base image authored at another width was scaled by Layout.build -
    # scale the stock canvas height the same way.
    user_canvas = (template.layout or {}).get("canvas") if template else None
    layout_h = defaults["canvas"][1]
    if isinstance(user_canvas, list) and len(user_canvas) == 2 and user_canvas[0]:
        layout_h = round(user_canvas[1] * EXTRAS_CANVAS_WIDTH / user_canvas[0])
    canvas_h = layout_h + growth
    for name, box in boxes.items():
        if box is not None:
            extent = box.bottom
            if name in _EXTRA_LISTS:
                extent = box.y + row_counts[name] * _EXTRA_LISTS[name]
            canvas_h = max(canvas_h, extent + FOOTER_HEIGHT)

    image = make_base(template, (EXTRAS_CANVAS_WIDTH, canvas_h), BACKGROUND_COLOR, stretch=False)
    draw = ImageDraw.Draw(image)

    def op(name: str, box, pad: int):
        return _opacity(image, layout.style(name)["opacity"], _pad_box(box, pad))

    cp_fill, cp_track, cp_overflow_color = layout.color("cp_bar_fill"), layout.color("cp_bar_track"), layout.color("cp_bar_overflow")
    mission_row, mission_row_cleared = layout.color("mission_row"), layout.color("mission_row_cleared")
    mission_border, accent, pending_text = layout.color("mission_border"), layout.color("mission_accent"), layout.color("mission_text")

    # header: icon + name only - no rating/title/ranks, this view is fully
    # separate from the core stats view.
    if box := boxes["icon"]:
        with op("icon", box, 0):
            _paste_square_icon(image, icon_bytes, box, 10)
    if box := boxes["name"]:
        with op("name", box, 20):
            name_font = layout.font(_JP_BOLD, 34, "name")
            name_text = _truncate_to_width(draw, profile.display_name, name_font, box.w)
            draw.text((box.x, box.y), name_text, font=name_font, fill=(255, 255, 255))

    # CP (class point) section - hand-drawn bar, no gauge/meter image asset
    # (the real site's version is CSS clip-rect masked, not a flat image).
    if box := boxes["cp_block"]:
        with op("cp_block", box, 20):
            x0, x1, y = box.x, box.right, box.y
            draw.text((x0, y + 6), "CLASS POINT", font=FONT_SECTION_TITLE, fill=(255, 255, 255))
            cp_current, cp_required = extras.cp_current, extras.cp_required
            cp_text = f"{cp_current if cp_current is not None else '-'} / {cp_required if cp_required is not None else '-'} CP"
            cp_text_w = draw.textlength(cp_text, font=FONT_BODY)
            draw.text((x1 - cp_text_w, y + 10), cp_text, font=FONT_BODY, fill=(220, 220, 225))
            bar_y = y + 44
            bar_h = 22
            bar_w = x1 - x0
            draw.rounded_rectangle([(x0, bar_y), (x1, bar_y + bar_h)], radius=bar_h // 2, fill=cp_track)
            overflow_tens = 0
            if cp_current is not None:
                if not cp_required:
                    # some classes have no maximum - render the gauge as completely full.
                    draw.rounded_rectangle([(x0, bar_y), (x1, bar_y + bar_h)], radius=bar_h // 2, fill=cp_fill)
                else:
                    # gauge only has 10 segments, so cp_current wraps like an odometer:
                    # the last digit fills the bar, the rest becomes a "+N" overflow badge.
                    ones = cp_current % 10
                    overflow_tens = cp_current // 10
                    if overflow_tens > 0:
                        draw.rounded_rectangle([(x0, bar_y), (x1, bar_y + bar_h)], radius=bar_h // 2, fill=cp_overflow_color)
                    fill_w = round(bar_w * ones / 10)
                    if fill_w > 0:
                        draw.rounded_rectangle([(x0, bar_y), (x0 + fill_w, bar_y + bar_h)], radius=bar_h // 2, fill=cp_fill)
            segments = 10
            seg_w = bar_w / segments
            # separators are cut in the canvas background colour; over template
            # art that would draw stray flat-colour lines, so they're darkened
            # track colour instead there.
            sep_color = BACKGROUND_COLOR if not (template and template.base) else (30, 30, 38)
            for i in range(1, segments):
                sep_x = round(x0 + seg_w * i)
                draw.line([(sep_x, bar_y + 2), (sep_x, bar_y + bar_h - 2)], fill=sep_color, width=2)
            if overflow_tens > 0:
                overflow_text = f"+{overflow_tens}"
                overflow_w = draw.textlength(overflow_text, font=FONT_BODY)
                draw.text((x1 - cp_text_w - overflow_w - 12, y + 10), overflow_text, font=FONT_BODY, fill=cp_overflow_color)

    # mile + mission status
    if box := boxes["mile_block"]:
        with op("mile_block", box, 20):
            x0, x1, y = box.x, box.right, box.y
            mile_text = f"{extras.mile_count:,} maimille" if extras.mile_count is not None else "- maimille"
            draw.text((x0, y + 4), mile_text, font=FONT_MILE, fill=accent)
            if extras.mission_deadline_text:
                deadline_w = draw.textlength(extras.mission_deadline_text, font=FONT_BODY_SMALL)
                draw.text((x1 - deadline_w, y + 6), extras.mission_deadline_text, font=FONT_BODY_SMALL, fill=(180, 180, 190))
            if extras.mission_clear_count is not None and extras.mission_total_count is not None:
                clear_text = f"CLEAR {extras.mission_clear_count}/{extras.mission_total_count}"
                draw.text((x0, y + 28), clear_text, font=FONT_BODY_SMALL, fill=(140, 220, 140))

    if box := boxes["mission_list"]:
        # the list's real extent is its row count, not its (stock-sized) box
        list_region = (box.x, box.y, box.right, box.y + max(box.h, len(extras.missions) * _MISSION_ROW_H))
        with _opacity(image, layout.style("mission_list")["opacity"], list_region):
            x0, x1, y = box.x, box.right, box.y
            for mission in extras.missions:
                row_color = mission_row_cleared if mission.cleared else mission_row
                draw.rectangle([(x0, y + 2), (x1, y + _MISSION_ROW_H - 6)], fill=row_color, outline=mission_border)
                check = "✓" if mission.cleared else "○"
                draw.text((x0 + 12, y + 12), check, font=FONT_BODY, fill=accent if mission.cleared else (120, 120, 130))
                text = "Complete!" if mission.cleared else (mission.text or "Complete previous mission to unlock!")
                text = _truncate_to_width(draw, text, FONT_BODY_SMALL, box.w - 150)
                draw.text((x0 + 40, y + 13), text, font=FONT_BODY_SMALL, fill=accent if mission.cleared else pending_text)
                if mission.mile_reward is not None:
                    reward_text = f"+{mission.mile_reward} maimille"
                    reward_w = draw.textlength(reward_text, font=FONT_BODY_SMALL)
                    draw.text((x1 - 12 - reward_w, y + 13), reward_text, font=FONT_BODY_SMALL, fill=accent)
                y += _MISSION_ROW_H

    if box := boxes["tickets_header"]:
        with op("tickets_header", box, 20):
            draw.text((box.x, box.y), "TICKETS", font=layout.font(_JP_BOLD, 20, "tickets_header"), fill=(255, 255, 255))
    if box := boxes["ticket_list"]:
        list_region = (box.x, box.y, box.right, box.y + max(box.h, row_counts["ticket_list"] * _TICKET_ROW_H))
        with _opacity(image, layout.style("ticket_list")["opacity"], list_region):
            x0, y = box.x, box.y
            if not extras.tickets:
                draw.text((x0, y), "No tickets owned.", font=FONT_BODY_SMALL, fill=(150, 150, 158))
            else:
                for ticket, img_bytes in zip(extras.tickets, ticket_image_bytes):
                    used = _paste_scaled(image, img_bytes, (x0, y), 56)
                    text_x = x0 + used + (16 if used else 0)
                    draw.text((text_x, y + 6), ticket.name, font=FONT_BODY_SMALL, fill=(220, 220, 225))
                    count_text = f"×{ticket.count:,}" if ticket.count is not None else "×-"
                    draw.text((text_x, y + 30), count_text, font=FONT_BODY_SMALL, fill=(180, 180, 190))
                    y += _TICKET_ROW_H

    if box := boxes["presents"]:
        with op("presents", box, 20):
            intimate_text = f"Presents: {extras.intimate_count:,}" if extras.intimate_count is not None else "Presents: -"
            draw.text((box.x, box.y), intimate_text, font=layout.font(_JP_MEDIUM, 16, "presents"), fill=(220, 220, 225))

    _draw_footer(image, draw, canvas_h, EXTRAS_CANVAS_WIDTH)
    image = apply_top(image, template, stretch=False)
    image.save(output, "PNG", compress_level=3)
    output.seek(0)
