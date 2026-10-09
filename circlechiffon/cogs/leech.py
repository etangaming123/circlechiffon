import discord
from discord import app_commands
from discord.ext import commands, tasks

from circlechiffon import access, accounts, leech
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.types import FriendEntry


class LeechCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.purge_expired_offers.start()

    async def cog_unload(self) -> None:
        self.purge_expired_offers.cancel()

    @tasks.loop(hours=1)
    async def purge_expired_offers(self) -> None:
        try:
            await leech.purge_expired()
        except Exception as e:
            print(f"Couldn't purge expired leech offers: {type(e).__name__}: {e}")

    @purge_expired_offers.before_loop
    async def _before_purge(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="cc-leech-send",
        description="Let another Discord user use the bot through your linked account, as one of your DX NET friends.",
    )
    @app_commands.describe(
        user="The Discord user to offer leech access to",
        friend="Their display name on your maimai DX NET friend list (or their exact id from /cc-friends show_ids:True)",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def leech_send(self, interaction: discord.Interaction, user: discord.User, friend: str):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-leech-send", access.MAIMAI_NET_COOLDOWN
        ):
            return
        if user.bot or user.id == interaction.user.id:
            access.clear_cooldown(interaction.user.id, "cc-leech-send")
            await interaction.response.send_message(
                "Pick another person - you can't leech yourself or a bot.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Resolving friend...")

        # imported lazily, same as cogs/score.py: avoids a cog import cycle
        from circlechiffon.cogs.friends import FriendPickView, _pick_prompt, _resolve_friend_entry

        async def resolve(client):
            return await _resolve_friend_entry(client, friend)

        try:
            resolved = await accounts.with_client(
                interaction.user.id, resolve, on_retry=accounts.default_retry_notice(interaction)
            )
            if resolved is None:
                await interaction.edit_original_response(content=f"No friend found matching `{friend}`.")
                return
            if isinstance(resolved, list):
                async def on_pick(component_interaction: discord.Interaction, entry: FriendEntry):
                    await self._finish_offer(component_interaction, user, entry)

                view = FriendPickView(interaction.user.id, resolved, on_pick)
                await interaction.edit_original_response(content=_pick_prompt(resolved), view=view)
                view.message = await interaction.original_response()
                return
            await self._finish_offer(interaction, user, resolved)
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="Leech mode needs your own linked account to read through. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't look up your friends: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't look up your friends: unexpected error ({type(e).__name__}: {e})"
            )

    async def _finish_offer(self, interaction: discord.Interaction, leecher: discord.User, entry: FriendEntry):
        """Stores the offer and pings the leecher. Works from the original
        (deferred) interaction or a FriendPickView select callback - an edit
        never pings, so the mention goes out as its own message."""
        host = interaction.user
        name = entry.profile.display_name
        await leech.offer(host.id, leecher.id, entry.idx, name[:64])
        await interaction.edit_original_response(
            content=f"Offer sent to {leecher.mention}.", view=None, allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.followup.send(
            f"{leecher.mention} - {host.mention} is offering you leech access to CiRCLE Chiffon, reading "
            f"your data through their account as their maimai DX NET friend **{name}**.\n"
            f"If that's you, run `/cc-leech-accept user:{host.name}` (valid for 7 days).\n"
            f"-# {leech.LIMITS_SUMMARY}",
            allowed_mentions=discord.AllowedMentions(users=[leecher]),
        )

    @app_commands.command(
        name="cc-leech-accept",
        description="Accept a leech offer: use the bot without a SEGA ID, through the offering user's account.",
    )
    @app_commands.describe(user="The Discord user who ran /cc-leech-send for you")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def leech_accept(self, interaction: discord.Interaction, user: discord.User):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-leech-accept", access.DEFAULT_COOLDOWN
        ):
            return
        link = await leech.accept(interaction.user.id, user.id)
        if link is None:
            await interaction.response.send_message(
                f"No pending leech offer from {user.mention} (offers expire after 7 days). "
                "Ask them to run `/cc-leech-send` again.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        own_account = ""
        if await accounts.is_linked(interaction.user.id):
            own_account = "\nYou have your own linked account, which always takes priority over leech mode."
        await interaction.response.send_message(
            f"Leech mode is on. `/cc-profile`, `/cc-best` and `/cc-scores` now show **{link.friend_name}**'s data, "
            f"read through {user.mention}'s account.\n{leech.LIMITS_SUMMARY}{own_account}\n"
            "-# `/cc-logout` removes the link.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="cc-leech-remove",
        description="Remove a leech link or pending offer, whether you sent it or received it.",
    )
    @app_commands.describe(user="The other person in the link. Leave empty to remove all of your leech links and offers")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def leech_remove(self, interaction: discord.Interaction, user: discord.User | None = None):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-leech-remove", access.DEFAULT_COOLDOWN
        ):
            return
        if user is None:
            removed = await leech.remove_for(interaction.user.id)
            done, none = "Removed all of your leech links and offers.", "You have no leech links or offers."
        else:
            removed = await leech.remove_between(interaction.user.id, user.id)
            done = f"Removed your leech link with {user.mention}."
            none = f"You have no leech link or offer with {user.mention}."
        await interaction.response.send_message(
            done if removed else none, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LeechCog(bot))
