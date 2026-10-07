"""
Data-driven element geometry for the customisable renderers (b50.py,
profile.py) - see docs/customisation.md for the user-facing side.

Every renderer that supports custom templates exposes `default_layout()`:
a JSON-shaped dict of named element boxes, in output pixels, that
reproduces its stock look exactly. A user's uploaded layout.json uses the
same shape and is merged over it (`Layout.build`), so a user file only has
to mention what it changes. Renderers ask `layout.box(name)` for where to
draw, and get `None` for an element the user hid.

The same defaults are exported to the static web editor as
siteresources/template-layouts.js by generate_templates.py - regenerate it
after adding or moving an element, or the editor drifts from the renderer.
"""

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont

LAYOUT_VERSION = 1

# attributes an element may carry, besides its box
_BOX_KEYS = ("x", "y", "w", "h")


@dataclass(slots=True, frozen=True)
class RenderTemplate:
    """A user's custom template for one render kind, as loaded by
    customisation/store.py. Any part may be missing. Images are already
    normalised to the canvas at upload time, but renderers still fit them
    defensively in case the canvas size changed since."""

    base: bytes | None = None
    top: bytes | None = None
    layout: dict | None = None


class Box(NamedTuple):
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@lru_cache(maxsize=256)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(6, size))


def _merge_elements(defaults: dict, user: dict | None, scale: float) -> dict:
    """User values override defaults key by key. Elements the defaults
    don't know are dropped (validate.py rejects them at upload anyway).
    User coordinates are scaled from the canvas they were authored at."""
    merged = {}
    user = user or {}
    for name, default in defaults.items():
        element = dict(default)
        override = user.get(name)
        if isinstance(override, dict):
            for key in _BOX_KEYS:
                if isinstance(override.get(key), (int, float)):
                    element[key] = round(override[key] * scale)
            if isinstance(override.get("visible"), bool):
                element["visible"] = override["visible"]
            if "follow" in override:
                element["follow"] = override["follow"] if override["follow"] in defaults else None
            if isinstance(override.get("gap"), (int, float)):
                element["gap"] = round(override["gap"] * scale)
        merged[name] = element
    return merged


class Layout:
    """Merged, scaled element geometry for one render. `drawn(name, right)`
    records where an element actually ended, so a `follow`ing element
    (e.g. the rating badge after a variable-width name) can sit right
    after it - elements must be drawn in follow order."""

    def __init__(self, elements: dict, defaults: dict, options: dict, card: "Layout | None" = None):
        self._elements = elements
        self._defaults = defaults
        self.options = options
        self.card = card
        self._drawn_right: dict[str, int | None] = {}

    @classmethod
    def build(cls, defaults: dict, user: dict | None = None, canvas_w: int | None = None) -> "Layout":
        user = user if isinstance(user, dict) else {}
        scale = 1.0
        user_canvas = user.get("canvas")
        target_w = canvas_w or defaults["canvas"][0]
        if isinstance(user_canvas, list) and len(user_canvas) == 2 and user_canvas[0]:
            scale = target_w / user_canvas[0]
        options = dict(defaults.get("options", {}))
        for key, value in (user.get("options") or {}).items():
            if key in options and isinstance(value, bool):
                options[key] = value
        card = None
        if "card" in defaults:
            card_defaults = defaults["card"]["elements"]
            user_card = (user.get("card") or {}).get("elements")
            card = cls(_merge_elements(card_defaults, user_card, scale), card_defaults, {})
        return cls(_merge_elements(defaults["elements"], user.get("elements"), scale), defaults["elements"], options, card)

    def reset_drawn(self) -> None:
        self._drawn_right.clear()

    def visible(self, name: str) -> bool:
        return self._elements[name].get("visible", True)

    def box(self, name: str, dy: int = 0) -> Box | None:
        element = self._elements[name]
        if not element.get("visible", True):
            return None
        x = element["x"]
        follow = element.get("follow")
        # a leader that hasn't been drawn yet (wrong order, or a follow
        # cycle) leaves this element at its own stored x.
        if follow and follow in self._drawn_right:
            right = self._drawn_right[follow]
            if right is None:
                # leader drew nothing (hidden, or no image) - take its
                # slot with no gap, same as the pre-layout row code did.
                x = self.box_x(follow)
            else:
                x = right + element.get("gap", 0)
        return Box(x, element["y"] + dy, element["w"], element["h"])

    def box_x(self, name: str, _seen: frozenset = frozenset()) -> int:
        """Resolved x of an element even if it's hidden - where a follower
        lands when its leader drew nothing."""
        element = self._elements[name]
        follow = element.get("follow")
        if follow and follow in self._drawn_right and follow not in _seen:
            right = self._drawn_right[follow]
            return self.box_x(follow, _seen | {name}) if right is None else right + element.get("gap", 0)
        return element["x"]

    def drawn(self, name: str, right: int | None) -> None:
        """Record the right edge an element actually drew to (None: drew
        nothing), for any element that follows it."""
        self._drawn_right[name] = right

    def scale(self, name: str, axis: str = "h") -> float:
        """How much the user resized an element relative to its default -
        fonts and inner offsets are multiplied by this."""
        default = self._defaults[name][axis]
        return self._elements[name][axis] / default if default else 1.0

    def font(self, path: str, size: int, name: str) -> ImageFont.FreeTypeFont:
        return font(path, round(size * self.scale(name)))

    def s(self, value: float, name: str) -> int:
        """An inner offset of element `name`, scaled with its height."""
        return round(value * self.scale(name))

    def raw(self, name: str) -> dict:
        return self._elements[name]


