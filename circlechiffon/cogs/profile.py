import asyncio
import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from circlechiffon import access, accounts, user_templates
from circlechiffon.adapters.maimai_net.badge_icons import get_all_badge_icons
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired
from circlechiffon.adapters.maimai_net.parser import parse_profile, parse_profile_extras
from circlechiffon.renderers.display import render_display
from circlechiffon.renderers.profile import render_profile_core, render_profile_extras

_PROFILE_VIEW_CHOICES = [
    app_commands.Choice(name="Core (rating, rank, clear counts)", value="core"),
    app_commands.Choice(name="Extra (CP, mile, missions, tickets)", value="extra"),
]


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cc-profile", description="View your linked maimai DX NET profile")
    @app_commands.choices(view=_PROFILE_VIEW_CHOICES)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def profile(self, interaction: discord.Interaction, view: app_commands.Choice[str] | None = None):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-profile", access.MAIMAI_NET_COOLDOWN):
            return
        view_value = view.value if view is not None else "core"
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            # both views come from the same page - fetch it once regardless
            # of which one was requested.
            html = await client.get_profile_page_html()
            profile = parse_profile(html)

            async def image_or_none(url):
                # maimaidx-eng.com serves these with a conflicting/"private"
                # Cache-Control header (confirmed live this session), which
                # makes Discord's own embed-image unfurl silently fail for a
                # bare url= reference - fetch the bytes ourselves through
                # the account's session and attach them as files instead,
                # same pattern already used for dxrating jackets everywhere
                # else.
                return await client.get_image_bytes(url) if url else None

            if view_value == "extra":
                extras = parse_profile_extras(html)
                icon_bytes, ticket_bytes = await asyncio.gather(
                    image_or_none(profile.icon_url),
                    asyncio.gather(*(image_or_none(t.image_url) for t in extras.tickets)),
                )
                return "extra", profile, extras, icon_bytes, list(ticket_bytes)

            icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes, badge_icons = await asyncio.gather(
                image_or_none(profile.icon_url),
                image_or_none(profile.course_rank_url),
                image_or_none(profile.class_rank_url),
                image_or_none(profile.rating_badge_url),
                get_all_badge_icons(),
            )
            return "core", profile, icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes, badge_icons

        try:
            # with_client() transparently retries once via a silent re-login
            # if the session had expired and the user opted into
            # remember_password on /cc-login.
            result = await accounts.with_client(interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction))
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
            return
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
            return
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your profile: {e}")
            return
        except Exception as e:
            # Catch-all so an unexpected failure can never leave the
            # interaction without a reply.
            await interaction.edit_original_response(
                content=f"Couldn't fetch your profile: unexpected error ({type(e).__name__}: {e})"
            )
            return

        await interaction.edit_original_response(content="Rendering...")
        buf = io.BytesIO()
        if result[0] == "extra":
            _, profile, extras, icon_bytes, ticket_bytes = result
            await asyncio.to_thread(
                render_profile_extras,
                profile=profile,
                extras=extras,
                icon_bytes=icon_bytes,
                ticket_image_bytes=ticket_bytes,
                output=buf,
            )
        else:
            _, profile, icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes, badge_icons = result
            template_bytes = await user_templates.load_template(interaction.user.id, "profile_core")
            await asyncio.to_thread(
                render_profile_core,
                profile=profile,
                icon_bytes=icon_bytes,
                course_rank_bytes=course_rank_bytes,
                class_rank_bytes=class_rank_bytes,
                rating_badge_bytes=rating_badge_bytes,
                badge_icons=badge_icons,
                template_bytes=template_bytes,
                output=buf,
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        await interaction.edit_original_response(
            content=None,
            attachments=[discord.File(buf, filename=f"profile-{view_value}-{profile.display_name}-{timestamp}.png")],
        )

    @app_commands.command(name="cc-display", description="Render your profile as it would appear on the maimai DX cab display (not accurate)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def display(self, interaction: discord.Interaction):
        if not await access.handle_command_access(interaction, interaction.user.id, "cc-display", access.MAIMAI_NET_COOLDOWN):
            return
        await interaction.response.defer()
        await interaction.edit_original_response(content="Getting data...")

        async def fetch(client):
            profile = await client.get_profile()
            circle = None
            try:
                circle = await client.get_circle()
            except MaimaiNetError:
                pass  # not being in a Circle (or a flaky fetch) shouldn't block the card

            async def image_or_none(url):
                return await client.get_image_bytes(url) if url else None

            async def equipped_image_or_none(url_coro):
                # these three live on separate collection pages (equipped
                # nameplate/frame/leading tour member) - best-effort, skipped
                # individually on failure rather than blocking the card.
                try:
                    return await image_or_none(await url_coro)
                except MaimaiNetError:
                    return None

            # All of these are independent of each other, so fetch them
            # concurrently instead of paying round-trip latency 9x
            # sequentially. title_plate/circle_color are the real SEGA art
            # for the title bar and the circle's name banner - both are
            # colored by tier/class, so the card uses the actual asset
            # rather than a palette that would drift (see render_display).
            (
                icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes,
                nameplate_bytes, frame_bytes, tour_member_bytes,
                title_plate_bytes, circle_color_bytes,
            ) = await asyncio.gather(
                image_or_none(profile.icon_url),
                image_or_none(profile.course_rank_url),
                image_or_none(profile.class_rank_url),
                image_or_none(profile.rating_badge_url),
                equipped_image_or_none(client.get_equipped_nameplate_url()),
                equipped_image_or_none(client.get_equipped_frame_url()),
                equipped_image_or_none(client.get_leader_tour_member_url()),
                image_or_none(profile.title_plate_url),
                image_or_none(circle.color_url if circle is not None else None),
            )

            return (
                profile, circle, icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes,
                nameplate_bytes, frame_bytes, tour_member_bytes,
                title_plate_bytes, circle_color_bytes,
            )

        try:
            (
                profile, circle, icon_bytes, course_rank_bytes, class_rank_bytes, rating_badge_bytes,
                nameplate_bytes, frame_bytes, tour_member_bytes,
                title_plate_bytes, circle_color_bytes,
            ) = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            await interaction.edit_original_response(content="Rendering...")
            buf = io.BytesIO()
            await asyncio.to_thread(
                render_display,
                profile=profile,
                circle=circle,
                icon_bytes=icon_bytes,
                course_rank_bytes=course_rank_bytes,
                class_rank_bytes=class_rank_bytes,
                rating_badge_bytes=rating_badge_bytes,
                nameplate_bytes=nameplate_bytes,
                frame_bytes=frame_bytes,
                tour_member_bytes=tour_member_bytes,
                title_plate_bytes=title_plate_bytes,
                circle_color_bytes=circle_color_bytes,
                output=buf,
            )

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            await interaction.edit_original_response(
                content="This command is still being tweaked! Renders are not accurate to the actual cab display.",
                attachments=[discord.File(buf, filename=f"display-{profile.display_name}-{timestamp}.png")],
            )
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't fetch your profile: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't render your profile card: unexpected error ({type(e).__name__}: {e})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCog(bot))
