"""
Frame + SFX capture from mai-notes.com's chart player, via headless Chromium.

mai-notes has no video export - its player draws into a 500x500 <canvas>
from simai data. This module drives that player and harvests frames, so
what we render is the site's own renderer pixel for pixel rather than a
reimplementation of simai that would have to get slides and touch-holds
right by itself.

## The virtual clock

Confirmed live: the player's playback clock is `performance.now()` read
inside a `requestAnimationFrame` loop, and it never consults
`AudioContext.currentTime`. So overriding both - RAF queues the callback
instead of scheduling it, `performance.now` returns a number we control -
lets us hand-step the animation as fast as the CPU allows instead of
waiting out the song.

`performance.now` is only swapped in at `arm()`, immediately before
playback starts. Freezing it from page load would lie to anything timing
the chart parse, and the player doesn't schedule a single frame before the
play click anyway (measured: `rafCalls` is 0 right up to it).

## Encoding: WebCodecs, not MediaRecorder

Frames go straight into a WebCodecs `VideoEncoder` inside the page. The two
alternatives were both measured on a 14-second clip and both lost badly:

  MediaRecorder + captureStream(0) + requestFrame()
      21x realtime, but ffprobe found **5 frames of the 420 requested**.
      It timestamps by wall clock and silently drops whatever its encoder
      can't keep up with, which at 20x realtime is essentially everything.

  toDataURL PNG frames pulled over the CDP bridge
      exact, but 3.4x realtime - 62KB a frame, and the bridge transfer
      dominated. (WebP is worse still: `toDataURL` costs 17.8ms a frame for
      WebP against 1.1ms for PNG.)

  WebCodecs VideoEncoder                                  <- what we use
      12x realtime, 420 frames in and 420 frames out, verified with
      `ffprobe -count_frames`.

VideoEncoder also fixes the timing problem by construction: each frame
carries an explicit `timestamp` of `k/30` seconds, so no wall clock is
involved and no `setpts` retiming is needed downstream - ffmpeg muxes the
Annex-B stream with `-c:v copy`. `encodeQueueSize` gives real backpressure,
so frames cannot be dropped; `encoded == captured` is asserted rather than
hoped for.

## Capturing the SFX timings

The site has no song audio, only tap/answer samples, and at 12x realtime
WebAudio can't be recorded live anyway. Instead we record *when* the
player would have played each sample and mix them offline.

The hooks are on standard Web Audio names rather than the bundle's
minified ones, so a rebuild of their bundle doesn't break us:
`AudioBufferSourceNode.prototype.start` records the virtual time of every
hit, and `AudioContext.prototype.decodeAudioData` is wrapped to learn which
decoded buffer is which. The two samples are told apart by the byte length
of the encoded data they came from (answer.wav is 17054 bytes, each.wav
16282) rather than by decode order.

Three things have to be set up first, or the audio comes out wrong in ways
the video gives no hint of:

1. **Step at 60fps, not 30.** The player only sounds a note whose time
   falls in a **20ms** window, checked once per animation frame:

       const r = e - n.timingMs - this.timingOffsetMs;
       if (r >= 0 && r < 20 && this.Me(n)) { ... }

   A 30fps step is 33.3ms - wider than that window - so only 20/33.3 of
   notes can land in it. Measured on 51 seconds of the reference chart:
   **60fps stepping recorded 232 sound events, 30fps recorded 140** - 60%,
   exactly as the ratio predicts. Stepping at 60fps (16.67ms) guarantees
   every note lands in the window exactly once; every second frame is
   captured, for a 30fps video.

2. **Switch the sounds on.** `#answerSoundEnabled` is unchecked by default
   and gates the whole scheduler, and `#eachSoundEnabled` is separately
   unchecked - without it every simultaneous pair plays the ordinary answer
   sample and each.wav is never touched. (Measured: enabling it took the
   "each" count on a test clip from 0 to 7.)

3. **Zero the latency offset.** The player defaults `timingOffsetMs` to
   -90, firing sounds 90ms *before* the note to compensate for real output
   latency. We have none, so `#answerSoundOffset` is set to 90 to cancel
   it, and the mix shifts back half a step so the residual quantisation
   error is symmetric rather than always late.

## Other live findings worth keeping

- **The play button is `disabled` until the chart parses**, and clicking a
  disabled button is a no-op. This is what made an early attempt look like
  a trusted-gesture problem. Waiting on the chart `.txt` response plus a
  settle wait is the fix; the retry loop is belt and braces.
- **A fresh browser context per render is required, not hygiene.** The site
  parks a "the last load died mid-parse" sentinel in localStorage and then
  refuses to auto-load charts, which would poison every later render in a
  shared profile.
"""

