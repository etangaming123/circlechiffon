"""
Where custom render templates live, and who may use them.

Files: user_templates/<discord_id>/<kind>/{base.png, top.png, layout.json},
anchored to the repo directory (same reasoning as generate_templates'
TEMPLATES_DIR). Everything written here has already been through
validate.py. Whitelist: the template_whitelist table, plus the bot owner
implicitly.

Renderers never see this module - cogs call `get_user_template()` and pass
the resulting RenderTemplate (or None) straight through.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

from sqlalchemy import delete, select

from circlechiffon import access
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import TemplateWhitelist
from circlechiffon.renderers.guides import TEMPLATE_KINDS
from circlechiffon.renderers.layout import RenderTemplate

USER_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "user_templates"
PART_FILES = {"base": "base.png", "top": "top.png", "layout": "layout.json"}


def template_dir(discord_id: int, kind: str) -> Path:
    if kind not in TEMPLATE_KINDS:
        raise ValueError(f"unknown template kind {kind!r}")
    return USER_TEMPLATES_DIR / str(int(discord_id)) / kind


def existing_parts(discord_id: int, kind: str) -> list[str]:
    folder = template_dir(discord_id, kind)
    return [part for part, filename in PART_FILES.items() if (folder / filename).is_file()]


def save_part(discord_id: int, kind: str, part: str, data: bytes) -> None:
    """Synchronous. Writes via a temp file + rename, so a render reading
    concurrently never sees a half-written file."""
    folder = template_dir(discord_id, kind)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / PART_FILES[part]
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def delete_parts(discord_id: int, kind: str, parts: list[str]) -> list[str]:
    """Synchronous. Returns the parts that actually existed and were removed."""
    folder = template_dir(discord_id, kind)
    removed = []
    for part in parts:
        path = folder / PART_FILES[part]
        if path.is_file():
            path.unlink()
            removed.append(part)
    for empty in (folder, folder.parent):  # the kind folder, then the user's folder
        if empty.is_dir() and not any(empty.iterdir()):
            shutil.rmtree(empty, ignore_errors=True)
    return removed


def read_part(discord_id: int, kind: str, part: str) -> bytes | None:
    path = template_dir(discord_id, kind) / PART_FILES[part]
    return path.read_bytes() if path.is_file() else None


def load_template_from_dir(folder: Path, kind: str) -> RenderTemplate | None:
    """Synchronous. None if the folder holds no parts at all."""
    folder = Path(folder)
    if not folder.is_dir():
        return None

    def read(part: str) -> bytes | None:
        path = folder / PART_FILES[part]
        return path.read_bytes() if path.is_file() else None

    base, top, layout_bytes = read("base"), read("top"), read("layout")
    layout = None
    if layout_bytes:
        try:
            layout = json.loads(layout_bytes)
        except ValueError:
            print(f"Ignoring unreadable template layout {folder / PART_FILES['layout']}")
        if isinstance(layout, dict) and layout.get("kind") != kind:
            layout = None
    if base is None and top is None and layout is None:
        return None
    return RenderTemplate(base=base, top=top, layout=layout)


async def is_whitelisted(discord_id: int) -> bool:
    if access.is_owner(discord_id):
        return True
    async with db_engine.session() as session:
        return await session.get(TemplateWhitelist, discord_id) is not None


async def add_to_whitelist(discord_id: int) -> bool:
    """Returns False if they were already on it."""
    async with db_engine.session() as session:
        if await session.get(TemplateWhitelist, discord_id) is not None:
            return False
        session.add(TemplateWhitelist(discord_id=discord_id))
        await session.commit()
        return True


async def remove_from_whitelist(discord_id: int) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(delete(TemplateWhitelist).where(TemplateWhitelist.discord_id == discord_id))
        await session.commit()
        return result.rowcount > 0


async def list_whitelist() -> list[int]:
    async with db_engine.session() as session:
        result = await session.execute(select(TemplateWhitelist.discord_id).order_by(TemplateWhitelist.added_at))
        return list(result.scalars())


async def get_user_template(discord_id: int, kind: str) -> RenderTemplate | None:
    """The template to render `kind` with for this user, or None for the
    stock look. Never raises: a broken template must not take a command
    down with it, so any failure just logs and falls back to stock."""
    try:
        if not await is_whitelisted(discord_id):
            return None
        return await asyncio.to_thread(load_template_from_dir, template_dir(discord_id, kind), kind)
    except Exception as e:
        print(f"Couldn't load {kind} template for {discord_id}: {type(e).__name__}: {e}")
        return None
