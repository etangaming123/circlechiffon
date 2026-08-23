from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, badge_emojis, embed_colors
from circlechiffon.adapters.dxrating.images import jacket_url
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.calculator import rank_tag_for_achievement
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import Difficulty, Score, Song, SongPlayStats

_DIFFICULTY_ORDER = [Difficulty.basic, Difficulty.advanced, Difficulty.expert, Difficulty.master, Difficulty.remaster]


def available_difficulties(song: Song) -> list[Difficulty]:
    present = {s.difficulty for s in song.sheets if s.difficulty is not None}
    return [d for d in _DIFFICULTY_ORDER if d in present]


def _matching_scores(scores: list[Score], title: str, difficulty: Difficulty) -> list[Score]:
    return [s for s in scores if s.title == title and s.difficulty == difficulty]


def _score_embed(
    song: Song, difficulty: Difficulty, scores: list[Score], play_stats: dict[Difficulty, SongPlayStats]
) -> discord.Embed:
    matches = _matching_scores(scores, song.title, difficulty)
    if matches:
        best = max(matches, key=lambda s: s.achievement)
        color = embed_colors.rank_color(rank_tag_for_achievement(best.achievement))
    else:
        color = embed_colors.difficulty_color(difficulty)

    embed = discord.Embed(title=f"{song.title} [{difficulty.display_name}]", color=color)
    # dxrating's jacket CDN is a public, cacheable asset host - linked
    # directly rather than fetched+attached, so it survives every </> toggle
    # (each rebuilds a fresh embed) without needing to re-attach a file.
    if song.image_name:
        embed.set_thumbnail(url=jacket_url(song.image_name))
    if not matches:
        embed.description = "No play recorded on this difficulty yet."
        return embed
    for score in matches:
        type_name = score.chart_type.value.upper() if score.chart_type else "?"
        rank_tag = rank_tag_for_achievement(score.achievement)
        value_lines = [f"{badge_emojis.rank_badge(rank_tag)} {score.achievement:.4f}%"]
        if score.dx_score is not None and score.dx_score_total is not None:
            value_lines.append(f"DX Score: {score.dx_score:,} / {score.dx_score_total:,}")
        value_lines.append(
            f"Combo: {badge_emojis.combo_badge(score.combo_flag)}  Sync: {badge_emojis.sync_badge(score.sync_flag)}"
        )
        if score.rating is not None:
            value_lines.append(f"Rating: {score.rating}")
        stats = play_stats.get(difficulty)
        if stats is not None and stats.play_count is not None:
            value_lines.append(f"Play count: {stats.play_count:,}")
        if stats is not None and stats.last_played is not None:
            value_lines.append(f"Last played: {stats.last_played.strftime('%d/%m/%Y %H:%M')}")
        embed.add_field(name=f"{type_name} chart", value="\n".join(value_lines), inline=False)
    return embed


class ScoreToggleView(discord.ui.View):
    """Toggles between a song's available difficulties via < / > buttons,
    starting on the highest one the song has a chart for. Scores are
    fetched once up front (not per click) - the buttons just re-render from
    the already-fetched list, and every response here is public (not
    ephemeral), unlike the account-gated commands' error messages."""

    def __init__(
        self,
        invoker_id: int,
        song: Song,
        scores: list[Score],
        difficulties: list[Difficulty],
        play_stats: dict[Difficulty, SongPlayStats],
    ):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.song = song
        self.scores = scores
        self.difficulties = difficulties
        self.play_stats = play_stats
        self.index = len(difficulties) - 1  # default: highest available difficulty
        self.message: discord.Message | discord.InteractionMessage | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.difficulties) - 1

    def embed(self) -> discord.Embed:
        return _score_embed(self.song, self.difficulties[self.index], self.scores, self.play_stats)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def build_score_view(
    invoker_id: int,
    song: Song,
    on_retry: Callable[[], Awaitable[None]] | None = None,
) -> ScoreToggleView | None:
    """Fetches the invoker's scores and builds a ScoreToggleView defaulted
    to the song's highest available difficulty. Returns None if the song
    has no standard-difficulty charts. Propagates the usual account/session
    errors (NotLinked/SessionExpired/MaimaiNetError) for the caller to
    handle in whatever way fits its own command's UI."""
    difficulties = available_difficulties(song)
    if not difficulties:
        return None

    async def fetch(client):
        scores = await client.get_music_scores()
        # any one of this song's score rows carries an idx that reaches the
        # same musicDetail page - it covers every difficulty at once, so one
        # extra fetch here is enough regardless of which difficulty is shown.
        idx = next((s.idx for s in scores if s.title == song.title and s.idx), None)
        play_stats = await client.get_song_play_stats(idx) if idx is not None else {}
        return scores, play_stats

    scores, play_stats = await accounts.with_client(invoker_id, fetch, on_retry=on_retry)
    return ScoreToggleView(invoker_id, song, scores, difficulties, play_stats)


class ScoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-scores", description="View your score on a song, by difficulty")
    @app_commands.describe(title="Song title (or part of it, including aliases) to search for")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def score(self, interaction: discord.Interaction, title: str):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-scores", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        try:
            catalog = get_catalog()
            results = catalog.search(title, limit=1)
            if not results:
                await interaction.edit_original_response(content=f"No songs found matching **{title}**.")
                return
            song = results[0]

            view = await build_score_view(
                interaction.user.id, song, on_retry=accounts.default_retry_notice(interaction)
            )
            if view is None:
                await interaction.edit_original_response(
                    content=f"**{song.title}** has no standard-difficulty charts to show."
                )
                return

            message = await interaction.edit_original_response(content=None, embed=view.embed(), view=view)
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your scores: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't look up that song: unexpected error ({type(e).__name__}: {e})"
            )

    @score.autocomplete("title")
    async def score_autocomplete(self, interaction: discord.Interaction, current: str):
        from circlechiffon.cogs.songs import SongsCog

        # reuse /cc-info's exact autocomplete callback rather than
        # duplicating its search/label logic - it doesn't touch `self` for
        # anything, so passing this cog's own instance through is harmless.
        return await SongsCog.song_autocomplete(self, interaction, current)


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoreCog(bot))
