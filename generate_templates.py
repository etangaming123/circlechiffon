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

from circlechiffon.renderers.b50 import render_b50_template
from circlechiffon.renderers.display import render_display_template
from circlechiffon.renderers.profile import render_profile_core_template, render_profile_extras_template

_GENERATORS = {
    "b50_template.png": render_b50_template,
    "display_template.png": render_display_template,
    "profile_core_template.png": render_profile_core_template,
    "profile_extras_template.png": render_profile_extras_template,
}


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "templates"
    os.makedirs(out_dir, exist_ok=True)

    for filename, generator in _GENERATORS.items():
        path = os.path.join(out_dir, filename)
        with open(path, "wb") as f:
            generator(f)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
