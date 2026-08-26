"""
Turns a mai-notes capture into an mp4 Discord will play inline.

Two jobs: rebuild the tap-SFX track offline from the timings
`adapters/mainotes/player.py` recorded (the capture runs ~12x realtime, so
the player's own WebAudio output can't be recorded as it happens), then mux
it against the captured video.

The capture hands over a raw Annex-B H.264 stream whose frames the in-page
WebCodecs encoder already stamped at exactly k/30 seconds, so the video is
**copied, not re-encoded** - the only transcode that ever happens is the
corrective one when a long chart overshoots Discord's upload limit.

ffmpeg is a system binary, not a Python dependency - if it isn't on PATH
the caller degrades to a metadata embed rather than failing outright.
"""

import asyncio
import shutil
import subprocess
import wave
from pathlib import Path

from circlechiffon.adapters.mainotes.player import (
    ANSWER_WAV_URL,
    EACH_WAV_URL,
    SFX_SHIFT_MS,
    CaptureResult,
)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "mainotes_cache"
_ANSWER_WAV = CACHE_DIR / "answer.wav"
_EACH_WAV = CACHE_DIR / "each.wav"

# Discord rejects uploads over 10MB without a boosted server. Leave headroom
# for container overhead rather than aiming at the cap exactly.
DISCORD_SIZE_LIMIT = 10 * 1024 * 1024
SIZE_BUDGET = int(9.2 * 1024 * 1024)

# The player mixes its answer sounds at half volume; matching that keeps a
# dense section from clipping into mush.
_SFX_GAIN = 0.5
_ENCODE_TIMEOUT = 300


class VideoEncodeError(RuntimeError):
    pass


class FfmpegUnavailable(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegUnavailable("ffmpeg isn't installed or isn't on PATH")
    return path


async def _ensure_sfx_samples() -> tuple[Path, Path] | None:
    """The two tap samples, cached next to the manifest. Returns None if
    they can't be had - a silent video still beats no video."""
    if _ANSWER_WAV.exists() and _EACH_WAV.exists():
        return _ANSWER_WAV, _EACH_WAV

    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            answer = await client.get(ANSWER_WAV_URL)
            answer.raise_for_status()
            each = await client.get(EACH_WAV_URL)
            each.raise_for_status()
    except httpx.HTTPError:
        return None

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_ANSWER_WAV.write_bytes, answer.content)
        await asyncio.to_thread(_EACH_WAV.write_bytes, each.content)
    except OSError:
        return None
    return _ANSWER_WAV, _EACH_WAV


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if width != 2:
        raise VideoEncodeError(f"unexpected sample width {width} in {path.name}")
    data = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _build_sfx_track(capture: CaptureResult, samples: tuple[Path, Path], out_path: Path) -> None:
    """Mixes one sample per recorded hit into a single mono wav.

    Thousands of hits is normal for a maimai chart, so this is a numpy
    add-in-place rather than an ffmpeg filtergraph with one `adelay` per
    note - that approach needs one filter input per hit and falls over well
    before four-figure counts.
    """
    import numpy as np

    answer, rate = _read_wav(samples[0])
    each, each_rate = _read_wav(samples[1])
    if each_rate != rate:
        raise VideoEncodeError("the two tap samples disagree on sample rate")

    # Long enough for a hit on the final frame to ring out, and no longer:
    # the mux can't use `-shortest` (see _mux_command), so this length is
    # what keeps the audio from trailing past the video.
    tail = max(len(answer), len(each))
    total = int(capture.duration_seconds * rate) + tail
    track = np.zeros(total, dtype="float32")

    for hit in capture.sfx:
        sample = each if hit.is_each else answer
        # Events are recorded at the simulation step that fired them, so
        # they sit 0..16.7ms late; half a step back centres the error.
        start = int((hit.time_ms - SFX_SHIFT_MS) / 1000.0 * rate)
        if start >= total:
            continue
        if start < 0:
            # A hit can land a fraction of a frame before frame 0 (the
            # rebase in _rebase_sfx is frame-granular). Clamp it to the
            # start rather than dropping the chart's opening note.
            if start <= -len(sample):
                continue
            start = 0
        end = min(start + len(sample), total)
        track[start:end] += sample[: end - start] * _SFX_GAIN

    # Simultaneous hits stack, so clip rather than let the wrap-around of an
    # int16 cast turn a dense section into noise.
    np.clip(track, -1.0, 1.0, out=track)
    pcm = (track * 32767.0).astype("<i2")

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_ENCODE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise VideoEncodeError("ffmpeg timed out")
    if proc.returncode != 0:
        lines = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise VideoEncodeError(lines[-1] if lines else f"ffmpeg exited {proc.returncode}")


