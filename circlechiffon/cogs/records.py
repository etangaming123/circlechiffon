import asyncio
import io
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, badge_emojis, embed_colors, user_templates
from circlechiffon.adapters.dxrating.images import get_jackets_bulk
from circlechiffon.adapters.maimai_net.badge_icons import get_all_badge_icons
from circlechiffon.adapters.maimai_site.version_logo import get_version_logo
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.best50 import calculate_best50
from circlechiffon.ratingcalc.calculator import calculate_rating, rank_tag_for_achievement
from circlechiffon.ratingcalc.judgement_loss import calculate_judgement_loss
from circlechiffon.renderers.b50 import render_b50
from circlechiffon.renderers.b50_share import build_detail_view_url
from circlechiffon.renderers.judgement_detail import render_judgement_detail
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


def _chunk_scores(scores: list[RecentScore], size: int = 25) -> list[list[RecentScore]]:
    """Flat 25-wide blocks (Discord's own Select option cap) feeding ONLY
    the track-picker dropdown - a separate axis from the credit-grouped
    overview embeds (_group_by_credit), so the dropdown can offer any of
    the last 25/50/... plays regardless of which credit is currently shown
    on screen."""
    return [scores[i : i + size] for i in range(0, len(scores), size)]


def _fmt(value: int | None) -> str:
    return f"{value:,}" if value is not None else "-"


def _score_summary(score: RecentScore) -> tuple[str, str, list[str]]:
    """Shared by the overview per-track embed and the single-track detail
    embed: (subtitle, rank_tag, detail_lines) - subtitle is e.g.
    'MASTER 13+ (13.8)', detail_lines is the RANK/ACC, DXSCORE/RATING,
    Combo/Sync trio both embeds show."""
    catalog = get_catalog()
    sheet = catalog.find_sheet(score.title, score.chart_type, score.difficulty)
    rating = None
    if sheet is not None and sheet.internal_level_value is not None:
        rating = calculate_rating(sheet.internal_level_value, score.achievement, score.combo_flag).rating

    diff_name = score.difficulty.display_name if score.difficulty else "?"
    rank_tag = rank_tag_for_achievement(score.achievement)

    subtitle = f"{diff_name} {sheet.level}" if sheet is not None and sheet.level else diff_name
    if sheet is not None and sheet.internal_level_value is not None:
        subtitle += f" ({sheet.internal_level_value})"

    dx_part = (
        f"{score.dx_score:,} / {score.dx_score_total:,}"
        if score.dx_score is not None and score.dx_score_total is not None
        else "-"
    )
    achievement_text = "No Chart" if score.achievement == 0 else f"{score.achievement:.4f}%"
    lines = [
        f"RANK: {badge_emojis.rank_badge(rank_tag)} - ACC: **{achievement_text}**",
        f"DXSCORE: **{dx_part}** - RATING: **{rating if rating is not None else '-'}**",
        f"Combo: {badge_emojis.combo_badge(score.combo_flag)}  Sync: {badge_emojis.sync_badge(score.sync_flag)}",
    ]
    return subtitle, rank_tag, lines


def _build_track_embed(score: RecentScore, jacket_index: int, has_jacket: bool) -> discord.Embed:
    """Per-track overview embed - one per track in the current credit page
    (3-4 embeds, well under Discord's 10-embeds-per-message cap). Never
    shows judgement-count detail; that only renders as an image once a
    track is picked from the dropdown (see RecentScoresView)."""
    subtitle, rank_tag, lines = _score_summary(score)

    embed = discord.Embed(title=score.title, color=embed_colors.rank_color(rank_tag))
    if score.track_no is not None:
        embed.set_author(name=f"TRACK {score.track_no:02d}")
    embed.description = subtitle
    embed.add_field(name="Details:", value="\n".join(lines), inline=False)

    if score.played_at is not None:
        embed.set_footer(text=score.played_at.strftime("%d/%m/%Y %H:%M"))
    if has_jacket:
        embed.set_thumbnail(url=f"attachment://jacket_{jacket_index}.jpg")
    return embed


