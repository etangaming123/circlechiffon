"""
The registry of customisable render kinds, plus guide PNGs drawn from
their layouts (renderers/layout.py).

A guide is a transparent image at the render's real output size with a
labelled outline at every element box - for designing a template's base/top
art in an external editor. Because it's drawn from the same Layout the
renderer uses (the defaults, or a user's own layout.json), it can't drift
from the real render the way the old hand-written guide functions did.
"""

from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageFont

from circlechiffon.renderers import b50, profile
from circlechiffon.renderers.layout import Layout, draw_guides


@dataclass(frozen=True)
class TemplateKind:
    key: str
    title: str  # user-facing name
    default_layout: Callable[[], dict]
    labels: dict[str, str]
    card_labels: dict[str, str] | None = None
    # True: fixed canvas, template images are stretched to it. False:
    # variable height (profile extra), images fit the width, anchored top.
    fixed_size: bool = True
    # how the web editor lets each element be resized: "both" (default),
    # "x" (width only) or "none" (move only). Advisory - the renderer
    # accepts any box, but e.g. a grid's size is fixed by its cell constants.
    resize: dict[str, str] | None = None
    # elements that draw text and so accept color / outline_color /
    # outline_width in layout.json (every element accepts opacity)
    text_elements: frozenset[str] = frozenset()
    card_text_elements: frozenset[str] = frozenset()
    # layout-level colour slots (layout.json `colors`) -> editor label; the
    # slot names and stock values are default_layout()["colors"]
    color_labels: dict[str, str] | None = None


TEMPLATE_KINDS: dict[str, TemplateKind] = {
    "b50": TemplateKind(
        "b50", "Best 50 (/cc-best)", b50.default_layout, b50.LABELS, b50.CARD_LABELS,
        resize={"grid_b35": "none", "grid_b15": "none", "card_divider": "x", "card_bg": "none"},
        text_elements=b50.TEXT_ELEMENTS, card_text_elements=b50.CARD_TEXT_ELEMENTS,
        color_labels=b50.COLOR_LABELS,
    ),
    "profile_core": TemplateKind(
        "profile_core", "Profile core (/cc-profile)", profile.default_core_layout, profile.CORE_LABELS,
        color_labels=profile.CORE_COLOR_LABELS,
    ),
    "profile_extra": TemplateKind(
        "profile_extra", "Profile extra (/cc-profile view:extra)", profile.default_extras_layout, profile.EXTRAS_LABELS,
        fixed_size=False, color_labels=profile.EXTRAS_COLOR_LABELS,
        # blocks keep their inner offsets at any width; lists grow by row
        resize={name: "x" for name in ("cp_block", "mile_block", "mission_list", "ticket_list")},
    ),
}

_FONT_PATH = str(b50.FONT_DIR / "Inter_28pt-Regular.ttf")


def editor_defaults() -> dict:
    """Everything the static web editor (template-editor.html) needs to
    know about each kind - written to siteresources/template-layouts.js by
    generate_templates.py."""
    kinds = {
        key: {
            "title": spec.title,
            "fixedSize": spec.fixed_size,
            "defaults": spec.default_layout(),
            "labels": spec.labels,
            "cardLabels": spec.card_labels,
            "resize": spec.resize or {},
            "textElements": sorted(spec.text_elements),
            "cardTextElements": sorted(spec.card_text_elements),
            "colorLabels": spec.color_labels or {},
        }
        for key, spec in TEMPLATE_KINDS.items()
    }
    # enough of the b50 grid geometry for the editor to outline every card
    # inside the grid boxes, and to show the base image behind card #1
    kinds["b50"]["grid"] = {
        "cell": [b50.COL_WIDTH, b50.ROW_HEIGHT],
        "cellPadding": b50.CELL_PADDING,
        "rows": b50.GRID_ROWS,
        "cols": {"grid_b35": b50.B35_COLS, "grid_b15": b50.B15_COLS},
    }
    return kinds


def render_guide(kind: str, output, user_layout: dict | None = None) -> None:
    """Synchronous. Writes the guide PNG for `kind`, drawn from
    `user_layout` merged over the defaults (or the defaults alone)."""
    spec = TEMPLATE_KINDS[kind]
    defaults = spec.default_layout()
    canvas_w, canvas_h = defaults["canvas"]
    user_canvas = (user_layout or {}).get("canvas")
    if isinstance(user_canvas, list) and len(user_canvas) == 2 and user_canvas[0] and not spec.fixed_size:
        canvas_h = round(user_canvas[1] * canvas_w / user_canvas[0])
    layout = Layout.build(defaults, user_layout, canvas_w)

    # guide lines/labels scale with the canvas, so b50's 2x supersample
    # doesn't get hairlines
    unit = max(1, round(canvas_w / 1500))
    image = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    label_font = ImageFont.truetype(_FONT_PATH, 11 * unit)
    draw_guides(image, layout, spec.labels, line=2 * unit, label_font=label_font)

    if spec.card_labels and layout.card is not None:
        small_font = ImageFont.truetype(_FONT_PATH, 9 * unit)
        for grid, cols in (("grid_b35", b50.B35_COLS), ("grid_b15", b50.B15_COLS)):
            box = layout.box(grid)
            if box is None:
                continue
            for i in range(cols * b50.GRID_ROWS):
                x = box.x + (i % cols) * b50.COL_WIDTH + b50.CELL_PADDING
                y = box.y + (i // cols) * b50.ROW_HEIGHT + b50.CELL_PADDING
                # only the first card carries labels - fifty copies of the
                # same labels just bury the outlines.
                labels = spec.card_labels if i == 0 else dict.fromkeys(spec.card_labels, "")
                draw_guides(image, layout.card, labels, origin=(x, y), line=unit, label_font=small_font)

    image.save(output, "PNG", compress_level=3)
    output.seek(0)
