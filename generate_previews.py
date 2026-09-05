"""
Renders the b50 and profile-core cards against a fixed, entirely fake
sample profile (circlechiffon/renderers/sample_data.py) so a candidate
custom template can be checked without a live linked account or a round
trip through Discord. Writes a single self-contained preview.html into
the given directory, with each render embedded inline as a base64 data
URI - open it directly in a browser.

Usage:
    python3 generate_previews.py <template_dir>

<template_dir> may optionally contain b50.png and/or profile_core.png -
the same names /cc-template-upload's render types map to, so this can be
pointed at the exact file you're about to upload. Missing files just
render with the default (flat-color) background instead.
"""

import base64
import io
import os
import sys

from circlechiffon.renderers import sample_data
from circlechiffon.renderers.b50 import render_b50
from circlechiffon.renderers.profile import render_profile_core

_RENDERERS = {
    "b50": "Best 50 (/cc-best)",
    "profile_core": "Profile - Core (/cc-profile)",
}


def _render(render_type: str, template_bytes: bytes | None) -> bytes:
    profile = sample_data.build_sample_profile()
    buf = io.BytesIO()
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
            output=buf,
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
            output=buf,
        )
    return buf.getvalue()


def generate(template_dir: str) -> str:
    os.makedirs(template_dir, exist_ok=True)
    sections = []
    for render_type, label in _RENDERERS.items():
        candidate_path = os.path.join(template_dir, f"{render_type}.png")
        template_bytes = None
        used_candidate = False
        if os.path.exists(candidate_path):
            with open(candidate_path, "rb") as f:
                template_bytes = f.read()
            used_candidate = True

        png_bytes = _render(render_type, template_bytes)
        data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        source_note = candidate_path if used_candidate else "default background (no candidate file found)"
        sections.append(
            f"<section><h2>{label}</h2><p class='source'>{source_note}</p>"
            f"<img src='{data_uri}' alt='{render_type} preview'></section>"
        )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Template preview</title><style>"
        "body{background:#1a1a20;color:#eee;font-family:sans-serif;padding:24px}"
        "h2{margin-bottom:4px}.source{color:#999;font-size:13px;margin-top:0}"
        "img{max-width:100%;border:1px solid #444;margin-bottom:32px}"
        "</style></head><body>"
        "<h1>Sample-profile template preview</h1>"
        f"{''.join(sections)}"
        "</body></html>"
    )

    out_path = os.path.join(template_dir, "preview.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    out_path = generate(sys.argv[1])
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