import asyncio
import base64
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from circlechiffon.adapters.mainotes.catalog import PLAYER_URL

# Step the player at 60fps (see module docstring - the audio scheduler's
# 20ms window makes anything coarser drop notes), capture every 2nd step.
STEP_HZ = 60
OUTPUT_FPS = 30
_STEP_MS = 1000.0 / STEP_HZ
_CAPTURE_EVERY = STEP_HZ // OUTPUT_FPS

# Half a simulation step. Sound events are recorded at the step that fired
# them, so they land 0..16.7ms late; shifting the whole track back by half
# a step makes that +/-8.3ms instead.
SFX_SHIFT_MS = _STEP_MS / 2

_PAGE_TIMEOUT_MS = 45_000
_CAPTURE_TIMEOUT = 300
# ~8 minutes of video. Nothing in maimai comes close; this only exists so a
# wedged player can't spin forever.
_MAX_FRAMES = OUTPUT_FPS * 60 * 8
# The encoded stream comes back base64'd in slices rather than one string.
_TRANSFER_CHUNK_BYTES = 4 * 1024 * 1024

# Duration pre-pass: hand-step with no canvas capture just to find where the
# chart ends. 100ms is the trade - measured on the reference chart, it lands
# on 121.30s (the real capture's exact length) for ~1s of wall time, where a
# 250ms step is 0.2s long and only saves half a second.
_PREPASS_STEP_MS = 100.0
_PREPASS_MAX_STEPS = 20_000

HI_SPEED_DEFAULT = 7.5
HI_SPEED_MIN = 3.0
HI_SPEED_MAX = 9.0
HI_SPEED_STEP = 0.25

_DEFAULT_BITRATE = 1_200_000
_MIN_BITRATE = 350_000
_MAX_BITRATE = 2_500_000

# Byte lengths of the two encoded samples, used to tell the decoded buffers
# apart. Verified live against the served files.
ANSWER_WAV_BYTES = 17054
EACH_WAV_BYTES = 16282

ANSWER_WAV_URL = "https://mai-notes.com/audio/answer.wav"
EACH_WAV_URL = "https://mai-notes.com/audio/each.wav"

# Preference order. H.264 in Annex-B needs no transcode at all downstream;
# `isConfigSupported` is the only honest way to know what a given Chromium
# build actually has.
_CODECS = ("avc1.42E01F", "avc1.4D401F")


def clamp_hi_speed(value: float) -> float:
    """Snap to the slider's 0.25 grid and clamp to its range.

    The player's setter is `t >= 3 && t <= 9 && (this.hiSpeed = ...)` - an
    out-of-range value is *silently ignored* and the previous speed stays in
    effect, so an unclamped value would render at the wrong speed with no
    error anywhere.
    """
    snapped = round(value / HI_SPEED_STEP) * HI_SPEED_STEP
    return min(HI_SPEED_MAX, max(HI_SPEED_MIN, snapped))


class ChartRenderUnavailable(RuntimeError):
    """Playwright, its browser, or WebCodecs isn't available on this host."""


class ChartRenderError(RuntimeError):
    """The capture ran but didn't produce anything usable."""


@dataclass(slots=True)
class SfxHit:
    time_ms: float
    is_each: bool


@dataclass(slots=True)
class CaptureResult:
    video_path: Path
    frame_count: int
    fps: int = OUTPUT_FPS
    width: int = 500
    height: int = 500
    sfx: list[SfxHit] = field(default_factory=list)
    hi_speed: float = HI_SPEED_DEFAULT
    start_measure: int = 0
    end_measure: int = 0
    total_measures: int = 0
    truncated: bool = False

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0


