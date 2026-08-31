from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, badge_emojis, embed_colors
from circlechiffon.adapters.dxrating.images import jacket_url
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.calculator import rank_tag_for_achievement
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import ChartType, Difficulty, FriendEntry, Score, Song, SongPlayStats

_DIFFICULTY_ORDER = [Difficulty.basic, Difficulty.advanced, Difficulty.expert, Difficulty.master, Difficulty.remaster]

# Same enum, same two choices, same default-DX-else-available shape as
# cogs/chart.py's _CHART_TYPE_CHOICES/wanted_type - the established pattern
# for a DX/STD command parameter in this codebase.
_CHART_TYPE_CHOICES = [
    app_commands.Choice(name="DX", value=ChartType.dx.value),
    app_commands.Choice(name="Standard", value=ChartType.std.value),
]

# Friend score pages carry far less than your own record pages do - see
# parse_friend_scores: achievement, combo and sync flags only, with no raw DX
# score, no rating, and no idx to reach a musicDetail page for play counts.
# _score_embed already omits each of those when absent, so this footer is the
# only thing that has to explain the gap.
_FRIEND_DATA_NOTE = (
    "Friend scores show achievement and combo/sync only - SEGA doesn't expose "
    "DX score, rating, or play counts for friends."
)


class NoScoresRecorded(Exception):
    """Raised by build_score_view() when the song has standard-difficulty
    charts but the player has no recorded score on any of them."""


def available_difficulties(song: Song, chart_type: ChartType) -> list[Difficulty]:
    # DX and STD sheets under one title don't always share the same
    # difficulty set (confirmed live: 11/81 dual-type songs differ, e.g.
    # POP TEAM EPIC has no DX Re:MASTER but does have a STD one) - filtering
    # by chart_type here, not just by score afterward, keeps the toggle from
    # offering a difficulty the resolved chart type doesn't actually have.
    present = {s.difficulty for s in song.sheets if s.difficulty is not None and s.type == chart_type}
    return [d for d in _DIFFICULTY_ORDER if d in present]


def _matching_scores(scores: list[Score], title: str, difficulty: Difficulty, chart_type: ChartType) -> list[Score]:
    return [s for s in scores if s.title == title and s.difficulty == difficulty and s.chart_type == chart_type]


