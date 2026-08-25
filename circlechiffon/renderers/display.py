import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from circlechiffon.renderers.b50 import (
    FONT_DIR,
    RATING_ACCENT_COLOR as RATING_TEXT_COLOR,
    _TIER_COLORS,
    _draw_guide_box,
    _fit_font,
    _hex_to_rgb,
    _paste_rating_badge,
    _paste_scaled,
    _rounded_mask,
)
from circlechiffon.types import Circle, Profile

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
FALLBACK_TEMPLATE_PATH = ASSETS_DIR / "b50" / "template.png"
# traced off the reference card - the group glyph on the circle banner's
# blue tab, which SEGA doesn't serve as a file anywhere.
CHIP_ICON_PATH = ASSETS_DIR / "display" / "circle_chip_icon.png"

_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_JP_MEDIUM = str(FONT_DIR / "NotoSansJP-Medium.ttf")

# Fixed output canvas - the equipped Frame collectible is a full-bleed
# backdrop covering the whole thing. The nameplate is a fixed-size inset
# box near the top-left (not the card's own background/bounds as earlier
# rounds tried - it's just one decorative element sized to its own real
# aspect ratio, ~722x117 at this canvas size), holding icon/rating/
# class/name/dan/title. Circle name renders as a separate label directly
# below the nameplate box, outside of it.
CANVAS_W, CANVAS_H = 1080, 452

NAMEPLATE_X, NAMEPLATE_Y = 24, 24
NAMEPLATE_W, NAMEPLATE_H = 722, 117

_ICON_PAD = 8
ICON_SIZE = NAMEPLATE_H - _ICON_PAD * 2
ICON_X = NAMEPLATE_X + _ICON_PAD
ICON_Y = NAMEPLATE_Y + _ICON_PAD

CONTENT_X = ICON_X + ICON_SIZE + 8
CONTENT_RIGHT = NAMEPLATE_X + NAMEPLATE_W - 8  # inner right margin most rows stay within
CLASS_GAP = 6  # class badge sits this far right of the rating pill's own right edge

# row geometry within the 117-tall nameplate.
ROW_A_Y, ROW_A_H = 5, 33  # rating pill + class badge
ROW_B_Y, ROW_B_H = 41, 45  # name box + dan badge
ROW_C_Y, ROW_C_H = 88, 25  # title, bottom row

# Row B's inner split, measured off the reference render: the name field
# and the dan badge share one 272-wide white box, with the badge fused
# against the box's right edge.
NAME_PAD_L, NAME_TEXT_W, NAME_BADGE_GAP, NAME_BADGE_W, NAME_PAD_R = 8, 168, 8, 80, 8
NAME_BOX_W = NAME_PAD_L + NAME_TEXT_W + NAME_BADGE_GAP + NAME_BADGE_W + NAME_PAD_R
NAME_FONT_H = 32
NAME_LETTER_GAP = 3  # extra px of pitch between characters, see _draw_monospaced_text

# img/trophy_<tier>.png's own pixel size on the live site - the title bar
# is that asset at 1:1, not a scaled guess.
TITLE_W, TITLE_H = 268, 25

# The circle banner (img/circle/profile/circle_profile_color_*.png) is
# natively 300x44, but the card prints it squashed to roughly 272x21 -
# measured off the real card, where it sits just under the nameplate's
# bottom edge and slightly left of the name column. Letting it keep its
# native aspect instead would make it nearly twice as tall as the title
# bar above it and swamp the card, so the squash is deliberate.
RIBBON_W = NAME_BOX_W
RIBBON_H = 22
RIBBON_X_OFFSET = -9  # relative to CONTENT_X
RIBBON_Y_OVERLAP = 2  # how far it rides up over the nameplate's bottom edge
RIBBON_CHIP_W = 50
RIBBON_CHIP_OVERLAP = 8  # how far the banner tucks in behind the chip
# sampled down the real chip: bright at the top, a darker band across the
# middle, then a highlight below it - the glossy-bar shading that a flat
# fill loses.
CHIP_GRADIENT = [
    (0.00, (42, 100, 212)),
    (0.45, (42, 83, 185)),
    (0.55, (76, 124, 214)),
    (1.00, (45, 109, 215)),
]

