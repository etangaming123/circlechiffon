"""
/cc-login and /cc-logout.

Credentials are collected via a discord.ui.Modal (never a plain command
argument, so they never appear in chat/interaction logs). By default they're
used once to perform the SEGA ID gateway login and then discarded - only the
resulting session cookie is persisted (encrypted).

/cc-login also has an opt-in `remember_password` option: if set, the
SEGA ID username/password are ALSO stored (encrypted), after an explicit
warning + confirmation, so the bot can silently re-login when the session
cookie expires instead of requiring /cc-login again every time.
"""

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts
from circlechiffon.adapters.maimai_net.client import MaimaiNetClient
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, TotpRequired

REMEMBER_PASSWORD_WARNING = (
    "**Warning: this stores your SEGA ID password.**\n\n"
    "Choosing to remember your password means I will keep your SEGA ID username "
    "and password, encrypted at rest, so I can automatically log you back in "
    "whenever your maimai DX NET session expires, without you needing to run "
    "`/cc-login` again.\n\n"
    "This is **less secure** than the default option (which only ever stores "
    "a session cookie, never your password). If this bot's database or "
    "encryption key were ever compromised, a stored password could be "
    "exposed. Only continue if you're comfortable with that tradeoff.\n\n"
    "You can remove everything that's stored at any time with "
    "`/cc-logout`. Do you want to continue?"
)


class SegaLoginModal(discord.ui.Modal, title="Link your maimai DX NET account"):
    sega_id = discord.ui.TextInput(label="SEGA ID username", required=True, max_length=256)
    password = discord.ui.TextInput(label="SEGA ID password", required=True, style=discord.TextStyle.short, max_length=128)
    totp = discord.ui.TextInput(
        label="2FA code (only if you have TOTP enabled)",
        required=False,
        max_length=16,
    )

    def __init__(self, remember_password: bool = False):
        super().__init__()
        self.remember_password = remember_password

    async def on_submit(self, interaction: discord.Interaction):
        # thinking=True is required here: defer() on a modal_submit interaction
        # defaults to InteractionResponseType.deferred_message_update (meant for
        # a modal opened from a message component, to edit that message) unless
        # thinking=True forces deferred_channel_message instead. Since this modal
        # is opened from a slash command (or a button, in the remember_password
        # flow), there's no message to "update" - without thinking=True the
        # defer resolves to nothing visible.
        await interaction.response.defer(ephemeral=True, thinking=True)

        client = MaimaiNetClient()
        try:
            try:
                await client.login(self.sega_id.value, self.password.value, self.totp.value or None)
            except TotpRequired:
                await interaction.edit_original_response(
                    content=(
                        "Login failed: this account has two-factor authentication enabled. "
                        "Run `/cc-login` again and fill in the 2FA code field."
                    )
                )
                return
            except MaimaiNetError as e:
                await interaction.edit_original_response(content=f"Login failed: {e}")
                return

            profile = None
            try:
                profile = await client.get_profile()
            except MaimaiNetError:
                pass  # login succeeded even if we couldn't immediately read the profile

            await accounts.save_session(
                interaction.user.id,
                client,
                region="intl",
                display_name=profile.display_name if profile else None,
            )

            if self.remember_password:
                await accounts.save_credentials(interaction.user.id, self.sega_id.value, self.password.value)
        except Exception as e:
            # Catch-all so an unexpected failure (network error, parsing bug,
            # DB error, ...) can never leave the interaction without a reply -
            # without this, an uncaught exception here just leaves the
            # deferred "thinking..." response blank forever.
            await interaction.edit_original_response(
                content=f"Login failed: unexpected error ({type(e).__name__}: {e})"
            )
            return
        finally:
            await client.close()

        credential_note = (
            " Your SEGA ID password has been stored (encrypted) so I can automatically log you "
            "back in when your session expires - run `/cc-logout` any time to remove it."
            if self.remember_password
            else " Your SEGA ID password was not stored - only a session cookie was, so you may "
            "need to `/cc-login` again if your session expires."
        )
        await interaction.edit_original_response(
            content=(
                "Login successful! Welcome, "
                + (f"**{profile.display_name}**!" if profile else "!")
                + credential_note
            )
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        # Last-resort safety net in case something fails before/outside the
        # try block above (e.g. discord.py itself raises while dispatching).
        message = f"Login failed: unexpected error ({type(error).__name__}: {error})"
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(content=message, ephemeral=True)


class RememberPasswordWarningView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.message: discord.InteractionMessage | None = None

    @discord.ui.button(label="Yes, store my password", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SegaLoginModal(remember_password=True))
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Cancelled - your password will not be stored. Run `/cc-login` again if you'd like to link "
            "your account with just a session cookie instead.",
            view=None,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AuthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-login", description="Link your maimai DX NET account")
    @app_commands.describe(
        remember_password=(
            "Also store your SEGA ID password (encrypted) so I can auto re-login when your session "
            "expires. Use at your own risk."
        )
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def login(self, interaction: discord.Interaction, remember_password: bool = False):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-login", access.MAIMAI_NET_COOLDOWN):
            return
        if remember_password:
            view = RememberPasswordWarningView()
            await interaction.response.send_message(
                content=REMEMBER_PASSWORD_WARNING,
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
        else:
            await interaction.response.send_modal(SegaLoginModal(remember_password=False))

    @app_commands.command(name="cc-logout", description="Unlink your maimai DX NET account from this bot")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def logout(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-logout", access.DEFAULT_COOLDOWN):
            return
        await interaction.response.defer(ephemeral=True)
        removed = await accounts.delete_account(interaction.user.id)
        if removed:
            await interaction.edit_original_response(
                content="Unlinked your maimai DX NET account. Any stored session cookie and password (if you opted "
                "into remembering it) have both been deleted."
            )
        else:
            await interaction.edit_original_response(content="You don't have a linked maimai DX NET account.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AuthCog(bot))
