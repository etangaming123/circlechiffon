import io
from pathlib import Path

from PIL import Image, ImageDraw

from circlechiffon.renderers.b50 import (
    FONT_DIR,
    RATING_ACCENT_COLOR as RATING_TEXT_COLOR,
    _TIER_COLORS,
    _draw_guide_box,
    _fit_font,
    _hex_to_rgb,
    _paste_rating_badge,
    _rounded_mask,
)
from circlechiffon.types import Circle, Profile

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
FALLBACK_TEMPLATE_PATH = ASSETS_DIR / "b50" / "template.png"

_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_JP_REGULAR = str(FONT_DIR / "NotoSansJP-Regular.ttf")

# Fixed output canvas - the equipped Frame collectible is a full-bleed
# backdrop covering the whole thing. The nameplate is a fixed-size inset
# box near the top-left (not the card's own background/bounds as earlier
# rounds tried - it's just one decorative element sized to its own real
# aspect ratio, 720x116), holding icon/rating/
# class/name/dan/title. Circle name renders as a separate label directly
# below the nameplate box, outside of it.
CANVAS_W, CANVAS_H = 1080, 452

# Every position below is measured pixel-for-pixel off the reference card
# (temporary/display.png - same 1080x452 canvas), not estimated. The
# nameplate is the equipped NamePlate asset at its native 720x116, square
# cornered, with no outline of our own - the thin white top/bottom edge on
# the reference is baked into the asset itself.
NAMEPLATE_X, NAMEPLATE_Y = 31, 25
NAMEPLATE_W, NAMEPLATE_H = 720, 116

# icon: 100px square with a 2px dark-teal keyline and barely-rounded corners.
ICON_SIZE = 100
ICON_X = NAMEPLATE_X + 9
ICON_Y = NAMEPLATE_Y + 7
ICON_RADIUS = 4
ICON_BORDER = (34, 106, 118)
ICON_BORDER_W = 2

CONTENT_X = ICON_X + ICON_SIZE + 3  # left edge of the name box and title plate

# row A: rating badge. The reference prints it at 170x32 - wider than the
# rating_base_*.png asset's own aspect - so it's rebuilt at that size with
# only its background widened (see b50._wide_rating_badge), one px left of
# the column.
RATING_X = CONTENT_X - 1
RATING_Y = NAMEPLATE_Y + 5
RATING_W, RATING_H = 170, 32

# the class-rank medal rides up over the nameplate's top edge (y18-61
# against the plate's y25), ~18px right of the rating badge.
CLASS_GAP = 18
CLASS_BADGE_CENTER_Y = 15  # relative to NAMEPLATE_Y
CLASS_BADGE_MAX_W, CLASS_BADGE_MAX_H = 80, 44

# row B: one white box holding the name, with the dan badge pinned 3px in
# from the box's right edge (so it's flush regardless of name length).
NAME_BOX_Y = NAMEPLATE_Y + 41
NAME_BOX_W, NAME_BOX_H = 269, 41
NAME_BOX_BORDER = (152, 147, 140)
NAME_PAD_L = 6
# names are stored full-width (e.g. 'ｈｖｌ．ＥＭＵ☆'), and full-width glyphs
# carry a 1em advance - drawn at their natural advances at this size they
# land on the reference's exact 22px pitch, with no manual cell layout.
NAME_FONT_SIZE = 22
DAN_BADGE_MAX_W, DAN_BADGE_MAX_H = 80, 31
DAN_BADGE_RIGHT_PAD = 3

# row C: img/trophy_<tier>.png's own pixel size on the live site - the
# title bar is that asset at 1:1, not a scaled guess.
TITLE_Y = NAMEPLATE_Y + 86
TITLE_W, TITLE_H = 268, 25
TITLE_FONT_H = 14
CIRCLE_FONT_H = 13