def _build_detail_embed(score: RecentScore, judgements: Judgements | None, has_jacket: bool) -> discord.Embed:
    """Single-track detail embed - carries every piece of context
    (TRACK NN, title, difficulty/constant, RANK/ACC, DXSCORE/RATING,
    Combo/Sync, FAST/LATE, total LOST%) as plain embed text/thumbnail,
    leaving the attached image (see renderers/judgement_detail.py) to show
    ONLY the judgement table itself."""
    subtitle, rank_tag, lines = _score_summary(score)
    lines = list(lines)
    if judgements is not None:
        if judgements.fast is not None or judgements.late is not None:
            lines.append(f"FAST/LATE: **{_fmt(judgements.fast)}/{_fmt(judgements.late)}**")
        loss = calculate_judgement_loss(judgements, score.achievement)
        lines.append(f"LOST: **{loss.total_lost_percent:.2f}%**")

    embed = discord.Embed(title=score.title, color=embed_colors.rank_color(rank_tag))
    if score.track_no is not None:
        embed.set_author(name=f"TRACK {score.track_no:02d}")
    embed.description = subtitle
    embed.add_field(name="Details:", value="\n".join(lines), inline=False)
    if has_jacket:
        embed.set_thumbnail(url="attachment://jacket_detail.jpg")
    embed.set_image(url="attachment://judgement_detail.png")
    return embed


async def _fetch_recent_with_details(client) -> tuple[list[RecentScore], dict[str, Judgements]]:
    """One get_recent_scores() call, then eagerly fetch every track's
    judgment-count/DX-score detail up front (rather than on-demand per
    click) so a single-track focus never needs an extra fetch - bounded
    only by the client's own internal rate limiter (client.py's
    AsyncLimiter), same as any other batch of calls through it."""
    scores = await client.get_recent_scores()
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


async def _build_credit_pages(
    scores: list[RecentScore],
) -> tuple[list[list[tuple[RecentScore, discord.Embed, bytes | None]]], dict[str, bytes]]:
    """Credit-grouped overview pages (3-4 tracks each, typically) with
    bulk-fetched jacket art - drives the visible Previous/Next embeds, a
    separate axis from the dropdown's flat 25-wide blocks (_chunk_scores).
    Also returns a title->jacket-bytes lookup covering every fetched score,
    since the dropdown can select a track from a different credit than the
    one currently on screen - the single-track detail view (which also
    wants a jacket thumbnail) can't rely on the currently-displayed credit
    page's tuples for that."""
    catalog = get_catalog()
    image_name_by_title: dict[str, str] = {}
    for score in scores:
        song = catalog.get_by_title(score.title)
        if song and song.image_name:
            image_name_by_title[score.title] = song.image_name
    jackets_by_image_name = await get_jackets_bulk(list(set(image_name_by_title.values())))

    jacket_by_title: dict[str, bytes] = {}
    for title, image_name in image_name_by_title.items():
        jacket = jackets_by_image_name.get(image_name)
        if jacket:
            jacket_by_title[title] = jacket

    pages: list[list[tuple[RecentScore, discord.Embed, bytes | None]]] = []
    for credit_scores in _group_by_credit(scores):
        page: list[tuple[RecentScore, discord.Embed, bytes | None]] = []
        for i, score in enumerate(credit_scores):
            jacket = jacket_by_title.get(score.title)
            embed = _build_track_embed(score, i, jacket is not None)
            page.append((score, embed, jacket))
        pages.append(page)
    return pages, jacket_by_title


class _TrackSelect(discord.ui.Select):
    """Options come from the owner's flat 25-wide dropdown_pages (NOT the
    credit-grouped overview pages) - lets the user jump straight to any of
    the current 25-block's tracks regardless of which credit page happens
    to be on screen. Rebuilt only when dropdown_pages's current block
    changes (the "Show X-Y" button), since Discord select options are
    static once sent."""

    def __init__(self, owner: "RecentScoresView"):
        super().__init__(placeholder="View detailed stats for a track...", min_values=1, max_values=1, row=1)
        self._owner = owner
        self.refresh_options()

    def refresh_options(self):
        block = self._owner.dropdown_pages[self._owner.dropdown_index]
        base = self._owner.dropdown_index * 25
        options = []
        for i, score in enumerate(block):
            diff_name = score.difficulty.display_name if score.difficulty else "?"
            options.append(
                discord.SelectOption(
                    label=f"{base + i + 1:02d}. {score.title}"[:100], description=diff_name, value=str(i)
                )
            )
        self.options = options

    async def callback(self, interaction: discord.Interaction):
        await self._owner.on_track_selected(interaction, int(self.values[0]))


