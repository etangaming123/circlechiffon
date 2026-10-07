"""
Upload-time checks for custom template parts - everything a user sends
through /cc-template-upload passes through here before it touches disk.

Images are decoded once, checked, fitted to the render canvas and
re-encoded as PNG, so render time never has to deal with an oversized,
oddly-formatted or metadata-laden file. Layout JSON is parsed against the
kind's default layout and rebuilt from known keys only, so what's stored is
always a clean subset of the schema (see docs/customisation.md).
"""

import io
import json
import math
import re

from PIL import Image

from circlechiffon.renderers.guides import TEMPLATE_KINDS
from circlechiffon.renderers.layout import LAYOUT_VERSION

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIM = 8192
MAX_LAYOUT_BYTES = 64 * 1024
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
# variable-height canvases (profile extra) keep the image's own aspect
# ratio - cap how tall a width-fitted image may end up.
_MAX_FITTED_HEIGHT = 8192

_ELEMENT_KEYS = {"x", "y", "w", "h", "visible", "follow", "gap", "opacity", "color", "outline_color", "outline_width"}
# styling that only means something on text elements
_TEXT_ONLY_KEYS = {"color", "outline_color", "outline_width"}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
MAX_OUTLINE_WIDTH = 32


class TemplateError(ValueError):
    """An upload was rejected. The message is safe to show the user."""


def normalise_image(data: bytes, kind: str) -> tuple[bytes, list[str]]:
    """Returns (PNG bytes fitted to `kind`'s canvas, warnings). Raises
    TemplateError for anything that isn't a sane PNG/JPEG/WebP."""
    spec = TEMPLATE_KINDS[kind]
    canvas_w, canvas_h = spec.default_layout()["canvas"]
    if len(data) > MAX_IMAGE_BYTES:
        raise TemplateError(f"Image is too large ({len(data) / 1024 / 1024:.1f} MB, max {MAX_IMAGE_BYTES // 1024 // 1024} MB).")
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise TemplateError(f"Image is far too large - each side must be at most {MAX_IMAGE_DIM}px.") from None
    except Exception:
        raise TemplateError("That file isn't an image Pillow can read (use PNG, JPEG or WebP).") from None
    with img:
        if img.format not in _ALLOWED_FORMATS:
            raise TemplateError(f"Unsupported image format {img.format or '?'} (use PNG, JPEG or WebP).")
        # Image.open only reads the header - check the size before any
        # pixel data is decoded, so a decompression bomb never gets loaded.
        w, h = img.size
        if w < 1 or h < 1 or w > MAX_IMAGE_DIM or h > MAX_IMAGE_DIM:
            raise TemplateError(f"Image is {w}x{h}; each side must be at most {MAX_IMAGE_DIM}px.")
        try:
            img = img.convert("RGBA")
        except Exception:
            raise TemplateError("That image couldn't be decoded - it may be corrupt.") from None

    warnings = []
    if spec.fixed_size:
        if abs(w / h - canvas_w / canvas_h) > 0.02 * (canvas_w / canvas_h):
            warnings.append(
                f"Image is {w}x{h} but the render is {canvas_w}x{canvas_h} - it was stretched to fit. "
                "Match that aspect ratio to avoid distortion."
            )
        fitted = img.resize((canvas_w, canvas_h), Image.Resampling.LANCZOS) if (w, h) != (canvas_w, canvas_h) else img
    else:
        fitted_h = min(_MAX_FITTED_HEIGHT, max(1, round(h * canvas_w / w)))
        if w != canvas_w:
            warnings.append(f"Image was scaled to the render's {canvas_w}px width ({canvas_w}x{fitted_h}).")
        fitted = img.resize((canvas_w, fitted_h), Image.Resampling.LANCZOS) if (w, h) != (canvas_w, fitted_h) else img

    out = io.BytesIO()
    fitted.save(out, "PNG", compress_level=6)
    return out.getvalue(), warnings


def _number(value, path: str, errors: list[str], *, limit: int, positive: bool = False) -> int | None:
    # bool is an int subclass in Python - reject it explicitly
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        errors.append(f"{path} must be a number.")
        return None
    if positive and value <= 0:
        errors.append(f"{path} must be greater than 0.")
        return None
    if abs(value) > limit:
        errors.append(f"{path} is out of range (±{limit}).")
        return None
    return round(value)