def _fit_to_width(img: Image.Image, width: int) -> Image.Image:
    if img.width == width:
        return img
    h = max(1, round(img.height * width / img.width))
    return img.resize((width, h), Image.Resampling.LANCZOS)


def _open_template_image(data: bytes | None, mode: str) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as src:
            return src.convert(mode)
    except Exception:
        return None


def make_base(template: RenderTemplate | None, size: tuple[int, int], bg: tuple[int, int, int], *, stretch: bool) -> Image.Image:
    """The canvas the renderer draws on: the user's base image if they have
    one, else flat `bg`. `stretch` fits a fixed-size canvas exactly;
    otherwise (variable-height canvases) the image is fitted to the canvas
    width and anchored top, with `bg` filling below it."""
    canvas = Image.new("RGB", size, bg)
    base = _open_template_image(template.base if template else None, "RGBA")
    if base is None:
        return canvas
    base = base.resize(size, Image.Resampling.LANCZOS) if stretch else _fit_to_width(base, size[0])
    canvas.paste(base, (0, 0), base)
    return canvas


def apply_top(image: Image.Image, template: RenderTemplate | None, *, stretch: bool) -> Image.Image:
    top = _open_template_image(template.top if template else None, "RGBA")
    if top is None:
        return image
    top = top.resize(image.size, Image.Resampling.LANCZOS) if stretch else _fit_to_width(top, image.width)
    image.paste(top, (0, 0), top)
    return image


_GUIDE_COLORS = [(255, 0, 255), (0, 200, 255), (255, 200, 0), (0, 230, 120)]


def draw_guides(image: Image.Image, layout: Layout, labels: dict[str, str], *, origin: tuple[int, int] = (0, 0), line: int = 2, label_font=None) -> None:
    """Outline + label chip for every visible element - the guide PNGs
    (generate_templates.py, /cc-template-get) and /cc-template-preview's
    guide mode. Drawn from the same Layout the renderer uses, so a guide
    can never drift from the real render."""
    draw = ImageDraw.Draw(image, "RGBA")
    ox, oy = origin
    for i, name in enumerate(labels):
        box = layout.box(name)
        if box is None:
            continue
        color = _GUIDE_COLORS[i % len(_GUIDE_COLORS)]
        x0, y0 = ox + box.x, oy + box.y
        draw.rectangle([(x0, y0), (x0 + box.w, y0 + box.h)], outline=color, width=line)
        label = labels[name]
        if label and label_font is not None:
            label_w = draw.textlength(label, font=label_font)
            chip_h = label_font.size + line * 2
            draw.rectangle([(x0, y0), (x0 + label_w + line * 3, y0 + chip_h)], fill=(0, 0, 0, 200))
            draw.text((x0 + line * 1.5, y0 + line * 0.5), label, font=label_font, fill=color)