def _mux_command(ffmpeg: str, capture: CaptureResult, audio: Path | None, out_path: Path) -> list[str]:
    """Copy the video, encode only the audio.

    Raw Annex-B carries no usable timing - the WebCodecs stream's SPS makes
    ffprobe report `r_frame_rate 1200000/1`, i.e. a guess - so the rate has
    to be imposed on the demuxer. That is exact here, because every captured
    frame is one 1/30 of a second of virtual time by construction.

    It has to be `-r`, not `-framerate`: measured on a 420-frame capture,
    `-r 30` yields 30/1 and a 14.000s duration, while `-framerate 30` yields
    60/1 and a 2.0s duration off the same bytes.
    """
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "h264", "-r", str(capture.fps), "-i", str(capture.video_path),
    ]
    if audio is not None:
        cmd += ["-i", str(audio), "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy"]
    if audio is not None:
        # No `-shortest` here, deliberately. Copied Annex-B packets carry no
        # timestamps, so ffmpeg reads stream 0 as having no duration and
        # `-shortest` truncates the audio to nothing - it muxes cleanly, exits
        # 0, and writes `audio:0KiB`. The SFX track is instead built to end
        # with the video (see _build_sfx_track).
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd += ["-movflags", "+faststart", str(out_path)]
    return cmd


def _shrink_command(ffmpeg: str, source: Path, out_path: Path, bitrate: int) -> list[str]:
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2),
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart", str(out_path),
    ]


async def encode_capture(
    capture: CaptureResult,
    out_path: Path,
    *,
    with_audio: bool = True,
    size_limit: int = SIZE_BUDGET,
) -> Path:
    """Captured stream (+ rebuilt SFX) -> an mp4 under Discord's limit."""
    ffmpeg = ffmpeg_path()

    audio: Path | None = None
    if with_audio and capture.sfx:
        samples = await _ensure_sfx_samples()
        if samples is not None:
            audio = out_path.with_name(out_path.stem + "-sfx.wav")
            try:
                await asyncio.to_thread(_build_sfx_track, capture, samples, audio)
            except (VideoEncodeError, OSError, ValueError):
                audio.unlink(missing_ok=True)
                audio = None  # a silent render beats a failed one

    try:
        await _run(_mux_command(ffmpeg, capture, audio, out_path))

        if out_path.stat().st_size > size_limit:
            # The in-page bitrate is chosen from an estimated duration, so
            # a chart with tempo changes can still overshoot. Now that the
            # real duration is known, one corrective pass at the exact
            # bitrate is enough.
            duration = max(capture.duration_seconds, 1.0)
            audio_bits = 64_000 if audio is not None else 0
            bitrate = max(200_000, int(size_limit * 8 / duration) - audio_bits)
            shrunk = out_path.with_name(out_path.stem + "-fit.mp4")
            await _run(_shrink_command(ffmpeg, out_path, shrunk, bitrate))
            shrunk.replace(out_path)
    finally:
        if audio is not None:
            audio.unlink(missing_ok=True)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise VideoEncodeError("ffmpeg produced no output")
    return out_path
