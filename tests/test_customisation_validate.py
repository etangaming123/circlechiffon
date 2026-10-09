"""customisation/validate.py - everything /cc-template-upload accepts or rejects.

An upload comes from a stranger, so the point of these tests is mostly the
"no" cases: each one must raise TemplateError with a message safe to show the
user, never crash or be silently accepted.
"""

import io
import json
import struct
import zlib

import pytest
from PIL import Image

from circlechiffon.customisation.validate import (
    MAX_IMAGE_BYTES,
    MAX_LAYOUT_BYTES,
    TemplateError,
    normalise_image,
    validate_layout,
)
from circlechiffon.renderers.guides import TEMPLATE_KINDS


def enc(obj) -> bytes:
    return json.dumps(obj).encode()


@pytest.fixture
def good():
    return TEMPLATE_KINDS["b50"].default_layout()


def reject(data: bytes, kind="b50", match=None):
    with pytest.raises(TemplateError, match=match):
        validate_layout(data, kind)


# -- layout.json -------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(TEMPLATE_KINDS))
def test_every_kinds_default_layout_is_valid(kind):
    defaults = TEMPLATE_KINDS[kind].default_layout()
    clean, warnings = validate_layout(enc(defaults), kind)
    assert clean["kind"] == kind and clean["canvas"] == list(defaults["canvas"])
    assert warnings == []


def test_utf8_bom_is_tolerated(good):
    clean, _ = validate_layout(b"\xef\xbb\xbf" + enc(good), "b50")
    assert clean["kind"] == "b50"


def test_wrong_version(good):
    reject(enc({**good, "version": 2}), match="version")


def test_layout_for_a_different_kind(good):
    reject(enc(good), kind="profile_core", match="not 'profile_core'")


@pytest.mark.parametrize("raw", [b"\xff\xfe garbage", b"not json", b"", b"[]", b'"string"', b"42"])
def test_non_object_json(raw):
    reject(raw)


def test_oversize_layout():
    reject(b" " * (MAX_LAYOUT_BYTES + 1), match="too large")


def test_nan_and_infinity_are_rejected(good):
    base = '{"version":1,"kind":"b50","canvas":[6144,2508],"elements":{"icon":{"x":%s}}}'
    for bad in ("NaN", "Infinity", "-Infinity"):
        reject((base % bad).encode(), match="number")


@pytest.mark.parametrize(
    "element, match",
    [
        ({"x": 10**12}, "out of range"),
        ({"w": -5}, "greater than 0"),
        ({"w": 0}, "greater than 0"),
        ({"x": True}, "must be a number"),
        ({"x": "10"}, "must be a number"),
        ({"visible": "no"}, "true or false"),
        ({"follow": "nope"}, "follow"),
        ({"follow": "icon"}, "follow"),                    # can't follow itself
        ({"opacity": 2}, "0 to 1"),
        ({"opacity": True}, "0 to 1"),
    ],
)
def test_bad_element_values(good, element, match):
    reject(enc({**good, "elements": {"icon": element}}), match=match)


def test_follow_loop_is_rejected(good):
    reject(enc({**good, "elements": {"name": {"follow": "rating_badge"}, "rating_badge": {"follow": "name"}}}),
           match="loop")


@pytest.mark.parametrize("canvas", [[0, 5], ["x", 5], [100], [1.5, 5], [20000, 5], "big", [True, 5]])
def test_bad_canvas(good, canvas):
    reject(enc({**good, "canvas": canvas}), match="canvas")


def test_elements_must_be_an_object(good):
    reject(enc({**good, "elements": []}), match="elements")


def test_option_must_be_boolean(good):
    option = next(iter(good["options"]))
    reject(enc({**good, "options": {option: 1}}), match="true or false")


def test_unknown_names_are_dropped_with_warnings_not_errors(good):
    clean, warnings = validate_layout(
        enc({**good, "elements": {"ghost": {}, "icon": {"x": 1.6, "zzz": 1}}, "options": {"what": True}}), "b50"
    )
    assert "ghost" not in clean["elements"]
    assert clean["elements"]["icon"] == {"x": 2}                    # float rounded, unknown key dropped
    assert any("ghost" in w for w in warnings) and any("zzz" in w for w in warnings)
    assert any("what" in w for w in warnings)


def test_all_problems_are_reported_together(good):
    with pytest.raises(TemplateError) as err:
        validate_layout(enc({**good, "version": 9, "elements": {"icon": {"x": "a", "w": -1}}}), "b50")
    assert str(err.value).count("\n- ") >= 3


def test_colours_must_be_six_digit_hex_and_are_lowercased(good):
    text_element = sorted(TEMPLATE_KINDS["b50"].text_elements)[0]
    clean, _ = validate_layout(enc({**good, "elements": {text_element: {"color": "#FFCC00"}}}), "b50")
    assert clean["elements"][text_element]["color"] == "#ffcc00"
    for bad in ("red", "#fc0", "ffcc00", "#gggggg", 5):
        reject(enc({**good, "elements": {text_element: {"color": bad}}}), match="hex colour")


def test_text_styling_on_a_non_text_element_is_ignored(good):
    non_text = next(n for n in good["elements"] if n not in TEMPLATE_KINDS["b50"].text_elements)
    clean, warnings = validate_layout(enc({**good, "elements": {non_text: {"color": "#ffffff"}}}), "b50")
    assert "color" not in clean["elements"][non_text]
    assert any("isn't a text element" in w for w in warnings)


# -- images --------------------------------------------------------------------------


def png(size, colour=(255, 0, 0), mode="RGB", fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, colour).save(buf, fmt)
    return buf.getvalue()


def test_fixed_size_kind_is_resized_to_the_canvas():
    out, warnings = normalise_image(png((1000, 1000), fmt="JPEG"), "b50")
    canvas = tuple(TEMPLATE_KINDS["b50"].default_layout()["canvas"])
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "PNG" and img.size == canvas and img.mode == "RGBA"
    assert warnings and "stretched" in warnings[0]                  # 1:1 isn't the canvas aspect


def test_right_aspect_ratio_gives_no_warning():
    w, h = TEMPLATE_KINDS["b50"].default_layout()["canvas"]
    _, warnings = normalise_image(png((w // 4, h // 4)), "b50")
    assert warnings == []


def test_variable_height_kind_keeps_aspect_ratio():
    canvas_w = TEMPLATE_KINDS["profile_extra"].default_layout()["canvas"][0]
    out, _ = normalise_image(png((canvas_w * 2, canvas_w)), "profile_extra")
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (canvas_w, canvas_w // 2)


def test_webp_and_transparency_are_accepted():
    out, _ = normalise_image(png((50, 50), (0, 0, 0, 0), mode="RGBA", fmt="WEBP"), "b50")
    with Image.open(io.BytesIO(out)) as img:
        assert img.getpixel((0, 0))[3] == 0


def test_not_an_image():
    with pytest.raises(TemplateError, match="isn't an image"):
        normalise_image(b"hello world", "b50")


def test_unsupported_format():
    with pytest.raises(TemplateError, match="Unsupported"):
        normalise_image(png((10, 10), fmt="GIF"), "b50")


def test_oversize_file():
    with pytest.raises(TemplateError, match="too large"):
        normalise_image(b"\0" * (MAX_IMAGE_BYTES + 1), "b50")


def test_decompression_bomb_header_is_rejected_before_decoding():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 6, 0, 0, 0)       # claims 20000x20000
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
    assert len(bomb) < 100
    with pytest.raises(TemplateError):
        normalise_image(bomb, "b50")
