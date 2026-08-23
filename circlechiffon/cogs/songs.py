import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, embed_colors
from circlechiffon.adapters.dxrating.images import jacket_url
from circlechiffon.adapters.dxrating.tags import get_tags_client
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.calculator import calculate_rating
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import ComboFlag, Difficulty, Song

_DIFFICULTY_CHOICES = [
    app_commands.Choice(name="BASIC", value="basic"),
    app_commands.Choice(name="ADVANCED", value="advanced"),
    app_commands.Choice(name="EXPERT", value="expert"),
    app_commands.Choice(name="MASTER", value="master"),
    app_commands.Choice(name="Re:MASTER", value="remaster"),
]


def _autocomplete_label(song: Song, query_lower: str) -> str:
    """Label a song choice with whichever alias matched the query, so a
    user searching an English/romanized name can see why a Japanese-titled
    song showed up (e.g. "夜に駆ける (YOASOBI)")."""
    if not query_lower or query_lower in song.title.lower():
        return song.title
    for alias in song.search_acronyms:
        if query_lower in alias.lower():
            return f"{song.title} ({alias})"[:100]
    return song.title


def _build_generic_embed(song: Song) -> discord.Embed:
    lines = []
    for sheet in song.sheets:
        diff_name = sheet.difficulty.display_name if sheet.difficulty else "UTAGE"
        type_name = sheet.type.value.upper() if sheet.type else "?"
        const_str = f" (constant {sheet.internal_level_value})" if sheet.internal_level_value else ""
        version_str = (
            f" - Version: {sheet.version}" + (f" ({sheet.release_date})" if sheet.release_date else "")
            if sheet.version
            else ""
        )
        lines.append(f"**{diff_name}** [{type_name}] - {sheet.level}{const_str}{version_str}")

    embed = discord.Embed(title=song.title, description=song.artist or "", color=embed_colors.GENERIC)
    embed.add_field(name="Charts", value="\n".join(lines) or "No chart data", inline=False)
    if song.search_acronyms:
        embed.set_footer(text=f"Aliases: {', '.join(song.search_acronyms)}")
    return embed


async def _build_detailed_embed(song: Song, difficulty: Difficulty) -> discord.Embed:
    matching_sheets = [s for s in song.sheets if s.difficulty == difficulty]

    # All difficulties of a chart are assumed to have been added in the same
    # update, so show the version once for the embed rather than per-sheet.
    version_sheet = next((s for s in matching_sheets if s.version), None)
    description_lines = [song.artist] if song.artist else []
    if version_sheet:
        version_line = f"Version: {version_sheet.version}"
        if version_sheet.release_date:
            version_line += f" ({version_sheet.release_date})"
        description_lines.append(version_line)

    embed = discord.Embed(
        title=f"{song.title} [{difficulty.display_name}]",
        description="\n".join(description_lines),
        color=embed_colors.difficulty_color(difficulty),
    )

    if not matching_sheets:
        embed.add_field(name="Chart", value="This song has no chart at that difficulty.", inline=False)
        return embed

    tags_client = get_tags_client()

    for sheet in matching_sheets:
        type_name = sheet.type.value.upper() if sheet.type else "?"
        lines = [f"Level **{sheet.level}** (constant {sheet.internal_level_value})"]
        if sheet.note_designer and sheet.note_designer.strip("-").strip():
            # dxdata.json uses a literal "-" placeholder for uncredited charts
            lines.append(f"Charter: {sheet.note_designer}")
        if sheet.note_counts is not None:
            nc = sheet.note_counts
            counts = ", ".join(
                f"{label} {value}"
                for label, value in [
                    ("Tap", nc.tap), ("Hold", nc.hold), ("Slide", nc.slide),
                    ("Touch", nc.touch), ("Break", nc.brk),
                ]
                if value is not None
            )
            if counts:
                lines.append(f"Notes: {counts}" + (f" (total {nc.total})" if nc.total is not None else ""))

        tags: list[str] = []
        if sheet.type is not None:
            try:
                found = await tags_client.find_tags(song.song_id, sheet.type.value, difficulty.value)
                tags = [t.name for t in found]
            except Exception:
                pass  # tags are enrichment only - never fail chart info over them
        lines.append(f"Tags: {', '.join(tags) if tags else 'none listed on dxrating.net'}")

        embed.add_field(name=f"{type_name} chart", value="\n".join(lines), inline=False)

    embed.set_footer(text="Tags provided by dxrating.net")
    return embed


