"""
/cc-chart - render a mai-notes.com chart as a video.

The chart text comes from mai-notes (`adapters/mainotes/catalog.py`); the
drawing is local (`renderers/chart_local.py`: simai -> skia frames -> x264
across a process pool); the audio mux is `renderers/chart_video.py`. This
file is the Discord surface: resolve the user's song to a mai-notes chart,
queue the render, and pick which of the several "can't render that"
messages applies.

A render takes every worker core for several seconds, so renders go through
a FIFO queue, one at a time, and each user then waits _RENDER_COOLDOWN
seconds before their next (the owner skips the cooldown, never the queue).
Who may render at all is `config.chart_render` ("owner" by default, or
"everyone"); anyone else gets the chart's data as an embed, which costs no
more than /cc-info does.

A long chart comes back as several overlapping videos rather than one
heavily compressed one (see chart_local._split); the message lists each
video's span of the song.

(`adapters/mainotes/player.py` - the old headless-Chromium capture - is no
longer called from here.)
"""

import asyncio
import contextlib
import io
import re
import tempfile
import time
import unicodedata
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import config

from circlechiffon import access, embed_colors
from circlechiffon.adapters.dxrating.images import jacket_url
from circlechiffon.adapters.mainotes.catalog import MaiNotesChart, fetch_chart_text, get_mainotes_catalog
from circlechiffon.renderers import chart_local, chart_skin
from circlechiffon.renderers.chart_local import (
    HI_SPEED_DEFAULT,
    HI_SPEED_MAX,
    HI_SPEED_MIN,
    MODE_GAME,
    MODE_MISS,
    MODE_SIMPLE,
    ChartRenderError,
    ChartRenderUnavailable,
    clamp_hi_speed,
    render_chart,
)
from circlechiffon.renderers.chart_video import (
    SIZE_BUDGET,
    FfmpegUnavailable,
    VideoEncodeError,
    encode_capture,
    ffmpeg_available,
    ffmpeg_path,
)
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import ChartType, Difficulty

# Seconds a user waits after a render finishes before starting another. A
# key of its own: the command's cheap lookup stays on DEFAULT_COOLDOWN.
_RENDER_COOLDOWN = 30
_RENDER_COOLDOWN_KEY = "cc-chart-render"
_MAX_QUEUE = 5


class _RenderQueue:
    """Renders run one at a time, first come first served. Each render
    already fans out across every worker process, so running two at once
    would only make both slower."""

    def __init__(self) -> None:
        self._tickets: list[tuple[object, int]] = []
        self._changed = asyncio.Condition()

    def __len__(self) -> int:
        return len(self._tickets)

    def has(self, user_id: int) -> bool:
        return any(uid == user_id for _, uid in self._tickets)

    def _ahead(self, ticket: object) -> int:
        return next(i for i, (t, _) in enumerate(self._tickets) if t is ticket)

    @contextlib.asynccontextmanager
    async def turn(self, user_id: int, on_wait):
        """Waits for this caller's turn, awaiting `on_wait(renders_ahead)`
        each time its place in line changes, and holds the turn for the
        body of the `async with`."""
        ticket = object()
        self._tickets.append((ticket, user_id))
        try:
            shown = None
            while True:
                async with self._changed:
                    ahead = self._ahead(ticket)
                    if ahead == 0:
                        break
                    if ahead == shown:
                        await self._changed.wait()
                        continue
                # Outside the lock: on_wait is a Discord edit.
                shown = ahead
                await on_wait(ahead)
            yield
        finally:
            self._tickets = [entry for entry in self._tickets if entry[0] is not ticket]
            async with self._changed:
                self._changed.notify_all()


_queue = _RenderQueue()


def _may_render(user_id: int) -> bool:
    return access.is_owner(user_id) or config.chart_render == "everyone"