# Installed via add_init_script, so it runs before any of the page's own
# scripts and is in place by the time the player module initialises.
_INIT_SCRIPT = """
(() => {
  const realRaf = window.requestAnimationFrame.bind(window);
  const realCaf = window.cancelAnimationFrame.bind(window);
  const realNow = performance.now.bind(performance);

  const cc = {
    mode: 'passthrough',
    vt: 0,
    queue: new Map(),
    nextId: 1,
    rafCalls: 0,
    sfx: [],
    encodedBytes: new WeakMap(),
    captured: 0,
  };
  window.__cc = cc;

  // A Map rather than a single slot: the page schedules more than one
  // callback at a time in places, and a single slot silently loses all but
  // the last. Passthrough until arm() so page startup keeps real timing.
  window.requestAnimationFrame = (cb) => {
    if (cc.mode === 'passthrough') return realRaf(cb);
    cc.rafCalls++;
    const id = cc.nextId++;
    cc.queue.set(id, cb);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    if (cc.mode === 'passthrough') return realCaf(id);
    cc.queue.delete(id);
  };

  cc.arm = () => {
    if (cc.mode === 'virtual') return;
    cc.mode = 'virtual';
    cc.vt = realNow();
    performance.now = () => cc.vt;
  };

  // Learn which decoded AudioBuffer came from which .wav, by the byte
  // length of the encoded data - decode order is not guaranteed.
  const AC = window.AudioContext || window.webkitAudioContext;
  if (AC && AC.prototype.decodeAudioData) {
    const orig = AC.prototype.decodeAudioData;
    AC.prototype.decodeAudioData = function (data, ...rest) {
      const len = data && data.byteLength;
      const out = orig.call(this, data, ...rest);
      if (out && typeof out.then === 'function') {
        return out.then((buf) => { try { cc.encodedBytes.set(buf, len); } catch (e) {} return buf; });
      }
      return out;
    };
  }

  // Every sample the player plays goes through here. In virtual mode we
  // record the time and deliberately do NOT start the node: at 12x realtime
  // that would queue thousands of buffer sources for nothing, and the audio
  // is rebuilt offline from these timings anyway.
  if (window.AudioBufferSourceNode) {
    const realStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      if (cc.mode !== 'virtual') return realStart.apply(this, args);
      try {
        cc.sfx.push({ t: cc.vt, bytes: cc.encodedBytes.get(this.buffer) || 0 });
      } catch (e) {}
      return undefined;
    };
  }
})();
"""

_PROBE_CODECS = """
async (codecs) => {
  if (typeof VideoEncoder === 'undefined') return null;
  const canvas = document.querySelector('canvas');
  for (const codec of codecs) {
    try {
      const support = await VideoEncoder.isConfigSupported({
        codec, width: canvas.width, height: canvas.height,
        framerate: 30, bitrate: 1000000, avc: { format: 'annexb' },
      });
      if (support && support.supported) return codec;
    } catch (e) {}
  }
  return null;
}
"""

