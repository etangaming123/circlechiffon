"""
Fallback painter for /cc-chart: vector notes in mai-notes.com's style, used
when no MajdataPlay skin is installed (see import_chart_skin.py).

Re-implemented from the sizes and colours mai-notes' player uses (read from
its public bundle, 2026-10-07) - no code or assets of theirs are copied.
Sizes are fractions of the judgement-ring radius R, as theirs are:

  tap ring    outer R/12.5*1.36, inner 0.65 of that, white 2R/300 outlines,
              centre dot 0.15 of outer
  hold        hollow hexagon, half-width R/12.5*1.5, hole 0.62 of that
  star        R/10.42 (x1.2 while travelling), white outline
  arrows      chevron 32R/300 tall, 9.6R/300 deep, 12.8R/300 stroke over
              a black edge and two soft drop shadows
  touch       point R/37.5*1.1; four hollow rounded triangles (hole 0.4)
              of size R/12.5*1.3 closing from R/6.25*1.1 on a quartic ease;
              blue #00BFFF->#0070FF (each #FFD700->#FFB800); touch holds
              use four solid colours and a four-colour progress diamond
  colours     tap/hold #ff69b4, each #FFD700, break #FF8C00 (radial
              #FFB347->#FF8C00->#FF6600); slide #00CED1, each #FFE44D

Takes the same Unity-unit geometry as `chart_skin.SkinPainter`.
"""

import math

import numpy as np

from circlechiffon.simai import geometry as g

BACKGROUND = (26, 27, 46)
TAP = {"normal": (255, 105, 180), "each": (255, 215, 0), "break": (255, 140, 0)}
SLIDE = {"normal": (0, 206, 209), "each": (255, 228, 77), "break": (255, 140, 0)}
TOUCH = {"normal": ((0, 191, 255), (0, 112, 255)), "each": ((255, 215, 0), (255, 184, 0)),
         "break": ((255, 140, 0), (255, 102, 0))}
TOUCH_HOLD = ((255, 107, 107), (255, 230, 109), (46, 204, 113), (52, 152, 219))
WHITE = (255, 255, 255)
GREY = (128, 128, 128)


# MajdataPlay's hold effect (Hold_Effect.prefab, used for touch holds too):
# a particle system that, while the note is held, emits a thin ring
# (CircleMiss.png) every 0.1s at the key or sensor. Each ring lives 0.3s and
# never moves; it grows and fades along these curves (keys at age / 0.3,
# interpolated linearly). Its quad is 2 (startSize x curve scalar) x 1.2
# (transform) = 2.4 units across at full size. Prewarmed, so it starts with
# three rings already alive, and it vanishes the moment the hold is let go.
HOLD_FX_PERIOD = 0.1
HOLD_FX_LIFE = 0.3
HOLD_FX_DIAMETER = 2.4
_HOLD_FX_SIZE = ((0.0, 0.2245), (0.4386, 0.8878), (0.9985, 1.0))
_HOLD_FX_ALPHA = (
    (0.0, 0.008), (0.0946, 0.541), (0.1429, 0.977), (0.502, 1.0),
    (0.5965, 0.301), (0.666, 0.31), (1.0, 0.0),
)
# Where CircleMiss.png's ring sits, as a fraction of the quad's half-width.
HOLD_FX_RING = 0.75


