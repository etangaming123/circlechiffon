import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from circlechiffon.renderers.b50 import (
    FONT_DIR,
    RATING_ACCENT_COLOR as RATING_TEXT_COLOR,
    _TIER_COLORS,
    _diagonal_gradient,
    _fit_font,
    _hex_to_rgb,
    _paste_rating_badge,
    _paste_scaled,
    _rounded_mask,
    _truncate_to_width,
)
from circlechiffon.types import Circle, Profile

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
FALLBACK_TEMPLATE_PATH = ASSETS_DIR / "b50" / "template.png"

_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_JP_MEDIUM = str(FONT_DIR / "NotoSansJP-Medium.ttf")

FONT_RIBBON = ImageFont.truetype(_JP_MEDIUM, 20)

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
_ROW_SCALE = NAMEPLATE_H / 134  # still used for the ribbon sizing below
ROW_A_Y, ROW_A_H = 5, 33  # rating pill + class badge
ROW_B_Y, ROW_B_H = 41, 45  # name box + dan badge
ROW_C_Y, ROW_C_H = 88, 25  # title, bottom row

RIBBON_GAP = 8  # circle label sits this far below the nameplate, not overlapping it
RIBBON_H = round(40 * _ROW_SCALE)
RIBBON_W = round(358 * _ROW_SCALE)
RIBBON_CHIP_W = round(46 * _ROW_SCALE)
RIBBON_NOTCH = round(14 * _ROW_SCALE)

BACKGROUND_COLOR = (24, 24, 32)

CARD_BORDER_COLOR = (198, 148, 84)
RIBBON_CHIP_COLOR = (30, 60, 150)
RIBBON_GRADIENT_1 = "#7e2a13"
RIBBON_GRADIENT_2 = "#db983f"

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


def _draw_tracked_text(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box_x: int,
    box_y: int,
    box_w: int,
    box_h: int,
    fill: tuple[int, int, int],
    center: bool = False,
    min_tracking: float = 0.68,
    font_h: int | None = None,
) -> None:
    """Draws `text` sized to fit font_h (defaults to box_h), pulling
    letters closer together (reduced tracking between glyphs, like
    negative letter-spacing) if the natural width overflows box_w -
    unlike a horizontal resize, each glyph keeps its own proportions,
    it's just packed tighter. Vertically centered within the full box_h
    even when font_h is smaller (e.g. a title bar taller than the text
    should actually render at). Only truncates as a last resort, if even
    the tightest tracking isn't enough."""
    if not text:
        return
    font = _fit_font(draw, text, font_path, 10_000, font_h if font_h is not None else box_h)
    natural_w = draw.textlength(text, font=font)
    tracking = min(1.0, box_w / natural_w) if natural_w > 0 else 1.0
    tracking = max(tracking, min_tracking)
    while text and draw.textlength(text, font=font) * tracking > box_w:
        text = text[:-1]
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    total_w = draw.textlength(text, font=font) * tracking
    x = box_x + (box_w - total_w) / 2 if center else box_x
    y = box_y + (box_h - text_h) / 2 - bbox[1]
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) * tracking