def _opacity(value, path: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        errors.append(f"{path} must be a number from 0 to 1.")
        return None
    if not 0 <= value <= 1:
        errors.append(f"{path} must be from 0 to 1.")
        return None
    return round(float(value), 3)


def _color(value, path: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
        errors.append(f"{path} must be a hex colour like #ffcc00.")
        return None
    return value.lower()


def _outline_width(value, path: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        errors.append(f"{path} must be a number.")
        return None
    if not 0 <= value <= MAX_OUTLINE_WIDTH:
        errors.append(f"{path} must be from 0 to {MAX_OUTLINE_WIDTH}.")
        return None
    return round(float(value), 2)


def _clean_group(
    raw, defaults: dict, path: str, limit: int, errors: list[str], warnings: list[str], text_names=frozenset()
) -> dict:
    if not isinstance(raw, dict):
        errors.append(f"{path} must be an object.")
        return {}
    clean = {}
    for name, element in raw.items():
        where = f"{path}.{name}"
        if name not in defaults:
            warnings.append(f"Ignored unknown element {where}.")
            continue
        if not isinstance(element, dict):
            errors.append(f"{where} must be an object.")
            continue
        out = {}
        for key, value in element.items():
            if key not in _ELEMENT_KEYS:
                warnings.append(f"Ignored unknown key {where}.{key}.")
            elif key in _TEXT_ONLY_KEYS and name not in text_names:
                warnings.append(f"Ignored {where}.{key} - {name} isn't a text element.")
            elif key == "opacity" and name.startswith("grid_"):
                warnings.append(f"Ignored {where}.opacity - grids have no opacity of their own.")
            elif key == "opacity":
                if (n := _opacity(value, f"{where}.opacity", errors)) is not None:
                    out[key] = n
            elif key in ("color", "outline_color"):
                if (c := _color(value, f"{where}.{key}", errors)) is not None:
                    out[key] = c
            elif key == "outline_width":
                if (n := _outline_width(value, f"{where}.outline_width", errors)) is not None:
                    out[key] = n
            elif key in ("x", "y", "gap"):
                if (n := _number(value, f"{where}.{key}", errors, limit=limit)) is not None:
                    out[key] = n
            elif key in ("w", "h"):
                if (n := _number(value, f"{where}.{key}", errors, limit=limit, positive=True)) is not None:
                    out[key] = n
            elif key == "visible":
                if isinstance(value, bool):
                    out[key] = value
                else:
                    errors.append(f"{where}.visible must be true or false.")
            elif key == "follow":
                if value is None or (isinstance(value, str) and value in defaults and value != name):
                    out[key] = value
                else:
                    errors.append(f"{where}.follow must be null or another element's name.")
        clean[name] = out

    # a follow chain that loops back on itself has no well-defined x
    def follows(name: str) -> str | None:
        element = clean.get(name, {})
        return element["follow"] if "follow" in element else defaults[name].get("follow")

    for start in clean:
        seen, current = {start}, follows(start)
        while current:
            if current in seen:
                errors.append(f"{path}.{start}.follow forms a loop.")
                break
            seen.add(current)
            current = follows(current)
    return clean


def validate_layout(data: bytes, kind: str) -> tuple[dict, list[str]]:
    """Parses and cleans an uploaded layout.json. Returns (clean layout,
    warnings); raises TemplateError listing every problem found."""
    if len(data) > MAX_LAYOUT_BYTES:
        raise TemplateError(f"Layout file is too large ({len(data) // 1024} KB, max {MAX_LAYOUT_BYTES // 1024} KB).")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise TemplateError("Layout file isn't valid JSON.") from None
    if not isinstance(raw, dict):
        raise TemplateError("Layout file must be a JSON object.")

    defaults = TEMPLATE_KINDS[kind].default_layout()
    errors: list[str] = []
    warnings: list[str] = []
    if raw.get("version") != LAYOUT_VERSION:
        errors.append(f"version must be {LAYOUT_VERSION}.")
    if raw.get("kind") != kind:
        errors.append(f"This layout is for {raw.get('kind')!r}, not {kind!r} - export it from the editor with {kind!r} selected.")

    canvas = raw.get("canvas")
    if (
        not isinstance(canvas, list)
        or len(canvas) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16384 for v in canvas)
    ):
        errors.append("canvas must be [width, height] in whole pixels.")
        canvas = defaults["canvas"]
    limit = 2 * max(canvas)

    clean = {"version": LAYOUT_VERSION, "kind": kind, "canvas": list(canvas)}
    spec = TEMPLATE_KINDS[kind]
    clean["elements"] = _clean_group(
        raw.get("elements", {}), defaults["elements"], "elements", limit, errors, warnings, spec.text_elements
    )

    if "card" in raw:
        if "card" not in defaults:
            warnings.append("Ignored card - this kind has no card layout.")
        elif not isinstance(raw["card"], dict):
            errors.append("card must be an object.")
        else:
            card_elements = _clean_group(
                raw["card"].get("elements", {}), defaults["card"]["elements"], "card.elements", limit, errors, warnings,
                spec.card_text_elements,
            )
            clean["card"] = {"elements": card_elements}

    if "colors" in raw:
        slots = defaults.get("colors", {})
        if not isinstance(raw["colors"], dict):
            errors.append("colors must be an object.")
        else:
            clean["colors"] = {}
            for slot, value in raw["colors"].items():
                if slot not in slots:
                    warnings.append(f"Ignored unknown colour {slot}.")
                elif (c := _color(value, f"colors.{slot}", errors)) is not None:
                    clean["colors"][slot] = c

    if "opacities" in raw:
        slots = defaults.get("opacities", {})
        if not isinstance(raw["opacities"], dict):
            errors.append("opacities must be an object.")
        else:
            clean["opacities"] = {}
            for slot, value in raw["opacities"].items():
                if slot not in slots:
                    warnings.append(f"Ignored unknown opacity {slot}.")
                elif (n := _opacity(value, f"opacities.{slot}", errors)) is not None:
                    clean["opacities"][slot] = n

    if "widths" in raw:
        slots = defaults.get("widths", {})
        if not isinstance(raw["widths"], dict):
            errors.append("widths must be an object.")
        else:
            clean["widths"] = {}
            for slot, value in raw["widths"].items():
                if slot not in slots:
                    warnings.append(f"Ignored unknown width {slot}.")
                elif (n := _outline_width(value, f"widths.{slot}", errors)) is not None:
                    clean["widths"][slot] = n

    options = raw.get("options", {})
    if not isinstance(options, dict):
        errors.append("options must be an object.")
        options = {}
    clean["options"] = {}
    for key, value in options.items():
        if key not in defaults.get("options", {}):
            warnings.append(f"Ignored unknown option {key}.")
        elif not isinstance(value, bool):
            errors.append(f"options.{key} must be true or false.")
        else:
            clean["options"][key] = value

    if errors:
        shown = errors[:10]
        more = f"\n...and {len(errors) - 10} more." if len(errors) > 10 else ""
        raise TemplateError("Layout file has problems:\n" + "\n".join(f"- {e}" for e in shown) + more)
    return clean, warnings
