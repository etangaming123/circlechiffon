from PIL import Image, ImageDraw, ImageFont

from circlechiffon.ratingcalc.judgement_loss import JudgementLoss, LossValue, calculate_judgement_loss
from circlechiffon.renderers.b50 import _DIFFICULTY_COLOR, FONT_DIR, _draw_guide_box, _fit_font, _hex_to_rgb, _truncate_to_width
from circlechiffon.types import Judgements, NoteTypeJudgement, RecentScore

_JP_BOLD = str(FONT_DIR / "NotoSansJP-Bold.ttf")
_JP_REGULAR = str(FONT_DIR / "NotoSansJP-Regular.ttf")
_INTER_BOLD = str(FONT_DIR / "Inter_28pt-Bold.ttf")
_INTER_REGULAR = str(FONT_DIR / "Inter_28pt-Regular.ttf")

FONT_TITLE = ImageFont.truetype(_JP_BOLD, 26)
FONT_SUBTITLE = ImageFont.truetype(_JP_REGULAR, 16)
FONT_TABLE_HEADER = ImageFont.truetype(_INTER_BOLD, 16)
FONT_ROW_LABEL = ImageFont.truetype(_INTER_BOLD, 15)
FONT_CELL = ImageFont.truetype(_INTER_REGULAR, 16)
FONT_SUBTEXT = ImageFont.truetype(_INTER_REGULAR, 11)
FONT_FAST_LATE = ImageFont.truetype(_INTER_BOLD, 16)
FONT_PLACEHOLDER = ImageFont.truetype(_INTER_REGULAR, 15)

BACKGROUND_COLOR = (24, 24, 32)
_PANEL_BG = (36, 36, 46)
_ROW_BG_A = (30, 30, 39)
_ROW_BG_B = (36, 36, 46)
_HEADER_BG = (52, 52, 68)
_GRID_LINE = (60, 60, 74)
_TEXT_COLOR = (235, 235, 240)
_SUBTEXT_COLOR = (160, 160, 172)
_MISS_COLOR = (255, 107, 129)
_ROW_LABEL_COLOR = "#7C9CFF"  # navy-ish blue, fixed - distinct from the difficulty accent below

CANVAS_W = 640
_MARGIN_X = 24
_HEADER_Y = 24
_SUBTITLE_Y = 58
_TABLE_Y0 = 96
_TABLE_H = 220  # fixed height used only for the "no judgement data" placeholder panel
_ROW_LABEL_W = 100
_HEADER_ROW_H = 40
_ROW_H_SUMMARY = 36
_ROW_H_FULL = 56
_FOOTER_H = 70  # room below the table for FAST/LATE + LOST lines

_ROWS: list[tuple[str, str]] = [
    ("TAP", "tap"),
    ("HOLD", "hold"),
    ("SLIDE", "slide"),
    ("TOUCH", "touch"),
    ("BREAK", "brk"),
]
# (header label, NoteTypeJudgement attr, header text color) - colors loosely
# match maimai DX NET's own judgement-detail page styling.
_COLUMNS: list[tuple[str, str, str]] = [
    ("C.PERF", "critical_perfect", "#FFC107"),
    ("PERFECT", "perfect", "#FF8F00"),
    ("GREAT", "great", "#FF4FA3"),
    ("GOOD", "good", "#4CAF50"),
    ("MISS", "miss", "#9E9E9E"),
]


def _fmt(value: int | None) -> str:
    return f"{value:,}" if value is not None else "-"


def _fmt_loss(value: LossValue) -> str:
    if isinstance(value, tuple):
        lo, hi = value
        return f"-{lo:.2f}~{hi:.2f}%"
    return f"-{value:.2f}%"


def _present_rows(judgements: Judgements) -> list[tuple[str, str, NoteTypeJudgement]]:
    return [(label, attr, nt) for label, attr in _ROWS if (nt := getattr(judgements, attr)) is not None]