_DIFFICULTY_CHOICES = [
    app_commands.Choice(name=d.display_name, value=d.value)
    for d in (Difficulty.basic, Difficulty.advanced, Difficulty.expert, Difficulty.master, Difficulty.remaster)
]
# The first DX/STD command parameter in the bot - every other command shows
# both types side by side rather than asking which one you meant.
_RENDER_MODE_CHOICES = [
    app_commands.Choice(name="Simple (mai-notes style, fastest)", value=MODE_SIMPLE),
    app_commands.Choice(name="Game (maimai skin, all CRITICAL PERFECT)", value=MODE_GAME),
    app_commands.Choice(name="Game (maimai skin, all MISS)", value=MODE_MISS),
]
_RENDER_MODE_LABELS = {MODE_SIMPLE: "simple", MODE_GAME: "all CRITICAL PERFECT", MODE_MISS: "all MISS"}
_CHART_TYPE_CHOICES = [
    app_commands.Choice(name="DX", value=ChartType.dx.value),
    app_commands.Choice(name="Standard", value=ChartType.std.value),
]

# mai-notes carries playable data for 2862 of its 6378 charts, and the split
# is heavily skewed - it's a chart-study site, so the hard difficulties are
# the ones people have transcribed.
_COVERAGE_HINT = (
    "mai-notes has playable data for about **45%** of the charts it lists, "
    "and it's mostly the hard ones - try **MASTER**."
)


def _safe_filename(title: str, difficulty: Difficulty, part: int | None = None) -> str:
    """Song titles include path separators and invisible characters (one
    real mai-notes title is a single U+200E), so a title can't go into a
    filename unfiltered."""
    cleaned = "".join(ch for ch in unicodedata.normalize("NFKC", title) if unicodedata.category(ch) != "Cf")
    slug = re.sub(r"[^\w\-]+", "-", cleaned, flags=re.UNICODE).strip("-")[:60]
    suffix = f"-{part}" if part is not None else ""
    return f"chart-{slug or 'song'}-{difficulty.value}{suffix}.mp4"


def _format_clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _part_span(capture) -> str:
    """`1:02 - 2:15`: where in the song one video runs (the lead-in before
    the first note counts as 0:00)."""
    start = max(0.0, capture.start_seconds)
    end = max(0.0, capture.start_seconds + capture.duration_seconds)
    return f"{_format_clock(start)} - {_format_clock(end)}"


def _upload_limit(interaction: discord.Interaction) -> int:
    """Discord's attachment cap depends on the server's boost tier, and on
    nothing at all in a DM. Assume the free 10MB unless told otherwise."""
    limit = getattr(interaction.guild, "filesize_limit", None)
    if not limit:
        return SIZE_BUDGET
    return min(int(limit) - 400_000, 50 * 1024 * 1024)


def _note_breakdown(chart: MaiNotesChart) -> str:
    parts = [
        ("Tap", chart.taps), ("Hold", chart.hold), ("Slide", chart.slide),
        ("Touch", chart.touch), ("Break", chart.breaks),
    ]
    return " · ".join(f"{name} {value}" for name, value in parts if value is not None)


def _chart_embed(chart: MaiNotesChart, title: str, difficulty: Difficulty) -> discord.Embed:
    level = chart.level or "?"
    constant = f" ({chart.internal_level})" if chart.internal_level is not None else ""
    embed = discord.Embed(
        title=f"{chart.song.title} [{difficulty.display_name} {level}{constant}]",
        description=chart.song.artist or "",
        color=embed_colors.difficulty_color(difficulty),
        url=chart.player_url,
    )
    type_name = chart.song.chart_type.value.upper() if chart.song.chart_type else "?"
    meta = [f"{type_name} chart"]
    if chart.song.bpm:
        meta.append(f"BPM {chart.song.bpm}")
    if chart.version:
        meta.append(chart.version)
    embed.add_field(name="Chart", value=" · ".join(meta), inline=False)

    breakdown = _note_breakdown(chart)
    if chart.notes is not None or breakdown:
        value = f"**{chart.notes}** notes" if chart.notes is not None else ""
        if breakdown:
            value = f"{value}\n{breakdown}" if value else breakdown
        embed.add_field(name="Notes", value=value, inline=False)
    if chart.notes_designer:
        embed.add_field(name="Charter", value=chart.notes_designer, inline=True)
    if chart.tags:
        embed.add_field(name="Tags", value=" · ".join(chart.tags[:8]), inline=False)
    return embed


class ChartCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        # Workers are separate processes that each import numpy and skia;
        # starting them now keeps that cost off the first render.
        try:
            await chart_local.start_workers()
        except Exception as e:
            print(f"Couldn't pre-start chart render workers ({e}); the first render will start them")

    async def cog_unload(self) -> None:
        await chart_local.stop_workers()

    @app_commands.command(
        name="cc-chart",
        description="Look up a chart on mai-notes.com and render it as a video",
    )
    @app_commands.describe(
        title="Song title (or part of it, including English/romanized aliases) to search for",
        difficulty="Which difficulty's chart to render (default: MASTER)",
        chart_type="DX or Standard chart, for songs that have both (default: DX)",
        notespeed="Note speed / ハイスピ, 1.0-10.0 (default: 7.5)",
        from_measure="Start at this measure instead of the beginning",
        to_measure="Stop at this measure instead of playing to the end",
        render_mode="Simple mai-notes style (default, fastest), or game look with every judgement shown",
    )
    @app_commands.choices(
        difficulty=_DIFFICULTY_CHOICES, chart_type=_CHART_TYPE_CHOICES, render_mode=_RENDER_MODE_CHOICES
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def chart(
        self,
        interaction: discord.Interaction,
        title: str,
        difficulty: app_commands.Choice[str] | None = None,
        chart_type: app_commands.Choice[str] | None = None,
        notespeed: app_commands.Range[float, HI_SPEED_MIN, HI_SPEED_MAX] = HI_SPEED_DEFAULT,
        from_measure: app_commands.Range[int, 0, 2000] | None = None,
        to_measure: app_commands.Range[int, 1, 2000] | None = None,
        render_mode: app_commands.Choice[str] | None = None,
    ):
        user_id = interaction.user.id
        # DEFAULT_COOLDOWN covers the lookup; the render's own cooldown is
        # checked (and set, once a render finishes) in _render.
        if not await access.handle_command_access(interaction, user_id, "cc-chart", access.DEFAULT_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Looking that chart up...")

        def bail():
            # Nothing was rendered, so don't charge the render cooldown.
            access.clear_cooldown(user_id, "cc-chart")

        try:
            wanted_difficulty = Difficulty(difficulty.value) if difficulty else Difficulty.master

            song = next(iter(get_catalog().search(title, limit=1)), None)
            if song is None:
                bail()
                await interaction.edit_original_response(content=f"No songs found matching **{title}**.")
                return

            catalog = get_mainotes_catalog()
            if not await catalog.ensure_loaded():
                bail()
                await interaction.edit_original_response(
                    content="Couldn't reach mai-notes.com to look up its chart list. Try again in a bit."
                )
                return

            available_types = catalog.available_chart_types(song.title)
            if not available_types:
                bail()
                await interaction.edit_original_response(
                    content=(
                        f"mai-notes.com doesn't have **{song.title}**. It only carries songs that are "
                        "currently in the game, so anything removed for licensing reasons is missing there."
                    )
                )
                return

            wanted_type = ChartType(chart_type.value) if chart_type else (
                ChartType.dx if ChartType.dx in available_types else available_types[0]
            )
            found = catalog.find_chart(song.title, wanted_type, wanted_difficulty, artist=song.artist)
            if found is None:
                bail()
                others = ", ".join(t.value.upper() for t in available_types)
                await interaction.edit_original_response(
                    content=(
                        f"mai-notes.com doesn't list a **{wanted_type.value.upper()} "
                        f"{wanted_difficulty.display_name}** chart for **{song.title}** "
                        f"(it has: {others})."
                    )
                )
                return

            embed = _chart_embed(found, song.title, wanted_difficulty)
            if song.image_name:
                embed.set_thumbnail(url=jacket_url(song.image_name))

            # Those who can't render still get the lookup, which is the same
            # local-manifest work /cc-info does.
            if not _may_render(user_id):
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content=(
                        "Rendering chart videos is limited to the bot owner, so here's this "
                        "chart's data instead."
                    ),
                    embed=embed,
                )
                return

            if not found.has_chart_data:
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content=(
                        f"mai-notes.com lists this chart but has no playable data for it, so there's "
                        f"nothing to render. {_COVERAGE_HINT}"
                    ),
                    embed=embed,
                )
                return

            if not ffmpeg_available():
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content=(
                        "Rendering chart videos needs `ffmpeg` on the host and this instance doesn't "
                        "have it, so here's the chart's data instead."
                    ),
                    embed=embed,
                )
                return

            if from_measure is not None and to_measure is not None and to_measure <= from_measure:
                bail()
                await interaction.edit_original_response(
                    content="`to_measure` has to be after `from_measure`."
                )
                return

            await self._render(
                interaction, found, song, wanted_difficulty, embed,
                clamp_hi_speed(notespeed), from_measure, to_measure, bail,
                render_mode.value if render_mode else MODE_SIMPLE,
            )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't render that chart: unexpected error ({type(e).__name__}: {e})"
            )

    async def _render(self, interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure, bail,
                      render_mode):
        user_id = interaction.user.id
        owner = access.is_owner(user_id)

        async def refuse(content: str) -> None:
            bail()
            embed.set_footer(text="Chart data from mai-notes.com")
            await interaction.edit_original_response(content=content, embed=embed)

        if _queue.has(user_id):
            await refuse("You already have a chart render queued or running - here's this chart's data meanwhile.")
            return
        retry_at = None if owner else access.cooldown_until(user_id, _RENDER_COOLDOWN_KEY)
        if retry_at is not None:
            await refuse(
                f"You can render another chart <t:{round(retry_at)}:R> - here's this chart's data meanwhile."
            )
            return
        if len(_queue) >= _MAX_QUEUE:
            await refuse("Chart renders are backed up right now - give it a minute and try again.")
            return

        async def on_wait(ahead: int) -> None:
            noun = "render" if ahead == 1 else "renders"
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=f"Queued to render **{song.title}** [{difficulty.display_name}] - "
                            f"{ahead} {noun} ahead of you..."
                )

        rendered = False
        try:
            async with _queue.turn(user_id, on_wait):
                await interaction.edit_original_response(
                    content=f"Rendering **{song.title}** [{difficulty.display_name}]..."
                )
                rendered = await self._render_locked(
                    interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure, bail,
                    render_mode,
                )
        finally:
            # From when the render finishes, so a long queue wait doesn't eat
            # into it. The owner is exempt, as from every cooldown.
            if rendered and not owner:
                access.set_cooldown(user_id, _RENDER_COOLDOWN_KEY, _RENDER_COOLDOWN)

    async def _render_locked(self, interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure,
                             bail, render_mode) -> bool:
        """Returns whether a render was actually attempted (which is what
        the render cooldown charges for)."""
        limit = _upload_limit(interaction)
        progress = _ProgressReporter(interaction, song.title, difficulty)

        with tempfile.TemporaryDirectory(prefix="cc-chart-") as tmp:
            tmp_dir = Path(tmp)
            raw = tmp_dir / "capture.h264"

            chart_text = await fetch_chart_text(chart.id)
            if chart_text is None:
                await progress.stop()
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content="Couldn't download this chart from mai-notes.com. Try again in a bit.",
                    embed=embed,
                )
                return False

            captures = []
            started = time.perf_counter()
            # "game" plays maimai's own tap sound when the skin import
            # brought it along; everything else keeps mai-notes' pair.
            hit_sound = chart_skin.game_hit_sound() if render_mode == MODE_GAME or render_mode == MODE_MISS else None
            try:
                captures = await render_chart(
                    chart_text, raw,
                    ffmpeg=ffmpeg_path(),
                    hi_speed=hi_speed,
                    from_measure=from_measure,
                    to_measure=to_measure,
                    size_budget_bytes=limit,
                    progress=progress.update,
                    render_mode=render_mode,
                )
                await progress.stop()
                plural = f" ({len(captures)} parts)" if len(captures) > 1 else ""
                await interaction.edit_original_response(content=f"Encoding video{plural}...")
                outs = []
                for k, capture in enumerate(captures, start=1):
                    out = tmp_dir / f"chart-{k}.mp4"
                    # Note times come straight from the chart, so no capture
                    # latency correction applies.
                    await encode_capture(capture, out, size_limit=limit, sfx_shift_ms=0, hit_sound=hit_sound)
                    outs.append(out)
                render_seconds = time.perf_counter() - started
            except ChartRenderUnavailable as e:
                await progress.stop()
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content=f"Chart rendering isn't set up on this instance ({e}). Here's the chart's data instead.",
                    embed=embed,
                )
                return False
            except (ChartRenderError, VideoEncodeError, FfmpegUnavailable) as e:
                await progress.stop()
                await interaction.edit_original_response(content=f"The render failed: {e}")
                return True
            finally:
                for capture in captures:
                    capture.video_path.unlink(missing_ok=True)
                raw.unlink(missing_ok=True)

            videos = [out.read_bytes() for out in outs]

        first, last = captures[0], captures[-1]
        footer = [
            f"Note speed {hi_speed:g}",
            _RENDER_MODE_LABELS.get(render_mode, render_mode),
            f"{last.start_seconds + last.duration_seconds - first.start_seconds:.0f}s",
            f"measures {first.start_measure}-{last.end_measure}/{first.total_measures}",
            f"rendered in {render_seconds:.1f}s",
            "chart data from mai-notes.com",
        ]
        embed.set_footer(text=" · ".join(footer))
        lines = []
        if len(captures) > 1:
            lines.append(" // ".join(
                f"{k}: {_part_span(c)}" for k, c in enumerate(captures, start=1)
            ))
        if first.truncated:
            lines.append("This chart ran past the render limit, so the video is cut short.")
        await self._send_videos(interaction, song, difficulty, embed, "\n".join(lines) or None, captures, videos)
        return True

    async def _send_videos(self, interaction, song, difficulty, embed, content, captures, videos):
        def file(k: int) -> discord.File:
            part = k + 1 if len(videos) > 1 else None
            return discord.File(io.BytesIO(videos[k]), filename=_safe_filename(song.title, difficulty, part))

        try:
            await interaction.edit_original_response(
                content=content, embed=embed, attachments=[file(k) for k in range(len(videos))]
            )
        except discord.HTTPException as e:
            # 413: every part fits the upload limit on its own, but not all
            # of them in one message. One message per video instead.
            if e.status != 413 or len(videos) == 1:
                raise
            await interaction.edit_original_response(content=content, embed=embed, attachments=[file(0)])
            for k in range(1, len(videos)):
                await interaction.followup.send(content=f"{k + 1}: {_part_span(captures[k])}", file=file(k))

    @chart.autocomplete("title")
    async def chart_autocomplete(self, interaction: discord.Interaction, current: str):
        from circlechiffon.cogs.songs import SongsCog

        return await SongsCog.song_autocomplete(self, interaction, current)


