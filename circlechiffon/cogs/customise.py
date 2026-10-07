import asyncio
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access
from circlechiffon.customisation import store
from circlechiffon.customisation.preview import render_preview
from circlechiffon.customisation.validate import MAX_IMAGE_BYTES, MAX_LAYOUT_BYTES, TemplateError, normalise_image, validate_layout
from circlechiffon.renderers.guides import TEMPLATE_KINDS, render_guide

EDITOR_URL = "https://cc.etangaming.xyz/template-editor.html"
DOCS_URL = "https://github.com/etangaming123/circlechiffon/blob/main/docs/customisation.md"
# files bigger than this aren't sent back by /cc-template-get - Discord's
# default upload cap is 10 MB per message.
_SEND_LIMIT = 8 * 1024 * 1024

_KIND_CHOICES = [app_commands.Choice(name=spec.title, value=key) for key, spec in TEMPLATE_KINDS.items()]
_PART_CHOICES = [
    app_commands.Choice(name="Everything", value="all"),
    app_commands.Choice(name="Base image", value="base"),
    app_commands.Choice(name="Top image", value="top"),
    app_commands.Choice(name="Layout", value="layout"),
]

_NOT_WHITELISTED = "Custom templates are limited to whitelisted users - ask the bot owner if you'd like access."


class CustomiseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _require_whitelist(self, interaction: discord.Interaction) -> bool:
        if await store.is_whitelisted(interaction.user.id):
            return True
        await interaction.response.send_message(content=_NOT_WHITELISTED, ephemeral=True)
        return False

    @app_commands.command(name="cc-template-upload", description="Upload a custom template for one of your renders (whitelisted users)")
    @app_commands.describe(
        kind="Which render this template is for",
        base="Image drawn underneath the render",
        top="Image drawn over the finished render (use transparency)",
        layout="layout.json exported from the web template editor",
    )
    @app_commands.choices(kind=_KIND_CHOICES)
    async def upload(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        base: discord.Attachment | None = None,
        top: discord.Attachment | None = None,
        layout: discord.Attachment | None = None,
    ):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-upload", access.DEFAULT_COOLDOWN):
            return
        if not await self._require_whitelist(interaction):
            return
        attachments = {part: a for part, a in (("base", base), ("top", top), ("layout", layout)) if a is not None}
        if not attachments:
            await interaction.response.send_message(
                content=f"Attach at least one of `base`, `top` or `layout`. Build a layout with the editor: {EDITOR_URL}",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        # check every part before saving any, so a bad file can't leave a
        # half-updated template behind
        processed: dict[str, bytes] = {}
        warnings: list[str] = []
        try:
            for part, attachment in attachments.items():
                limit = MAX_LAYOUT_BYTES if part == "layout" else MAX_IMAGE_BYTES
                if attachment.size > limit:
                    raise TemplateError(f"`{part}` is too large ({attachment.size // 1024} KB, max {limit // 1024} KB).")
                data = await attachment.read()
                if part == "layout":
                    clean, part_warnings = await asyncio.to_thread(validate_layout, data, kind.value)
                    processed[part] = json.dumps(clean, indent=1).encode()
                else:
                    processed[part], part_warnings = await asyncio.to_thread(normalise_image, data, kind.value)
                warnings += [f"`{part}`: {w}" for w in part_warnings]
        except TemplateError as e:
            await interaction.edit_original_response(content=f"Nothing was saved.\n{e}")
            return
        except discord.HTTPException as e:
            await interaction.edit_original_response(content=f"Nothing was saved - couldn't download an attachment ({e}).")
            return

        for part, data in processed.items():
            await asyncio.to_thread(store.save_part, interaction.user.id, kind.value, part, data)
        lines = [f"Saved {', '.join(f'`{p}`' for p in processed)} for **{kind.name}**."]
        if warnings:
            lines += ["", "Warnings:", *(f"- {w}" for w in warnings[:10])]
        lines += ["", "Check it with `/cc-template-preview`."]
        await interaction.edit_original_response(content="\n".join(lines))

    @app_commands.command(name="cc-template-remove", description="Remove your custom template (or part of it) for a render")
    @app_commands.describe(kind="Which render's template", part="Which part to remove (default: everything)")
    @app_commands.choices(kind=_KIND_CHOICES, part=_PART_CHOICES)
    async def remove(self, interaction: discord.Interaction, kind: app_commands.Choice[str], part: app_commands.Choice[str] | None = None):
        # no whitelist check - someone taken off the whitelist can still
        # clear out what they uploaded
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-remove", access.DEFAULT_COOLDOWN):
            return
        parts = list(store.PART_FILES) if part is None or part.value == "all" else [part.value]
        removed = await asyncio.to_thread(store.delete_parts, interaction.user.id, kind.value, parts)
        if removed:
            content = f"Removed {', '.join(f'`{p}`' for p in removed)} from your **{kind.name}** template."
        else:
            content = f"You had nothing to remove for **{kind.name}**."
        await interaction.response.send_message(content=content, ephemeral=True)

    @app_commands.command(name="cc-template-get", description="Download your current template for a render, plus a layout guide")
    @app_commands.describe(kind="Which render's template")
    @app_commands.choices(kind=_KIND_CHOICES)
    async def get(self, interaction: discord.Interaction, kind: app_commands.Choice[str]):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-get", access.DEFAULT_COOLDOWN):
            return
        if not await self._require_whitelist(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        def collect():
            parts = {part: store.read_part(interaction.user.id, kind.value, part) for part in store.PART_FILES}
            layout = json.loads(parts["layout"]) if parts["layout"] else None
            if parts["layout"] is None:
                # nothing uploaded yet - hand back the defaults as a
                # starting point for the editor's Import button
                parts["layout"] = json.dumps(TEMPLATE_KINDS[kind.value].default_layout(), indent=1).encode()
            guide = io.BytesIO()
            render_guide(kind.value, guide, layout)
            return parts, guide.getvalue(), layout is not None

        parts, guide, has_layout = await asyncio.to_thread(collect)
        files = [discord.File(io.BytesIO(guide), filename=f"{kind.value}-guide.png")]
        skipped = []
        for part, data in parts.items():
            if data is None:
                continue
            if len(data) > _SEND_LIMIT:
                skipped.append(part)
                continue
            filename = f"{kind.value}-{store.PART_FILES[part]}" if (part != "layout" or has_layout) else f"{kind.value}-default-layout.json"
            files.append(discord.File(io.BytesIO(data), filename=filename))

        uploaded = [p for p in store.PART_FILES if p != "layout" and parts[p] is not None] + (["layout"] if has_layout else [])
        lines = [
            f"**{kind.name}** - uploaded: {', '.join(f'`{p}`' for p in uploaded) or 'nothing yet'}.",
            f"The guide shows where every element goes with your current layout. Edit layouts at {EDITOR_URL}",
            f"-# How templates work: <{DOCS_URL}>",
        ]
        if skipped:
            lines.append(f"-# Too large to send back: {', '.join(skipped)}.")
        await interaction.edit_original_response(content="\n".join(lines), attachments=files)

    @app_commands.command(name="cc-template-preview", description="Render your custom template with sample data (no account needed)")
    @app_commands.describe(kind="Which render to preview", guides="Draw the layout guide boxes over the render")
    @app_commands.choices(kind=_KIND_CHOICES)
    async def preview(self, interaction: discord.Interaction, kind: app_commands.Choice[str], guides: bool = False):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-preview", access.DEFAULT_COOLDOWN):
            return
        if not await self._require_whitelist(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        template = await store.get_user_template(interaction.user.id, kind.value)
        try:
            icon_bytes = await interaction.user.display_avatar.replace(size=256, format="png").read()
        except discord.HTTPException:
            icon_bytes = None
        try:
            buf = await render_preview(kind.value, template, icon_bytes=icon_bytes, guides=guides)
        except Exception as e:
            await interaction.edit_original_response(content=f"Couldn't render the preview: {type(e).__name__}: {e}")
            return
        note = "your template" if template else "the stock look (you have no template uploaded for this yet)"
        await interaction.edit_original_response(
            content=f"**{kind.name}** preview with {note}, using sample data.",
            attachments=[discord.File(buf, filename=f"{kind.value}-preview.png")],
        )

    @app_commands.command(name="cc-template-whitelist", description="Manage who can upload custom templates. (Owner only)")
    @app_commands.describe(action="add, remove or list", user="The user to add or remove")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def whitelist(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.User | None = None):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-template-whitelist"):
            return
        if not access.is_owner(interaction.user.id):
            await interaction.response.send_message(content="You don't have permission to use this command.", ephemeral=True)
            return

        if action.value == "list":
            ids = await store.list_whitelist()
            content = "Template whitelist:\n" + "\n".join(f"- <@{i}> (`{i}`)" for i in ids) if ids else "The template whitelist is empty."
            await interaction.response.send_message(content=content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        if user is None:
            await interaction.response.send_message(content=f"Pick a `user` to {action.value}.", ephemeral=True)
            return
        if action.value == "add":
            added = await store.add_to_whitelist(user.id)
            content = f"Added {user.mention} to the template whitelist." if added else f"{user.mention} is already whitelisted."
        else:
            removed = await store.remove_from_whitelist(user.id)
            if not removed:
                content = f"{user.mention} isn't on the template whitelist."
            elif access.is_owner(user.id):
                content = f"Removed {user.mention} from the template whitelist. The bot owner can always use templates, so nothing changes."
            else:
                content = f"Removed {user.mention} from the template whitelist. Their uploaded templates stay on disk but are no longer used."
        await interaction.response.send_message(content=content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomiseCog(bot))