class RecentScoresView(discord.ui.View):
    """Two independent paging axes on one view:

    - `pages` (credit-grouped, via _group_by_credit): drives the VISIBLE
      overview - Previous/Next walk between credits, each showing every
      track in that credit as its own embed (3-4 embeds typically, well
      under Discord's 10-embeds-per-message cap). Never shows judgement
      detail.
    - `dropdown_pages` (flat 25-wide blocks, via _chunk_scores): drives ONLY
      the track-picker dropdown's options. The "Show X-Y" button flips
      between these blocks WITHOUT touching whatever's currently on
      screen - it lets the dropdown offer any of the last 25/50/... plays
      regardless of which credit is currently displayed.

    Picking a track from the dropdown (from whichever 25-block is active)
    swaps the message to that single track's rendered judgement-detail
    image, with a toggle button to show/hide the lost-achievement%
    breakdown on it. `Back` (and Previous/Next, which also exit detail view
    directly) return to the credit-grouped overview.

    Jacket bytes (not discord.File objects) are stored on the credit pages
    and turned into fresh discord.File instances on every render, since a
    File's stream is exhausted once sent and can't be resent."""

    def __init__(
        self,
        invoker_id: int,
        pages: list[list[tuple[RecentScore, discord.Embed, bytes | None]]],
        dropdown_pages: list[list[RecentScore]],
        details_by_idx: dict[str, Judgements],
        jacket_by_title: dict[str, bytes],
    ):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.pages = pages
        self.dropdown_pages = dropdown_pages
        self.details_by_idx = details_by_idx
        self.jacket_by_title = jacket_by_title
        self.credit_index = 0
        self.dropdown_index = 0
        self.viewing_detail = False
        self.selected_score: RecentScore | None = None
        self.detail_mode: str = "summary"
        self.message: discord.InteractionMessage | None = None
        self.select = _TrackSelect(self)
        self.add_item(self.select)
        # A decorator-based (@discord.ui.button) item is always auto-added
        # by View.__init__, so a toggle that should only appear when the
        # dropdown actually has a second 25-block has to be a plain Button
        # built and added here instead - the common case (<=25 recent
        # plays) then shows no dead disabled button.
        self.page_toggle: discord.ui.Button | None = None
        if len(self.dropdown_pages) > 1:
            self.page_toggle = discord.ui.Button(style=discord.ButtonStyle.secondary, row=0)
            self.page_toggle.callback = self._on_page_toggle
            self._update_toggle_label()
            self.add_item(self.page_toggle)
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.credit_index == 0
        self.next.disabled = self.credit_index == len(self.pages) - 1
        self.back.disabled = not self.viewing_detail
        self.detail_toggle.disabled = not self.viewing_detail
        self._update_detail_toggle_label()

    def _update_detail_toggle_label(self):
        self.detail_toggle.label = "Hide Lost %" if self.detail_mode == "full" else "Show Lost %"

    def _update_toggle_label(self):
        next_index = (self.dropdown_index + 1) % len(self.dropdown_pages)
        starts = [0]
        for block in self.dropdown_pages[:-1]:
            starts.append(starts[-1] + len(block))
        start = starts[next_index] + 1
        end = starts[next_index] + len(self.dropdown_pages[next_index])
        self.page_toggle.label = f"Show {start}-{end}"

    async def _on_page_toggle(self, interaction: discord.Interaction):
        # Only changes which 25-block the dropdown offers - whatever's
        # currently on screen (overview or a track's detail) is unaffected.
        self.dropdown_index = (self.dropdown_index + 1) % len(self.dropdown_pages)
        self._update_toggle_label()
        self.select.refresh_options()
        if self.viewing_detail:
            await self._render_current_detail(interaction)
        else:
            await self._render_page(interaction)

    def current_embeds_and_files(self) -> tuple[list[discord.Embed], list[discord.File]]:
        embeds, files = [], []
        for i, (_score, embed, jacket) in enumerate(self.pages[self.credit_index]):
            embeds.append(embed)
            if jacket:
                files.append(discord.File(io.BytesIO(jacket), filename=f"jacket_{i}.jpg"))
        return embeds, files

    async def _render_page(self, interaction: discord.Interaction):
        self.viewing_detail = False
        self.selected_score = None
        self._update_buttons()
        embeds, files = self.current_embeds_and_files()
        await interaction.response.edit_message(content=None, embeds=embeds, view=self, attachments=files)

    async def on_track_selected(self, interaction: discord.Interaction, block_local_index: int):
        self.selected_score = self.dropdown_pages[self.dropdown_index][block_local_index]
        self.viewing_detail = True
        self._update_buttons()
        await self._render_current_detail(interaction)

    async def _render_current_detail(self, interaction: discord.Interaction):
        """Renders self.selected_score's judgement-detail image using
        self.detail_mode - shared by on_track_selected (first pick),
        detail_toggle (cycling the lost-% mode), and _on_page_toggle
        (dropdown-block switch while already viewing a track), so none of
        these re-fetch data. Only reachable while viewing_detail is True
        (detail_toggle is disabled otherwise, and _on_page_toggle checks
        it), so selected_score is always set here."""
        score = self.selected_score
        judgements = self.details_by_idx.get(score.idx) if score.idx else None
        jacket = self.jacket_by_title.get(score.title)

        buf = io.BytesIO()
        await asyncio.to_thread(
            render_judgement_detail, judgements=judgements, achievement=score.achievement, mode=self.detail_mode,
            output=buf,
        )
        files = [discord.File(buf, filename="judgement_detail.png")]
        if jacket:
            files.append(discord.File(io.BytesIO(jacket), filename="jacket_detail.jpg"))

        embed = _build_detail_embed(score, judgements, jacket is not None)
        await interaction.response.edit_message(content=None, embeds=[embed], view=self, attachments=files)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.credit_index -= 1
        await self._render_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.credit_index += 1
        await self._render_page(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._render_page(interaction)

    @discord.ui.button(label="Show Lost %", style=discord.ButtonStyle.secondary, row=0)
    async def detail_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.detail_mode = "summary" if self.detail_mode == "full" else "full"
        self._update_detail_toggle_label()
        await self._render_current_detail(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class B50ShareView(discord.ui.View):
    """Wraps a single link-style button pointing at the b50 detail-view
    static page (see renderers/b50_share.py). Link buttons are resolved
    entirely client-side by Discord - no interaction ever reaches the bot for
    them, so unlike RecentScoresView there's no interaction_check, callback,
    or timeout-driven disable needed."""

    def __init__(self, share_url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="View Detailed List ↗", style=discord.ButtonStyle.link, url=share_url))


class RecordsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-recent", description="View your recent maimai DX plays")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def recent(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-recent", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        try:
            # with_client() transparently retries once via a silent re-login
            # if the session had expired and the user opted into
            # remember_password on /cc-login.
            scores, details_by_idx = await accounts.with_client(
                interaction.user.id, _fetch_recent_with_details, on_retry=accounts.default_retry_notice(interaction)
            )

            if not scores:
                await interaction.edit_original_response(content="No recent plays found.")
                return

            pages, jacket_by_title = await _build_credit_pages(scores)
            dropdown_pages = _chunk_scores(scores)

            view = RecentScoresView(interaction.user.id, pages, dropdown_pages, details_by_idx, jacket_by_title)
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
            share_url = build_detail_view_url(result, catalog)

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
            template_bytes = await user_templates.load_template(interaction.user.id, "b50")

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
                template_bytes=template_bytes,
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
            share_note = "" if share_url else "\n-# Detailed list view unavailable this time - use the image above."
            await interaction.edit_original_response(
                content=(
                    f"{preview_note}"
                    f"Total rating: **{result.total_rating}** "
                    f"(New Charts: **{result.b15_total}**, Old Charts: **{result.b35_total}**)\n"
                    f"Average rating: Overall **{avg_overall:.2f}**, New **{avg_new:.2f}**, Old **{avg_old:.2f}**\n"
                    f"Floor: New: **{floor_new if floor_new is not None else 'N/A'}**, "
                    f"Old: **{floor_old if floor_old is not None else 'N/A'}**\n"
                    f"-# Rendered in `{elapsed:.2f}s`"
                    f"{share_note}"
                ),
                attachments=[discord.File(buf, filename=f"best50-{profile.display_name}-{timestamp}{filename_suffix}.png")],
                view=B50ShareView(share_url) if share_url else None,
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
