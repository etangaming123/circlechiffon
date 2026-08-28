from PIL import Image, ImageDraw, ImageFont

from circlechiffon.renderers.b50 import FONT_DIR

_INTER_BOLD = str(FONT_DIR / "Inter_28pt-Bold.ttf")

# Matches the real maimai DX NET achievement gauge widget: a row of
# same-size cells, each worth 100% achievement, followed by a stadium-
# shaped "Achievement Progress" readout pill. Segment count is fixed at 8
# (not derived from the value) so the bar has a stable size regardless of
# how high a circle's cumulative achievement climbs - values above 800%
# just read as a fully-filled bar, same clamp-for-display philosophy as
# cogs/circle.py's text _gauge_bar.
SEGMENT_COUNT = 8
SEGMENT_VALUE = 100.0

CANVAS_W, CANVAS_H = 440, 150

_PANEL_MARGIN = 6
_PANEL_RADIUS = 16
_PANEL_BG = (0xFB, 0xE4, 0xF0)
_PANEL_BORDER = (0xE3, 0x9F, 0xC9)

_BAR_X0, _BAR_Y0 = 22, 22
_BAR_X1, _BAR_Y1 = CANVAS_W - 22, 22 + 50
_SEGMENT_GAP = 4
_SEGMENT_RADIUS = 7
_SEGMENT_FILL = (0xFF, 0x2E, 0x92)
_SEGMENT_EMPTY = (0xC9, 0xC9, 0xC9)

_PILL_X0, _PILL_Y0 = 20, 92
_PILL_X1, _PILL_Y1 = CANVAS_W - 20, 92 + 42
_PILL_BORDER = (0x17, 0x34, 0x8C)
_PILL_BG = (0xFF, 0xFF, 0xFF)
_PILL_BORDER_WIDTH = 3
_LABEL_COLOR = (0x17, 0x34, 0x8C)
_VALUE_COLOR = (0xFF, 0x00, 0x8C)


def _draw_segment(draw: ImageDraw.ImageDraw, x0: int, x1: int, fraction: float) -> None:
    if fraction <= 0:
        draw.rounded_rectangle([(x0, _BAR_Y0), (x1, _BAR_Y1)], radius=_SEGMENT_RADIUS, fill=_SEGMENT_EMPTY)
        return
    if fraction >= 1:
        draw.rounded_rectangle([(x0, _BAR_Y0), (x1, _BAR_Y1)], radius=_SEGMENT_RADIUS, fill=_SEGMENT_FILL)
        return
    draw.rounded_rectangle([(x0, _BAR_Y0), (x1, _BAR_Y1)], radius=_SEGMENT_RADIUS, fill=_SEGMENT_EMPTY)
    split_x = x0 + round((x1 - x0) * fraction)
    if split_x > x0:
        draw.rounded_rectangle(
            [(x0, _BAR_Y0), (split_x, _BAR_Y1)],
            radius=_SEGMENT_RADIUS,
            corners=(True, False, False, True),
            fill=_SEGMENT_FILL,
        )


def render_achievement_gauge(achievement_percent: float, output) -> None:
    image = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [(_PANEL_MARGIN, _PANEL_MARGIN), (CANVAS_W - _PANEL_MARGIN, CANVAS_H - _PANEL_MARGIN)],
        radius=_PANEL_RADIUS,
        fill=_PANEL_BG,
        outline=_PANEL_BORDER,
        width=2,
    )

    clamped = max(0.0, min(achievement_percent, SEGMENT_COUNT * SEGMENT_VALUE))
    bar_w = _BAR_X1 - _BAR_X0
    cell_w = (bar_w - _SEGMENT_GAP * (SEGMENT_COUNT - 1)) / SEGMENT_COUNT
    for i in range(SEGMENT_COUNT):
        remaining = clamped - i * SEGMENT_VALUE
        fraction = max(0.0, min(1.0, remaining / SEGMENT_VALUE))
        x0 = round(_BAR_X0 + i * (cell_w + _SEGMENT_GAP))
        x1 = round(x0 + cell_w)
        _draw_segment(draw, x0, x1, fraction)

    draw.rounded_rectangle(
        [(_PILL_X0, _PILL_Y0), (_PILL_X1, _PILL_Y1)],
        radius=(_PILL_Y1 - _PILL_Y0) // 2,
        fill=_PILL_BG,
        outline=_PILL_BORDER,
        width=_PILL_BORDER_WIDTH,
    )

    pill_mid_y = (_PILL_Y0 + _PILL_Y1) / 2
    label_font = ImageFont.truetype(_INTER_BOLD, 13)
    label_x = _PILL_X0 + 22
    draw.text((label_x, pill_mid_y), "Achievement Progress", font=label_font, fill=_LABEL_COLOR, anchor="lm")

    value_text = f"{achievement_percent:,.4f}%"
    value_font = ImageFont.truetype(_INTER_BOLD, 22)
    value_x = _PILL_X1 - _PILL_BORDER_WIDTH - 18
    draw.text((value_x, pill_mid_y), value_text, font=value_font, fill=_VALUE_COLOR, anchor="rm")

    image.save(output, "PNG", compress_level=3)
