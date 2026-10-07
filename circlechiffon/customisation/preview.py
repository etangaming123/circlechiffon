"""
/cc-template-preview's render: a template drawn over the real renderer
with sample_data's fixed inputs - no DX NET account or request involved.
"""

import asyncio
import io

from PIL import Image

from circlechiffon.adapters.dxrating.images import get_jackets_bulk
from circlechiffon.adapters.maimai_net.badge_icons import get_all_badge_icons
from circlechiffon.adapters.maimai_site.version_logo import get_version_logo
from circlechiffon.customisation import sample_data
from circlechiffon.renderers.b50 import render_b50
from circlechiffon.renderers.guides import render_guide
from circlechiffon.renderers.layout import RenderTemplate
from circlechiffon.renderers.profile import render_profile_core, render_profile_extras
from circlechiffon.songdata.catalog import get_catalog


def _overlay_guides(render: io.BytesIO, kind: str, layout: dict | None) -> io.BytesIO:
    guide_buf = io.BytesIO()
    render_guide(kind, guide_buf, layout)
    with Image.open(render) as base_src, Image.open(guide_buf) as guide_src:
        base = base_src.convert("RGBA")
        guide = guide_src.convert("RGBA")
        if guide.size != base.size:
            # profile extra's real height depends on its lists - keep the
            # guide top-anchored at its own height rather than stretching.
            padded = Image.new("RGBA", base.size, (0, 0, 0, 0))
            padded.paste(guide, (0, 0))
            guide = padded
        base.alpha_composite(guide)
    out = io.BytesIO()
    base.convert("RGB").save(out, "PNG", compress_level=3)
    out.seek(0)
    return out


async def render_preview(kind: str, template: RenderTemplate | None, *, icon_bytes: bytes | None = None, guides: bool = False) -> io.BytesIO:
    buf = io.BytesIO()
    if kind == "b50":
        catalog = get_catalog()
        result = sample_data.sample_best50(catalog)
        image_names = {}
        for entry in result.b15 + result.b35:
            if entry is not None and (song := catalog.get_by_title(entry.score.title)) and song.image_name:
                image_names[entry.score.title] = song.image_name
        jackets, badge_icons, version_logo = await asyncio.gather(
            get_jackets_bulk(list(image_names.values())), get_all_badge_icons(), get_version_logo()
        )
        b15_versions = [v for v in (catalog.current_version, catalog.previous_version) if v is not None]
        await asyncio.to_thread(
            render_b50,
            player_name=sample_data.SAMPLE_NAME,
            rating=sample_data.SAMPLE_RATING,
            icon_bytes=icon_bytes,
            result=result,
            b15_version_label=" and ".join(b15_versions) or "CURRENT VERSION",
            jackets_by_title={t: jackets[n] for t, n in image_names.items() if n in jackets},
            badge_icons=badge_icons,
            version_logo_bytes=version_logo,
            output=buf,
            template=template,
        )
    elif kind == "profile_core":
        await asyncio.to_thread(
            render_profile_core,
            profile=sample_data.sample_profile(),
            icon_bytes=icon_bytes,
            course_rank_bytes=None,
            class_rank_bytes=None,
            rating_badge_bytes=None,
            badge_icons=await get_all_badge_icons(),
            output=buf,
            template=template,
        )
    elif kind == "profile_extra":
        extras = sample_data.sample_profile_extras()
        await asyncio.to_thread(
            render_profile_extras,
            profile=sample_data.sample_profile(),
            extras=extras,
            icon_bytes=icon_bytes,
            ticket_image_bytes=[None] * len(extras.tickets),
            output=buf,
            template=template,
        )
    else:
        raise ValueError(f"unknown template kind {kind!r}")
    if guides:
        return await asyncio.to_thread(_overlay_guides, buf, kind, template.layout if template else None)
    return buf
