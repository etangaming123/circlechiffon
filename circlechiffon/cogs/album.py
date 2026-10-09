import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, embed_colors, leech
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.types import Photo


def _photo_embed(photo: Photo, index: int, total: int, has_image: bool, hide_location: bool) -> discord.Embed:
    embed = discord.Embed(
        title=photo.title or "Untitled",
        color=embed_colors.difficulty_color(photo.difficulty),
    )
    type_name = photo.chart_type.value.upper() if photo.chart_type else None
    diff_name = photo.difficulty.display_name if photo.difficulty else None
    if type_name or diff_name:
        embed.description = " ".join(p for p in (type_name, diff_name) if p)
    # only reference the attachment if a File is actually being sent this
    # render - a dangling attachment:// with no matching file renders as a
    # broken image rather than just omitting the image
    if has_image:
        embed.set_image(url="attachment://photo.jpg")
    else:
        embed.description = (embed.description + "\n" if embed.description else "") + "*(photo failed to load)*"
    footer = f"Photo {index + 1}/{total}"
    if photo.venue and not hide_location:
        footer += f" · {photo.venue}"
    embed.set_footer(text=footer)
    if photo.played_at is not None:
        embed.timestamp = photo.played_at
    return embed


class AlbumView(discord.ui.View):
    """Browses the account's photo album through a dropdown, one photo per
    page - cloned from ScoreToggleView's shape (cogs/score.py), not the
    heavier RecentScoresView, since this is a flat list with no grouping.
    All photo bytes are fetched once up front (there are at most 10,
    confirmed live - the site itself keeps no more, well under a Select's
    25-option cap), so switching never blocks on a network call and can't
    be stranded half populated by a mid-session expiry.

    `image_bytes[i]` is raw bytes, not a discord.File - a File's
    underlying stream is exhausted once sent, so revisiting an
    already-shown page needs a fresh File built from the cached bytes
    each render (same reasoning as RecentScoresView.current_embeds_and_files
    in cogs/records.py)."""

    def __init__(
        self, invoker_id: int, photos: list[Photo], image_bytes: list[bytes | None], hide_location: bool
    ):
        super().__init__(timeout=30)
        self.invoker_id = invoker_id
        self.photos = photos
        self.image_bytes = image_bytes
        self.hide_location = hide_location
        self.index = 0
        self._lock = asyncio.Lock()
        self.message: discord.InteractionMessage | None = None
        self.picker.options = self._build_options()

    def _build_options(self) -> list[discord.SelectOption]:
        options = []
        for i, photo in enumerate(self.photos):
            type_name = photo.chart_type.value.upper() if photo.chart_type else None
            diff_name = photo.difficulty.display_name if photo.difficulty else None
            desc = [p for p in (type_name, diff_name) if p]
            if photo.venue and not self.hide_location:
                desc.append(photo.venue)
            options.append(
                discord.SelectOption(
                    label=f"{i + 1}. {photo.title or 'Untitled'}"[:100],
                    description=" · ".join(desc)[:100] or None,
                    value=str(i),
                    default=i == self.index,
                )
            )
        return options

    def _update_picker(self):
        for opt in self.picker.options:
            opt.default = opt.value == str(self.index)

    def embed_and_file(self) -> tuple[discord.Embed, discord.File | None]:
        raw = self.image_bytes[self.index]
        file = discord.File(io.BytesIO(raw), filename="photo.jpg") if raw else None
        embed = _photo_embed(
            self.photos[self.index],
            self.index,
            len(self.photos),
            has_image=file is not None,
            hide_location=self.hide_location,
        )
        return embed, file

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "This isn't your embed!", ephemeral=True
            )
            return False
        return True

    @discord.ui.select(placeholder="Pick a photo", min_values=1, max_values=1, options=[discord.SelectOption(label="-")])
    async def picker(self, interaction: discord.Interaction, select: discord.ui.Select):
        # Ack immediately: re-uploading the photo as an attachment can take
        # longer than the 3s initial-response window ("App did not respond").
        await interaction.response.defer()
        # Serialise picks and only commit the new index once the edit
        # lands, so a failed/raced edit can't leave the server index out of
        # step with the visible page.
        async with self._lock:
            target = int(select.values[0])
            if target == self.index:
                return
            old = self.index
            self.index = target
            self._update_picker()
            embed, file = self.embed_and_file()
            try:
                await interaction.edit_original_response(
                    embed=embed, view=self, attachments=[file] if file else []
                )
            except Exception:
                self.index = old
                self._update_picker()
                raise

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AlbumCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-album", description="Browse your maimai DX NET photo album")
    @app_commands.describe(hide_location="Hide the arcade/venue name in each photo's footer (default: on)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def album(self, interaction: discord.Interaction, hide_location: bool = True):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-album", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            photos = await client.get_photos()
            image_bytes = await asyncio.gather(*(client.get_image_bytes(p.image_url) for p in photos))
            return photos, list(image_bytes)

        try:
            photos, image_bytes = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )
            if not photos:
                await interaction.edit_original_response(content="No photos in your album yet.")
                return

            view = AlbumView(interaction.user.id, photos, image_bytes, hide_location)
            embed, file = view.embed_and_file()
            message = await interaction.edit_original_response(
                content=None, embed=embed, view=view, attachments=[file] if file else []
            )
            view.message = message
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content=await leech.not_linked_text(interaction.user.id)
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your album: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't fetch your album: unexpected error ({type(e).__name__}: {e})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AlbumCog(bot))
