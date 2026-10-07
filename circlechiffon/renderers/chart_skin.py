"""
Sprite painter for /cc-chart: draws notes with an imported MajdataPlay skin.

The skin lives in `assets/chart_skin/` (or `CC_CHART_SKIN_DIR`), written by
`import_chart_skin.py`. It is never committed - see that script. Without it,
`chart_local.Scene` uses `chart_vector.VectorPainter` instead.

Geometry arrives in MajdataPlay's own units (y up, ring radius 4.8, degrees
counter-clockwise) and every sprite is drawn the way MajdataPlay draws it:
centred, at 100 skin pixels per unit. Images are pre-scaled once at load to
the canvas's pixels-per-unit, so per-frame draws are close to 1:1.

Sizes, offsets and animation curves come from MajdataPlay's source
(CustomSkin.cs, TapDrop.cs, HoldDrop.cs, TouchDrop.cs, TouchHoldDrop.cs,
JudgeTextDisplayer.cs, SlideOK.cs and the .anim clips); the `.claude/docs`
notes carry the citations.
"""

import math
import os
from pathlib import Path

import numpy as np

from circlechiffon.simai import geometry as g

DEFAULT_SKIN_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "chart_skin"

_SUBDIRS_PLAIN = ("TapSkins", "StarSkins", "HoldSkins", "SlideSkins", "WifiSkins", "TouchSkins",
                  "TouchHoldSkins", "JudgeTextSkins", "SlideOKSkins")

# Hold sprites are 9-sliced: 58 skin px top and bottom stay fixed.
_HOLD_CAP = 58
# touchhold_border reveals clockwise through CircleMask, whose alpha rises
# non-linearly with angle. Measured: (alpha, degrees clockwise from 12).
_CIRCLE_MASK = ((0.0, 0.0), (0.184, 90.0), (0.451, 180.0), (0.718, 270.0), (0.902, 359.0))

EX_TINT_TAP = (255, 172, 225)
EX_TINT_EACH = (255, 254, 119)

# touchhold_border's band runs 0.49-0.85 units out (along its flat sides),
# hugging the closed fans' 0.5. Drawn at 0.9 scale *under* the fans, only
# 0.5-0.77 of it shows: thinner, with its glow intact.
_TOUCHHOLD_BORDER_SCALE = 0.9

# tapEffect.anim's four orbiting stars ("GameObject (k)/Hex"): start offset
# from the burst centre, offset at 0.25s, the time they reach the centre,
# their end scale (from 0.4788 at 0.267s), and their orbit direction.
_HIT_STARS = (
    ((0.552, 0.5), (0.4368, 0.3957), 0.567, 0.6, 1.0),
    ((-0.431, -0.527), (-0.3193, -0.3904), 0.5, 0.6, 1.0),
    ((0.473, -0.468), (0.3504, -0.3467), 0.5, 0.8, -1.0),
    ((-0.502, 0.492), (-0.3719, 0.3644), 0.5, 0.8, -1.0),
)
# Firework rays cycle the note palette: each yellow, touch blue, tap magenta.
_FIREWORK_COLORS = ((255, 215, 0), (0, 191, 255), (255, 64, 200))
_FIREWORK_SECONDS = 0.6


def skin_dir() -> Path:
    configured = os.environ.get("CC_CHART_SKIN_DIR")
    return Path(configured) if configured else DEFAULT_SKIN_DIR


def game_hit_sound(path: Path | None = None) -> Path | None:
    """maimai's own tap sound (MajdataPlay's SFX/answer.wav), if imported."""
    sound = (path or skin_dir()) / "SFX" / "answer.wav"
    return sound if sound.is_file() else None


def skin_installed(path: Path | None = None) -> bool:
    path = path or skin_dir()
    return (path / "slides.json").exists() and (path / "TapSkins" / "tap.png").exists()