# The circle banner (img/circle/profile/circle_profile_color_*.png) is
# natively 300x44, but the card prints it much wider than tall - measured
# off the real card, where it sits just under the nameplate's bottom edge
# and slightly left of the name column. Letting it keep its native aspect
# instead would make it nearly twice as tall as the title bar above it and
# swamp the card, so it's three-sliced (see _paste_sliced_plate) rather
# than resized: the chevron ends and their embossed stars stay in
# proportion and only the flat middle takes up the slack.
#
# The box below is the chip + banner's outer white keyline: chip and banner
# share its top and bottom edges exactly, and the banner's left point sits
# in the chip's notch so the two read as one piece.
RIBBON_X = CONTENT_X - 4
RIBBON_Y = NAMEPLATE_Y + NAMEPLATE_H - 4  # rides up over the plate's bottom edge
RIBBON_W = CONTENT_X + NAME_BOX_W + 1 - RIBBON_X
RIBBON_H = 23
RIBBON_CAP_FRAC = 0.22  # reaches past the stars into the plain gradient
RIBBON_CHIP_W = 55
RIBBON_CHIP_NOTCH = 9  # how deep the chip's right-hand notch cuts in
# the banner starts at the notch's vertex, under the chip's keyline
RIBBON_BODY_X = RIBBON_X + RIBBON_CHIP_W - 1 - RIBBON_CHIP_NOTCH
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


def _paste_sliced_plate(
    base: Image.Image,
    plate_bytes: bytes | None,
    pos: tuple[int, int],
    size: tuple[int, int],
    cap_frac: float,
) -> bool:
    """Three-slice horizontal scale: the two end caps scale *uniformly*
    (by the height ratio alone, keeping their aspect) and only the flat
    middle is stretched to make up the width.

    A plain resize distorts, because the target is nowhere near the
    asset's own aspect - the circle banner is 300x44 natively but the card
    prints it around 230x22, so a straight resize squashes it to half
    height while only trimming a quarter of the width. That shows up on
    everything with a recognisable shape: the chevron points flatten out
    and the embossed stars go oval. Slicing keeps those ends honest and
    puts the whole discrepancy into the middle, which is a smooth gradient
    with nothing in it to look wrong.

    `cap_frac` is how much of the source width each cap claims - it needs
    to reach past the last shaped element (the stars, here). Falls back to
    a plain resize if the caps wouldn't fit the target width.

    The asset is trimmed to its opaque bbox first, so the *visible* plate
    fills `size` - the circle banner ships with a few px of transparent
    margin that would otherwise leave it short of the chip beside it."""
    if not plate_bytes:
        return False
    try:
        with Image.open(io.BytesIO(plate_bytes)) as img:
            img = img.convert("RGBA")
            opaque = img.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
            if opaque:
                img = img.crop(opaque)
            target_w, target_h = size
            cap_src = max(1, round(img.width * cap_frac))
            cap_dst = max(1, round(cap_src * target_h / img.height))
            if cap_dst * 2 >= target_w or cap_src * 2 >= img.width:
                base.paste(img.resize(size, Image.Resampling.LANCZOS), pos, img.resize(size, Image.Resampling.LANCZOS))
                return True

            plate = Image.new("RGBA", size, (0, 0, 0, 0))
            left = img.crop((0, 0, cap_src, img.height)).resize((cap_dst, target_h), Image.Resampling.LANCZOS)
            right = img.crop((img.width - cap_src, 0, img.width, img.height)).resize(
                (cap_dst, target_h), Image.Resampling.LANCZOS
            )
            middle = img.crop((cap_src, 0, img.width - cap_src, img.height)).resize(
                (target_w - cap_dst * 2, target_h), Image.Resampling.LANCZOS
            )
            plate.paste(left, (0, 0))
            plate.paste(middle, (cap_dst, 0))
            plate.paste(right, (target_w - cap_dst, 0))
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


_SUPERSAMPLE = 4  # drawn shapes are rendered at 4x and downsampled for clean edges

# the chip and its glyph in the reference card's own pixels (a 23px-tall
# chip), scaled to RIBBON_H at draw time. Outer = white keyline, inner =
# blue face - thin on the left, a thick white band along the notch.
_CHIP_DESIGN_H = 23
_CHIP_OUTER = [(0, 11.5), (11.5, 0), (54, 0), (45, 11.5), (54, 23), (11.5, 23)]
_CHIP_INNER = [(1.5, 11.5), (12.5, 1), (49, 1), (41, 11.5), (49, 22), (12.5, 22)]
# three figures, the outer two with an arm raised - relative to the
# glyph's own origin, which sits at (11, 4) on the chip.
_GLYPH_ORIGIN = (11, 4)
_GLYPH_HEADS = [(14, 3.5, 2.4), (7.5, 5, 1.9), (20.5, 5, 1.9)]  # cx, cy, r
_GLYPH_BODIES = [(11, 7, 17, 14, 2.2), (5, 8, 9, 14, 1.6), (19, 8, 23, 14, 1.6)]  # x0, y0, x1, y1, r
_GLYPH_ARMS = [[(1.5, 3.2), (3, 7.2), (5.5, 8.8)], [(26.5, 3.2), (25, 7.2), (22.5, 8.8)]]
_GLYPH_ARM_W = 1.7


