import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, embed_colors
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.renderers.gauge import render_achievement_gauge
from circlechiffon.types import Circle, CircleChallenge, CircleMember

_PAGE_SIZE = 10


def _profile_embed(circle: Circle) -> discord.Embed:
    embed = discord.Embed(title=circle.name, color=embed_colors.INFO)
    embed.add_field(name="Code", value=circle.code or "?", inline=True)
    embed.add_field(name="Leader", value=circle.leader_name or "?", inline=True)
    if circle.points_this_month is not None:
        embed.add_field(name="Points (this month)", value=f"{circle.points_this_month:,} PT", inline=True)
    if circle.rank_this_month is not None:
        embed.add_field(name="Ranking (this month)", value=f"#{circle.rank_this_month:,}", inline=True)
    if circle.comment:
        embed.add_field(name="Comment", value=circle.comment, inline=False)
    if circle.tags:
        embed.add_field(name="Tags", value="\n".join(circle.tags), inline=False)
    return embed


def _members_embed(circle: Circle, members: list[CircleMember], page: int, page_count: int) -> discord.Embed:
    start = page * _PAGE_SIZE
    chunk = members[start : start + _PAGE_SIZE]
    embed = discord.Embed(title=f"{circle.name} - Members", color=embed_colors.INFO)
    if chunk:
        def _line(i: int, m: CircleMember) -> str:
            if m.points is not None:
                return f"{start + i + 1}. {m.name} - {m.points:,} PT"
            return f"{start + i + 1}. {m.name}"

        embed.description = "\n".join(_line(i, m) for i, m in enumerate(chunk))
    else:
        embed.description = (
            "No member list available. This page is best-effort - maimai DX NET's "
            "circle member roster markup wasn't confirmed live ahead of time - "
            "or it may be temporarily unavailable, since it errored intermittently "
            "even during manual testing."
        )
    embed.set_footer(text=f"Page {page + 1}/{max(page_count, 1)} - {len(members)} member(s)")
    return embed


class CircleMembersView(discord.ui.View):
    def __init__(self, invoker_id: int, circle: Circle, members: list[CircleMember]):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.circle = circle
        self.members = members
        self.page = 0
        self.page_count = max(1, (len(members) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.message: discord.InteractionMessage | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.page_count - 1

    def embeds(self) -> list[discord.Embed]:
        return [_profile_embed(self.circle), _members_embed(self.circle, self.members, self.page, self.page_count)]

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
        await interaction.response.edit_message(embeds=self.embeds(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embeds=self.embeds(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def _challenge_embed(challenge: CircleChallenge, has_jacket: bool, has_gauge: bool) -> discord.Embed:
    embed = discord.Embed(title=challenge.song_title, color=embed_colors.INFO)
    if challenge.category:
        embed.add_field(name="Category", value=challenge.category, inline=True)
    if challenge.note_designer:
        embed.add_field(name="Note Designer", value=challenge.note_designer, inline=True)

    member = challenge.member
    if member is not None:
        lines = []
        if member.rating is not None:
            lines.append(f"Rating {member.rating:,}")
        if member.title:
            tier = f" ({member.title_tier.title()})" if member.title_tier else ""
            lines.append(f"{member.title}{tier}")
        embed.add_field(
            name=f"Track selected by {member.name}",
            value="\n".join(lines) if lines else "​",
            inline=False,
        )

    if has_jacket:
        embed.set_thumbnail(url="attachment://jacket.jpg")
    if has_gauge:
        embed.set_image(url="attachment://gauge.png")
    return embed


class CircleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-circle", description="View your maimai DX Circle (team) info, points, ranking, and members")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def circle(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-circle", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            circle = await client.get_circle()
            members = await client.get_circle_members() if circle is not None else []
            members.sort(key=lambda m: m.points if m.points is not None else -1, reverse=True)
            return circle, members

        try:
            circle, members = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            if circle is None:
                await interaction.edit_original_response(
                    content="You don't appear to be in a Circle right now, or the Circle profile page "
                    "returned an unexpected layout."
                )
                return

            view = CircleMembersView(interaction.user.id, circle, members)
            message = await interaction.edit_original_response(content=None, embeds=view.embeds(), view=view)
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your Circle info: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch your Circle info: unexpected error ({type(e).__name__}: {e})"
            )

    @app_commands.command(name="cc-circle-challenge", description="View this week's maimai DX Circle challenge")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def circle_challenge(self, interaction: discord.Interaction):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-circle-challenge", access.MAIMAI_NET_COOLDOWN
        ):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            challenge = await client.get_circle_challenge()
            image_bytes = None
            if challenge is not None and challenge.jacket_url:
                image_bytes = await client.get_image_bytes(challenge.jacket_url)
            return challenge, image_bytes

        try:
            challenge, image_bytes = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            if challenge is None:
                await interaction.edit_original_response(
                    content="No active Circle challenge right now, or you don't appear to be in a Circle."
                )
                return

            files = []
            if image_bytes:
                files.append(discord.File(io.BytesIO(image_bytes), filename="jacket.jpg"))

            gauge_buf = None
            if challenge.achievement_percent is not None:
                gauge_buf = io.BytesIO()
                await asyncio.to_thread(render_achievement_gauge, challenge.achievement_percent, gauge_buf)
                gauge_buf.seek(0)
                files.append(discord.File(gauge_buf, filename="gauge.png"))

            embed = _challenge_embed(challenge, has_jacket=image_bytes is not None, has_gauge=gauge_buf is not None)
            await interaction.edit_original_response(content=None, embed=embed, attachments=files)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch the Circle challenge: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch the Circle challenge: unexpected error ({type(e).__name__}: {e})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(CircleCog(bot))