def _lerp_keys(keys, x: float) -> float:
    if x <= keys[0][0]:
        return keys[0][1]
    for (x0, y0), (x1, y1) in zip(keys, keys[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return keys[-1][1]


def hold_effect_rings(since: float) -> list[tuple[float, float]]:
    """(diameter in units, alpha) of each ring alive `since` seconds into
    a hold, oldest first so the newest draws on top."""
    rings = []
    age = since % HOLD_FX_PERIOD
    while age < HOLD_FX_LIFE:
        f = age / HOLD_FX_LIFE
        rings.append((HOLD_FX_DIAMETER * _lerp_keys(_HOLD_FX_SIZE, f), _lerp_keys(_HOLD_FX_ALPHA, f)))
        age += HOLD_FX_PERIOD
    rings.reverse()
    return rings


class VectorPainter:
    uses_guides = False

    def __init__(self, size: int, ring_px: float):
        import skia

        self.skia = skia
        self.size = size
        self.R = ring_px
        self.k = ring_px / g.UNIT_RING
        self.c = size / 2
        self.line = 2 * ring_px / 300
        self.fill = skia.Paint(AntiAlias=True, Style=skia.Paint.kFill_Style)
        # Gradient fills get their own paint: skia-python can't clear a shader.
        self.grad = skia.Paint(AntiAlias=True, Style=skia.Paint.kFill_Style)
        self.stroke = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style)
        self.stroke.setStrokeJoin(skia.Paint.kRound_Join)
        self.stroke.setStrokeCap(skia.Paint.kRound_Cap)
        typeface = skia.Typeface.MakeFromName("Arial", skia.FontStyle.Bold()) or skia.Typeface.MakeDefault()
        self.font = skia.Font(typeface, 0.06 * ring_px)
        self._star_unit = self._star_path(1.0, 0.45)

    # -- helpers ----------------------------------------------------------------

    def xy(self, pos) -> tuple[float, float]:
        return self.c + float(pos[0]) * self.k, self.c - float(pos[1]) * self.k

    def _col(self, paint, rgb, alpha=1.0):
        paint.setColor(self.skia.Color(*rgb, int(255 * max(0.0, min(1.0, alpha)))))
        return paint

    def _star_path(self, r, inner):
        p = self.skia.Path()
        for i in range(10):
            a = i * math.pi / 5 - math.pi / 2
            rr = r if i % 2 == 0 else r * inner
            (p.moveTo if i == 0 else p.lineTo)(math.cos(a) * rr, math.sin(a) * rr)
        p.close()
        return p

    # -- background -------------------------------------------------------------

    def background(self, canvas) -> None:
        canvas.drawCircle(self.c, self.c, self.R, self._stroke(WHITE, self.line * 1.5))
        dot = self._col(self.fill, WHITE)
        for key in range(1, 9):
            x, y = self.xy(g.key_point_u(key))
            canvas.drawCircle(x, y, self.R / 45, dot)

    def _stroke(self, rgb, width, alpha=1.0):
        self.stroke.setStrokeWidth(width)
        return self._col(self.stroke, rgb, alpha)

    def guide(self, canvas, kind, rot, scale) -> None:
        pass

    def each_line(self, canvas, steps, start_key, scale) -> None:
        pass

    # -- taps / stars -----------------------------------------------------------

    def tap(self, canvas, pos, rot, scale, kind, ex, brightness=1.0):
        skia = self.skia
        x, y = self.xy(pos)
        o = self.R / 12.5 * 1.36 * scale
        a = 0.65 * o
        if o <= 0.5:
            return
        ring = skia.Path()
        ring.addCircle(x, y, o)
        ring.addCircle(x, y, a, skia.PathDirection.kCCW)
        ring.setFillType(skia.PathFillType.kEvenOdd)
        if kind == "break":
            self.grad.setShader(skia.GradientShader.MakeRadial(
                (x, y), o, [skia.Color(255, 179, 71), skia.Color(255, 140, 0), skia.Color(255, 102, 0)],
                [a / o, (a / o + 1) / 2, 1.0]))
            canvas.drawPath(ring, self.grad)
        else:
            canvas.drawPath(ring, self._col(self.fill, TAP[kind]))
        w = self._stroke(WHITE, self.line)
        canvas.drawCircle(x, y, o, w)
        canvas.drawCircle(x, y, a, w)
        canvas.drawCircle(x, y, 0.15 * o, self._col(self.fill, TAP[kind]))
        if ex:
            canvas.drawCircle(x, y, o * 1.12, self._stroke(WHITE, self.line * 1.5, 0.8))

    def star(self, canvas, pos, rot, scale, kind, ex, double, brightness=1.0, alpha=1.0):
        self._draw_star(canvas, pos, self.R / 10.42 * scale, SLIDE[kind], alpha)
        if double:
            self._draw_star(canvas, pos, self.R / 10.42 * scale * 0.55, WHITE, alpha * 0.6)
        if ex:
            x, y = self.xy(pos)
            canvas.drawCircle(x, y, self.R / 10.42 * scale * 1.15, self._stroke(WHITE, self.line * 1.5, 0.8 * alpha))

    def _draw_star(self, canvas, pos, r, rgb, alpha=1.0):
        if r <= 0.5:
            return
        x, y = self.xy(pos)
        canvas.save()
        canvas.translate(x, y)
        canvas.scale(r, r)
        canvas.drawPath(self._star_unit, self._col(self.fill, rgb, alpha))
        canvas.drawPath(self._star_unit, self._stroke(WHITE, self.line / r, alpha))
        canvas.restore()

    # -- holds ------------------------------------------------------------------

    def hold(self, canvas, rot, head_r, tail_r, scale, kind, ex, state, show_end, brightness=1.0):
        """mai-notes' hold: a hollow hexagon stretched from head to tail,
        pointed ends along the lane, centre dots at both ends."""
        skia = self.skia
        a = math.radians(rot + 90.0)
        hx, hy = self.xy((math.cos(a) * head_r, math.sin(a) * head_r))
        tx, ty = self.xy((math.cos(a) * min(tail_r, head_r), math.sin(a) * min(tail_r, head_r)))
        u = math.atan2(hy - self.c, hx - self.c)  # screen angle, outward
        f = self.R / 12.5 * 1.5 * scale
        if f <= 0.5:
            return

        def hexagon(k):
            r = f * k
            pts = [(hx + math.cos(u) * r, hy + math.sin(u) * r),
                   (hx + math.cos(u + math.pi / 3) * r, hy + math.sin(u + math.pi / 3) * r),
                   (tx + math.cos(u + 2 * math.pi / 3) * r, ty + math.sin(u + 2 * math.pi / 3) * r),
                   (tx - math.cos(u) * r, ty - math.sin(u) * r),
                   (tx + math.cos(u - 2 * math.pi / 3) * r, ty + math.sin(u - 2 * math.pi / 3) * r),
                   (hx + math.cos(u - math.pi / 3) * r, hy + math.sin(u - math.pi / 3) * r)]
            path = skia.Path()
            path.addPoly([skia.Point(*q) for q in pts], True)
            return path

        outer, inner = hexagon(1.0), hexagon(0.62)
        ring = skia.Path(outer)
        ring.addPath(inner)
        ring.setFillType(skia.PathFillType.kEvenOdd)
        rgb = GREY if state == "off" else TAP[kind]
        if ex:
            glow = skia.Path(hexagon(1.19))
            glow.addPath(outer)
            glow.setFillType(skia.PathFillType.kEvenOdd)
            ex_rgb = (255, 200, 120) if kind == "break" else (255, 245, 150) if kind == "each" else (255, 180, 210)
            canvas.drawPath(glow, self._col(self.fill, ex_rgb, 0.8))
        canvas.drawPath(ring, self._col(self.fill, rgb))
        w = self._stroke(WHITE, self.line)
        canvas.drawPath(outer, w)
        canvas.drawPath(inner, w)
        dot = self._col(self.fill, rgb)
        canvas.drawCircle(hx, hy, 0.15 * f, dot)
        if show_end:
            canvas.drawCircle(tx, ty, 0.15 * f, dot)

    # -- slides -----------------------------------------------------------------

    def _chevron_stroke(self, canvas, path, width, rgb, alpha):
        """mai-notes' arrow stroke: a black edge, two soft shadows trailing
        behind (drawn by the caller's offset path), then the colour."""
        canvas.drawPath(path, self._stroke((0, 0, 0), width + 2, alpha))
        canvas.drawPath(path, self._stroke(rgb, width, alpha))

    def _chevron(self, canvas, world, rgb, alpha):
        pos = world[:2, 2]
        d = -world[:2, 0]  # local -x is the direction of travel
        n = float(np.hypot(*d)) or 1.0
        dx, dy = d[0] / n, -d[1] / n  # to screen (y down)
        x, y = self.xy(pos)
        unit = self.R / 300
        half_h, depth, width = 16 * unit, 9.6 * unit, 12.8 * unit

        def path(back):
            cx, cy = x - dx * back, y - dy * back
            p = self.skia.Path()
            p.moveTo(cx - dx * depth / 2 - dy * half_h, cy - dy * depth / 2 + dx * half_h)
            p.lineTo(cx + dx * depth / 2, cy + dy * depth / 2)
            p.lineTo(cx - dx * depth / 2 + dy * half_h, cy - dy * depth / 2 - dx * half_h)
            return p

        canvas.drawPath(path(5 * unit), self._stroke((0, 0, 0), width + 6, 0.2 * alpha))
        canvas.drawPath(path(3 * unit), self._stroke((0, 0, 0), width + 3, 0.5 * alpha))
        self._chevron_stroke(canvas, path(0.0), width, rgb, alpha)

    def slide_arrows(self, canvas, arrows, first_visible, alpha, kind, brightness=1.0):
        # End first, so the start of the slide sits on top where it crosses.
        for w in reversed(arrows[first_visible:]):
            self._chevron(canvas, w, SLIDE[kind], alpha)

    def wifi_bars(self, canvas, wifi: g.WifiTrack, first_visible, alpha, kind, brightness=1.0):
        start = wifi.starts[1]
        for i in range(10, first_visible - 1, -1):
            f = (i + 1) / 12
            pts = [self.xy(start + (e - start) * f) for e in wifi.ends]
            p = self.skia.Path()
            p.moveTo(*pts[0])
            p.lineTo(*pts[1])
            p.lineTo(*pts[2])
            self._chevron_stroke(canvas, p, 12.8 * self.R / 300, SLIDE[kind], alpha)

    def slide_star(self, canvas, pos, rot, scale, kind, alpha, brightness=1.0):
        # mai-notes doesn't rotate its travelling star by default.
        self._draw_star(canvas, pos, self.R / 10.42 * 1.2 * min(scale / 1.5, 1.0), SLIDE[kind], alpha)

    # -- touches ----------------------------------------------------------------

    def _rounded_triangle(self, path, pts, radius):
        """mai-notes rounds each corner with a quadratic through the vertex."""
        for k in range(3):
            prev, cur, nxt = pts[k - 1], pts[k], pts[(k + 1) % 3]
            l1 = math.hypot(cur[0] - prev[0], cur[1] - prev[1]) or 1.0
            l2 = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) or 1.0
            r = min(radius, l1 / 2, l2 / 2)
            a = (cur[0] - (cur[0] - prev[0]) / l1 * r, cur[1] - (cur[1] - prev[1]) / l1 * r)
            b = (cur[0] + (nxt[0] - cur[0]) / l2 * r, cur[1] + (nxt[1] - cur[1]) / l2 * r)
            (path.moveTo if k == 0 else path.lineTo)(*a)
            path.quadTo(*cur, *b)
        path.close()

    def _triangle(self, canvas, x, y, angle, dist, colors, alpha, hollow):
        """One touch arrowhead: centred `dist` from (x, y) along `angle`,
        pointing back at (x, y). Hollow ones are the plain touch's crosshair."""
        skia = self.skia
        c = self.R / 12.5 * 1.3
        cx, cy = x + math.cos(angle) * dist, y + math.sin(angle) * dist
        tip = (cx - math.cos(angle) * c, cy - math.sin(angle) * c)
        left = (cx + math.cos(angle + math.pi / 2) * c, cy + math.sin(angle + math.pi / 2) * c)
        right = (cx + math.cos(angle - math.pi / 2) * c, cy + math.sin(angle - math.pi / 2) * c)
        corner = 8 * self.R / 300
        outer = skia.Path()
        self._rounded_triangle(outer, [tip, left, right], corner)
        shape = skia.Path(outer)
        inner = None
        if hollow:
            mx, my = (tip[0] + left[0] + right[0]) / 3, (tip[1] + left[1] + right[1]) / 3
            shrink = [(mx + (px - mx) * 0.4, my + (py - my) * 0.4) for px, py in (tip, right, left)]
            inner = skia.Path()
            self._rounded_triangle(inner, shrink, corner * 0.4)
            shape.addPath(inner)
            shape.setFillType(skia.PathFillType.kEvenOdd)
        c0, c1 = colors
        self.grad.setShader(skia.GradientShader.MakeLinear(
            [(cx, cy), tip], [skia.Color(*c0, int(255 * alpha)), skia.Color(*c1, int(255 * alpha))]))
        canvas.drawPath(shape, self.grad)
        w = self._stroke(WHITE, self.line, alpha)
        canvas.drawPath(outer, w)
        if inner is not None:
            canvas.drawPath(inner, w)

    def _touch_spread(self, d: float) -> float:
        # Scene hands over d = 0.4 (far) -> 0 (closed); see Scene._touch_d.
        near, far = self.R / 12.5 * 1.3, self.R / 6.25 * 1.1
        return near + (far - near) * min(max(d / 0.4, 0.0), 1.0)

    def touch(self, canvas, pos, d, alpha, kind, arrived, borders, brightness=1.0, firework=False):
        x, y = self.xy(pos)
        dist = self._touch_spread(d)
        for k in range(4):
            self._triangle(canvas, x, y, -math.pi / 2 + k * math.pi / 2, dist, TOUCH[kind], alpha, True)
        canvas.drawCircle(x, y, self.R / 37.5 * 1.1, self._col(self.fill, TOUCH[kind][0], alpha))
        if firework:
            self._firework_mark(canvas, x, y, alpha)
        for n, bkind in borders:
            half = self.R / 4.46 * 1.1 * 1.2 * (1.2 if n == 3 else 1.0) / 2
            rgb = TOUCH["each" if bkind == "each" else "normal"][0]
            w = self._stroke(rgb, 3 * self.R / 300, alpha)
            arm = half * 0.6
            for sx in (-1, 1):
                for sy in (-1, 1):
                    cx, cy = x + sx * half, y + sy * half
                    canvas.drawLine(cx, cy, cx - sx * arm, cy, w)
                    canvas.drawLine(cx, cy, cx, cy - sy * arm, w)

    def _firework_mark(self, canvas, x, y, alpha):
        # mai-notes marks a firework touch with a gold star on the sensor.
        r = self.R / 37.5 * 2.2
        canvas.save()
        canvas.translate(x, y)
        canvas.scale(r, r)
        canvas.drawPath(self._star_unit, self._col(self.fill, (255, 215, 0), alpha))
        canvas.restore()

    def touch_hold(self, canvas, pos, d, alpha, kind, progress, off, brightness=1.0, firework=False):
        x, y = self.xy(pos)
        dist = self._touch_spread(d)
        for k, rgb in enumerate(TOUCH_HOLD):
            angle = -math.pi / 4 + k * math.pi / 2
            colors = (GREY, (96, 96, 96)) if off else (rgb, rgb)
            self._triangle(canvas, x, y, angle, dist, colors, alpha, False)
        if firework:
            self._firework_mark(canvas, x, y, alpha)
        if progress is None or progress <= 0:
            return
        # mai-notes' progress diamond: one side per colour, filled in turn
        # clockwise from the top - at half its stroke width.
        o = self.R / 4.46 * 1.1 / 2 * math.sqrt(2)
        corners = ((x, y - o), (x + o, y), (x, y + o), (x - o, y))
        fill = 4 * min(progress, 1.0)
        width = self.R / 31.25 * 1.1 / 2
        for k in range(4):
            part = max(0.0, min(1.0, fill - k))
            if part <= 0:
                break
            (ax, ay), (bx, by) = corners[k], corners[(k + 1) % 4]
            rgb = GREY if off else TOUCH_HOLD[k]
            canvas.drawLine(ax, ay, ax + (bx - ax) * part, ay + (by - ay) * part, self._stroke(rgb, width, alpha))

    # -- judgement --------------------------------------------------------------

    def _text(self, canvas, world, lines, rgb, alpha, scale=1.0):
        x, y = self.xy(world[:2, 2])
        rot = -g.mat_angle(world)
        canvas.save()
        canvas.translate(x, y)
        canvas.rotate(rot)
        canvas.scale(scale, scale)
        h = self.font.getSize()
        for i, line in enumerate(lines):
            w = self.font.measureText(line)
            ty = (i - (len(lines) - 1) / 2) * h * 1.05 + h * 0.35
            canvas.drawString(line, -w / 2, ty, self.font, self._stroke((0, 0, 0), self.line * 2.5, alpha))
            canvas.drawString(line, -w / 2, ty, self.font, self._col(self.fill, rgb, alpha))
        canvas.restore()

    def judge(self, canvas, world, grade, is_break, since):
        if since > 0.267:
            return
        scale = 1.2 - 0.2 * min(since / 0.0667, 1.0)
        alpha = 1.0 if since < 0.1667 else max(0.0, 1.0 - (since - 0.1667) / 0.1)
        if grade == "miss":
            self._text(canvas, world, ["MISS"], (200, 200, 210), alpha, scale)
        else:
            rgb = (255, 140, 0) if is_break and int(since * 30) % 2 else (255, 215, 0)
            self._text(canvas, world, ["CRITICAL", "PERFECT"], rgb, alpha, scale)

    def banner(self, canvas, world, kind, is_r, grade, is_break, since):
        if since > 0.4167:
            return
        alpha = min(1.0, since / 0.0333) if since < 0.2833 else max(0.0, 1.0 - (since - 0.2833) / 0.1334)
        if grade == "miss":
            self._text(canvas, world, ["MISS"], (200, 200, 210), alpha)
        else:
            self._text(canvas, world, ["CRITICAL PERFECT"], (255, 215, 0), alpha)

    # -- effects ----------------------------------------------------------------

    def hit(self, canvas, pos, rot, since, touch=False):
        if touch:
            self._touch_hit(canvas, pos, since)
            return
        if since > 0.2:
            return
        f = since / 0.2
        x, y = self.xy(pos)
        r = self.R / 12.5 * 1.36 * (1.0 + 0.8 * f)
        canvas.drawCircle(x, y, r, self._stroke(WHITE, self.line * 1.5, 1.0 - f))

    def hold_effect(self, canvas, pos, since, rgb=WHITE, strength=0.5):
        """The game's hold effect as plain stroked rings; white and at half
        strength in simple mode (mai-notes has none)."""
        x, y = self.xy(pos)
        for diameter, alpha in hold_effect_rings(since):
            r = diameter / 2 * HOLD_FX_RING * self.k
            canvas.drawCircle(x, y, r, self._stroke(rgb, self.line * 1.5, strength * alpha))

    def _touch_hit(self, canvas, pos, since):
        """A plain take on the game's touch hit: a soft disc opening out
        with four sparkle points, fading over 0.3s."""
        if since > 0.3:
            return
        f = since / 0.3
        x, y = self.xy(pos)
        r = self.R * (0.06 + 0.12 * (1 - (1 - f) ** 2))
        canvas.drawCircle(x, y, r, self._col(self.fill, WHITE, 0.25 * (1 - f)))
        canvas.drawCircle(x, y, r, self._stroke(WHITE, self.line, 0.6 * (1 - f)))
        d = r * 1.25
        for k in range(4):
            a = k * math.pi / 2
            canvas.drawCircle(x + math.cos(a) * d, y + math.sin(a) * d, self.line * 1.5,
                              self._col(self.fill, WHITE, 0.8 * (1 - f)))

    def firework(self, canvas, pos, since):
        """A faint ring of thin white rays - mai-notes has no firework
        effect at all, so simple mode keeps it subtle."""
        if since > 0.6:
            return
        f = since / 0.6
        x, y = self.xy(pos)
        r0, r1 = self.R * (0.05 + 0.3 * f), self.R * (0.15 + 0.85 * f)
        p = self._stroke(WHITE, self.line * 0.75, 0.35 * (1 - f) ** 2)
        for k in range(24):
            a = math.radians(k * 15)
            canvas.drawLine(x + math.cos(a) * r0, y + math.sin(a) * r0, x + math.cos(a) * r1, y + math.sin(a) * r1, p)
