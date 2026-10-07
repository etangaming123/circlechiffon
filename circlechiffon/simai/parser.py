"""
simai notes-section parser.

Input is the notes text for ONE difficulty (what mai-notes serves in its
player's `#simaiInput` textarea - no `&inote_N=` headers), e.g.

    (155){1},,{4}1,2/8,3h[4:1],1-5[8:1],B2,Ch[4:1]f,E

Output is a `Chart`: every note with an absolute time in seconds, plus the
BPM timeline so measures can be turned into times.

## The format, as implemented

- `(bpm)` sets the tempo; `{n}` makes each comma one 1/n of a whole note
  (4 beats); `{#s}` makes each comma `s` seconds outright.
- `,` advances time by one step. Everything between two commas sounds at
  once: `/` separates simultaneous notes, and a run of bare digits (`18`)
  is shorthand for that many taps. A backtick is a "pseudo-each" - notes a
  hair apart rather than truly together.
- Buttons are 1-8, touch sensors A1-8 / B1-8 / C / D1-8 / E1-8.
- Modifiers: `b` break, `x` EX, `h[..]` hold, `f` firework (touch), `$`
  star-shaped tap (`$$` spinning), `@` slide with a plain tap head, `?`
  slide with no head (arrows fade in), `!` slide with no head that appears
  suddenly. `3/0` colours a lone note as an each.
- Slides: `<start><shape><end>[timing]`, shapes `- ^ < > v p q pp qq s z
  V w` (`V` takes a turn point too: `1V35`). Segments chain (`1-4q7-2[..]`)
  and `*` launches several slides from one star (`1-5[8:1]*-3[8:1]`).
- Slide timing: `[a:b]` waits one beat, then travels b/a whole notes;
  `[bpm#a:b]` and `[bpm#secs]` do both at another tempo; `[#secs]` waits a
  beat and travels for `secs`; `[delay##secs]` and `[delay##a:b]` give the
  wait in seconds; `[0:0]` is instant.

Reference: beer-psi's simai guide,
https://gist.github.com/beer-psi/db93f0f75a56af35ba44c20431d52748
- `||` starts a comment; `E` alone ends the chart.

Malformed notes are skipped, not fatal: a typo in one bar of a fan-made
chart shouldn't cost the whole render. `Chart.warnings` says what was
dropped.
"""

import math
from dataclasses import dataclass, field

SLIDE_SHAPES = ("pp", "qq", "-", "^", "<", ">", "v", "p", "q", "s", "z", "V", "w")
_SHAPE_START = set("-^<>vpqszVw")
_HEAD_FLAGS = set("bx$@?!hfm")
TOUCH_AREAS = "ABCDE"

# Backtick-separated notes ("pseudo-each") land this far apart. Small enough
# to read as one chord, large enough not to be coloured as a true each.
_PSEUDO_EACH_STEP = 0.010


@dataclass(slots=True)
class Tap:
    time: float
    position: int  # 1..8
    is_break: bool = False
    is_ex: bool = False
    is_star: bool = False
    is_each: bool = False


@dataclass(slots=True)
class Hold:
    time: float
    position: int
    duration: float
    is_break: bool = False
    is_ex: bool = False
    is_each: bool = False


@dataclass(slots=True)
class Touch:
    time: float
    area: str  # "A".."E"
    index: int  # 1..8, 0 for C
    firework: bool = False
    duration: float | None = None  # set for a touch hold
    is_break: bool = False
    is_each: bool = False


@dataclass(slots=True)
class SlideSegment:
    shape: str
    start: int
    end: int
    via: int | None = None  # the turn point of a `V`
    duration: float | None = None  # per-segment timing, if the chart gave one


@dataclass(slots=True)
class Slide:
    head_time: float  # when the star is hit
    launch_time: float  # when the star leaves the start
    duration: float  # launch -> arrival, over all segments
    segments: list[SlideSegment]
    is_break: bool = False
    is_each: bool = False
    sudden: bool = False  # `!` - no fade-in

    @property
    def end_time(self) -> float:
        return self.launch_time + self.duration


@dataclass(slots=True)
class BpmPoint:
    beat: float
    time: float
    bpm: float


