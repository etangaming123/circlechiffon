import asyncio
import io
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, badge_emojis, embed_colors
from circlechiffon.adapters.dxrating.images import get_jackets_bulk
from circlechiffon.adapters.maimai_net.badge_icons import get_all_badge_icons
from circlechiffon.adapters.maimai_site.version_logo import get_version_logo
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.best50 import calculate_best50
from circlechiffon.ratingcalc.calculator import calculate_rating, rank_tag_for_achievement
from circlechiffon.renderers.b50 import render_b50
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import Judgements, RecentScore


def _group_by_credit(scores: list[RecentScore]) -> list[list[RecentScore]]:
    """maimai DX NET numbers each play TRACK 01, TRACK 02, ... starting over
    at 1 for every new credit. Whether the scraped list presents a credit's
    tracks ascending (1,2,3,4) or descending (4,3,2,1) isn't confirmed, so
    rather than special-case track_no == 1 (which only groups correctly for
    one of those two orderings), a new group starts wherever the track_no
    sequence breaks continuity - i.e. doesn't simply step by 1 from the
    previous entry. This works the same regardless of which direction the
    list turns out to run. Scores with no track_no (parse failure) always
    start a new group, since continuity can't be determined for them."""
    groups: list[list[RecentScore]] = []
    current: list[RecentScore] = []
    prev_track_no: int | None = None
    for score in scores:
        if current and (score.track_no is None or prev_track_no is None or abs(score.track_no - prev_track_no) != 1):
            groups.append(current)
            current = []
        current.append(score)
        prev_track_no = score.track_no
    if current:
        groups.append(current)
    return groups


def _fmt(value: int | None) -> str:
    return f"{value:,}" if value is not None else "-"


def _format_judgement_table(judgements: Judgements) -> str:
    """Fixed-width table matching maimai DX NET's own detail page layout:
    one row per note type, one column per judgment tier."""
    rows = [
        ("TAP", judgements.tap),
        ("HOLD", judgements.hold),
        ("SLIDE", judgements.slide),
        ("TOUCH", judgements.touch),
        ("BREAK", judgements.brk),
    ]
    lines = [f"{'':<7}{'CritP':>6}{'Perf':>6}{'Great':>6}{'Good':>6}{'Miss':>6}"]
    for label, nt in rows:
        if nt is None:
            continue
        lines.append(
            f"{label + ':':<7}{_fmt(nt.critical_perfect):>6}{_fmt(nt.perfect):>6}"
            f"{_fmt(nt.great):>6}{_fmt(nt.good):>6}{_fmt(nt.miss):>6}"
        )
    return "\n".join(lines)


def _build_track_embed(
    score: RecentScore,
    jacket_index: int,
    has_jacket: bool,
    judgements: Judgements | None,
    show_detail: bool,
) -> discord.Embed:
    catalog = get_catalog()
    sheet = catalog.find_sheet(score.title, score.chart_type, score.difficulty)
    rating = None
    if sheet is not None and sheet.internal_level_value is not None:
        rating = calculate_rating(sheet.internal_level_value, score.achievement, score.combo_flag).rating

    diff_name = score.difficulty.display_name if score.difficulty else "?"
    rank_tag = rank_tag_for_achievement(score.achievement)

    embed = discord.Embed(title=score.title, color=embed_colors.rank_color(rank_tag))
    if score.track_no is not None:
        embed.set_author(name=f"TRACK {score.track_no:02d}")

    subtitle = f"{diff_name} {sheet.level}" if sheet is not None and sheet.level else diff_name
    if sheet is not None and sheet.internal_level_value is not None:
        subtitle += f" ({sheet.internal_level_value})"
    embed.description = subtitle

    # Judgement-count/fast-late detail only renders when this track was
    # explicitly picked from the dropdown (show_detail=True) - see
    # RecentScoresView. In the grouped multi-embed listing every track's
    # embed is built with show_detail=False regardless of whether its
    # detail was successfully fetched, so the "unavailable" line below only
    # ever appears for the one selected track.
    lines = []
    if show_detail:
        if judgements is not None:
            lines.append(f"```\n{_format_judgement_table(judgements)}\n```")
            if judgements.fast is not None or judgements.late is not None:
                lines.append(f"FAST/LATE: **{_fmt(judgements.fast)}/{_fmt(judgements.late)}**")
        else:
            lines.append("*Play detail unavailable for this track.*")

    dx_part = (
        f"{score.dx_score:,} / {score.dx_score_total:,}"
        if score.dx_score is not None and score.dx_score_total is not None
        else "-"
    )
    achievement_text = "No Chart" if score.achievement == 0 else f"{score.achievement:.4f}%"
    lines.append(f"RANK: {badge_emojis.rank_badge(rank_tag)} - ACC: **{achievement_text}**")
    lines.append(f"DXSCORE: **{dx_part}** - RATING: **{rating if rating is not None else '-'}**")
    lines.append(f"Combo: {badge_emojis.combo_badge(score.combo_flag)}  Sync: {badge_emojis.sync_badge(score.sync_flag)}")

    embed.add_field(name="Details:", value="\n".join(lines), inline=False)

    if score.played_at is not None:
        embed.set_footer(text=score.played_at.strftime("%d/%m/%Y %H:%M"))
    if has_jacket:
        embed.set_thumbnail(url=f"attachment://jacket_{jacket_index}.jpg")
    return embed