class CheckScoreView(discord.ui.View):
    """Button on /cc-info's response that anyone viewing the message can
    click - not just the original invoker, since the song lookup itself
    isn't account-specific. Each click posts a *new* message with the
    clicking user's own score (the same </> difficulty-toggle view
    /cc-scores uses), rather than overwriting the shared song-info embed -
    that embed needs to stay intact for other people to use the same
    button independently."""

    def __init__(self, song: Song):
        super().__init__(timeout=30)
        self.song = song
        self.message: discord.Message | discord.InteractionMessage | None = None

    @discord.ui.button(label="Check my score", style=discord.ButtonStyle.primary, emoji="🎯")
    async def check_score(self, interaction: discord.Interaction, button: discord.ui.Button):
        # imported lazily to avoid a module-load-time circular import
        # (cogs.score imports SongsCog from this module for its own
        # autocomplete reuse).
        from circlechiffon.cogs.score import NoScoresRecorded, build_score_view

        await interaction.response.send_message("Getting data...")
        invoker_id = interaction.user.id
        try:
            view = await build_score_view(
                invoker_id, self.song, on_retry=accounts.default_retry_notice(interaction)
            )
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
            return
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
            return
        except NoScoresRecorded:
            await interaction.edit_original_response(content="You don't have any scores for that chart...")
            return
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your scores: {e}")
            return
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch your scores: unexpected error ({type(e).__name__}: {e})"
            )
            return

        if view is None:
            await interaction.edit_original_response(
                content=f"**{self.song.title}** has no standard-difficulty charts to show."
            )
            return

        message = await interaction.edit_original_response(content=None, embed=view.embed(), view=view)
        view.message = message

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SongsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-info", description="Look up a maimai DX song and its chart levels")
    @app_commands.describe(
        title="Song title (or part of it, including English/romanized aliases) to search for",
        difficulty="Show full detail (version, charter, note counts, dxrating.net tags) for this difficulty",
    )
    @app_commands.choices(difficulty=_DIFFICULTY_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def song(
        self,
        interaction: discord.Interaction,
        title: str,
        difficulty: app_commands.Choice[str] | None = None,
    ):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-info", access.DEFAULT_COOLDOWN):
            return
        await interaction.response.defer()

        try:
            catalog = get_catalog()
            results = catalog.search(title, limit=5)
            if not results:
                await interaction.edit_original_response(content=f"No songs found matching **{title}**.")
                return

            song = results[0]

            if difficulty is None:
                embed = _build_generic_embed(song)
            else:
                embed = await _build_detailed_embed(song, Difficulty(difficulty.value))

            # dxrating's jacket CDN is a public, cacheable asset host (unlike
            # maimaidx-eng.com's per-account images) - link it directly
            # rather than fetching+attaching bytes ourselves.
            if song.image_name:
                embed.set_thumbnail(url=jacket_url(song.image_name))

            view = CheckScoreView(song)
            view.message = await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't look up that song: unexpected error ({type(e).__name__}: {e})"
            )

    @song.autocomplete("title")
    async def song_autocomplete(self, interaction: discord.Interaction, current: str):
        # discord.py swallows any exception raised here (it can't surface an
        # error through an autocomplete response) and only logs it internally,
        # so without this try/except a single bad entry silently breaks
        # autocomplete with no visible symptom beyond Discord's UI sitting on
        # "loading" forever. Also: Choice.name and Choice.value must each be
        # 1-100 characters - a single song in the catalog has a 149-character
        # title, and Discord rejects the *entire* choices list if any one
        # entry violates this, so anything over the limit must be dropped
        # rather than just truncated (a truncated value wouldn't round-trip
        # back to the right song anyway).
        try:
            catalog = get_catalog()
            results = catalog.search(current, limit=25) if current else []
            query_lower = current.lower().strip()
            choices = []
            for s in results:
                if not s.title or len(s.title) > 100:
                    continue
                name = _autocomplete_label(s, query_lower)
                if not name or len(name) > 100:
                    name = s.title[:100]
                choices.append(app_commands.Choice(name=name, value=s.title))
            return choices
        except Exception as e:
            print(f"cc-info autocomplete failed for query {current!r}: {type(e).__name__}: {e}")
            return []

    @app_commands.command(name="cc-rating", description="Calculate the rating points a score would earn")
    @app_commands.describe(
        constant="Chart constant (internal level), e.g. 13.9",
        achievement="Achievement percentage, e.g. 100.5",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def rating(
        self,
        interaction: discord.Interaction,
        constant: float,
        achievement: float
    ):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-rating", access.DEFAULT_COOLDOWN):
            return
        await interaction.response.defer()

        try:
            if constant < 1 or constant > 15:
                await interaction.edit_original_response(content="Constant must be between 1 and 15.")
                return
            if achievement < 0 or achievement > 101:
                await interaction.edit_original_response(content="Achievement must be between 0 and 101.")
                return
            result = calculate_rating(constant, achievement)

            await interaction.edit_original_response(
                content=(
                    f"A **{achievement:.4f}%** achievement for a level **{constant}** chart is a **{result.rating}** rating play!\nThis will be {result.rating + 1} rating if you get an **All Perfect** or **All Perfect+**!"
                )
            )
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't calculate rating: unexpected error ({type(e).__name__}: {e})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SongsCog(bot))