class Skin:
    """Every PNG of the skin, pre-scaled, keyed by file stem. NoteGuideSkins
    and the effect sprites are prefixed (`guide/Normal`, `fx/Star`) so their
    names can't collide with note sprites."""

    def __init__(self, path: Path, px_per_unit: float):
        import skia
        from PIL import Image

        self.images: dict[str, object] = {}
        scale = px_per_unit / 100.0

        def load(file: Path, key: str) -> None:
            with Image.open(file) as im:
                im = im.convert("RGBA")
                w = max(1, round(im.width * scale))
                h = max(1, round(im.height * scale))
                arr = np.ascontiguousarray(np.asarray(im.resize((w, h), Image.LANCZOS)))
            self.images[key] = skia.Image.fromarray(
                arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType
            )

        for sub in _SUBDIRS_PLAIN:
            for f in sorted((path / sub).glob("*.png")):
                load(f, f.stem)
        for f in sorted((path / "NoteGuideSkins").glob("*.png")):
            load(f, f"guide/{f.stem}")
        for f in sorted((path / "Effects").glob("*.png")):
            load(f, f"fx/{f.stem}")
        if (path / "outline.png").exists():
            load(path / "outline.png", "outline")
        self.hold_cap = _HOLD_CAP * scale

    def get(self, *names: str):
        """First of `names` that exists - skins don't all ship every variant."""
        for n in names:
            img = self.images.get(n)
            if img is not None:
                return img
        return None


_loaded: dict[tuple[str, float], tuple[Skin, dict] | None] = {}


def load_skin(px_per_unit: float, path: Path | None = None) -> tuple[Skin, dict] | None:
    """(skin, slide table), or None if no complete skin is installed. Cached
    per process: decoding and rescaling ~200 PNGs is worth doing once."""
    path = path or skin_dir()
    key = (str(path), round(px_per_unit, 4))
    if key in _loaded:
        return _loaded[key]
    result = None
    if skin_installed(path):
        table = g.load_slide_table(path / "slides.json")
        if table is not None:
            result = (Skin(path, px_per_unit), table)
    _loaded[key] = result
    return result


def _variant(kind: str) -> str:
    """'normal' | 'each' | 'break' -> filename suffix."""
    return "" if kind == "normal" else f"_{kind}"