# The whole capture runs as one evaluate: a round-trip per frame would cost
# thousands of them. It yields to the event loop periodically so the encoder
# drains and progress polling stays responsive.
_CAPTURE_LOOP = """
async ([codec, stepMs, captureEvery, fps, maxFrames, bitrate, toMeasure]) => {
  const cc = window.__cc;
  const canvas = document.querySelector('canvas');
  const slider = document.getElementById('measureSlider');

  const chunks = [];
  let encoded = 0;
  let encodeError = null;

  const encoder = new VideoEncoder({
    output: (chunk) => {
      const bytes = new Uint8Array(chunk.byteLength);
      chunk.copyTo(bytes);
      chunks.push(bytes);
      encoded++;
    },
    error: (e) => { encodeError = String((e && e.message) || e); },
  });
  encoder.configure({
    codec, width: canvas.width, height: canvas.height,
    framerate: fps, bitrate, avc: { format: 'annexb' },
  });

  // The virtual clock does NOT start at zero - arm() seeds it from the real
  // performance.now(), i.e. however long the page took to load. Sound events
  // are stamped with it, so this origin is what makes them video-relative.
  const startVt = cc.vt;
  let captured = 0;
  let ended = false;
  let reachedEnd = false;
  let truncated = false;

  while (!encodeError) {
    for (let i = 0; i < captureEvery; i++) {
      // Drain every queued callback, not just one - the page schedules
      // more than the player's own frame loop.
      const pending = Array.from(cc.queue.entries());
      if (pending.length === 0) { ended = true; break; }
      cc.vt += stepMs;
      cc.queue.clear();
      for (const [, cb] of pending) cb(cc.vt);
    }

    // Real backpressure: this is what MediaRecorder had no equivalent of.
    while (encoder.encodeQueueSize > 20) {
      await new Promise((r) => encoder.addEventListener('dequeue', r, { once: true }));
    }
    const frame = new VideoFrame(canvas, {
      timestamp: Math.round(captured * 1e6 / fps),
      duration: Math.round(1e6 / fps),
    });
    encoder.encode(frame, { keyFrame: captured % (fps * 2) === 0 });
    frame.close();
    captured++;
    cc.captured = captured;

    if (ended) break;
    if (toMeasure !== null && slider && Number(slider.value) > toMeasure) { reachedEnd = true; break; }
    if (captured >= maxFrames) { truncated = true; break; }
    if (captured % 120 === 0) await new Promise((r) => setTimeout(r, 0));
  }

  await encoder.flush();
  encoder.close();

  let total = 0;
  for (const c of chunks) total += c.length;
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) { merged.set(c, offset); offset += c.length; }
  cc.stream = merged;

  const sfx = cc.sfx;
  cc.sfx = [];
  return {
    startVt,
    captured, encoded, truncated, ended, reachedEnd,
    error: encodeError,
    bytes: total,
    sfx,
    measure: slider ? Number(slider.value) : 0,
    width: canvas.width,
    height: canvas.height,
  };
}
"""

# Steps the player to the end without drawing or encoding anything, purely
# to time the chart. See _measure_duration for why this beats estimating.
_PREPASS = """
async ([stepMs, maxSteps, toMeasure]) => {
  const cc = window.__cc;
  const slider = document.getElementById('measureSlider');
  const start = cc.vt;
  let steps = 0;
  while (steps < maxSteps) {
    const pending = Array.from(cc.queue.entries());
    if (pending.length === 0) break;
    cc.vt += stepMs;
    cc.queue.clear();
    for (const [, cb] of pending) cb(cc.vt);
    steps++;
    if (toMeasure !== null && slider && Number(slider.value) > toMeasure) break;
    if (steps % 200 === 0) await new Promise((r) => setTimeout(r, 0));
  }
  return { steps, elapsed: cc.vt - start };
}
"""

# Rewind to the render's first measure and drop everything the pre-pass
# recorded, so the real capture starts from a clean slate.
_REWIND = """
([measure]) => {
  // `#continuePlaybackOnSeek` ships *checked*, so a seek resumes playback on
  // its own - and then the play click that follows would pause it again,
  // costing a 4s wait for a state that never arrives. Turn it off so the
  // rewind reliably leaves the player stopped.
  const cont = document.getElementById('continuePlaybackOnSeek');
  if (cont && cont.checked) {
    cont.checked = false;
    cont.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const s = document.getElementById('measureSlider');
  if (s) {
    s.value = String(measure);
    s.dispatchEvent(new Event('input', { bubbles: true }));
    s.dispatchEvent(new Event('change', { bubbles: true }));
  }
  window.__cc.sfx = [];
  window.__cc.captured = 0;
  return { measure: s ? Number(s.value) : 0, playing: window.__cc.queue.size > 0 };
}
"""

_TRANSFER_SLICE = """
([start, length]) => {
  const bytes = window.__cc.stream.subarray(start, start + length);
  let s = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(s);
}
"""


def _import_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ChartRenderUnavailable(
            "playwright isn't installed - run `pip install playwright` and "
            "`python -m playwright install chromium`"
        ) from exc
    return async_playwright


