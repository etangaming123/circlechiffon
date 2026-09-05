import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, user_templates
from circlechiffon.renderers import sample_data
from circlechiffon.renderers.b50 import render_b50, render_b50_template
from circlechiffon.renderers.profile import render_profile_core, render_profile_core_template

_TYPE_CHOICES = [
    app_commands.Choice(name="Best 50 (/cc-best)", value="b50"),
    app_commands.Choice(name="Profile - Core (/cc-profile)", value="profile_core"),
]

_GUIDE_GENERATORS = {
    "b50": render_b50_template,
    "profile_core": render_profile_core_template,
}


async def _render_sample(render_type: str, template_bytes: bytes | None, output: io.BytesIO) -> None:
    """Renders `render_type` with sample_data's fixed fake profile, so a
    candidate template can be checked without needing a live linked
    account. Synchronous Pillow work - call via asyncio.to_thread()."""
    profile = sample_data.build_sample_profile()
    if render_type == "b50":
        render_b50(
            player_name=profile.display_name,
            rating=profile.rating,
            icon_bytes=sample_data.build_sample_icon_bytes(),
            rating_badge_bytes=sample_data.build_sample_rating_badge_bytes(),
            result=sample_data.build_sample_best50(),
            jackets_by_title=sample_data.build_sample_jackets_by_title(),
            badge_icons=sample_data.build_sample_badge_icons(),
            template_bytes=template_bytes,
            output=output,
        )
    else:
        render_profile_core(
            profile=profile,
            icon_bytes=sample_data.build_sample_icon_bytes(),
            course_rank_bytes=None,
            class_rank_bytes=None,
            rating_badge_bytes=sample_data.build_sample_rating_badge_bytes(),
            badge_icons=sample_data.build_sample_badge_icons(),
            template_bytes=template_bytes,
            output=output,
        )


class TemplatesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-template-grant", description="Allow a user to upload custom b50/profile templates. (Owner only)")
    @app_commands.describe(user="The user to grant template-upload access to")
    async def grant(self, interaction: discord.Interaction, user: discord.User):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-grant"):
            return
        if not access.is_owner(interaction.user.id):
            await interaction.response.send_message(content="You don't have permission to use this command.", ephemeral=True)
            return
        await user_templates.grant_access(user.id, interaction.user.id)
        await interaction.response.send_message(content=f"Granted {user.mention} template-upload access.", ephemeral=True)

    @app_commands.command(name="cc-template-revoke", description="Revoke a user's custom template access. (Owner only)")
    @app_commands.describe(user="The user to revoke template-upload access from")
    async def revoke(self, interaction: discord.Interaction, user: discord.User):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-revoke"):
            return
        if not access.is_owner(interaction.user.id):
            await interaction.response.send_message(content="You don't have permission to use this command.", ephemeral=True)
            return
        if await user_templates.revoke_access(user.id):
            await interaction.response.send_message(content=f"Revoked {user.mention}'s template-upload access.", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"{user.mention} didn't have template-upload access.", ephemeral=True)

    @app_commands.command(name="cc-template-upload", description="Upload a custom background template for one of your renders")
    @app_commands.describe(type="Which render this template is for", file="A PNG/JPEG/WEBP image")
    @app_commands.choices(type=_TYPE_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def upload(self, interaction: discord.Interaction, type: app_commands.Choice[str], file: discord.Attachment):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-upload", access.DEFAULT_COOLDOWN):
            return
        if not await user_templates.has_template_access(interaction.user.id):
            await interaction.response.send_message(
                content="You don't have template-upload access. Ask the bot owner for `/cc-template-grant`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        raw_bytes = await file.read()
        try:
            await user_templates.save_template(interaction.user.id, type.value, raw_bytes)
        except user_templates.TemplateValidationError as e:
            await interaction.edit_original_response(content=str(e))
            return
        await interaction.edit_original_response(
            content=f"Uploaded your custom **{type.name}** template. Run `/cc-template-preview` to check it, or the real command to use it."
        )

    @app_commands.command(name="cc-template-download", description="Download your active custom template, or the blank layout guide")
    @app_commands.describe(type="Which render to download for", guide="Download the blank labeled layout guide instead of your active template")
    @app_commands.choices(type=_TYPE_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def download(self, interaction: discord.Interaction, type: app_commands.Choice[str], guide: bool = False):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-download"):
            return
        if not await user_templates.has_template_access(interaction.user.id):
            await interaction.response.send_message(
                content="You don't have template-upload access. Ask the bot owner for `/cc-template-grant`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        current = None if guide else await user_templates.load_template(interaction.user.id, type.value)
        if current is not None:
            await interaction.edit_original_response(
                content=f"Your active **{type.name}** template:",
                attachments=[discord.File(io.BytesIO(current), filename=f"{type.value}-template.png")],
            )
            return

        buf = io.BytesIO()
        await asyncio.to_thread(_GUIDE_GENERATORS[type.value], buf)
        note = "" if guide else " (you don't have a custom one uploaded yet)"
        await interaction.edit_original_response(
            content=f"Blank layout guide for **{type.name}**{note} - design a background around these boxes, then `/cc-template-upload` it:",
            attachments=[discord.File(buf, filename=f"{type.value}-guide.png")],
        )

    @app_commands.command(name="cc-template-reset", description="Remove your custom template and revert to the default")
    @app_commands.describe(type="Which render to reset")
    @app_commands.choices(type=_TYPE_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def reset(self, interaction: discord.Interaction, type: app_commands.Choice[str]):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-reset"):
            return
        if not await user_templates.has_template_access(interaction.user.id):
            await interaction.response.send_message(
                content="You don't have template-upload access. Ask the bot owner for `/cc-template-grant`.", ephemeral=True
            )
            return
        if await user_templates.delete_template(interaction.user.id, type.value):
            await interaction.response.send_message(content=f"Reset your **{type.name}** template to the default.", ephemeral=True)
        else:
            await interaction.response.send_message(content=f"You don't have a custom **{type.name}** template set.", ephemeral=True)

    @app_commands.command(name="cc-template-list", description="Show which of your renders currently have a custom template")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def list_templates(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-list"):
            return
        has_access = await user_templates.has_template_access(interaction.user.id)
        if not has_access:
            await interaction.response.send_message(
                content="You don't have template-upload access. Ask the bot owner for `/cc-template-grant`.", ephemeral=True
            )
            return
        active = await user_templates.list_templates(interaction.user.id)
        lines = [
            f"**{choice.name}**: {'custom template active' if choice.value in active else 'default'}"
            for choice in _TYPE_CHOICES
        ]
        await interaction.response.send_message(content="\n".join(lines), ephemeral=True)

    @app_commands.command(name="cc-template-preview", description="Render a sample profile with a template, without needing a linked account")
    @app_commands.describe(
        type="Which render to preview",
        file="Test this file without saving it (optional - defaults to your active template)",
    )
    @app_commands.choices(type=_TYPE_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preview(self, interaction: discord.Interaction, type: app_commands.Choice[str], file: discord.Attachment | None = None):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-preview", access.DEFAULT_COOLDOWN):
            return
        if not await user_templates.has_template_access(interaction.user.id):
            await interaction.response.send_message(
                content="You don't have template-upload access. Ask the bot owner for `/cc-template-grant`.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        if file is not None:
            raw_bytes = await file.read()
            try:
                template_bytes = user_templates.sanitize_upload(raw_bytes)
            except user_templates.TemplateValidationError as e:
                await interaction.edit_original_response(content=str(e))
                return
        else:
            template_bytes = await user_templates.load_template(interaction.user.id, type.value)

        buf = io.BytesIO()
        await asyncio.to_thread(_render_sample, type.value, template_bytes, buf)
        await interaction.edit_original_response(
            content=f"Sample-profile preview of **{type.name}**{' using the attached file' if file else ''}:",
            attachments=[discord.File(buf, filename=f"{type.value}-preview.png")],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplatesCog(bot))
