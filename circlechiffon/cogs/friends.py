import asyncio
import difflib
import io
import time
import unicodedata
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, embed_colors
from circlechiffon.adapters.dxrating.images import get_jackets_bulk
from circlechiffon.adapters.maimai_net.badge_icons import get_all_badge_icons
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.ratingcalc.best50 import calculate_best50
from circlechiffon.renderers.b50 import render_b50
from circlechiffon.songdata.catalog import get_catalog
from circlechiffon.types import FriendEntry

_PAGE_SIZE = 10

_NOT_FAVORITED_HINT = (
    "No scores found for this friend. If they're a real friend with real "
    "plays, maimai DX NET may only expose score data for friends you've "
    "marked as a Favorite on the friend list - try favoriting them there "
    "and running this again."
)


def _normalize_name(text: str) -> str:
    """NFKC-folds fullwidth/halfwidth/compatibility variants (e.g. fullwidth
    Ａ-Ｚ, halfwidth katakana, circled digits) down to their plain form, then
    casefolds. maimai display names commonly use these instead of anything
    typable on a standard keyboard, so a naive substring match against the
    raw text misses almost everything - this makes "ethan" match "Ｅｔｈａｎ"
    or "ｲｰｻﾝ"-style halfwidth katakana the same as their generic spelling."""
    return unicodedata.normalize("NFKC", text).casefold().strip()


async def _resolve_friend_entry(client, query: str) -> FriendEntry | list[FriendEntry] | None:
    """Looks up one friend by (in order): exact idx - the hidden id every
    friend sub-page uses, digits-only so a query that's all digits is tried
    here first - then a normalized substring match on display name, falling
    back to fuzzy close-matching (via difflib) if no substring match hits at
    all, so a typo or an unnormalizable decorative name still surfaces
    candidates. Returns a single FriendEntry on a clean match, a list of
    candidates on an ambiguous/fuzzy match (for the caller to offer as a
    dropdown), or None on no match at all."""
    query = query.strip()
    if not query:
        return None
    if query.isdigit():
        entry = await client.get_friend_profile(query)
        if entry is not None:
            return entry
    entries = await client.get_friend_list()
    norm_query = _normalize_name(query)
    norm_names = [_normalize_name(e.profile.display_name) for e in entries]

    matches = [e for e, n in zip(entries, norm_names) if norm_query in n]
    if not matches:
        close = set(difflib.get_close_matches(norm_query, norm_names, n=25, cutoff=0.5))
        matches = [e for e, n in zip(entries, norm_names) if n in close]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    return matches


def _friends_list_embed(entries: list[FriendEntry], show_ids: bool, page: int, page_count: int) -> discord.Embed:
    start = page * _PAGE_SIZE
    chunk = entries[start : start + _PAGE_SIZE]
    embed = discord.Embed(title="Friends", color=embed_colors.INFO)
    if chunk:
        def _line(i: int, e: FriendEntry) -> str:
            line = f"{start + i + 1}. {e.profile.display_name}"
            if e.profile.rating is not None:
                line += f" - {e.profile.rating}"
            if show_ids:
                line += f" (id: `{e.idx}`)"
            if e.comment:
                line += f"\n> {e.comment}"
            return line

        embed.description = "\n".join(_line(i, e) for i, e in enumerate(chunk))
    else:
        embed.description = "No friends found."
    embed.set_footer(text=f"Page {page + 1}/{max(page_count, 1)} - {len(entries)} friend(s)")
    return embed


