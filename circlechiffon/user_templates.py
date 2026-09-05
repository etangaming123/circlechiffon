"""
User-uploaded custom background templates for /cc-best (b50) and
/cc-profile's "core" view. A separate, self-contained whitelist
(TemplateAccess) gates who can upload - the owner always has access
(access.is_owner()) independent of it, same convention as
access.handle_command_access exempting the owner from cooldowns.

Uploaded images are never persisted verbatim: save_template() decodes
with Pillow, validates dimensions/format/frame-count, and re-encodes to
PNG before writing to disk. That's what strips EXIF/any embedded payload
from the untrusted upload, not just a format nicety - the file on disk is
always a Pillow-produced PNG, never bytes that came directly from Discord.
"""

import io
from pathlib import Path

from PIL import Image
from sqlalchemy import delete, select

from circlechiffon import access
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import TemplateAccess, UserTemplate, _utcnow

RENDER_TYPES = ("b50", "profile_core")

# anchored to this file's own directory rather than a bare relative name -
# same reasoning as generate_templates.py's TEMPLATES_DIR: a bare relative
# path resolves against the process's current working directory, which can
# differ from the repo directory depending on how the bot is launched.
_BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = _BASE_DIR / "data" / "user_templates"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB, belt-and-braces on top of Discord's own attachment cap
MAX_PIXELS = 40_000_000  # guards decompression bombs before any resize is attempted
MIN_DIMENSION = 32
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


class TemplateValidationError(Exception):
    """Raised with a user-facing message - the cog replies it ephemeral."""


def _template_path(discord_id: int, render_type: str) -> Path:
    return TEMPLATE_DIR / str(discord_id) / f"{render_type}.png"


async def has_template_access(discord_id: int) -> bool:
    if access.is_owner(discord_id):
        return True
    async with db_engine.session() as session:
        return await session.get(TemplateAccess, discord_id) is not None


async def grant_access(discord_id: int, granted_by: int) -> None:
    async with db_engine.session() as session:
        grant = await session.get(TemplateAccess, discord_id)
        if grant is None:
            grant = TemplateAccess(discord_id=discord_id, granted_by=granted_by)
            session.add(grant)
        else:
            grant.granted_by = granted_by
        await session.commit()


async def revoke_access(discord_id: int) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(delete(TemplateAccess).where(TemplateAccess.discord_id == discord_id))
        await session.commit()
        return result.rowcount > 0


def sanitize_upload(raw_bytes: bytes) -> bytes:
    """Validates an untrusted image upload and re-encodes it to PNG,
    raising TemplateValidationError (a user-facing message) on any
    rejection. Public so callers that need to validate without saving yet
    (e.g. /cc-template-preview's "test this file" path) can reuse the same
    checks save_template() applies before writing to disk."""
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise TemplateValidationError(f"File too large - max {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB.")

    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
    except Exception:
        raise TemplateValidationError("Couldn't read that as an image. Only PNG, JPEG, and WEBP are supported.")

    if image.format not in _ALLOWED_FORMATS:
        raise TemplateValidationError(
            f"Unsupported image format ({image.format or 'unknown'}). Only PNG, JPEG, and WEBP are supported."
        )
    if getattr(image, "is_animated", False):
        raise TemplateValidationError("Animated images aren't supported - upload a single static frame.")
    if image.width * image.height > MAX_PIXELS:
        raise TemplateValidationError("Image resolution is too large.")
    if image.width < MIN_DIMENSION or image.height < MIN_DIMENSION:
        raise TemplateValidationError(f"Image is too small - minimum {MIN_DIMENSION}x{MIN_DIMENSION}.")

    out = io.BytesIO()
    image.convert("RGB").save(out, "PNG")
    return out.getvalue()


async def save_template(discord_id: int, render_type: str, raw_bytes: bytes) -> None:
    if render_type not in RENDER_TYPES:
        raise TemplateValidationError(f"Unknown template type {render_type!r}.")

    sanitized = sanitize_upload(raw_bytes)

    path = _template_path(discord_id, render_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sanitized)

    async with db_engine.session() as session:
        row = await session.get(UserTemplate, (discord_id, render_type))
        if row is None:
            row = UserTemplate(discord_id=discord_id, render_type=render_type)
            session.add(row)
        else:
            row.uploaded_at = _utcnow()
        await session.commit()


async def load_template(discord_id: int, render_type: str) -> bytes | None:
    async with db_engine.session() as session:
        row = await session.get(UserTemplate, (discord_id, render_type))
        if row is None:
            return None
    path = _template_path(discord_id, render_type)
    try:
        return path.read_bytes()
    except OSError:
        return None


async def delete_template(discord_id: int, render_type: str) -> bool:
    async with db_engine.session() as session:
        result = await session.execute(
            delete(UserTemplate).where(UserTemplate.discord_id == discord_id, UserTemplate.render_type == render_type)
        )
        await session.commit()
        deleted = result.rowcount > 0
    if deleted:
        _template_path(discord_id, render_type).unlink(missing_ok=True)
    return deleted


async def list_templates(discord_id: int) -> list[str]:
    async with db_engine.session() as session:
        result = await session.execute(select(UserTemplate.render_type).where(UserTemplate.discord_id == discord_id))
        return [row[0] for row in result.all()]
