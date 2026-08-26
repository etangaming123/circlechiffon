"""
/cc-chart - render a chart from mai-notes.com as a video.

Everything that knows how mai-notes works lives in
`adapters/mainotes/`; everything that knows about ffmpeg lives in
`renderers/chart_video.py`. This file is the Discord surface: resolve the
user's song to a mai-notes chart, queue the render, and pick which of the
several "can't render that" messages applies.

The render is the heaviest thing this bot does - a Chromium context, a few
thousand canvas draws and an H.264 encode - so **rendering is owner-only**
and serialised behind a semaphore. Everyone else gets the chart's data as an
embed, which costs no more than /cc-info does.
"""

import asyncio
import io
import re
import tempfile
import unicodedata
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, embed_colors
from circlechiffon.adapters.dxrating.images import jacket_url
from circlechiffon.adapters.mainotes.catalog import MaiNotesChart, get_mainotes_catalog
from circlechiffon.adapters.mainotes.player import (
    HI_SPEED_DEFAULT,
    HI_SPEED_MAX,
    HI_SPEED_MIN,
    ChartRenderError,
    ChartRenderUnavailable,
    capture_chart,
    clamp_hi_speed,
    cleanup,
)
from circlechiffon.renderers.chart_video import (
    SIZE_BUDGET,
    FfmpegUnavailable,
    VideoEncodeError,
    encode_capture,
    ffmpeg_available,
)
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import ChartType, Difficulty

# One render at a time. Two concurrent Chromium contexts each doing
# thousands of canvas draws thrash any modest host, and the throughput win
# from running them in parallel is negative.
_RENDER_LOCK = asyncio.Semaphore(1)
_MAX_QUEUE = 3
_queued = 0

_DIFFICULTY_CHOICES = [
    app_commands.Choice(name=d.display_name, value=d.value)
    for d in (Difficulty.basic, Difficulty.advanced, Difficulty.expert, Difficulty.master, Difficulty.remaster)
]
# The first DX/STD command parameter in the bot - every other command shows
# both types side by side rather than asking which one you meant.
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


def _safe_filename(title: str, difficulty: Difficulty) -> str:
    """Song titles include path separators and invisible characters (one
    real mai-notes title is a single U+200E), so a title can't go into a
    filename unfiltered."""
    cleaned = "".join(ch for ch in unicodedata.normalize("NFKC", title) if unicodedata.category(ch) != "Cf")
    slug = re.sub(r"[^\w\-]+", "-", cleaned, flags=re.UNICODE).strip("-")[:60]
    return f"chart-{slug or 'song'}-{difficulty.value}.mp4"


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

    @app_commands.command(
        name="cc-chart",
        description="Look up a chart on mai-notes.com (owner-only: render it as a video)",
    )
    @app_commands.describe(
        title="Song title (or part of it, including English/romanized aliases) to search for",
        difficulty="Which difficulty's chart to render (default: MASTER)",
        chart_type="DX or Standard chart, for songs that have both (default: DX)",
        notespeed="In-player note speed / ハイスピ, 3.0-9.0 (default: 7.5)",
        from_measure="Start at this measure instead of the beginning",
        to_measure="Stop at this measure instead of playing to the end",
    )
    @app_commands.choices(difficulty=_DIFFICULTY_CHOICES, chart_type=_CHART_TYPE_CHOICES)
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
    ):
        user_id = interaction.user.id
        # DEFAULT_COOLDOWN, not a render-sized one: handle_command_access
        # exempts the owner from cooldowns outright, and the owner is now the
        # only person who can trigger a render - so a long tier would only
        # ever throttle the cheap metadata lookup everyone else gets.
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

            # Rendering is owner-only; everyone else still gets the lookup,
            # which is the same local-manifest work /cc-info does.
            if not access.is_owner(user_id):
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
            )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't render that chart: unexpected error ({type(e).__name__}: {e})"
            )

    async def _render(self, interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure, bail):
        global _queued

        if _queued >= _MAX_QUEUE:
            bail()
            await interaction.edit_original_response(
                content="Chart renders are backed up right now - give it a minute and try again."
            )
            return

        if _RENDER_LOCK.locked():
            await interaction.edit_original_response(
                content=f"Queued behind {_queued} other render(s)..."
            )

        _queued += 1
        try:
            async with _RENDER_LOCK:
                await interaction.edit_original_response(
                    content=f"Rendering **{song.title}** [{difficulty.display_name}] on mai-notes.com..."
                )
                await self._render_locked(
                    interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure, bail
                )
        finally:
            _queued -= 1

    async def _render_locked(self, interaction, chart, song, difficulty, embed, hi_speed, from_measure, to_measure, bail):
        limit = _upload_limit(interaction)
        progress = _ProgressReporter(interaction, song.title, difficulty)

        with tempfile.TemporaryDirectory(prefix="cc-chart-") as tmp:
            tmp_dir = Path(tmp)
            raw = tmp_dir / "capture.h264"
            out = tmp_dir / "chart.mp4"
            try:
                bpm = float(chart.song.bpm) if chart.song.bpm else None
            except (TypeError, ValueError):
                bpm = None

            try:
                capture = await capture_chart(
                    chart.id, raw,
                    hi_speed=hi_speed,
                    from_measure=from_measure,
                    to_measure=to_measure,
                    bpm=bpm,
                    size_budget_bytes=limit,
                    progress=progress.update,
                )
                await progress.stop()
                await interaction.edit_original_response(content="Encoding video...")
                await encode_capture(capture, out, size_limit=limit)
            except ChartRenderUnavailable as e:
                await progress.stop()
                bail()
                embed.set_footer(text="Chart data from mai-notes.com")
                await interaction.edit_original_response(
                    content=f"Chart rendering isn't set up on this instance ({e}). Here's the chart's data instead.",
                    embed=embed,
                )
                return
            except (ChartRenderError, VideoEncodeError, FfmpegUnavailable) as e:
                await progress.stop()
                await interaction.edit_original_response(content=f"The render failed: {e}")
                return
            finally:
                cleanup(raw)

            data = out.read_bytes()

        footer = [
            f"Note speed {hi_speed:g}",
            f"{capture.duration_seconds:.0f}s",
            f"measures {capture.start_measure}-{capture.end_measure}/{capture.total_measures}",
            "rendered from mai-notes.com",
        ]
        embed.set_footer(text=" · ".join(footer))
        note = ""
        if capture.truncated:
            note = "This chart ran past the render limit, so the video is cut short.\n"

        await interaction.edit_original_response(
            content=note or None,
            embed=embed,
            attachments=[discord.File(io.BytesIO(data), filename=_safe_filename(song.title, difficulty))],
        )

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

    def update(self, seconds: float, expected: float | None) -> None:
        if self._stopped:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._edit(seconds, expected))

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

    async def _edit(self, seconds: float, expected: float | None) -> None:
        if self._stopped:
            return
        header = f"Rendering **{self._title}** [{self._difficulty.display_name}]"
        if expected:
            # The estimate comes from measure count and BPM, so a chart with
            # tempo changes runs past it. Hold at 99% rather than showing a
            # bar that claims to be finished while it plainly isn't.
            body = _progress_bar(min(seconds / expected, 0.99))
        else:
            body = f"{seconds:.0f}s captured"
        try:
            await self._interaction.edit_original_response(content=f"{header}\n{body}")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ChartCog(bot))
