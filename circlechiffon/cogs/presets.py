import asyncio
import json

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete, select

from circlechiffon import access, accounts
from circlechiffon.adapters.maimai_net import urls
from circlechiffon.adapters.maimai_net.errors import ItemNotOwned, MaimaiNetError, SessionExpired
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import CollectionPreset

MAX_SLOTS = 5
PARTS = urls.COLLECTION_SLOT_ORDER


def _part_label(part: str) -> str:
    return urls.COLLECTION_SLOTS[part]["label"]


def _item_label(entry: dict | None) -> str:
    if not entry:
        return "-"
    label = entry.get("label") or entry.get("key") or "-"
    # a stray backtick in an item name would break out of the code span
    return f"`{label.replace('`', '')}`"


def _preset_summary(preset: CollectionPreset) -> str:
    items = json.loads(preset.items)
    return " · ".join(f"{_part_label(p)}: {_item_label(items.get(p))}" for p in PARTS)


async def _get_preset(discord_id: int, slot: int) -> CollectionPreset | None:
    async with db_engine.session() as session:
        return await session.get(CollectionPreset, (discord_id, slot))


async def _all_presets(discord_id: int) -> list[CollectionPreset]:
    async with db_engine.session() as session:
        result = await session.execute(
            select(CollectionPreset)
            .where(CollectionPreset.discord_id == discord_id)
            .order_by(CollectionPreset.slot)
        )
        return list(result.scalars())


async def _store_preset(discord_id: int, slot: int, name: str | None, items: dict) -> None:
    async with db_engine.session() as session:
        preset = await session.get(CollectionPreset, (discord_id, slot))
        if preset is None:
            preset = CollectionPreset(discord_id=discord_id, slot=slot, name=name, items=json.dumps(items))
            session.add(preset)
        else:
            preset.items = json.dumps(items)
            if name is not None:
                preset.name = name
        await session.commit()


async def _drop_preset(discord_id: int, slot: int) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(CollectionPreset).where(
                CollectionPreset.discord_id == discord_id, CollectionPreset.slot == slot
            )
        )
        await session.commit()
        return result.rowcount > 0


class PresetsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="cc-preset-save",
        description="Save your currently equipped icon, name plate, frame and title to a preset slot",
    )
    @app_commands.describe(slot="Preset slot, 1-5", name="Optional name for this preset")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preset_save(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Range[int, 1, MAX_SLOTS],
        name: app_commands.Range[str, 1, 32] | None = None,
    ):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-preset-save", access.MAIMAI_NET_COOLDOWN
        ):
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="Reading your equipped items...")

        async def fetch(client):
            equipped = await asyncio.gather(*(client.get_equipped_collection_item(p) for p in PARTS))
            return dict(zip(PARTS, equipped))

        try:
            equipped = await accounts.with_client(
                interaction.user.id, fetch, on_retry=accounts.default_retry_notice(interaction)
            )

            items = {
                part: {"key": item.key, "label": item.label}
                for part, item in equipped.items()
                if item is not None
            }
            if not items:
                await interaction.edit_original_response(
                    content="Couldn't read any equipped items from maimai DX NET, so nothing was saved."
                )
                return

            previous = await _get_preset(interaction.user.id, slot)
            await _store_preset(interaction.user.id, slot, name, items)

            lines = [f"{_part_label(p)}: {_item_label(items.get(p))}" for p in PARTS]
            title = f"Saved to preset {slot}" + (f" ({name})" if name else "")
            if previous is not None:
                title += f", replacing: {_preset_summary(previous)}"
            missing = [_part_label(p) for p in PARTS if p not in items]
            if missing:
                lines.append(f"(not saved, couldn't read: {', '.join(missing)})")
            await interaction.edit_original_response(content=title + "\n" + "\n".join(lines))
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't read your equipped items: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't save that preset: unexpected error ({type(e).__name__}: {e})"
            )

    @app_commands.command(
        name="cc-preset-load", description="Equip everything saved in one of your preset slots"
    )
    @app_commands.describe(slot="Preset slot, 1-5")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preset_load(
        self, interaction: discord.Interaction, slot: app_commands.Range[int, 1, MAX_SLOTS]
    ):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-preset-load", access.COLLECTION_WRITE_COOLDOWN
        ):
            return

        preset = await _get_preset(interaction.user.id, slot)
        if preset is None:
            access.clear_cooldown(interaction.user.id, "cc-preset-load")
            await interaction.response.send_message(
                f"Preset {slot} is empty. Save one with `/cc-preset-save slot:{slot}`.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="Equipping...")
        saved = json.loads(preset.items)

        async def apply(client):
            # Sequential on purpose: each set re-fetches its page for a fresh
            # single-use idx, and concurrent writes on one session invite the
            # eviction DX NET does when a session is re-minted mid-flight.
            results = {}
            for part in PARTS:
                entry = saved.get(part)
                if not entry:
                    continue
                try:
                    results[part] = (await client.set_collection_item(part, entry["key"]), entry)
                except ItemNotOwned:
                    results[part] = ("missing", entry)
            return results

        try:
            results = await accounts.with_client(
                interaction.user.id, apply, on_retry=accounts.default_retry_notice(interaction)
            )

            words = {
                "applied": "equipped",
                "unchanged": "already equipped",
                "missing": "not in your collection",
            }
            lines = [
                f"{_part_label(p)}: {_item_label(entry)} - {words[outcome]}"
                for p, (outcome, entry) in ((p, results[p]) for p in PARTS if p in results)
            ]
            header = f"Loaded preset {slot}" + (f" ({preset.name})" if preset.name else "")
            await interaction.edit_original_response(content=header + "\n" + "\n".join(lines))
        except accounts.NotLinked:
            await interaction.edit_original_response(
                content="You haven't linked a maimai DX NET account yet. Run `/cc-login` first."
            )
        except SessionExpired as e:
            await interaction.edit_original_response(content=str(e))
        except MaimaiNetError as e:
            await interaction.edit_original_response(content=f"Couldn't equip that preset: {e}")
        except Exception as e:
            await interaction.edit_original_response(
                content=f"Couldn't load that preset: unexpected error ({type(e).__name__}: {e})"
            )

    @app_commands.command(name="cc-preset-list", description="List your saved collection presets")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preset_list(self, interaction: discord.Interaction):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-preset-list", access.DEFAULT_COOLDOWN
        ):
            return

        presets = {p.slot: p for p in await _all_presets(interaction.user.id)}
        embed = discord.Embed(title="Collection presets", color=discord.Color.blurple())
        for slot in range(1, MAX_SLOTS + 1):
            preset = presets.get(slot)
            if preset is None:
                embed.add_field(name=f"Slot {slot}", value="*empty*", inline=False)
            else:
                name = f"Slot {slot}" + (f" - {preset.name}" if preset.name else "")
                embed.add_field(name=name, value=_preset_summary(preset), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cc-preset-delete", description="Clear one of your preset slots")
    @app_commands.describe(slot="Preset slot, 1-5")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preset_delete(
        self, interaction: discord.Interaction, slot: app_commands.Range[int, 1, MAX_SLOTS]
    ):
        if not await access.handle_command_access(
            interaction, interaction.user.id, "cc-preset-delete", access.DEFAULT_COOLDOWN
        ):
            return
        deleted = await _drop_preset(interaction.user.id, slot)
        await interaction.response.send_message(
            f"Preset {slot} cleared." if deleted else f"Preset {slot} was already empty.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PresetsCog(bot))
