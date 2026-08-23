"""
Generates guide-only PNGs for every image-generating renderer in
circlechiffon/renderers/ - each one is a transparent-background image at
the renderer's real output size, with labeled outline boxes marking every
position the real render function draws content. Meant to be opened in an
external image editor (Photoshop/GIMP/etc.) as a reference for designing a
matching background/decoration around the real content, without needing a
live account or guessing at layout coordinates.

Usage:
    python3 generate_templates.py               # writes PNGs into ./templates/
    python3 generate_templates.py my_output_dir  # writes into a custom directory
"""

import os
import sys
from pathlib import Path

from circlechiffon.renderers.b50 import render_b50_template
from circlechiffon.renderers.display import render_display_template
from circlechiffon.renderers.profile import render_profile_core_template, render_profile_extras_template

# anchored to this file's own directory rather than a bare relative name -
# same reasoning as config.CONFIG_PATH/crypto_utils.KEY_FILE: a bare
# relative path resolves against the process's current working directory,
# which can differ from the repo directory depending on how the bot is
# launched.
_BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = str(_BASE_DIR / "templates")

_GENERATORS = {
    "b50_template.png": render_b50_template,
    "display_template.png": render_display_template,
    "profile_core_template.png": render_profile_core_template,
    "profile_extras_template.png": render_profile_extras_template,
}


def generate_all(out_dir: str | None = None) -> None:
    """(Re)generates every template PNG, overwriting any that already exist."""
    out_dir = out_dir or TEMPLATES_DIR
    os.makedirs(out_dir, exist_ok=True)
    for filename, generator in _GENERATORS.items():
        path = os.path.join(out_dir, filename)
        with open(path, "wb") as f:
            generator(f)
        print(f"wrote {path}")


def generate_missing(out_dir: str | None = None) -> list[str]:
    """Generates only the template PNGs that don't already exist in
    out_dir (default TEMPLATES_DIR) - used at bot startup so a fresh
    install gets them for free without ever clobbering ones the user has
    already opened and started decorating in an image editor. Returns the
    filenames that were generated."""
    out_dir = out_dir or TEMPLATES_DIR
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for filename, generator in _GENERATORS.items():
        path = os.path.join(out_dir, filename)
        if os.path.exists(path):
            continue
        with open(path, "wb") as f:
            generator(f)
        written.append(filename)
    return written


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else None
    generate_all(out_dir)


if __name__ == "__main__":
    main()