class FriendsListView(discord.ui.View):
    def __init__(self, invoker_id: int, entries: list[FriendEntry], show_ids: bool):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.entries = entries
        self.show_ids = show_ids
        self.page = 0
        self.page_count = max(1, (len(entries) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.message: discord.InteractionMessage | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.page_count - 1

    def embed(self) -> discord.Embed:
        return _friends_list_embed(self.entries, self.show_ids, self.page, self.page_count)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
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


class FriendPickView(discord.ui.View):
    """Shown when a name search matches more than one friend (substring or
    fuzzy). `on_pick` is called with the *component* interaction (not the
    original slash-command one) and the chosen FriendEntry once the user
    picks from the dropdown - the caller is responsible for responding to
    that interaction itself (it hasn't been responded to when on_pick runs)."""

    def __init__(self, invoker_id: int, matches: list[FriendEntry], on_pick):
        super().__init__(timeout=60)
        self.invoker_id = invoker_id
        self.on_pick = on_pick
        self.message: discord.InteractionMessage | None = None
        # Discord caps a select at 25 options - matches beyond that just
        # aren't offered; the prompt text tells the user to narrow instead.
        self.shown = matches[:25]

        options = [
            discord.SelectOption(
                label=e.profile.display_name[:100],
                value=e.idx,
                description=f"Rating: {e.profile.rating}" if e.profile.rating is not None else None,
            )
            for e in self.shown
        ]
        select = discord.ui.Select(placeholder="Select a friend...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran this command can pick.", ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        idx = interaction.data["values"][0]
        entry = next((e for e in self.shown if e.idx == idx), None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Getting data...", embed=None, view=self)
        if entry is not None:
            await self.on_pick(interaction, entry)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def _pick_prompt(matches: list[FriendEntry]) -> str:
    if len(matches) > 25:
        return f"{len(matches)} friends match that - showing the first 25. Narrow your search for the rest, or pick below:"
    return "Multiple friends match that - pick one:"


class FriendsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-friends", description="List your maimai DX NET friends")
    @app_commands.describe(show_ids="Show each friend's internal id (needed to disambiguate friends with the same name)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def friends(self, interaction: discord.Interaction, show_ids: bool = False):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-friends", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            entries = await client.get_friend_list()
            entries.sort(key=lambda e: e.profile.rating if e.profile.rating is not None else -1, reverse=True)
            return entries

        try:
            entries = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )
            view = FriendsListView(interaction.user.id, entries, show_ids)
            message = await interaction.edit_original_response(content=None, embed=view.embed(), view=view)
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your friends: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch your friends: unexpected error ({type(e).__name__}: {e})"
            )

    @app_commands.command(
        name="cc-friend-profile",
        description="View a friend's (limited) maimai DX NET profile - SEGA exposes far less for friends than for you",
    )
    @app_commands.describe(friend="Friend's display name (or their exact id from /cc-friends show_ids:True)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def friend_profile(self, interaction: discord.Interaction, friend: str):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-friend-profile", access.MAIMAI_NET_COOLDOWN
        ):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            return await _resolve_friend_entry(client, friend)

        try:
            resolved = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )
            if resolved is None:
                await interaction.edit_original_response(content=f"No friend found matching `{friend}`.")
                return
            if isinstance(resolved, list):
                view = FriendPickView(interaction.user.id, resolved, self._send_friend_profile)
                await interaction.edit_original_response(content=_pick_prompt(resolved), view=view)
                view.message = await interaction.original_response()
                return
            await self._send_friend_profile(interaction, resolved)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch that friend's profile: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch that friend's profile: unexpected error ({type(e).__name__}: {e})"
            )

    async def _send_friend_profile(self, interaction: discord.Interaction, entry: FriendEntry):
        """Fetches the icon and edits `interaction`'s response with the
        profile embed. Works from either the original slash-command
        interaction (already deferred) or a FriendPickView select callback
        (already responded to via edit_message) - both support
        edit_original_response against the same underlying message."""

        async def fetch(client):
            return await client.get_image_bytes(entry.profile.icon_url) if entry.profile.icon_url else None

        try:
            icon_bytes = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            profile = entry.profile
            embed = discord.Embed(title=profile.display_name, color=embed_colors.INFO)
            embed.add_field(name="Rating", value=str(profile.rating) if profile.rating is not None else "?", inline=True)
            if profile.title:
                title_value = f"{profile.title} ({profile.title_tier})" if profile.title_tier else profile.title
                embed.add_field(name="Title", value=title_value, inline=True)
            if profile.star_count is not None:
                embed.add_field(name="Stars", value=f"×{profile.star_count:,}", inline=True)
            if entry.comment:
                embed.add_field(name="Comment", value=entry.comment, inline=False)
            embed.set_footer(
                text="Friend profiles are limited - SEGA doesn't expose play counts or "
                "clear-count grids for anyone but yourself."
            )

            files = []
            if icon_bytes:
                files.append(discord.File(io.BytesIO(icon_bytes), filename="icon.png"))
                embed.set_thumbnail(url="attachment://icon.png")

            await interaction.edit_original_response(content=None, embed=embed, view=None, attachments=files)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first.", view=None
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e), view=None)
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch that friend's profile: {e}", view=None)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch that friend's profile: unexpected error ({type(e).__name__}: {e})", view=None
            )

    @app_commands.command(
        name="cc-friend-best",
        description="Render a friend's best-50 rating image, computed from their scores.",
    )
    @app_commands.describe(friend="Friend's display name (or their exact id from /cc-friends show_ids:True)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def friend_best(self, interaction: discord.Interaction, friend: str):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-friend-best", access.MAIMAI_NET_COOLDOWN
        ):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Resolving friend...")

        async def fetch(client):
            return await _resolve_friend_entry(client, friend)

        try:
            resolved = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )
            if resolved is None:
                await interaction.edit_original_response(content=f"No friend found matching `{friend}`.")
                return
            if isinstance(resolved, list):
                view = FriendPickView(interaction.user.id, resolved, self._render_friend_best)
                await interaction.edit_original_response(content=_pick_prompt(resolved), view=view)
                view.message = await interaction.original_response()
                return
            await self._render_friend_best(interaction, resolved)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch that friend's scores: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't render that friend's best-50: unexpected error ({type(e).__name__}: {e})"
            )

    async def _render_friend_best(self, interaction: discord.Interaction, entry: FriendEntry):
        """Fetches scores + renders the best-50 image, editing `interaction`'s
        response throughout. Works from either the original slash-command
        interaction (already deferred) or a FriendPickView select callback
        (already responded to via edit_message)."""
        start_time = time.monotonic()

        async def fetch(client):
            display_order = ["Re:MASTER", "MASTER", "EXPERT", "ADVANCED", "BASIC"]
            done: set[str] = set()

            async def report_progress(diff_label: str) -> None:
                done.add(diff_label)
                lines = [
                    f"Fetching {label} Charts... ✅" if label in done else f"Fetching {label} Charts..."
                    for label in display_order
                ]
                await interaction.edit_original_response(content="\n".join(lines), view=None)

            scores = await client.get_friend_scores(entry.idx, on_progress=report_progress)
            icon_bytes = await client.get_image_bytes(entry.profile.icon_url) if entry.profile.icon_url else None
            return scores, icon_bytes

        try:
            scores, icon_bytes = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            if not scores:
                await interaction.edit_original_response(content=_NOT_FAVORITED_HINT, view=None)
                return

            catalog = get_catalog()
            result = calculate_best50(scores, catalog)

            entries = [e for e in (result.b15 + result.b35) if e is not None]
            if not entries:
                await interaction.edit_original_response(
                    content="Fetched this friend's scores, but couldn't match any of them to the song catalog to compute a rating.",
                    view=None,
                )
                return

            image_name_by_title: dict[str, str] = {}
            for e in entries:
                song = catalog.get_by_title(e.score.title)
                if song and song.image_name:
                    image_name_by_title[e.score.title] = song.image_name

            jackets_by_image_name = await get_jackets_bulk(list(image_name_by_title.values()))
            jackets_by_title = {
                title: jackets_by_image_name[image_name]
                for title, image_name in image_name_by_title.items()
                if image_name in jackets_by_image_name
            }
            badge_icons = await get_all_badge_icons()

            b15_versions = [v for v in (catalog.current_version, catalog.previous_version) if v is not None]
            b15_version_label = " and ".join(b15_versions) if b15_versions else "CURRENT VERSION"

            await interaction.edit_original_response(content="Rendering...", view=None)
            buf = io.BytesIO()
            await asyncio.to_thread(
                render_b50,
                player_name=entry.profile.display_name,
                rating=result.total_rating,
                icon_bytes=icon_bytes,
                rating_badge_bytes=None,
                result=result,
                b15_version_label=b15_version_label,
                jackets_by_title=jackets_by_title,
                badge_icons=badge_icons,
                output=buf,
            )

            elapsed = time.monotonic() - start_time
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            await interaction.edit_original_response(
                content=(
                    f"-# Computed locally from scraped achievements - not SEGA's own rating number, "
                    f"which isn't shown for friends\n"
                    f"Computed rating: **{result.total_rating}** "
                    f"(New Charts: **{result.b15_total}**, Old Charts: **{result.b35_total}**)\n"
                    f"-# Rendered in `{elapsed:.2f}s`"
                ),
                attachments=[discord.File(buf, filename=f"friend-best50-{entry.profile.display_name}-{timestamp}.png")],
                view=None,
            )
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first.", view=None
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e), view=None)
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch that friend's scores: {e}", view=None)
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't render that friend's best-50: unexpected error ({type(e).__name__}: {e})", view=None
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(FriendsCog(bot))