def _ribbon_polygon(x0: int, y0: int, x1: int, y1: int, notch: int) -> list[tuple[int, int]]:
    """A banner/ribbon shape: concave notch on the left edge (where it
    meets the icon chip), convex point on the right edge."""
    mid_y = (y0 + y1) // 2
    return [
        (x0 + notch, y0),
        (x1, y0),
        (x1 + notch, mid_y),
        (x1, y1),
        (x0 + notch, y1),
        (x0, mid_y),
    ]


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
    output,
) -> None:
    """Synchronous - CPU-bound Pillow work. Call via asyncio.to_thread().

    Fixed 1080x452 canvas: `frame_bytes` (the equipped Frame collectible)
    is a cover-fit (non-stretching) full-bleed backdrop behind everything.
    The nameplate is a fixed ~722x117 inset box near the top-left holding
    the profile content - icon on the left; rating pill + class-rank
    badge (allowed to spill a bit past the nameplate's right edge) on
    top; name box + dan/course-rank badge below that; title capsule along
    the bottom edge. The circle name renders as a separate ribbon label
    directly beneath the nameplate box, outside of it. The title/trophy
    banner has no image asset anywhere on the site (confirmed via
    collection/trophy/'s DOM - it's CSS-styled text), so it's drawn as a
    gradient capsule here, colored by `profile.title_tier` (e.g. "Gold").
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
    name_pad = 8
    name_text_w = 170
    name_badge_gap = 6
    name_badge_w = 80
    name_box_w = name_pad + name_text_w + name_badge_gap + name_badge_w + name_pad
    draw.rounded_rectangle(
        [(CONTENT_X, row_b_y), (CONTENT_X + name_box_w, row_b_y + ROW_B_H)],
        radius=6,
        fill=(255, 255, 255),
        outline=(200, 200, 200),
    )
    if profile.display_name:
        _draw_tracked_text(
            image,
            draw,
            profile.display_name,
            _JP_MEDIUM,
            CONTENT_X + name_pad,
            row_b_y,
            name_text_w,
            ROW_B_H,
            (20, 20, 20),
            font_h=round((ROW_B_H - 10) * 0.85),
            min_tracking=0.55,
        )
    _paste_contain_left(
        image,
        course_rank_bytes,
        CONTENT_X + name_pad + name_text_w + name_badge_gap,
        row_b_y + ROW_B_H // 2,
        name_badge_w,
        ROW_B_H - 6,
    )

    # row C: title bar, along the very bottom edge of the nameplate,
    # capsule (fully rounded ends) - no image asset exists for this
    # anywhere on the site, so it's drawn. Narrower for the same reason
    # as the name box, and the text is capped well under the bar's own
    # height so it doesn't poke above/below the capsule into the
    # background art.
    row_c_y = NAMEPLATE_Y + ROW_C_Y
    title_box_w = min(270, round(content_w * 0.58))
    light_hex, mid_hex, dark_hex = _TIER_COLORS.get(profile.title_tier or "", _TIER_COLORS["Gold"])
    capsule_radius = ROW_C_H // 2
    draw.rounded_rectangle(
        [(CONTENT_X, row_c_y), (CONTENT_X + title_box_w, row_c_y + ROW_C_H)],
        radius=capsule_radius,
        fill=_hex_to_rgb(dark_hex),
    )
    inset = 3
    draw.rounded_rectangle(
        [(CONTENT_X + inset, row_c_y + inset), (CONTENT_X + title_box_w - inset, row_c_y + ROW_C_H - inset)],
        radius=max(capsule_radius - inset, 2),
        fill=_hex_to_rgb(mid_hex),
    )
    if profile.title:
        _draw_tracked_text(
            image,
            draw,
            profile.title,
            _JP_BOLD,
            CONTENT_X + 12,
            row_c_y,
            title_box_w - 24,
            ROW_C_H,
            _hex_to_rgb(dark_hex),
            center=True,
            font_h=ROW_C_H - 12,
        )

    # circle ribbon - disabled for now, workin' on it.
    # if circle is not None and circle.name:
    #     ribbon_y = NAMEPLATE_Y + NAMEPLATE_H + RIBBON_GAP
    #     body_x0 = NAMEPLATE_X + RIBBON_CHIP_W
    #     body_x1 = NAMEPLATE_X + RIBBON_W
    #     poly = _ribbon_polygon(body_x0, ribbon_y, body_x1, ribbon_y + RIBBON_H, RIBBON_NOTCH)
    #     gradient = _diagonal_gradient(
    #         (body_x1 - body_x0 + RIBBON_NOTCH, RIBBON_H), _hex_to_rgb(RIBBON_GRADIENT_1), _hex_to_rgb(RIBBON_GRADIENT_2)
    #     ).convert("RGBA")
    #     mask = Image.new("L", image.size, 0)
    #     ImageDraw.Draw(mask).polygon(poly, fill=255)
    #     gradient_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    #     gradient_layer.paste(gradient, (body_x0 - RIBBON_NOTCH, ribbon_y))
    #     image.paste(gradient_layer, (0, 0), mask)
    #     draw.polygon(poly, outline=_hex_to_rgb(RIBBON_GRADIENT_1), width=2)
    #
    #     draw.rounded_rectangle(
    #         [(NAMEPLATE_X, ribbon_y - 2), (NAMEPLATE_X + RIBBON_CHIP_W + RIBBON_NOTCH, ribbon_y + RIBBON_H + 2)],
    #         radius=8,
    #         fill=RIBBON_CHIP_COLOR,
    #     )
    #     glyph_cx, glyph_cy = NAMEPLATE_X + RIBBON_CHIP_W // 2, ribbon_y + RIBBON_H // 2
    #     r = RIBBON_CHIP_W // 5
    #     for dx in (-r, r):
    #         draw.ellipse([(glyph_cx + dx - r, glyph_cy - r), (glyph_cx + dx + r, glyph_cy + r)], fill=(235, 240, 255))
    #
    #     # circle names on the real site already commonly end in their own
    #     # "☆" (as this account's does) - only add the leading one here to
    #     # avoid doubling up.
    #     circle_label = f"☆{_truncate_to_width(draw, circle.name, FONT_RIBBON, body_x1 - body_x0 - 30)}"
    #     label_w = draw.textlength(circle_label, font=FONT_RIBBON)
    #     bbox = draw.textbbox((0, 0), circle_label, font=FONT_RIBBON)
    #     label_h = bbox[3] - bbox[1]
    #     text_x = body_x0 + (body_x1 - body_x0 - label_w) / 2
    #     text_y = ribbon_y + (RIBBON_H - label_h) / 2 - bbox[1]
    #     draw.text(
    #         (text_x, text_y),
    #         circle_label,
    #         font=FONT_RIBBON,
    #         fill=(255, 255, 255),
    #         stroke_width=2,
    #         stroke_fill=_hex_to_rgb(RIBBON_GRADIENT_1),
    #     )

    image.save(output, "PNG", compress_level=3)
    output.seek(0)