def _bitrate_for(duration_s: float | None, size_budget_bytes: int | None) -> int:
    """Pick an encoder bitrate that lands inside the upload budget.

    The duration comes from the pre-pass, so it's the real length of what is
    about to be encoded rather than an estimate - hence only a small margin
    for the container overhead and rate-control slop, not the 20% a
    BPM-derived guess needed.
    """
    if not duration_s or not size_budget_bytes:
        return _DEFAULT_BITRATE
    budget = int(size_budget_bytes * 8 / (duration_s * 1.05))
    return max(_MIN_BITRATE, min(_MAX_BITRATE, budget))


async def capture_chart(
    chart_id: str,
    out_path: Path,
    *,
    hi_speed: float = HI_SPEED_DEFAULT,
    from_measure: int | None = None,
    to_measure: int | None = None,
    size_budget_bytes: int | None = None,
    progress=None,
) -> CaptureResult:
    """Drives the mai-notes player for one chart, writing a raw Annex-B
    H.264 stream to `out_path` and returning it with the SFX timings.

    `progress`, if given, is called as `progress(seconds_captured)` while
    the capture runs - it takes long enough that a command wants to say so.
    """
    async_playwright = _import_playwright()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import Error as PlaywrightError

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--mute-audio",
                    "--disable-dev-shm-usage",
                ]
            )
            try:
                return await _capture(
                    browser, chart_id, out_path, hi_speed, from_measure, to_measure,
                    size_budget_bytes, progress,
                )
            finally:
                await browser.close()
    except (ChartRenderError, ChartRenderUnavailable):
        raise
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise ChartRenderUnavailable(
                "Chromium isn't installed for playwright - run `python -m playwright install chromium`"
            ) from exc
        raise ChartRenderError(message) from exc


