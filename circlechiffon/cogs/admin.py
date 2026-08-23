import re

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access


def _parse_duration(length: str) -> tuple[int | None, bool] | None:
    """Parses a ban length string into (duration_seconds, ncmd). Returns
    (None, False) for permanent, or None if `length` couldn't be parsed."""
    length = length.strip().lower()
    if length == "permanent":
        return None, False
    if length == "ncmd":
        return None, True

    matches = re.findall(r"(\d+)([dhms])", length)
    if not matches:
        return None

    total_seconds = 0
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    for value, unit in matches:
        total_seconds += int(value) * units[unit]
    return total_seconds, False


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-ban", description="Ban a user from using the bot. (Owner only)")
    @app_commands.describe(
        user="The user to ban",
        length="'permanent', 'ncmd' (lifts after their next command attempt), or a duration like 2d1h30m",
        reason="The reason for banning the user (shown to them)",
    )
    async def ban(self, interaction: discord.Interaction, user: discord.User, length: str, reason: str | None = None):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-ban"):
            return
        if not access.is_owner(interaction.user.id):
            await interaction.response.send_message(content="You don't have permission to use this command.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(content="You cannot ban yourself.", ephemeral=True)
            return

        parsed = _parse_duration(length)
        if parsed is None:
            await interaction.response.send_message(
                content="Invalid length. Use 'permanent', 'ncmd', or a duration like '2d1h30m2s'.", ephemeral=True
            )
            return
        duration_seconds, ncmd = parsed

        await access.ban_user(user.id, duration_seconds=duration_seconds, ncmd=ncmd, reason=reason)
        await interaction.response.send_message(content=f"Banned {user.mention} from using circlechiffon.", ephemeral=True)

    @app_commands.command(name="cc-unban", description="Unban a user from using the bot. (Owner only)")
    @app_commands.describe(user="The user to unban")
    async def unban(self, interaction: discord.Interaction, user: discord.User):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-unban"):
            return
        if not access.is_owner(interaction.user.id):
            await interaction.response.send_message(content="You don't have permission to use this command.", ephemeral=True)
            return

        if await access.unban_user(user.id):
            await interaction.response.send_message(content=f"Unbanned {user.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"{user.mention} is not banned.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