def _draw_table(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    judgements: Judgements | None,
    mode: str,
    loss: JudgementLoss | None,
) -> int:
    """Returns the y-coordinate just below the table (for the footer lines
    that follow it)."""
    table_x0, table_x1 = _MARGIN_X, CANVAS_W - _MARGIN_X
    table_w = table_x1 - table_x0
    col_w = (table_w - _ROW_LABEL_W) / len(_COLUMNS)

    if judgements is None:
        draw.rounded_rectangle(
            [(table_x0, _TABLE_Y0), (table_x1, _TABLE_Y0 + _TABLE_H)], radius=10, fill=_PANEL_BG, outline=_GRID_LINE
        )
        draw.text(
            (CANVAS_W / 2, _TABLE_Y0 + _TABLE_H / 2),
            "Play detail unavailable for this track.",
            font=FONT_PLACEHOLDER,
            fill=_TEXT_COLOR,
            anchor="mm",
        )
        return _TABLE_Y0 + _TABLE_H

    rows = _present_rows(judgements)
    full = mode == "full"
    row_h = _ROW_H_FULL if full else _ROW_H_SUMMARY
    loss_by_attr = {row.attr: row for row in loss.rows} if loss is not None else {}

    # header row
    draw.rectangle([(table_x0, _TABLE_Y0), (table_x1, _TABLE_Y0 + _HEADER_ROW_H)], fill=_HEADER_BG)
    for i, (label, _attr, color) in enumerate(_COLUMNS):
        cx = table_x0 + _ROW_LABEL_W + col_w * i + col_w / 2
        draw.text(
            (cx, _TABLE_Y0 + _HEADER_ROW_H / 2), label, font=FONT_TABLE_HEADER, fill=_hex_to_rgb(color), anchor="mm"
        )

    # body rows
    y = _TABLE_Y0 + _HEADER_ROW_H
    for row_index, (label, attr, nt) in enumerate(rows):
        bg = _ROW_BG_A if row_index % 2 == 0 else _ROW_BG_B
        draw.rectangle([(table_x0, y), (table_x1, y + row_h)], fill=bg)

        row_loss = loss_by_attr.get(attr)
        label_anchor_y = y + (row_h * 0.34 if full else row_h / 2)
        draw.text((table_x0 + 14, label_anchor_y), label, font=FONT_ROW_LABEL, fill=_hex_to_rgb(_ROW_LABEL_COLOR), anchor=("l" + ("a" if full else "m")))
        if full and row_loss is not None:
            draw.text(
                (table_x0 + 14, y + row_h * 0.68),
                f"-{row_loss.loss_percent:.2f}% ({row_loss.row_total_percent:.2f}%)",
                font=FONT_SUBTEXT,
                fill=_SUBTEXT_COLOR,
                anchor="la",
            )

        for i, (_col_label, col_attr, _color) in enumerate(_COLUMNS):
            value = getattr(nt, col_attr)
            cx = table_x0 + _ROW_LABEL_W + col_w * i + col_w / 2
            color = _MISS_COLOR if col_attr == "miss" and value else _TEXT_COLOR
            cell_loss = row_loss.cells.get(col_attr) if row_loss is not None else None
            count_anchor_y = y + (row_h * 0.34 if full else row_h / 2)
            draw.text(
                (cx, count_anchor_y), _fmt(value), font=FONT_CELL, fill=color, anchor=("m" + ("a" if full else "m"))
            )
            if full and cell_loss is not None:
                draw.text(
                    (cx, y + row_h * 0.68), _fmt_loss(cell_loss), font=FONT_SUBTEXT, fill=_SUBTEXT_COLOR, anchor="ma"
                )
        y += row_h

    draw.rounded_rectangle([(table_x0, _TABLE_Y0), (table_x1, y)], radius=10, outline=_GRID_LINE, width=2)
    for i in range(1, len(_COLUMNS)):
        x = table_x0 + _ROW_LABEL_W + col_w * i
        draw.line([(x, _TABLE_Y0), (x, y)], fill=_GRID_LINE, width=1)
    draw.line([(table_x0, _TABLE_Y0 + _HEADER_ROW_H), (table_x1, _TABLE_Y0 + _HEADER_ROW_H)], fill=_GRID_LINE, width=1)
    return y