BACKGROUND_COLOR = (24, 24, 32)

CARD_BORDER_COLOR = (198, 148, 84)

def _cover_fit(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scales `image` to fully cover a target_w x target_h box (matching
    whichever dimension needs more scale) and center-crops the overflow -
    same idea as CSS `background-size: cover`. Preserves the source aspect
    ratio, unlike a plain stretch-to-fit resize."""
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    scaled_w, scaled_h = round(src_w * scale), round(src_h * scale)
    resized = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
    left = (scaled_w - target_w) // 2
    top = (scaled_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_frame(frame_bytes: bytes | None, canvas_w: int, canvas_h: int) -> Image.Image:
    if frame_bytes:
        try:
            with Image.open(io.BytesIO(frame_bytes)) as frame:
                return _cover_fit(frame.convert("RGB"), canvas_w, canvas_h)
        except Exception:
            pass
    try:
        with Image.open(FALLBACK_TEMPLATE_PATH) as template:
            return _cover_fit(template.convert("RGB"), canvas_w, canvas_h)
    except (FileNotFoundError, OSError):
        return Image.new("RGB", (canvas_w, canvas_h), BACKGROUND_COLOR)


def _fit_nameplate(nameplate_bytes: bytes | None, box_w: int, box_h: int) -> Image.Image:
    """Cover-fits the account's equipped nameplate image into a fixed
    box_w x box_h box - the nameplate here is just one inset decoration
    on the canvas, not the card's own bounds, so unlike earlier rounds it
    doesn't get to dictate the overall layout width."""
    if nameplate_bytes:
        try:
            with Image.open(io.BytesIO(nameplate_bytes)) as plate:
                return _cover_fit(plate.convert("RGB"), box_w, box_h)
        except Exception:
            pass
    return Image.new("RGB", (box_w, box_h), (245, 238, 222))


def _paste_contain_right(
    base: Image.Image, icon_bytes: bytes | None, right_x: int, center_y: int, max_w: int, max_h: int
) -> int:
    """Like `_paste_scaled`, but right-aligned to `right_x`, vertically
    centered on `center_y`, and scaled to *fit within* (max_w, max_h)
    preserving aspect - used for the class/course badges, whose real
    assets are wide ribbon/seal shapes. Scaling by height alone (as a
    plain aspect-preserving resize would) lets a wide badge balloon past
    its row and cover the name/rating text next to it; capping by both
    dimensions keeps it proportionate to the row regardless of the
    asset's own aspect ratio."""
    if not icon_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(icon_bytes)) as img:
            img = img.convert("RGBA")
            scale = min(max_w / img.width, max_h / img.height)
            w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            pos = (right_x - w, center_y - h // 2)
            base.paste(img, pos, img)
            return w
    except Exception:
        return 0


def _paste_contain_left(
    base: Image.Image, icon_bytes: bytes | None, left_x: int, center_y: int, max_w: int, max_h: int
) -> int:
    """Like `_paste_contain_right`, but left-anchored to `left_x` - used
    for the class badge, which sits immediately to the right of the
    rating pill rather than pinned to a fixed right edge."""
    if not icon_bytes:
        return 0
    try:
        with Image.open(io.BytesIO(icon_bytes)) as img:
            img = img.convert("RGBA")
            scale = min(max_w / img.width, max_h / img.height)
            w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            pos = (left_x, center_y - h // 2)
            base.paste(img, pos, img)
            return w
    except Exception:
        return 0



def _paste_plate(base: Image.Image, plate_bytes: bytes | None, pos: tuple[int, int], size: tuple[int, int]) -> bool:
    """Pastes a UI plate asset (the trophy/title banner, the circle's name
    banner) resized to exactly `size`, honouring its alpha. These are
    9-slice-ish bars SEGA already ships at the right proportions, so a
    straight resize is faithful - and using the real asset is the only way
    the colors stay correct, since both change with the account's title
    tier / the circle's class. Returns False if there was nothing to
    paste, so callers can fall back to drawing their own."""
    if not plate_bytes:
        return False
    try:
        with Image.open(io.BytesIO(plate_bytes)) as img:
            plate = img.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
            base.paste(plate, pos, plate)
            return True
    except Exception:
        return False


def _vertical_gradient(size: tuple[int, int], stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    """A top-to-bottom gradient through `stops` (each an offset in 0..1
    plus its color), interpolated linearly between neighbours. Painting
    the chip with this rather than one flat fill is what keeps it from
    reading as a dead sticker next to the banner's own shading."""
    w, h = size
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        lo = stops[0]
        hi = stops[-1]
        for i in range(len(stops) - 1):
            if stops[i][0] <= t <= stops[i + 1][0]:
                lo, hi = stops[i], stops[i + 1]
                break
        span = hi[0] - lo[0]
        f = 0.0 if span <= 0 else (t - lo[0]) / span
        px[0, y] = tuple(round(lo[1][c] + (hi[1][c] - lo[1][c]) * f) for c in range(3))
    return grad.resize((w, h), Image.Resampling.NEAREST)


def _paste_circle_chip(base: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """The blue "circle" tab fused to the left end of the circle banner:
    a double-pointed chevron with a white keyline and the three-figure
    group glyph.

    The chevron has no downloadable asset anywhere on maimai DX NET (it
    only exists on the card itself), so it's drawn - but shaded with a
    vertical gradient lifted off the real card rather than filled flat,
    which is what made an earlier pass look pasted-on. The glyph inside
    it *was* traced off the card and lives in assets/display/."""
    notch = h // 2
    poly = [
        (x, y + h // 2),
        (x + notch, y),
        (x + w - notch, y),
        (x + w, y + h // 2),
        (x + w - notch, y + h),
        (x + notch, y + h),
    ]
    mask = Image.new("L", (w + 1, h + 1), 0)
    ImageDraw.Draw(mask).polygon([(px_ - x, py_ - y) for px_, py_ in poly], fill=255)
    base.paste(_vertical_gradient((w + 1, h + 1), CHIP_GRADIENT), (x, y), mask)
    draw.line(poly + [poly[0]], fill=(255, 255, 255), width=2, joint="curve")

    try:
        with Image.open(CHIP_ICON_PATH) as icon:
            icon = icon.convert("RGBA")
            target_h = max(1, round(h * 0.62))
            target_w = max(1, round(icon.width * target_h / icon.height))
            icon = icon.resize((target_w, target_h), Image.Resampling.LANCZOS)
            base.paste(icon, (x + (w - target_w) // 2, y + (h - target_h) // 2), icon)
    except (FileNotFoundError, OSError):
        pass


def _draw_monospaced_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box_x: int,
    box_y: int,
    box_w: int,
    box_h: int,
    fill: tuple[int, int, int],
    font_h: int,
    letter_gap: int = 2,
) -> None:
    """Lays `text` out on a fixed pitch - every character gets the same
    cell, `letter_gap` px wider than the widest glyph, and is centred in
    it. maimai names are stored full-width (e.g. 'ｈｖｌ．ＥＭＵ☆'), and
    the card prints them on an even pitch like this rather than at the
    font's natural proportional advances, so a proportional layout reads
    visibly tighter and more cramped than the real thing.

    The font shrinks until the whole run fits `box_w`, so a long name
    scales down instead of overflowing into the badge beside it."""
    if not text:
        return
    size = font_h
    while size > 6:
        font = ImageFont.truetype(font_path, size)
        cell = max(draw.textlength(ch, font=font) for ch in text) + letter_gap
        if cell * len(text) <= box_w:
            break
        size -= 1
    else:
        font = ImageFont.truetype(font_path, 6)
        cell = max(draw.textlength(ch, font=font) for ch in text) + letter_gap

    bbox = draw.textbbox((0, 0), text, font=font)
    y = box_y + (box_h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    x = box_x
    for ch in text:
        draw.text((x + (cell - draw.textlength(ch, font=font)) / 2, y), ch, font=font, fill=fill)
        x += cell


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    outline: tuple[int, int, int],
    stroke: int = 1,
    font_h: int | None = None,
) -> None:
    """Centers `text` in `box` (x, y, w, h) with a solid outline around
    every glyph - maimai DX NET draws both the title and the circle name
    this way (an 8-direction 1px text-shadow in its CSS), which is what
    keeps them readable over the busy plate art underneath."""
    if not text:
        return
    x, y, w, h = box
    inner_w = w - stroke * 2
    font = _fit_font(draw, text, font_path, inner_w, font_h if font_h is not None else h)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (x + (w - text_w) / 2 - bbox[0], y + (h - text_h) / 2 - bbox[1]),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=outline,
    )


def render_display(
    *,
    profile: Profile,
    circle: Circle | None,
    icon_bytes: bytes | None,
    course_rank_bytes: bytes | None,
    class_rank_bytes: bytes | None,
    rating_badge_bytes: bytes | None,
    nameplate_bytes: bytes | None,
    frame_bytes: bytes | None,
    tour_member_bytes: bytes | None,
    title_plate_bytes: bytes | None = None,
    circle_color_bytes: bytes | None = None,
    output,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().

    Fixed 1080x452 canvas: `frame_bytes` (the equipped Frame collectible)
    is a cover-fit (non-stretching) full-bleed backdrop behind everything.
    The nameplate is a fixed ~722x117 inset box near the top-left holding
    the profile content - icon on the left; rating pill + class-rank
    badge (allowed to spill a bit past the nameplate's right edge) on
    top; name box + dan/course-rank badge below that; title capsule along
    the bottom edge. The circle's name banner renders directly beneath
    the nameplate box, riding slightly over its bottom edge.

    The title/trophy banner and the circle banner are both real SEGA
    assets (`img/trophy_<tier>.png`, 268x25, and
    `img/circle/profile/circle_profile_color_<class>.png`, 300x44) rather
    than hand-drawn plaques. An earlier round concluded no title asset
    existed, having swept collection/trophy/ for `<img>` tags - it's
    actually a stylesheet `background-image` on `.trophy_block`, which is
    why it was missed. Using the real art matters because both colors
    change (with the account's title tier and the circle's class), so any
    fixed palette here would silently go stale; both still fall back to a
    drawn shape when the fetch fails.

    All image params are optional and degrade gracefully; `circle` may be
    `None` (account not in a Circle) and is simply skipped.
    """
    image = _load_frame(frame_bytes, CANVAS_W, CANVAS_H).convert("RGB")
    draw = ImageDraw.Draw(image)

    # nameplate inset box, rounded corners
    nameplate_img = _fit_nameplate(nameplate_bytes, NAMEPLATE_W, NAMEPLATE_H)
    border_radius = 12
    nameplate_mask = _rounded_mask((NAMEPLATE_W, NAMEPLATE_H), border_radius)
    image.paste(nameplate_img, (NAMEPLATE_X, NAMEPLATE_Y), nameplate_mask)
    draw.rounded_rectangle(
        [(NAMEPLATE_X, NAMEPLATE_Y), (NAMEPLATE_X + NAMEPLATE_W - 1, NAMEPLATE_Y + NAMEPLATE_H - 1)],
        radius=border_radius,
        outline=CARD_BORDER_COLOR,
        width=3,
    )

    # icon
    used = _paste_scaled(image, icon_bytes, (ICON_X, ICON_Y), ICON_SIZE)
    if used == 0:
        mask = _rounded_mask((ICON_SIZE, ICON_SIZE), 10)
        placeholder = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (200, 200, 205))
        image.paste(placeholder, (ICON_X, ICON_Y), mask)

    content_w = CONTENT_RIGHT - CONTENT_X

    # row A: rating pill (left) + class-rank badge (right, allowed to
    # spill past the nameplate's own right edge)
    row_a_y = NAMEPLATE_Y + ROW_A_Y
    rating_text = str(profile.rating) if profile.rating is not None else "?"
    rating_w = _paste_rating_badge(image, draw, rating_badge_bytes, rating_text, (CONTENT_X, row_a_y), ROW_A_H, RATING_TEXT_COLOR)
    _paste_contain_left(
        image,
        class_rank_bytes,
        CONTENT_X + rating_w + CLASS_GAP,
        row_a_y + ROW_A_H // 2,
        round(content_w * 0.22),
        round(ROW_A_H * 1.15),
    )

    # row B: name box + dan/course-rank badge overlapping its right edge.
    # Fixed max width, well clear of the small chibi lineup baked into
    # the nameplate art further right.
    row_b_y = NAMEPLATE_Y + ROW_B_Y
    draw.rounded_rectangle(
        [(CONTENT_X, row_b_y), (CONTENT_X + NAME_BOX_W, row_b_y + ROW_B_H)],
        radius=6,
        fill=(255, 255, 255),
        outline=(200, 200, 200),
    )
    if profile.display_name:
        _draw_monospaced_text(
            draw,
            profile.display_name,
            _JP_MEDIUM,
            CONTENT_X + NAME_PAD_L,
            row_b_y,
            NAME_TEXT_W,
            ROW_B_H,
            (20, 20, 20),
            font_h=NAME_FONT_H,
            letter_gap=NAME_LETTER_GAP,
        )
    _paste_contain_left(
        image,
        course_rank_bytes,
        CONTENT_X + NAME_PAD_L + NAME_TEXT_W + NAME_BADGE_GAP,
        row_b_y + ROW_B_H // 2,
        NAME_BADGE_W,
        ROW_B_H - 6,
    )

    # row C: title/trophy bar along the bottom edge of the nameplate. The
    # real img/trophy_<tier>.png is 268x25, drawn here 1:1, with the site's
    # own text treatment on top (white, 1px hard outline all round).
    row_c_y = NAMEPLATE_Y + ROW_C_Y
    if not _paste_plate(image, title_plate_bytes, (CONTENT_X, row_c_y), (TITLE_W, ROW_C_H)):
        # asset unavailable - fall back to the drawn capsule, tinted by
        # whatever tier the page reported.
        _, mid_hex, dark_hex = _TIER_COLORS.get(profile.title_tier or "", _TIER_COLORS["Gold"])
        capsule_radius = ROW_C_H // 2
        draw.rounded_rectangle(
            [(CONTENT_X, row_c_y), (CONTENT_X + TITLE_W, row_c_y + ROW_C_H)],
            radius=capsule_radius,
            fill=_hex_to_rgb(dark_hex),
        )
        inset = 3
        draw.rounded_rectangle(
            [(CONTENT_X + inset, row_c_y + inset), (CONTENT_X + TITLE_W - inset, row_c_y + ROW_C_H - inset)],
            radius=max(capsule_radius - inset, 2),
            fill=_hex_to_rgb(mid_hex),
        )
    if profile.title:
        _draw_outlined_text(
            draw,
            profile.title,
            _JP_MEDIUM,
            (CONTENT_X + 10, row_c_y, TITLE_W - 20, ROW_C_H),
            (255, 255, 255),
            (0, 0, 0),
            stroke=1,
            font_h=ROW_C_H - 12,
        )

    # circle banner: the real rank-colored name plate from the circle
    # profile page, tucked under the nameplate the way the physical card
    # overlaps it. Skipped entirely when the account is not in a circle.
    if circle is not None and circle.name:
        ribbon_x = CONTENT_X + RIBBON_X_OFFSET
        ribbon_y = NAMEPLATE_Y + NAMEPLATE_H - RIBBON_Y_OVERLAP
        # the banner starts where the chip ends (bar a few px of overlap),
        # rather than running the full width underneath it - otherwise the
        # chip swallows the banner art's own left-hand star.
        body_x = ribbon_x + RIBBON_CHIP_W - RIBBON_CHIP_OVERLAP
        body_w = RIBBON_W - RIBBON_CHIP_W + RIBBON_CHIP_OVERLAP
        if not _paste_plate(image, circle_color_bytes, (body_x, ribbon_y), (body_w, RIBBON_H)):
            draw.rounded_rectangle(
                [(body_x, ribbon_y), (body_x + body_w, ribbon_y + RIBBON_H)],
                radius=6,
                fill=(150, 84, 48),
                outline=(255, 255, 255),
                width=2,
            )
        _paste_circle_chip(image, draw, ribbon_x, ribbon_y, RIBBON_CHIP_W, RIBBON_H)
        # the site prints the circle name over this banner in bold with a
        # white outline - the banner art is busy enough that plain dark
        # text on it is hard to read. Centred over the body only, so the
        # chip doesn't push it off-centre.
        _draw_outlined_text(
            draw,
            circle.name,
            _JP_BOLD,
            (body_x + 14, ribbon_y, body_w - 28, RIBBON_H),
            (11, 56, 113),
            (255, 255, 255),
            stroke=2,
            font_h=RIBBON_H - 9,
        )

    image.save(output, "PNG", compress_level=3)
    output.seek(0)


def render_display_template(output) -> None:
    """Synchronous, no live data needed. Renders a transparent-background
    guide PNG at the exact /cc-display canvas size, with labeled outline
    boxes at every position render_display() actually draws content -
    meant to be opened in an external image editor to design a Frame
    background around the real content instead of guessing at its layout."""
    image = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    _draw_guide_box(draw, (NAMEPLATE_X, NAMEPLATE_Y, NAMEPLATE_X + NAMEPLATE_W, NAMEPLATE_Y + NAMEPLATE_H), "NAMEPLATE INSET")
    _draw_guide_box(draw, (ICON_X, ICON_Y, ICON_X + ICON_SIZE, ICON_Y + ICON_SIZE), "ICON", color=(0, 200, 255))

    content_w = CONTENT_RIGHT - CONTENT_X

    row_a_y = NAMEPLATE_Y + ROW_A_Y
    _draw_guide_box(draw, (CONTENT_X, row_a_y, CONTENT_X + 150, row_a_y + ROW_A_H), "RATING BADGE", color=(0, 200, 255))
    _draw_guide_box(
        draw,
        (CONTENT_X + 150 + CLASS_GAP, row_a_y, CONTENT_X + 150 + CLASS_GAP + round(content_w * 0.22), row_a_y + round(ROW_A_H * 1.15)),
        "CLASS BADGE",
        color=(0, 200, 255),
    )

    row_b_y = NAMEPLATE_Y + ROW_B_Y
    name_text_x = CONTENT_X + NAME_PAD_L
    badge_x = name_text_x + NAME_TEXT_W + NAME_BADGE_GAP
    _draw_guide_box(draw, (CONTENT_X, row_b_y, CONTENT_X + NAME_BOX_W, row_b_y + ROW_B_H), "NAME BOX")
    _draw_guide_box(
        draw,
        (name_text_x, row_b_y, name_text_x + NAME_TEXT_W, row_b_y + ROW_B_H),
        "PLAYER NAME",
        color=(0, 200, 255),
    )
    _draw_guide_box(
        draw,
        (badge_x, row_b_y, badge_x + NAME_BADGE_W, row_b_y + ROW_B_H),
        "DAN/COURSE RANK BADGE",
        color=(0, 200, 255),
    )

    row_c_y = NAMEPLATE_Y + ROW_C_Y
    _draw_guide_box(draw, (CONTENT_X, row_c_y, CONTENT_X + TITLE_W, row_c_y + ROW_C_H), "TITLE PLATE")

    ribbon_x = CONTENT_X + RIBBON_X_OFFSET
    ribbon_y = NAMEPLATE_Y + NAMEPLATE_H - RIBBON_Y_OVERLAP
    _draw_guide_box(draw, (ribbon_x, ribbon_y, ribbon_x + RIBBON_W, ribbon_y + RIBBON_H), "CIRCLE BANNER")

    image.save(output, "PNG", compress_level=3)
    output.seek(0)
