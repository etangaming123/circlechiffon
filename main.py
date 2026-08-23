print("Loading modules...")
import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, badge_emojis
from config import config
from circlechiffon.cogs import COG_LIST
from circlechiffon.database import engine as db_engine
from circlechiffon.songdata.catalog import get_catalog

intents = discord.Intents.default()


class CircleChiffon(commands.Bot):
    async def setup_hook(self):
        db_engine.init_engine(config.db_path)
        await db_engine.create_all()

        get_catalog()  # load the song catalog once at startup

        for item in COG_LIST:
            try:
                await self.load_extension(f"circlechiffon.cogs.{item}")
                print(f"Loaded cog {item}")
            except Exception as e:
                print(f"Failed to load cog {item}: {e}")

        await badge_emojis.load(self)


bot = CircleChiffon(command_prefix="!", intents=intents)
bot.tree.allowed_installs = app_commands.AppInstallationType(guild=True, user=True)
bot.tree.allowed_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        print("Syncing commands...")
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print("Bot is up and running!")


@bot.tree.command(name="cc-ping", description="Ping the bot")
async def ping(interaction: discord.Interaction):
    if not await access.handle_command_access(interaction, interaction.user.id, "cc-ping", access.DEFAULT_COOLDOWN):
        return
    await interaction.response.defer()
    await interaction.edit_original_response(content=f"Pong! [{round(bot.latency * 1000)}ms]")


async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # Bot-wide safety net: if a command's own error handling still lets
    # something through, make sure the interaction never ends up blank.
    print(f"Unhandled app command error in {interaction.command}: {type(error).__name__}: {error}")
    message = f"Something went wrong: {type(error).__name__}: {error}"
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(content=message, ephemeral=True)
    except discord.HTTPException:
        pass


bot.tree.on_error = on_tree_error


def main():
    try:
        bot.run(config.token)
    except discord.LoginFailure:
        print("Invalid bot token. Make sure 'token' is set correctly in config.json.")
    except discord.PrivilegedIntentsRequired:
        print("A privileged intent is required but not enabled for this bot in the Discord developer portal.")


if __name__ == "__main__":
    main()
