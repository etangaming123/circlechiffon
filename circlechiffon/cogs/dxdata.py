import asyncio

from discord.ext import commands, tasks

import update_dxdata
from circlechiffon.adapters.maimai_site import version_logo
from circlechiffon.songdata.catalog import get_catalog, reload_catalog
from config import config

CHECK_EVERY_HOURS = 12


class DxdataCog(commands.Cog):
    """Keeps data/dxdata.json (constants, versions, new songs) current without a restart."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if config.auto_update_dxdata:
            self.refresh_dxdata.start()

    async def cog_unload(self) -> None:
        self.refresh_dxdata.cancel()

    @tasks.loop(hours=CHECK_EVERY_HOURS)
    async def refresh_dxdata(self) -> None:
        # Any failure (network, upstream shape change, sanity check) leaves the
        # current file and in-memory catalog untouched; the next tick retries.
        try:
            changed, summary = await asyncio.to_thread(update_dxdata.sync)
            if not changed:
                return
            before = get_catalog().current_version
            fresh = await asyncio.to_thread(reload_catalog)
            print(f"dxdata refreshed: {summary}")
            if fresh.current_version != before:
                print(f"INTL version changed: {before} -> {fresh.current_version}")
                version_logo.invalidate()
        except Exception as e:
            print(f"dxdata auto-update skipped: {type(e).__name__}: {e}")

    @refresh_dxdata.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(DxdataCog(bot))