_BAR_WIDTH = 20


def _progress_bar(fraction: float) -> str:
    """`[########------------] 40%`, wrapped in a code span so Discord renders
    it monospaced - in a proportional font the fill and empty characters are
    different widths and the bar visibly jitters as it advances."""
    fraction = min(1.0, max(0.0, fraction))
    # Floor, not round: rounding fills the last segment at 97.5%, so a
    # capture held at 99% would show a full bar while still running.
    filled = int(fraction * _BAR_WIDTH)
    return f"`[{'#' * filled}{'-' * (_BAR_WIDTH - filled)}]` {fraction * 100:.0f}%"


class _ProgressReporter:
    """Edits the interaction while the capture runs. The capture calls this
    every couple of seconds; edits are throttled well under Discord's rate
    limit and dropped silently if one fails - progress is cosmetic."""

    def __init__(self, interaction: discord.Interaction, title: str, difficulty: Difficulty):
        self._interaction = interaction
        self._title = title
        self._difficulty = difficulty
        self._task: asyncio.Task | None = None
        self._stopped = False

    def update(self, elapsed: float, total: float | None, fraction: float) -> None:
        if self._stopped:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._edit(elapsed, total, fraction))

    async def stop(self) -> None:
        """Must be awaited before the final edit. A progress edit still in
        flight would otherwise land *after* the result and replace the
        finished embed (and its attachment) with a stale 'rendering...'."""
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _edit(self, elapsed: float, total: float | None, fraction: float) -> None:
        if self._stopped:
            return
        header = f"Rendering **{self._title}** [{self._difficulty.display_name}]"
        # Hold just short of full until the capture actually ends - the
        # measure cursor reaches the last measure slightly before the chart
        # finishes, and a completed bar on a running render reads as a hang.
        clock = _format_clock(elapsed)
        if total:
            clock = f"{clock} / {_format_clock(total)}"
        body = f"{_progress_bar(min(fraction, 0.99))} · {clock}"
        try:
            await self._interaction.edit_original_response(content=f"{header}\n{body}")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ChartCog(bot))