async def _capture(
    browser, chart_id, out_path, hi_speed, from_measure, to_measure,
    size_budget_bytes, progress,
) -> CaptureResult:
    # A fresh context every time: the site parks a "last load crashed"
    # sentinel in localStorage and then refuses to auto-load charts.
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = await context.new_page()
        page.set_default_timeout(_PAGE_TIMEOUT_MS)
        await page.add_init_script(_INIT_SCRIPT)

        # The chart text arrives well after the page shell, and the play
        # button stays `disabled` until it parses - clicking early is a
        # silent no-op, not an error.
        async with page.expect_response(lambda r: f"/data/charts/{chart_id}" in r.url):
            await page.goto(PLAYER_URL.format(chart_id=chart_id), wait_until="domcontentloaded")
        await page.wait_for_selector("#playPauseButton")
        await page.wait_for_selector("canvas")
        await page.wait_for_function(
            "() => { const s = document.getElementById('measureSlider'); return s && Number(s.max) > 0; }"
        )
        await _wait_for_stable_length(page)

        codec = await page.evaluate(_PROBE_CODECS, list(_CODECS))
        if not codec:
            raise ChartRenderUnavailable(
                "this Chromium build has no WebCodecs H.264 encoder, so charts can't be rendered"
            )

        total_measures = int(await page.evaluate("() => Number(document.getElementById('measureSlider').max)"))
        applied_speed = await _apply_hi_speed(page, hi_speed)
        await _enable_sounds(page)

        start_measure = 0
        if from_measure:
            start_measure = max(0, min(int(from_measure), total_measures))
            await page.evaluate(
                """(m) => { const s = document.getElementById('measureSlider');
                            s.value = String(m);
                            s.dispatchEvent(new Event('input', {bubbles: true}));
                            s.dispatchEvent(new Event('change', {bubbles: true})); }""",
                start_measure,
            )

        await page.evaluate("() => window.__cc.arm()")
        await _start_playback(page)

        end = None if to_measure is None else max(start_measure, int(to_measure))
        span_seconds = await _measure_duration(page, end)
        await page.evaluate(_REWIND, [start_measure])
        await _start_playback(page)

        bitrate = _bitrate_for(span_seconds, size_budget_bytes)
        watcher = (
            asyncio.create_task(_watch_progress(page, progress, span_seconds))
            if progress else None
        )
        try:
            summary = await asyncio.wait_for(
                page.evaluate(
                    _CAPTURE_LOOP,
                    [codec, _STEP_MS, _CAPTURE_EVERY, OUTPUT_FPS, _MAX_FRAMES, bitrate, end],
                ),
                timeout=_CAPTURE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ChartRenderError("the capture ran past its time limit")
        finally:
            if watcher is not None:
                watcher.cancel()

        if summary.get("error"):
            raise ChartRenderError(f"the in-page encoder failed: {summary['error']}")
        captured = int(summary.get("captured") or 0)
        encoded = int(summary.get("encoded") or 0)
        if captured == 0:
            raise ChartRenderError("the player produced no frames - playback never started")
        if encoded != captured:
            # WebCodecs gives real backpressure, so this should be
            # impossible - if it ever fires, the video is silently missing
            # frames and would play at the wrong speed.
            raise ChartRenderError(f"the encoder dropped frames ({encoded} of {captured} survived)")

        await _transfer_stream(page, int(summary.get("bytes") or 0), out_path)

        return CaptureResult(
            video_path=out_path,
            frame_count=captured,
            width=int(summary.get("width") or 500),
            height=int(summary.get("height") or 500),
            sfx=_rebase_sfx(summary.get("sfx") or [], float(summary.get("startVt") or 0.0)),
            hi_speed=applied_speed,
            start_measure=start_measure,
            end_measure=int(summary.get("measure") or 0),
            total_measures=total_measures,
            truncated=bool(summary.get("truncated")),
        )
    finally:
        await context.close()


async def _measure_duration(page, to_measure: int | None) -> float | None:
    """How long the render will be, in seconds, measured rather than guessed.

    Steps the player to the end with no canvas capture and no encoding, then
    the caller rewinds and captures for real. Costs about a second on a
    2-minute chart and lands on its exact length (measured: 121.30s against a
    real capture of 121.30s).

    The alternative - `measures * 4 * 60 / bpm` - is wrong twice over. It
    can't parse the 37 songs whose manifest BPM is prose (`"162-180(162)"`,
    `"120～240(120)"`), and on any chart with a tempo change the measures
    aren't equal lengths, so a total derived from the measure cursor drifts
    as it plays: PANDORA PARADOXXX projected anywhere from 2:40 down to its
    real 2:24 over the course of one render. A total that keeps changing is
    worse than no total at all.

    Returns None if the pre-pass ends immediately, in which case the caller
    falls back to a default bitrate and elapsed-only progress.
    """
    result = await page.evaluate(_PREPASS, [_PREPASS_STEP_MS, _PREPASS_MAX_STEPS, to_measure])
    elapsed_ms = float(result.get("elapsed") or 0.0)
    if elapsed_ms < 1000.0:
        return None
    # The loop stops on the first step *past* the end, so it overshoots by up
    # to one step; centre that.
    return (elapsed_ms - _PREPASS_STEP_MS / 2) / 1000.0


def _rebase_sfx(raw: list[dict], start_vt: float) -> list[SfxHit]:
    """Put sound events on the video's timeline.

    Two shifts, and getting either wrong is audible:

    `start_vt` - the virtual clock is seeded from `performance.now()` at
    arm(), not from zero, so raw event stamps carry however long the page
    took to load (measured: ~1.2s, and it varies per run). Without this the
    whole track plays that far late.

    Half a frame - the loop steps `_CAPTURE_EVERY` times and *then* grabs
    the canvas, so captured frame k depicts virtual time
    `start_vt + (k+1) * frame` while the video plays frame k at `k * frame`.
    A note at time T therefore becomes visible on the first frame depicting
    `>= T`, which plays at `(T - start_vt) - r` for `r` uniform in
    `[0, frame)` - a mean of half a frame early, not a whole one. Subtracting
    a full frame here (with the half-step shift on top) put the audio a full
    video frame ahead of the note it belongs to.
    """
    origin = start_vt + 500.0 / OUTPUT_FPS
    return [
        SfxHit(time_ms=float(h.get("t", 0.0)) - origin, is_each=h.get("bytes") == EACH_WAV_BYTES)
        for h in raw
    ]


async def _watch_progress(page, progress, total_seconds: float | None) -> None:
    """Reports `(elapsed_seconds, total_seconds, fraction)`.

    Both numbers are measured: the total from the pre-pass, the elapsed from
    frames actually captured. The total is fixed for the whole render, which
    is the point - deriving it live from the measure cursor made it wander.
    """
    while True:
        await asyncio.sleep(2.0)
        try:
            captured = await page.evaluate("() => window.__cc.captured || 0")
        except Exception:
            return
        elapsed = int(captured) / OUTPUT_FPS
        fraction = min(1.0, elapsed / total_seconds) if total_seconds else 0.0
        progress(elapsed, total_seconds, fraction)


async def _transfer_stream(page, total_bytes: int, out_path: Path) -> None:
    """Pulls the encoded stream back in slices. One base64 string for a
    multi-megabyte stream is a needlessly large single CDP message."""
    if total_bytes <= 0:
        raise ChartRenderError("the encoder produced an empty stream")

    with out_path.open("wb") as fh:
        offset = 0
        while offset < total_bytes:
            length = min(_TRANSFER_CHUNK_BYTES, total_bytes - offset)
            encoded = await page.evaluate(_TRANSFER_SLICE, [offset, length])
            fh.write(base64.b64decode(encoded))
            offset += length


async def _wait_for_stable_length(page) -> None:
    """The measure slider's max climbs as the chart parses (it sits at a
    placeholder 5 early on). Wait for it to settle rather than trusting the
    first non-zero value."""
    previous = -1
    for _ in range(20):
        current = int(await page.evaluate("() => Number(document.getElementById('measureSlider').max)"))
        if current == previous and current > 0:
            return
        previous = current
        await asyncio.sleep(0.15)


async def _start_playback(page) -> None:
    from playwright.async_api import Error as PlaywrightError

    for attempt in range(3):
        await page.click("#playPauseButton")
        try:
            await page.wait_for_function("() => window.__cc.queue.size > 0", timeout=4_000)
            return
        except PlaywrightError:
            if attempt == 2:
                raise ChartRenderError(
                    "the player never started - the play button stayed inert after three clicks"
                )
            await asyncio.sleep(0.5)


async def _apply_hi_speed(page, hi_speed: float) -> float:
    """Sets ハイスピ, clamped to whatever range the slider actually
    advertises rather than to a hardcoded 3.0-9.0 - if mai-notes widens it,
    that shows up as a wider option instead of a silent clip."""
    return await page.evaluate(
        """(wanted) => {
            const s = document.getElementById('hiSpeedSlider');
            if (!s) return wanted;
            const min = Number(s.min), max = Number(s.max);
            const step = Number(s.step) || 0.25;
            let v = Math.min(max, Math.max(min, wanted));
            v = Math.round((v - min) / step) * step + min;
            v = Math.min(max, Math.max(min, v));
            s.value = String(v);
            s.dispatchEvent(new Event('input', {bubbles: true}));
            s.dispatchEvent(new Event('change', {bubbles: true}));
            return Number(s.value);
        }""",
        hi_speed,
    )


async def _enable_sounds(page) -> None:
    """See the module docstring: `#answerSoundEnabled` gates the whole note
    scheduler and is off by default, `#eachSoundEnabled` is what selects the
    second sample and is separately off, and `#answerSoundOffset` defaults
    to firing sounds 90ms early to hide real output latency we don't have."""
    await page.evaluate(
        """() => {
            const fire = (el) => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            };
            for (const id of ['answerSoundEnabled', 'eachSoundEnabled', 'holdEndSoundEnabled', 'touchSoundEnabled']) {
                const el = document.getElementById(id);
                if (el && !el.checked) { el.checked = true; fire(el); }
            }
            const offset = document.getElementById('answerSoundOffset');
            if (offset) {
                // The handler is `setOffset(value - 90)`, so 90 means zero.
                offset.value = String(Math.min(Number(offset.max), 90));
                fire(offset);
            }
        }"""
    )


def cleanup(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