class SkinPainter:
    """Draws every element from skin sprites. All positions/transforms are
    Unity units; `self.px` maps them onto the canvas."""

    uses_guides = True

    def __init__(self, skin: Skin, size: int, ring_px: float):
        import skia

        self.skia = skia
        self.skin = skin
        self.k = ring_px / g.UNIT_RING
        self.size = size
        c = size / 2
        self.px = np.array([[self.k, 0.0, c], [0.0, -self.k, c], [0.0, 0.0, 1.0]])
        self.sampling = skia.SamplingOptions(skia.FilterMode.kLinear)
        self.paint = skia.Paint(AntiAlias=True)
        self.filtered = skia.Paint(AntiAlias=True)
        self._static: dict[tuple[int, int], tuple] = {}
        self._filters: dict[tuple, object] = {}

    # -- primitives -------------------------------------------------------------

    def _color_filter(self, brightness: float = 1.0, tint: tuple[int, int, int] | None = None):
        r = g_ = b = brightness
        if tint is not None:
            r, g_, b = (brightness * tint[0] / 255, brightness * tint[1] / 255, brightness * tint[2] / 255)
        key = (round(r, 3), round(g_, 3), round(b, 3))
        f = self._filters.get(key)
        if f is None:
            f = self.skia.ColorFilters.Matrix([r, 0, 0, 0, 0, 0, g_, 0, 0, 0, 0, 0, b, 0, 0, 0, 0, 0, 1, 0])
            if len(self._filters) > 512:
                self._filters.clear()
            self._filters[key] = f
        return f

    def _prep(self, alpha: float, brightness: float = 1.0, tint=None):
        # skia-python can't clear a colour filter, so filtered draws get
        # their own paint rather than toggling one.
        if brightness != 1.0 or tint:
            p = self.filtered
            p.setColorFilter(self._color_filter(brightness, tint))
        else:
            p = self.paint
        p.setAlphaf(max(0.0, min(1.0, alpha)))
        return p

    def sprite(self, canvas, img, world: np.ndarray, alpha: float = 1.0, brightness: float = 1.0, tint=None):
        """Draw `img` centred on the origin of the Unity transform `world`."""
        if img is None or alpha <= 0.0:
            return
        w, h = img.width(), img.height()
        k = self.k
        m = self.px @ world @ np.array([[1 / k, 0, -w / (2 * k)], [0, -1 / k, h / (2 * k)], [0, 0, 1]])
        canvas.save()
        canvas.concat(self.skia.Matrix.MakeAll(m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2], 0, 0, 1))
        canvas.drawImage(img, 0, 0, self.sampling, self._prep(alpha, brightness, tint))
        canvas.restore()

    def static_sprite(self, canvas, img, world: np.ndarray, alpha: float = 1.0, brightness: float = 1.0):
        """`sprite` for transforms that never change (slide arrows, wifi
        bars): the canvas matrix is built once per transform and reused,
        which is most of a slide-heavy frame's draws."""
        if img is None or alpha <= 0.0:
            return
        key = (id(world), id(img))
        m = self._static.get(key)
        if m is None:
            w, h = img.width(), img.height()
            k = self.k
            a = self.px @ world @ np.array([[1 / k, 0, -w / (2 * k)], [0, -1 / k, h / (2 * k)], [0, 0, 1]])
            m = self.skia.Matrix.MakeAll(a[0, 0], a[0, 1], a[0, 2], a[1, 0], a[1, 1], a[1, 2], 0, 0, 1)
            self._static[key] = (m, world)  # keep `world` alive so its id stays unique
        else:
            m = m[0]
        canvas.save()
        canvas.concat(m)
        canvas.drawImage(img, 0, 0, self.sampling, self._prep(alpha, brightness))
        canvas.restore()

    @staticmethod
    def frame(pos, rot: float = 0.0, scale: float = 1.0) -> np.ndarray:
        return g.mat_t(float(pos[0]), float(pos[1])) @ g.mat_r(rot) @ g.mat_s(scale, scale)

    # -- background -------------------------------------------------------------

    def background(self, canvas) -> None:
        self.sprite(canvas, self.skin.get("outline"), np.eye(3))

    # -- guides -----------------------------------------------------------------

    def guide(self, canvas, kind: str, rot: float, scale: float) -> None:
        name = {"normal": "guide/Normal", "each": "guide/Each", "break": "guide/Break",
                "slide": "guide/Slide"}[kind]
        self.sprite(canvas, self.skin.get(name), g.mat_r(rot) @ g.mat_s(scale, scale))

    def each_line(self, canvas, steps: int, start_key: int, scale: float) -> None:
        img = self.skin.get(f"guide/EachLine{steps}")
        self.sprite(canvas, img, g.mat_r(-45.0 * (start_key - 1)) @ g.mat_s(scale, scale))

    # -- taps / stars -----------------------------------------------------------

    def tap(self, canvas, pos, rot, scale, kind, ex, brightness=1.0):
        world = self.frame(pos, rot, scale)
        self.sprite(canvas, self.skin.get(f"tap{_variant(kind)}", "tap"), world, brightness=brightness)
        if ex:
            tint = EX_TINT_TAP if kind == "normal" else EX_TINT_EACH
            self.sprite(canvas, self.skin.get("tap_ex"), world, tint=tint)

    def star(self, canvas, pos, rot, scale, kind, ex, double, brightness=1.0, alpha=1.0):
        world = self.frame(pos, rot, scale)
        base = "star" + ("_each" if kind == "each" else "_break" if kind == "break" else "")
        names = [base + "_double", base] if double else [base]
        self.sprite(canvas, self.skin.get(*names, "star"), world, alpha=alpha, brightness=brightness)
        if ex:
            tint = (255, 255, 255) if kind == "normal" else EX_TINT_EACH
            self.sprite(canvas, self.skin.get("star_ex_double" if double else "star_ex", "star_ex"), world,
                        alpha=alpha, tint=tint)

    # -- holds ------------------------------------------------------------------

    def _nine(self, canvas, img, world, height_units, alpha, brightness=1.0, tint=None):
        if img is None:
            return
        skia = self.skia
        w, h = img.width(), img.height()
        cap = self.skin.hold_cap
        H = max(height_units * self.k, 2 * cap)
        m = self.px @ world @ g.mat_s(1 / self.k, -1 / self.k)
        canvas.save()
        canvas.concat(skia.Matrix.MakeAll(m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2], 0, 0, 1))
        p = self._prep(alpha, brightness, tint)
        x0, x1, y0 = -w / 2, w / 2, -H / 2
        canvas.drawImageRect(img, skia.Rect.MakeLTRB(0, 0, w, cap), skia.Rect.MakeLTRB(x0, y0, x1, y0 + cap),
                             self.sampling, p)
        canvas.drawImageRect(img, skia.Rect.MakeLTRB(0, cap, w, h - cap),
                             skia.Rect.MakeLTRB(x0, y0 + cap, x1, -y0 - cap), self.sampling, p)
        canvas.drawImageRect(img, skia.Rect.MakeLTRB(0, h - cap, w, h), skia.Rect.MakeLTRB(x0, -y0 - cap, x1, -y0),
                             self.sampling, p)
        canvas.restore()

    def hold(self, canvas, rot, head_r, tail_r, scale, kind, ex, state, show_end, brightness=1.0):
        """`state`: 'idle' (approaching), 'on' (being held), 'off' (released/missed)."""
        center_r = (head_r + tail_r) / 2
        a = math.radians(rot + 90.0)
        center = (math.cos(a) * center_r, math.sin(a) * center_r)
        # Growing at the spawn point it's a fixed 1.22x1.42 hexagon scaled
        # up as a whole; once moving it stretches between head and tail.
        height = (head_r - tail_r + 1.4) if scale >= 1.0 else 1.42
        world = self.frame(center, rot, scale)
        v = _variant(kind)
        if state == "off":
            img = self.skin.get("hold_off", f"hold{v}")
        elif state == "on":
            img = self.skin.get(f"hold{v}_on", "hold_on", f"hold{v}")
        else:
            img = self.skin.get(f"hold{v}", "hold")
        self._nine(canvas, img, world, height, 1.0, brightness)
        if ex:
            tint = EX_TINT_TAP if kind == "normal" else EX_TINT_EACH
            self._nine(canvas, self.skin.get("hold_ex"), world, height, 1.0, tint=tint)
        if show_end:
            end = {"each": "guide/Hold_Each_End", "break": "guide/Hold_Break_End"}.get(kind, "guide/Hold_End")
            tail = (math.cos(a) * tail_r, math.sin(a) * tail_r)
            self.sprite(canvas, self.skin.get(end, "guide/Hold_End"), self.frame(tail, rot))

    # -- slides -----------------------------------------------------------------

    def slide_arrows(self, canvas, arrows, first_visible, alpha, kind, brightness=1.0):
        img = self.skin.get(f"slide{_variant(kind)}", "slide")
        for w in arrows[first_visible:]:
            self.static_sprite(canvas, img, w, alpha, brightness)

    def wifi_bars(self, canvas, wifi: g.WifiTrack, first_visible, alpha, kind, brightness=1.0):
        prefix = {"each": "wifi_each_", "break": "wifi_break_"}.get(kind, "wifi_")
        for i, w in enumerate(wifi.bars):
            if i >= first_visible:
                self.static_sprite(canvas, self.skin.get(f"{prefix}{i}", f"wifi_{i}"), w, alpha, brightness)

    def slide_star(self, canvas, pos, rot, scale, kind, alpha, brightness=1.0):
        name = {"each": "star_each", "break": "star_break"}.get(kind, "star")
        self.sprite(canvas, self.skin.get(name, "star"), self.frame(pos, rot, scale), alpha=alpha,
                    brightness=brightness)

    # -- touches ----------------------------------------------------------------

    def touch(self, canvas, pos, d, alpha, kind, arrived, borders, brightness=1.0, firework=False):
        v = _variant(kind)
        fan = self.skin.get(f"touch{v}", "touch")
        dist = 0.226 + d
        for dx, dy, rot in ((0, 1, 180.0), (1, 0, 90.0), (0, -1, 0.0), (-1, 0, 270.0)):
            self.sprite(canvas, fan, self.frame((pos[0] + dx * dist, pos[1] + dy * dist), rot), alpha=alpha,
                        brightness=brightness)
        point = self.skin.get(f"touch_point{v}" if kind != "break" else "touch_break_point", "touch_point")
        self.sprite(canvas, point, self.frame(pos), alpha=alpha)
        if arrived:
            self.sprite(canvas, self.skin.get("touch_just"), self.frame(pos), alpha=alpha)
        for n, bkind in borders:
            bv = "_each" if bkind == "each" else ""
            name = f"touch_break_border_{n}" if bkind == "break" else f"touch_border_{n}{bv}"
            self.sprite(canvas, self.skin.get(name, f"touch_border_{n}"), self.frame(pos), alpha=alpha)

    def touch_hold(self, canvas, pos, d, alpha, kind, progress, off, brightness=1.0, firework=False):
        brk = kind == "break"
        if progress is not None:
            self._touch_hold_border(canvas, pos, progress, brk, off)
        dist = 0.226 + d
        s = dist / math.sqrt(2)
        for i, (dx, dy, rot) in enumerate(((1, 1, 135.0), (1, -1, 45.0), (-1, -1, -45.0), (-1, 1, 225.0))):
            img = self.skin.get(f"touchhold_break_{i}" if brk else f"touchhold_{i}", f"touchhold_{i}")
            self.sprite(canvas, img, self.frame((pos[0] + dx * s, pos[1] + dy * s), rot), alpha=alpha,
                        brightness=brightness)
        self.sprite(canvas, self.skin.get("touch_break_point" if brk else "touch_point"), self.frame(pos),
                    alpha=alpha)

    def _touch_hold_border(self, canvas, pos, progress, brk, off):
        """The progress ring, revealed clockwise from 12 o'clock."""
        border = self.skin.get("touchhold_off") if off else self.skin.get(
            "touchhold_break_border" if brk else "touchhold_border", "touchhold_border")
        sweep = _mask_sweep(0.91 * progress)
        if border is None or sweep <= 0:
            return
        skia = self.skia
        c = self.px @ np.array([pos[0], pos[1], 1.0])
        cx, cy = float(c[0]), float(c[1])
        r = border.width()
        canvas.save()
        clip = skia.Path()
        clip.moveTo(cx, cy)
        clip.arcTo(skia.Rect.MakeLTRB(cx - r, cy - r, cx + r, cy + r), -90.0, sweep, False)
        clip.close()
        canvas.clipPath(clip, skia.ClipOp.kIntersect, True)
        self.sprite(canvas, border, self.frame(pos, 0.0, _TOUCHHOLD_BORDER_SCALE))
        canvas.restore()

    # -- judgement --------------------------------------------------------------

    def judge(self, canvas, world, grade, is_break, since):
        """`since`: seconds since the judgement. JudgePerfect/JudgeBreak.anim."""
        if since > 0.267:
            return
        scale = 1.2 - 0.2 * min(since / 0.0667, 1.0)
        alpha = 1.0 if since < 0.1667 else max(0.0, 1.0 - (since - 0.1667) / 0.1)
        world = world @ g.mat_s(scale, scale)
        if grade == "miss":
            self.sprite(canvas, self.skin.get("judge_text_miss"), world, alpha=alpha)
            return
        self.sprite(canvas, self.skin.get("judge_text_cPerfect", "judge_text_perfect"), world, alpha=alpha)
        # Break: the shine layer blinks on every other 1/30s step.
        if is_break and int(since / (1 / 30)) % 2 == 1:
            self.sprite(canvas, self.skin.get("judge_text_cPerfect_break"), world, alpha=alpha)

    def banner(self, canvas, world, kind, is_r, grade, is_break, since):
        """Slide completion (StarOver.anim / BreakStarOver.anim)."""
        if since > 0.4167:
            return
        if since < 0.0333:
            alpha = since / 0.0333
        elif since < 0.2833:
            alpha = 1.0
        else:
            alpha = max(0.0, 1.0 - (since - 0.2833) / 0.1334)
        side = ("u" if is_r else "d") if kind == "wifi" else ("r" if is_r else "l")
        name = f"{'just' if grade != 'miss' else 'miss'}_{kind}_{side}"
        brightness = 1.0
        if is_break and grade != "miss":
            brightness = 1.6 if int(since / 0.0333) % 2 else 0.9
        self.sprite(canvas, self.skin.get(name), world, alpha=alpha, brightness=brightness)

    # -- effects ----------------------------------------------------------------

    def hit(self, canvas, pos, rot, since, touch=False):
        """Perfect hit (tapEffect.anim): a star bursts 0 -> 0.7 -> 1.3 while
        four small stars orbit it and draw in to its centre; everything fades
        out between 0.233s and 0.417s. The skin's own tap burst is a hexagon
        (Hex.png, stars are the break burst); stars are used for both."""
        if since > 0.417:
            return
        img = self.skin.get("fx/Star")
        if since < 0.083:
            scale = 0.7 * since / 0.083
        else:
            scale = 0.7 + 0.6 * min((since - 0.083) / 0.367, 1.0)
        alpha = 1.0 if since < 0.233 else max(0.0, 1.0 - (since - 0.233) / 0.184)
        if touch:
            scale *= 0.6
        root = self.frame(pos, rot, scale)
        self.sprite(canvas, img, root, alpha=alpha)
        # Each small star sits on a parent spinning 180 degrees in 0.467s
        # while it counter-rotates itself, so it orbits without turning.
        orbit = 180.0 * since / 0.4667
        for start, mid, arrive, end_scale, direction in _HIT_STARS:
            if since < 0.25:
                f = since / 0.25
                x, y = start[0] + (mid[0] - start[0]) * f, start[1] + (mid[1] - start[1]) * f
            else:
                f = min((since - 0.25) / (arrive - 0.25), 1.0)
                x, y = mid[0] * (1 - f), mid[1] * (1 - f)
            small = 0.4788 + (end_scale - 0.4788) * min(since / 0.2667, 1.0)
            world = root @ g.mat_r(direction * orbit) @ g.mat_t(x, y) @ g.mat_r(-direction * orbit) @ g.mat_s(
                small, small)
            self.sprite(canvas, img, world, alpha=alpha)

    def firework(self, canvas, pos, since):
        """Firework_new.png is 24 white rays. Drawn large, thickened by
        stamping it three times a few degrees apart, then tinted ray by ray
        with the note colours (a hard-stop sweep gradient over it, kSrcIn)."""
        if since > _FIREWORK_SECONDS:
            return
        f = since / _FIREWORK_SECONDS
        img = self.skin.get("fx/Firework_new")
        if img is None:
            return
        skia = self.skia
        scale = 0.4 + 0.9 * f
        c = self.px @ np.array([pos[0], pos[1], 1.0])
        cx, cy = float(c[0]), float(c[1])
        r = img.width() * scale / 2 + 2
        bounds = skia.Rect.MakeLTRB(cx - r, cy - r, cx + r, cy + r)
        layer = skia.Paint()
        layer.setAlphaf(0.75 * (1.0 - f) ** 2)
        canvas.saveLayer(bounds, layer)
        for spread in (-2.5, 0.0, 2.5):  # each ray 7.5deg wide -> 12.5deg
            self.sprite(canvas, img, self.frame(pos, spread, scale))
        canvas.drawRect(bounds, self._firework_paint(cx, cy))
        canvas.restore()

    def _firework_paint(self, cx, cy):
        # 24 rays, one every 15 degrees with one pointing straight up; a
        # 15-degree band centred on each ray, colours cycling.
        skia = self.skia
        colors, stops = [], []
        for k in range(25):  # ray 0's band wraps round, so it closes the sweep too
            rgb = skia.Color(*_FIREWORK_COLORS[k % 24 % 3])
            colors += [rgb, rgb]
            stops += [max(0.0, (k * 15 - 7.5) / 360), min(1.0, (k * 15 + 7.5) / 360)]
        paint = skia.Paint(AntiAlias=True)
        paint.setShader(skia.GradientShader.MakeSweep(cx, cy, colors, stops))
        paint.setBlendMode(skia.BlendMode.kSrcIn)
        return paint


def _mask_sweep(cutoff: float) -> float:
    """Degrees of touchhold_border revealed at a CircleMask cutoff."""
    if cutoff <= 0:
        return 0.0
    for (a0, d0), (a1, d1) in zip(_CIRCLE_MASK, _CIRCLE_MASK[1:]):
        if cutoff <= a1:
            return d0 + (d1 - d0) * (cutoff - a0) / (a1 - a0)
    return 360.0