def _hexagon(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """A <=> shape filling (x0, y0)-(x1, y1), with 45-degree points - the
    circle banner's outline."""
    half = (y1 - y0) / 2
    return [
        (x0, y0 + half), (x0 + half, y0), (x1 - half, y0),
        (x1, y0 + half), (x1 - half, y1), (x0 + half, y1),
    ]


def _paste_supersampled(base: Image.Image, layer: Image.Image, pos: tuple[int, int]) -> None:
    w, h = layer.width // _SUPERSAMPLE, layer.height // _SUPERSAMPLE
    small = layer.resize((w, h), Image.Resampling.LANCZOS)
    base.paste(small, pos, small)


def _paste_circle_banner(base: Image.Image, plate_bytes: bytes | None, x: int, y: int, w: int, h: int) -> None:
    """The circle's rank-colored name banner inside a 1px white keyline,
    which the reference card draws around it."""
    ss = _SUPERSAMPLE
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(_hexagon(0, 0, w * ss, h * ss), fill=(255, 255, 255, 255))
    _paste_supersampled(base, layer, (x, y))
    if not _paste_sliced_plate(base, plate_bytes, (x + 1, y + 1), (w - 2, h - 2), RIBBON_CAP_FRAC):
        # asset unavailable - a flat bronze-ish fill in the same shape
        layer = Image.new("RGBA", ((w - 2) * ss, (h - 2) * ss), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(_hexagon(0, 0, (w - 2) * ss, (h - 2) * ss), fill=(150, 84, 48, 255))
        _paste_supersampled(base, layer, (x + 1, y + 1))


def _paste_circle_chip(base: Image.Image, x: int, y: int, h: int) -> None:
    """The blue "circle" tab fused to the left end of the circle banner:
    a chevron pointing out on the left and notched on the right (the
    banner's own left point nests into the notch), with a white keyline
    and the three-figure group glyph.

    Neither the chevron nor the glyph has a downloadable asset anywhere on
    maimai DX NET - they only exist on the card itself - so both are drawn
    from shapes measured off the reference, at 4x and downsampled. The
    face is shaded with a vertical gradient lifted off the real card
    rather than filled flat."""
    ss = _SUPERSAMPLE
    k = h / _CHIP_DESIGN_H * ss
    w = RIBBON_CHIP_W
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.polygon([(px * k, py * k) for px, py in _CHIP_OUTER], fill=(255, 255, 255, 255))

    face_mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(face_mask).polygon([(px * k, py * k) for px, py in _CHIP_INNER], fill=255)
    layer.paste(_vertical_gradient(layer.size, CHIP_GRADIENT).convert("RGBA"), (0, 0), face_mask)

    gx, gy = _GLYPH_ORIGIN
    white = (255, 255, 255, 255)
    for cx, cy, r in _GLYPH_HEADS:
        ldraw.ellipse([((gx + cx - r) * k, (gy + cy - r) * k), ((gx + cx + r) * k, (gy + cy + r) * k)], fill=white)
    for x0, y0, x1, y1, r in _GLYPH_BODIES:
        # rounded shoulders only - the feet are cut square by the chip
        ldraw.rounded_rectangle(
            [((gx + x0) * k, (gy + y0) * k), ((gx + x1) * k - 1, (gy + y1) * k - 1)],
            radius=r * k,
            fill=white,
            corners=(True, True, False, False),
        )
    for arm in _GLYPH_ARMS:
        ldraw.line([((gx + px) * k, (gy + py) * k) for px, py in arm], fill=white, width=round(_GLYPH_ARM_W * k), joint="curve")
        for px, py in (arm[0], arm[-1]):  # round the stroke ends
            r = _GLYPH_ARM_W * k / 2
            ldraw.ellipse([((gx + px) * k - r, (gy + py) * k - r), ((gx + px) * k + r, (gy + py) * k + r)], fill=white)

    _paste_supersampled(base, layer, (x, y))


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
    The nameplate is a fixed 720x116 inset box near the top-left holding
    the profile content - icon on the left; rating badge + class-rank
    medal (riding up over the nameplate's top edge) on top; name box with
    the dan/course-rank badge pinned inside its right edge below that;
    title plate along the bottom edge. The circle's name banner renders directly beneath
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

    # nameplate: square-cornered, no outline of our own (see NAMEPLATE_*)
    nameplate_img = _fit_nameplate(nameplate_bytes, NAMEPLATE_W, NAMEPLATE_H)
    image.paste(nameplate_img, (NAMEPLATE_X, NAMEPLATE_Y))

    # icon - cover-fit so a non-square source still fills the slot, inside
    # a thin dark-teal keyline.
    icon_mask = _rounded_mask((ICON_SIZE, ICON_SIZE), ICON_RADIUS)
    icon_img = None
    if icon_bytes:
        try:
            with Image.open(io.BytesIO(icon_bytes)) as icon_src:
                icon_img = _cover_fit(icon_src.convert("RGB"), ICON_SIZE, ICON_SIZE)
        except Exception:
            icon_img = None
    if icon_img is None:
        icon_img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (200, 200, 205))
    image.paste(icon_img, (ICON_X, ICON_Y), icon_mask)
    draw.rounded_rectangle(
        [(ICON_X, ICON_Y), (ICON_X + ICON_SIZE - 1, ICON_Y + ICON_SIZE - 1)],
        radius=ICON_RADIUS,
        outline=ICON_BORDER,
        width=ICON_BORDER_W,
    )

    # row A: rating badge (widened to the reference's box) + class-rank
    # medal to its right, riding up over the nameplate's top edge.
    rating_text = str(profile.rating) if profile.rating is not None else "?"
    rating_w = _paste_rating_badge(
        image, draw, rating_badge_bytes, rating_text, (RATING_X, RATING_Y), RATING_H, RATING_TEXT_COLOR, width=RATING_W
    )
    _paste_contain_left(
        image,
        class_rank_bytes,
        RATING_X + (rating_w or RATING_W) + CLASS_GAP,
        NAMEPLATE_Y + CLASS_BADGE_CENTER_Y,
        CLASS_BADGE_MAX_W,
        CLASS_BADGE_MAX_H,
    )

    # row B: name box, dan/course-rank badge pinned to its right edge.
    draw.rounded_rectangle(
        [(CONTENT_X, NAME_BOX_Y), (CONTENT_X + NAME_BOX_W - 1, NAME_BOX_Y + NAME_BOX_H)],
        radius=3,
        fill=(255, 255, 255),
        outline=NAME_BOX_BORDER,
    )
    dan_right = CONTENT_X + NAME_BOX_W - DAN_BADGE_RIGHT_PAD
    dan_w = _paste_contain_right(
        image,
        course_rank_bytes,
        dan_right,
        NAME_BOX_Y + NAME_BOX_H // 2 + 1,
        DAN_BADGE_MAX_W,
        DAN_BADGE_MAX_H,
    )
    if profile.display_name:
        name_x = CONTENT_X + NAME_PAD_L
        name_max_w = (dan_right - (dan_w or 0)) - name_x - 2
        name_font = _fit_font(draw, profile.display_name, _JP_REGULAR, name_max_w, NAME_FONT_SIZE)
        # vertical centring on the em box, not on the ink, so a name with
        # no ascenders sits at the same baseline as one with them.
        ascent, descent = name_font.getmetrics()
        name_y = NAME_BOX_Y + (NAME_BOX_H - (ascent + descent)) / 2
        draw.text((name_x, name_y), profile.display_name, font=name_font, fill=(20, 20, 20))

    # row C: title/trophy bar. The real img/trophy_<tier>.png is 268x25,
    # drawn here 1:1, with the title in bold white and a black keyline - the
    # same treatment the reference card uses on every tier, which is also
    # what keeps it readable on the pale Normal/Silver plates.
    if not _paste_plate(image, title_plate_bytes, (CONTENT_X, TITLE_Y), (TITLE_W, TITLE_H)):
        # asset unavailable - fall back to the drawn capsule, tinted by
        # whatever tier the page reported.
        _, mid_hex, dark_hex = _TIER_COLORS.get(profile.title_tier or "", _TIER_COLORS["Gold"])
        capsule_radius = TITLE_H // 2
        draw.rounded_rectangle(
            [(CONTENT_X, TITLE_Y), (CONTENT_X + TITLE_W, TITLE_Y + TITLE_H)],
            radius=capsule_radius,
            fill=_hex_to_rgb(dark_hex),
        )
        inset = 3
        draw.rounded_rectangle(
            [(CONTENT_X + inset, TITLE_Y + inset), (CONTENT_X + TITLE_W - inset, TITLE_Y + TITLE_H - inset)],
            radius=max(capsule_radius - inset, 2),
            fill=_hex_to_rgb(mid_hex),
        )
    if profile.title:
        _draw_outlined_text(
            draw,
            profile.title,
            _JP_BOLD,
            (CONTENT_X + 10, TITLE_Y, TITLE_W - 20, TITLE_H - 3),
            (255, 255, 255),
            (0, 0, 0),
            stroke=1,
            font_h=TITLE_FONT_H,
        )

    # circle banner: the real rank-colored name plate from the circle
    # profile page, tucked under the nameplate the way the physical card
    # overlaps it. Skipped entirely when the account is not in a circle.
    if circle is not None and circle.name:
        # the banner starts at the chip's notch, not under the whole chip -
        # otherwise the chip swallows the banner art's own left-hand star.
        # The chip goes on top, so its keyline covers the seam.
        body_x = RIBBON_BODY_X
        body_w = RIBBON_X + RIBBON_W - body_x
        _paste_circle_banner(image, circle_color_bytes, body_x, RIBBON_Y, body_w, RIBBON_H)
        _paste_circle_chip(image, RIBBON_X, RIBBON_Y, RIBBON_H)
        # the site prints the circle name over this banner in bold with a
        # white outline - the banner art is busy enough that plain dark
        # text on it is hard to read. Centred over the body only, so the
        # chip doesn't push it off-centre.
        _draw_outlined_text(
            draw,
            circle.name,
            _JP_BOLD,
            (body_x + 14, RIBBON_Y, body_w - 28, RIBBON_H),
            (11, 56, 113),
            (255, 255, 255),
            stroke=1,
            font_h=CIRCLE_FONT_H,
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

    _draw_guide_box(draw, (RATING_X, RATING_Y, RATING_X + RATING_W, RATING_Y + RATING_H), "RATING BADGE", color=(0, 200, 255))
    class_x = RATING_X + RATING_W + CLASS_GAP
    class_top = NAMEPLATE_Y + CLASS_BADGE_CENTER_Y - CLASS_BADGE_MAX_H // 2
    _draw_guide_box(
        draw,
        (class_x, class_top, class_x + CLASS_BADGE_MAX_W, class_top + CLASS_BADGE_MAX_H),
        "CLASS BADGE",
        color=(0, 200, 255),
    )

    _draw_guide_box(draw, (CONTENT_X, NAME_BOX_Y, CONTENT_X + NAME_BOX_W, NAME_BOX_Y + NAME_BOX_H), "NAME BOX")
    dan_right = CONTENT_X + NAME_BOX_W - DAN_BADGE_RIGHT_PAD
    dan_top = NAME_BOX_Y + NAME_BOX_H // 2 + 1 - DAN_BADGE_MAX_H // 2
    _draw_guide_box(
        draw,
        (dan_right - DAN_BADGE_MAX_W, dan_top, dan_right, dan_top + DAN_BADGE_MAX_H),
        "DAN/COURSE RANK BADGE",
        color=(0, 200, 255),
    )

    _draw_guide_box(draw, (CONTENT_X, TITLE_Y, CONTENT_X + TITLE_W, TITLE_Y + TITLE_H), "TITLE PLATE")
    _draw_guide_box(draw, (RIBBON_X, RIBBON_Y, RIBBON_X + RIBBON_W, RIBBON_Y + RIBBON_H), "CIRCLE BANNER")

    image.save(output, "PNG", compress_level=3)
    output.seek(0)