def _score_embed(
    song: Song,
    difficulty: Difficulty,
    chart_type: ChartType,
    scores: list[Score],
    play_stats: dict[Difficulty, SongPlayStats],
    player_name: str,
    footer_note: str | None = None,
) -> discord.Embed:
    matches = _matching_scores(scores, song.title, difficulty, chart_type)
    if matches:
        best = max(matches, key=lambda s: s.achievement)
        color = embed_colors.rank_color(rank_tag_for_achievement(best.achievement))
    else:
        color = embed_colors.difficulty_color(difficulty)

    embed = discord.Embed(title=f"{song.title} [{difficulty.display_name}]", color=color)
    # whose scores these are, rendered on the small author line above the
    # title so the song stays the heading. Plain text, so every </> rebuild
    # (each of which builds a fresh embed) keeps it.
    embed.set_author(name=f"{player_name}'s scores")
    # dxrating's jacket CDN is a public, cacheable asset host - linked
    # directly rather than fetched+attached, so it survives every </> toggle
    # (each rebuilds a fresh embed) without needing to re-attach a file.
    if song.image_name:
        embed.set_thumbnail(url=jacket_url(song.image_name))
    if footer_note:
        embed.set_footer(text=footer_note)
    if not matches:
        embed.description = "No play recorded on this difficulty yet."
        return embed
    for score in matches:
        type_name = score.chart_type.value.upper() if score.chart_type else "?"
        rank_tag = rank_tag_for_achievement(score.achievement)
        achievement_text = "No Chart" if score.achievement == 0 else f"{score.achievement:.4f}%"
        value_lines = [f"{badge_emojis.rank_badge(rank_tag)} {achievement_text}"]
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
        chart_type: ChartType,
        scores: list[Score],
        difficulties: list[Difficulty],
        play_stats: dict[Difficulty, SongPlayStats],
        player_name: str,
        footer_note: str | None = None,
    ):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.song = song
        self.chart_type = chart_type
        self.scores = scores
        self.difficulties = difficulties
        self.play_stats = play_stats
        self.player_name = player_name
        self.footer_note = footer_note
        self.index = len(difficulties) - 1  # default: highest available difficulty
        self.message: discord.Message | discord.InteractionMessage | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.difficulties) - 1

    def embed(self) -> discord.Embed:
        return _score_embed(
            self.song,
            self.difficulties[self.index],
            self.chart_type,
            self.scores,
            self.play_stats,
            self.player_name,
            self.footer_note,
        )

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
    *,
    chart_type: ChartType | None = None,
    friend: FriendEntry | None = None,
    on_retry: Callable[[], Awaitable[None]] | None = None,
    on_progress: Callable[[str], Awaitable[None]] | None = None,
) -> ScoreToggleView | None:
    """Fetches scores for one song and builds a ScoreToggleView defaulted to
    the song's highest available difficulty. `chart_type` picks DX or STD;
    None resolves to DX, falling back to STD if the song has no DX chart (or
    to whichever type was actually requested, if that specific type turns
    out not to exist for this song). With `friend` given, the scores are
    that friend's rather than the invoker's - the invoker's own session is
    still what fetches them, so the account/session errors below are the
    same either way. Returns None if the song has no standard-difficulty
    charts at all. Propagates the usual account/session errors
    (NotLinked/SessionExpired/MaimaiNetError) for the caller to handle in
    whatever way fits its own command's UI."""
    types_present = {s.type for s in song.sheets if s.type in (ChartType.dx, ChartType.std)}
    if not types_present:
        return None

    expected_type = chart_type or ChartType.dx
    resolved_type = chart_type if chart_type in types_present else (
        ChartType.dx if ChartType.dx in types_present else ChartType.std
    )
    fallback_note = None
    if resolved_type != expected_type:
        fallback_note = (
            f"No {expected_type.value.upper()} chart for this song - showing "
            f"{resolved_type.value.upper()} instead."
        )

    difficulties = available_difficulties(song, resolved_type)
    if not difficulties:
        return None

    async def fetch(client):
        if friend is not None:
            # No idx on a friend's score rows, so musicDetail (and with it
            # play counts) is unreachable for anyone but yourself.
            scores = await client.get_friend_scores(friend.idx, on_progress=on_progress)
            return scores, {}, friend.profile.display_name

        scores = await client.get_music_scores()
        # any one of this song's score rows of the resolved chart type
        # carries an idx that reaches the same musicDetail page - it covers
        # every difficulty of that same chart type at once (DX and STD are
        # separate idx values/pages with independent data, so this is
        # already scoped to the one type this view is showing).
        idx = next(
            (s.idx for s in scores if s.title == song.title and s.idx and s.chart_type == resolved_type),
            None,
        )
        play_stats = await client.get_song_play_stats(idx) if idx is not None else {}
        # normally free - login records the name - but an account whose login
        # couldn't read its profile has none stored, so recover it once here.
        name = await accounts.get_display_name(invoker_id)
        if not name:
            name = (await client.get_profile()).display_name
            await accounts.set_display_name(invoker_id, name)
        return scores, play_stats, name

    scores, play_stats, player_name = await accounts.with_client(invoker_id, fetch, on_retry=on_retry)

    scored_difficulties = [d for d in difficulties if _matching_scores(scores, song.title, d, resolved_type)]
    if not scored_difficulties:
        raise NoScoresRecorded()

    return ScoreToggleView(
        invoker_id,
        song,
        resolved_type,
        scores,
        scored_difficulties,
        play_stats,
        player_name,
        _FRIEND_DATA_NOTE if friend is not None else fallback_note,
    )


class ScoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-scores", description="View your (or a friend's) score on a song, by difficulty")
    @app_commands.describe(
        title="Song title (or part of it, including aliases) to search for",
        friend="Friend's display name (or their exact id from /cc-friends show_ids:True). Omit for your own scores.",
        chart="DX or Standard chart, for songs that have both (default: DX)",
    )
    @app_commands.choices(chart=_CHART_TYPE_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def score(
        self,
        interaction: discord.Interaction,
        title: str,
        friend: str | None = None,
        chart: app_commands.Choice[str] | None = None,
    ):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-scores", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")
        chart_type = ChartType(chart.value) if chart else None

        try:
            catalog = get_catalog()
            results = catalog.search(title, limit=1)
            if not results:
                await interaction.edit_original_response(content=f"No songs found matching **{title}**.")
                return
            song = results[0]

            if friend is None:
                await self._send_score_view(interaction, song, chart_type=chart_type)
                return

            # /cc-friend-profile's exact resolution flow, reused wholesale:
            # normalized substring match, difflib fallback, and a dropdown
            # when more than one friend matches. Imported lazily to keep the
            # cogs free of module-load-time import cycles, same as the
            # SongsCog autocomplete reuse below.
            from circlechiffon.cogs.friends import FriendPickView, _pick_prompt, _resolve_friend_entry

            async def resolve(client):
                return await _resolve_friend_entry(client, friend)

            resolved = await accounts.with_client(
                interaction.user.id, resolve, on_retry=accounts.default_retry_notice(interaction)
            )
            if resolved is None:
                await interaction.edit_original_response(content=f"No friend found matching `{friend}`.")
                return
            if isinstance(resolved, list):
                async def on_pick(component_interaction: discord.Interaction, entry: FriendEntry):
                    await self._send_score_view(component_interaction, song, friend=entry, chart_type=chart_type)

                view = FriendPickView(interaction.user.id, resolved, on_pick)
                await interaction.edit_original_response(content=_pick_prompt(resolved), view=view)
                view.message = await interaction.original_response()
                return

            await self._send_score_view(interaction, song, friend=resolved, chart_type=chart_type)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch those scores: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't look up that song: unexpected error ({type(e).__name__}: {e})"
            )

    async def _send_score_view(
        self,
        interaction: discord.Interaction,
        song: Song,
        friend: FriendEntry | None = None,
        chart_type: ChartType | None = None,
    ):
        """Fetches the scores and edits `interaction`'s response with the
        toggle view. Works from either the original slash-command interaction
        (already deferred) or a FriendPickView select callback (already
        responded to via edit_message) - hence `view=None` on every edit, so
        a dropdown that led here is cleared rather than left hanging."""
        whose = "your" if friend is None else f"**{friend.profile.display_name}**'s"

        # a friend's scores are five separate pages, one per difficulty, so
        # report them the way /cc-friend-best does rather than sitting on
        # "Getting data..." for the whole fan-out.
        on_progress = None
        if friend is not None:
            display_order = ["Re:MASTER", "MASTER", "EXPERT", "ADVANCED", "BASIC"]
            done: set[str] = set()

            async def report_progress(diff_label: str) -> None:
                done.add(diff_label)
                lines = [
                    f"Fetching {label} Charts... ✅" if label in done else f"Fetching {label} Charts..."
                    for label in display_order
                ]
                await interaction.edit_original_response(content="\n".join(lines), view=None)

            on_progress = report_progress

        try:
            view = await build_score_view(
                interaction.user.id,
                song,
                chart_type=chart_type,
                friend=friend,
                on_retry=accounts.default_retry_notice(interaction),
                on_progress=on_progress,
            )
            if view is None:
                await interaction.edit_original_response(
                    content=f"**{song.title}** has no standard-difficulty charts to show.", view=None
                )
                return

            message = await interaction.edit_original_response(content=None, embed=view.embed(), view=view)
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first.", view=None
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e), view=None)
        except NoScoresRecorded:
            subject = "You don't" if friend is None else f"**{friend.profile.display_name}** doesn't"
            await interaction.edit_original_response(
                content=f"{subject} have any scores for that chart...", view=None
            )
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch {whose} scores: {e}", view=None)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch {whose} scores: unexpected error ({type(e).__name__}: {e})", view=None
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