def render_judgement_detail(
    *, score: RecentScore, judgements: Judgements | None, mode: str = "summary", output
) -> None:
    """Table-only judgement-breakdown image for a single recent play - one
    row per note type (tap/hold/slide/touch/break), one column per
    judgement tier, mirroring cogs/records.py's `_format_judgement_table`
    text version but as a Pillow grid. Called via asyncio.to_thread from
    RecentScoresView's image_mode branch.

    mode="summary" (default): today's note-count table, plus one LOST%
    total line below FAST/LATE. mode="full": same table, but every scored
    cell (Great/Good/Miss, and BREAK's Perfect/Great/Good/Miss) gets a
    small loss% annotation underneath its count, and every row label gets
    a "-loss% (row total%)" line underneath it - ported from
    ratingcalc/judgement_loss.py's achievement-loss math. Canvas height is
    computed per-call since "full" mode's taller rows don't fit the
    "summary" layout's fixed size."""
    rows = _present_rows(judgements) if judgements is not None else []
    full = mode == "full"
    row_h = _ROW_H_FULL if full else _ROW_H_SUMMARY
    table_h = (_HEADER_ROW_H + row_h * len(rows)) if judgements is not None else _TABLE_H
    canvas_h = _TABLE_Y0 + table_h + _FOOTER_H

    image = Image.new("RGB", (CANVAS_W, canvas_h), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)

    title_font = _fit_font(draw, score.title, _JP_BOLD, CANVAS_W - _MARGIN_X * 2, 30)
    title_text = _truncate_to_width(draw, score.title, title_font, CANVAS_W - _MARGIN_X * 2)
    draw.text((_MARGIN_X, _HEADER_Y), title_text, font=title_font, fill=_TEXT_COLOR)

    diff_name = score.difficulty.display_name if score.difficulty else "?"
    accent = _hex_to_rgb(_DIFFICULTY_COLOR.get(score.difficulty, "#9e45e2"))
    draw.text((_MARGIN_X, _SUBTITLE_Y), diff_name, font=FONT_SUBTITLE, fill=accent)

    loss = calculate_judgement_loss(judgements, score.achievement) if judgements is not None else None
    table_bottom = _draw_table(image, draw, judgements, mode, loss)

    footer_y = table_bottom + 24
    if judgements is not None and (judgements.fast is not None or judgements.late is not None):
        draw.text(
            (_MARGIN_X, footer_y),
            f"FAST: {_fmt(judgements.fast)}   LATE: {_fmt(judgements.late)}",
            font=FONT_FAST_LATE,
            fill=_TEXT_COLOR,
        )
        footer_y += 24
    if loss is not None:
        draw.text((_MARGIN_X, footer_y), f"LOST: {loss.total_lost_percent:.2f}%", font=FONT_FAST_LATE, fill=_TEXT_COLOR)

    image.save(output, "PNG", compress_level=3)
    output.seek(0)


def render_judgement_detail_template(output) -> None:
    """Transparent-background guide PNG for judgement_detail.py's layout -
    illustrates the default "summary" mode only (templates document one
    canonical shape, same convention as every other renderer's template).
    See generate_templates.py."""
    canvas_h = _TABLE_Y0 + _TABLE_H + _FOOTER_H
    image = Image.new("RGBA", (CANVAS_W, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_guide_box(draw, (_MARGIN_X, _HEADER_Y, CANVAS_W - _MARGIN_X, _HEADER_Y + 30), "title")
    _draw_guide_box(draw, (_MARGIN_X, _SUBTITLE_Y, CANVAS_W - _MARGIN_X, _SUBTITLE_Y + 20), "difficulty")
    _draw_guide_box(draw, (_MARGIN_X, _TABLE_Y0, CANVAS_W - _MARGIN_X, _TABLE_Y0 + _TABLE_H), "judgement table")
    _draw_guide_box(
        draw, (_MARGIN_X, _TABLE_Y0 + _TABLE_H + 12, CANVAS_W - _MARGIN_X, _TABLE_Y0 + _TABLE_H + 36), "fast/late"
    )
    _draw_guide_box(
        draw, (_MARGIN_X, _TABLE_Y0 + _TABLE_H + 36, CANVAS_W - _MARGIN_X, _TABLE_Y0 + _TABLE_H + 60), "lost %"
    )
    image.save(output, "PNG", compress_level=3)
    output.seek(0)