class _TrackSelect(discord.ui.Select):
    """Options are rebuilt from the parent view's current page every time
    that page changes (see refresh_options) - Discord select options are
    static once sent, they're not derived at render time like the embeds
    are, so this has to be done explicitly on every Prev/Next/Back click."""

    def __init__(self, owner: "RecentScoresView"):
        super().__init__(placeholder="View detailed stats for a track...", min_values=1, max_values=1, row=1)
        self._owner = owner
        self.refresh_options()

    def refresh_options(self):
        page = self._owner.pages[self._owner.index]
        options = []
        for i, (score, _embed, _jacket) in enumerate(page):
            track_label = f"TRACK {score.track_no:02d}" if score.track_no is not None else "TRACK ??"
            diff_name = score.difficulty.display_name if score.difficulty else "?"
            options.append(
                discord.SelectOption(label=f"{track_label} - {score.title}"[:100], description=diff_name, value=str(i))
            )
        self.options = options

    async def callback(self, interaction: discord.Interaction):
        await self._owner.on_track_selected(interaction, int(self.values[0]))


class RecentScoresView(discord.ui.View):
    """Pages between *credits* - one message edit per Prev/Next click, each
    showing every track played in that credit as its own embed (Discord
    allows multiple embeds per message via the `embeds=` param). Those
    grouped embeds never show judgment-count/fast-late detail, even though
    it was fetched eagerly up front for every track (see
    MaimaiNetClient.get_recent_score_detail) - that detail only renders
    when a track is explicitly picked from the dropdown below the buttons,
    which swaps the message to that single track's embed alone.

    `pages` is a list of credits; each credit is a list of
    (RecentScore, Embed, jacket_bytes) tuples, where Embed was built with
    show_detail=False. Jacket bytes (not discord.File objects) are stored
    and turned into fresh discord.File instances on every render, since a
    File's stream is exhausted once sent and can't be resent (e.g. paging
    back to an already-visited credit, or back out of a detail view)."""

    def __init__(
        self,
        invoker_id: int,
        pages: list[list[tuple[RecentScore, discord.Embed, bytes | None]]],
        details_by_idx: dict[str, Judgements],
    ):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.pages = pages
        self.details_by_idx = details_by_idx
        self.index = 0
        self.viewing_detail = False
        self.message: discord.InteractionMessage | None = None
        self.select = _TrackSelect(self)
        self.add_item(self.select)
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.pages) - 1
        self.back.disabled = not self.viewing_detail

    def current_embeds_and_files(self) -> tuple[list[discord.Embed], list[discord.File]]:
        embeds, files = [], []
        for i, (_score, embed, jacket) in enumerate(self.pages[self.index]):
            embeds.append(embed)
            if jacket:
                files.append(discord.File(io.BytesIO(jacket), filename=f"jacket_{i}.jpg"))
        return embeds, files

    async def _render_page(self, interaction: discord.Interaction):
        self.viewing_detail = False
        self.select.refresh_options()
        self._update_buttons()
        embeds, files = self.current_embeds_and_files()
        await interaction.response.edit_message(embeds=embeds, view=self, attachments=files)

    async def on_track_selected(self, interaction: discord.Interaction, page_local_index: int):
        score, _embed, jacket = self.pages[self.index][page_local_index]
        judgements = self.details_by_idx.get(score.idx) if score.idx else None
        embed = _build_track_embed(score, 0, jacket is not None, judgements, show_detail=True)
        files = [discord.File(io.BytesIO(jacket), filename="jacket_0.jpg")] if jacket else []
        self.viewing_detail = True
        self._update_buttons()
        await interaction.response.edit_message(embeds=[embed], view=self, attachments=files)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        await self._render_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        await self._render_page(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._render_page(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class RecordsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-recent", description="View your recent maimai DX plays, grouped by credit")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def recent(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-recent", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            scores = await client.get_recent_scores()

            # eagerly fetch every track's judgment-count/DX-score detail up
            # front (rather than on-demand per click) so it can be shown
            # inline in each track's embed - bounded only by the client's
            # own internal rate limiter (client.py's AsyncLimiter), same as
            # any other batch of calls through it.
            details_by_idx: dict[str, Judgements] = {}

            async def fetch_detail(score: RecentScore):
                if score.idx is None:
                    return
                try:
                    judgements = await client.get_recent_score_detail(score.idx)
                except Exception:
                    return
                if judgements is not None:
                    details_by_idx[score.idx] = judgements

            await asyncio.gather(*(fetch_detail(s) for s in scores))
            return scores, details_by_idx

        try:
            # with_client() transparently retries once via a silent re-login
            # if the session had expired and the user opted into
            # remember_password on /cc-login.
            scores, details_by_idx = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            if not scores:
                await interaction.edit_original_response(content="No recent plays found.")
                return

            catalog = get_catalog()

            image_name_by_title: dict[str, str] = {}
            for score in scores:
                song = catalog.get_by_title(score.title)
                if song and song.image_name:
                    image_name_by_title[score.title] = song.image_name
            jackets_by_image_name = await get_jackets_bulk(list(set(image_name_by_title.values())))

            credits_ = _group_by_credit(scores)

            pages: list[list[tuple[RecentScore, discord.Embed, bytes | None]]] = []
            for credit_scores in credits_:
                page: list[tuple[RecentScore, discord.Embed, bytes | None]] = []
                for i, score in enumerate(credit_scores):
                    jacket = jackets_by_image_name.get(image_name_by_title.get(score.title))
                    # show_detail=False here: the grouped listing never shows
                    # judgment-count/fast-late detail, even though it was
                    # just fetched into details_by_idx - that only appears
                    # for a track explicitly picked from RecentScoresView's
                    # dropdown.
                    embed = _build_track_embed(score, i, jacket is not None, None, show_detail=False)
                    page.append((score, embed, jacket))
                pages.append(page)

            view = RecentScoresView(interaction.user.id, pages, details_by_idx)
            embeds, files = view.current_embeds_and_files()

            message = await interaction.edit_original_response(content=None, embeds=embeds, view=view, attachments=files)
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch recent plays: {e}")
        except Exception as e:
            # Catch-all so an unexpected failure can never leave the
            # interaction without a reply.
            await interaction.edit_original_response(
                content=f"Couldn't fetch recent plays: unexpected error ({type(e).__name__}: {e})"
            )

    @app_commands.command(name="cc-best", description="Render your best-50 maimai DX rating image (B15 + B35, like dxrating.net)")
    @app_commands.describe(
        next_update="Preview your B15 once the current version ages out of the B15 window "
        "(only current-version charts count toward B15, like after the next update)"
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def best(self, interaction: discord.Interaction, next_update: bool = False):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-best", access.MAIMAI_NET_COOLDOWN):
            return
        start_time = time.monotonic()
        await interaction.response.defer()
        await interaction.edit_original_response(content="Preparing...")

        async def fetch(client):
            display_order = ["Re:MASTER", "MASTER", "EXPERT", "ADVANCED", "BASIC"]
            done: set[str] = set()

            async def report_progress(diff_label: str) -> None:
                done.add(diff_label)
                lines = [
                    f"Fetching {label} Charts... ✅" if label in done else f"Fetching {label} Charts..."
                    for label in display_order
                ]
                await interaction.edit_original_response(content="\n".join(lines))

            profile = await client.get_profile()
            scores = await client.get_music_scores(on_progress=report_progress, include_utage=False)
            # same pattern as cogs/profile.py's /cc-display - fetch these
            # ourselves through the account's session rather than passing
            # bare URLs, since maimaidx-eng.com serves them with headers
            # that break Discord's own embed-image unfurling.
            icon_bytes = await client.get_image_bytes(profile.icon_url) if profile.icon_url else None
            rating_badge_bytes = (
                await client.get_image_bytes(profile.rating_badge_url) if profile.rating_badge_url else None
            )
            return profile, scores, icon_bytes, rating_badge_bytes

        try:
            profile, scores, icon_bytes, rating_badge_bytes = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            if not scores:
                await interaction.edit_original_response(content="No scores found.")
                return

            catalog = get_catalog()
            result = calculate_best50(scores, catalog, next_update_preview=next_update)

            entries = [e for e in (result.b15 + result.b35) if e is not None]
            if not entries:
                await interaction.edit_original_response(
                    content="Fetched your scores, but couldn't match any of them to the song catalog to compute ratings."
                )
                return

            image_name_by_title: dict[str, str] = {}
            for entry in entries:
                song = catalog.get_by_title(entry.score.title)
                if song and song.image_name:
                    image_name_by_title[entry.score.title] = song.image_name

            jackets_by_image_name = await get_jackets_bulk(list(image_name_by_title.values()))
            jackets_by_title = {
                title: jackets_by_image_name[image_name]
                for title, image_name in image_name_by_title.items()
                if image_name in jackets_by_image_name
            }
            badge_icons = await get_all_badge_icons()
            version_logo_bytes = await get_version_logo()

            # B15 eligibility window (see calculate_best50) is normally
            # {current_version, previous_version} - name it after the
            # actual two versions rather than a generic "current version"
            # label, so e.g. "CiRCLE PLUS and CiRCLE" instead of just
            # "CURRENT VERSION". In next_update preview mode the window
            # narrows to just current_version, so label that distinctly.
            if next_update:
                b15_version_label = f"{catalog.current_version or 'CURRENT VERSION'} ONLY (next update preview)"
            else:
                b15_versions = [v for v in (catalog.current_version, catalog.previous_version) if v is not None]
                b15_version_label = " and ".join(b15_versions) if b15_versions else "CURRENT VERSION"

            await interaction.edit_original_response(content="Rendering...")
            buf = io.BytesIO()
            await asyncio.to_thread(
                render_b50,
                player_name=profile.display_name,
                rating=result.total_rating if next_update else profile.rating,
                icon_bytes=icon_bytes,
                rating_badge_bytes=rating_badge_bytes,
                result=result,
                b15_version_label=b15_version_label,
                jackets_by_title=jackets_by_title,
                badge_icons=badge_icons,
                version_logo_bytes=version_logo_bytes,
                output=buf,
            )

            new_entries = [e for e in result.b15 if e is not None]
            old_entries = [e for e in result.b35 if e is not None]
            total_count = len(new_entries) + len(old_entries)
            avg_overall = result.total_rating / total_count if total_count else 0
            avg_new = result.b15_total / len(new_entries) if new_entries else 0
            avg_old = result.b35_total / len(old_entries) if old_entries else 0
            # both lists are sorted highest-rating-first, so the last actual
            # (non-None) entry is the lowest-rated one in that bucket.
            floor_new = new_entries[-1].rating if new_entries else None
            floor_old = old_entries[-1].rating if old_entries else None

            elapsed = time.monotonic() - start_time
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            preview_note = (
                f"-# Preview: simulates next update (B15 = {catalog.current_version} only)\n" if next_update else ""
            )
            filename_suffix = "-preview" if next_update else ""
            await interaction.edit_original_response(
                content=(
                    f"{preview_note}"
                    f"Total rating: **{result.total_rating}** "
                    f"(New Charts: **{result.b15_total}**, Old Charts: **{result.b35_total}**)\n"
                    f"Average rating: Overall **{avg_overall:.2f}**, New **{avg_new:.2f}**, Old **{avg_old:.2f}**\n"
                    f"Floor: New: **{floor_new if floor_new is not None else 'N/A'}**, "
                    f"Old: **{floor_old if floor_old is not None else 'N/A'}**\n"
                    f"-# Rendered in `{elapsed:.2f}s`"
                ),
                attachments=[discord.File(buf, filename=f"best50-{profile.display_name}-{timestamp}{filename_suffix}.png")],
            )
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your scores: {e}")
        except Exception as e:
            # Catch-all so an unexpected failure can never leave the
            # interaction without a reply.
            await interaction.edit_original_response(
                content=f"Couldn't render your best-50: unexpected error ({type(e).__name__}: {e})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecordsCog(bot))