@dataclass(slots=True)
class Chart:
    taps: list[Tap] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)
    touches: list[Touch] = field(default_factory=list)
    slides: list[Slide] = field(default_factory=list)
    bpms: list[BpmPoint] = field(default_factory=list)
    each_markers: list[float] = field(default_factory=list)
    total_beats: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def total_measures(self) -> int:
        return max(1, math.ceil(self.total_beats / 4 - 1e-9))

    def time_at_beat(self, beat: float) -> float:
        if not self.bpms:
            return 0.0
        point = self.bpms[0]
        for p in self.bpms:
            if p.beat > beat:
                break
            point = p
        return point.time + (beat - point.beat) * 60.0 / point.bpm

    def measure_time(self, measure: int) -> float:
        return self.time_at_beat(4.0 * measure)

    def measure_at(self, time: float) -> int:
        """Inverse of measure_time, floored."""
        point = self.bpms[0] if self.bpms else BpmPoint(0, 0, 120)
        for p in self.bpms:
            if p.time > time:
                break
            point = p
        beat = point.beat + (time - point.time) * point.bpm / 60.0
        return max(0, int(beat // 4))

    @property
    def end_time(self) -> float:
        """When the last thing on screen finishes."""
        ends = [0.0]
        ends += [t.time for t in self.taps]
        ends += [h.time + h.duration for h in self.holds]
        ends += [t.time + (t.duration or 0.0) for t in self.touches]
        ends += [s.end_time for s in self.slides]
        return max(ends)

    @property
    def first_time(self) -> float:
        starts = [t.time for t in self.taps] + [h.time for h in self.holds]
        starts += [t.time for t in self.touches] + [s.head_time for s in self.slides]
        return min(starts) if starts else 0.0

    @property
    def note_count(self) -> int:
        return len(self.taps) + len(self.holds) + len(self.touches) + len(self.slides)


class SimaiError(ValueError):
    pass


# -- timing brackets ------------------------------------------------------------


def _beats_fraction(text: str, bpm: float) -> float:
    """`a:b` -> seconds at `bpm`: b notes of 1/a of a whole note."""
    a, b = text.split(":", 1)
    a, b = float(a), float(b)
    if a == 0 and b == 0:
        return 0.0  # `[0:0]` - an instant slide
    if a <= 0 or bpm <= 0:
        raise SimaiError(f"bad length [{text}]")
    return 240.0 / bpm / a * b


def parse_hold_length(inner: str, bpm: float) -> float:
    """Hold brackets: `a:b`, `bpm#a:b`, `#secs`."""
    inner = inner.strip()
    if inner.startswith("#"):
        return float(inner[1:])
    if "#" in inner:
        other, rest = inner.split("#", 1)
        return _beats_fraction(rest, float(other))
    return _beats_fraction(inner, bpm)


def parse_slide_timing(inner: str, bpm: float) -> tuple[float, float]:
    """Slide brackets -> (wait before launch, travel time), in seconds."""
    inner = inner.strip()
    if inner.startswith("#") and not inner.startswith("##"):
        return 60.0 / bpm, float(inner[1:])  # `[#secs]`
    if "##" in inner:
        delay_text, rest = inner.split("##", 1)
        delay = float(delay_text)
        if ":" not in rest:
            return delay, float(rest)
        if "#" in rest:
            other, frac = rest.split("#", 1)
            return delay, _beats_fraction(frac, float(other))
        return delay, _beats_fraction(rest, bpm)
    if "#" in inner:
        other_text, rest = inner.split("#", 1)
        other = float(other_text)
        delay = 60.0 / other
        if ":" in rest:
            return delay, _beats_fraction(rest, other)
        return delay, float(rest)
    return 60.0 / bpm, _beats_fraction(inner, bpm)


# -- note strings -----------------------------------------------------------------


class _Cursor:
    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def peek(self, n: int = 1) -> str:
        return self.text[self.i:self.i + n]

    def take(self, n: int = 1) -> str:
        s = self.text[self.i:self.i + n]
        self.i += n
        return s

    def done(self) -> bool:
        return self.i >= len(self.text)

    def bracket(self) -> str | None:
        if self.peek() != "[":
            return None
        end = self.text.find("]", self.i)
        if end < 0:
            raise SimaiError(f"unclosed [ in {self.text!r}")
        inner = self.text[self.i + 1:end]
        self.i = end + 1
        return inner

    def flags(self, allowed: set[str]) -> str:
        start = self.i
        while not self.done() and self.peek() in allowed:
            self.i += 1
        return self.text[start:self.i]


def _position(ch: str) -> int:
    if len(ch) != 1 or ch not in "12345678":
        raise SimaiError(f"expected a button 1-8, got {ch!r}")
    return int(ch)


def _parse_touch(note: str, time: float, bpm: float, chart: Chart) -> None:
    cur = _Cursor(note)
    area = cur.take()
    index = 0
    if cur.peek().isdigit():
        index = int(cur.take())
    if area != "C" and not 1 <= index <= 8:
        raise SimaiError(f"bad touch sensor {note!r}")
    flags = cur.flags(set("fhbx"))
    duration = None
    if "h" in flags:
        inner = cur.bracket()
        duration = parse_hold_length(inner, bpm) if inner is not None else 0.0
        flags += cur.flags(set("fhbx"))
    if not cur.done():
        raise SimaiError(f"trailing characters in {note!r}")
    chart.touches.append(Touch(
        time=time, area=area, index=index, firework="f" in flags,
        duration=duration, is_break="b" in flags,
    ))


def _parse_slide_chain(cur: _Cursor, start: int, bpm: float, head_time: float) -> Slide:
    segments: list[SlideSegment] = []
    timings: list[tuple[float, float] | None] = []
    is_break = False
    pos = start
    while not cur.done() and cur.peek() != "*":
        shape = cur.take(2) if cur.peek(2) in ("pp", "qq") else cur.take()
        if shape not in SLIDE_SHAPES:
            raise SimaiError(f"unknown slide shape {shape!r}")
        via = None
        if shape == "V":
            via = _position(cur.take())
        end = _position(cur.take())
        is_break |= "b" in cur.flags(set("bx"))
        inner = cur.bracket()
        timings.append(parse_slide_timing(inner, bpm) if inner is not None else None)
        is_break |= "b" in cur.flags(set("bx"))
        segments.append(SlideSegment(shape=shape, start=pos, end=end, via=via))
        pos = end

    if not segments:
        raise SimaiError("slide with no segments")
    given = [t for t in timings if t is not None]
    if not given:
        raise SimaiError("slide with no timing")

    delay = given[0][0]
    if len(given) == len(segments):
        # Per-segment timing: each bracket's travel time is that segment's.
        for seg, (_, dur) in zip(segments, given):
            seg.duration = dur
        total = sum(t[1] for t in given)
    else:
        # One bracket for the whole chain; geometry splits it by length.
        total = given[-1][1]
    return Slide(
        head_time=head_time, launch_time=head_time + delay, duration=total,
        segments=segments, is_break=is_break,
    )


def _parse_button(note: str, time: float, bpm: float, chart: Chart) -> None:
    cur = _Cursor(note)
    position = _position(cur.take())
    flags = cur.flags(_HEAD_FLAGS)
    hold_len = None
    if "h" in flags:
        inner = cur.bracket()
        hold_len = parse_hold_length(inner, bpm) if inner is not None else 0.0
        flags += cur.flags(_HEAD_FLAGS)

    slides: list[Slide] = []
    while not cur.done():
        if cur.peek() == "*":
            cur.take()
            # `1-5[8:1]*1-3[8:1]` repeats the start button; it's optional.
            if cur.peek() == str(position):
                cur.take()
        if cur.done() or cur.peek() not in _SHAPE_START:
            raise SimaiError(f"can't read {note!r}")
        slides.append(_parse_slide_chain(cur, position, bpm, time))

    is_break, is_ex = "b" in flags, "x" in flags
    if hold_len is not None:
        chart.holds.append(Hold(time=time, position=position, duration=hold_len, is_break=is_break, is_ex=is_ex))
    elif not slides or not ("?" in flags or "!" in flags):
        is_star = (bool(slides) and "@" not in flags) or "$" in flags
        chart.taps.append(Tap(time=time, position=position, is_break=is_break, is_ex=is_ex, is_star=is_star))
    for slide in slides:
        slide.sudden = "!" in flags
    chart.slides.extend(slides)


def _parse_note(note: str, time: float, bpm: float, chart: Chart) -> None:
    if note[0] in TOUCH_AREAS:
        _parse_touch(note, time, bpm, chart)
    else:
        _parse_button(note, time, bpm, chart)


def _parse_group(group: str, time: float, bpm: float, chart: Chart) -> None:
    for part in group.split("/"):
        for k, note in enumerate(part.split("`")):
            if not note:
                continue
            t = time + k * _PSEUDO_EACH_STEP
            if note == "0":
                # `3/0` - no note, just makes the rest of the group an each.
                chart.each_markers.append(t)
                continue
            try:
                if note.isdigit() and len(note) > 1:
                    for ch in note:
                        chart.taps.append(Tap(time=t, position=_position(ch)))
                else:
                    _parse_note(note, t, bpm, chart)
            except (SimaiError, ValueError, IndexError) as e:
                chart.warnings.append(f"{time:.3f}s {note!r}: {e}")


def _mark_each(chart: Chart) -> None:
    """Notes sharing an exact moment are drawn yellow. Slides go by launch
    instead - two slides leaving together are an each, whatever their heads."""
    def key(t: float) -> int:
        return round(t * 1000)

    counts: dict[int, int] = {}
    heads = [*chart.taps, *chart.holds, *chart.touches]
    for n in heads:
        counts[key(n.time)] = counts.get(key(n.time), 0) + 1
    for t in chart.each_markers:
        counts[key(t)] = counts.get(key(t), 0) + 1
    for n in heads:
        n.is_each = counts[key(n.time)] > 1

    slide_counts: dict[int, int] = {}
    for s in chart.slides:
        slide_counts[key(s.launch_time)] = slide_counts.get(key(s.launch_time), 0) + 1
    for s in chart.slides:
        s.is_each = slide_counts[key(s.launch_time)] > 1


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("||", 1)[0] for line in text.splitlines())


def parse_chart(text: str, default_bpm: float = 120.0) -> Chart:
    text = _strip_comments(text)
    chart = Chart()
    bpm = default_bpm
    divisor: float | None = 4.0
    step_seconds: float | None = None
    time = 0.0
    beat = 0.0
    buf: list[str] = []
    bpm_set = False

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            end = text.find(")", i)
            if end < 0:
                raise SimaiError("unclosed (")
            bpm = float(text[i + 1:end])
            if bpm <= 0:
                raise SimaiError(f"bad BPM {bpm}")
            if chart.bpms and abs(chart.bpms[-1].beat - beat) < 1e-9:
                chart.bpms[-1] = BpmPoint(beat, time, bpm)
            else:
                chart.bpms.append(BpmPoint(beat, time, bpm))
            bpm_set = True
            i = end + 1
            continue
        if ch == "{":
            end = text.find("}", i)
            if end < 0:
                raise SimaiError("unclosed {")
            inner = text[i + 1:end].strip()
            if inner.startswith("#"):
                step_seconds, divisor = float(inner[1:]), None
            else:
                divisor, step_seconds = float(inner), None
                if divisor <= 0:
                    raise SimaiError(f"bad divisor {inner}")
            i = end + 1
            continue
        if ch == "[":
            # Brackets can hold `#`, `:` and `,`-free text only, but copy them
            # whole so nothing inside is mistaken for structure.
            end = text.find("]", i)
            if end < 0:
                raise SimaiError("unclosed [")
            buf.append(text[i:end + 1])
            i = end + 1
            continue
        if ch == ",":
            if not bpm_set:
                chart.bpms.append(BpmPoint(0.0, 0.0, bpm))
                bpm_set = True
            group = "".join(buf)
            buf.clear()
            if group:
                _parse_group(group, time, bpm, chart)
            if step_seconds is not None:
                time += step_seconds
                beat += step_seconds * bpm / 60.0
            else:
                time += 240.0 / bpm / divisor
                beat += 4.0 / divisor
            i += 1
            continue
        if ch == "E" and (i + 1 >= n or not text[i + 1].isdigit()):
            # A bare `E` is the end marker (sensor E always has a number),
            # sometimes glued straight onto the last note with no comma.
            break
        buf.append(ch)
        i += 1

    if not chart.bpms:
        chart.bpms.append(BpmPoint(0.0, 0.0, bpm))
    leftover = "".join(buf)
    if leftover:
        _parse_group(leftover, time, bpm, chart)
    chart.total_beats = beat
    _mark_each(chart)
    return chart
