# Custom render templates

Whitelisted users can restyle three of the bot's images:

| Kind | Used by | Canvas (px) | Image fit |
|---|---|---|---|
| `b50` | `/cc-best`, `/cc-friend-best` | 6144 × 2508 | stretched to the canvas |
| `profile_core` | `/cc-profile` | 640 × 961 | stretched to the canvas |
| `profile_extra` | `/cc-profile view:Extra` | 640 × 837 (height varies) | fitted to the width, anchored at the top |

`/cc-display` and the `/cc-recent` judgement table are not customisable.

A template has up to three parts, and each one is optional:

- **Base image**: drawn first. The bot renders on top of it.
- **Top image**: drawn last, over the finished render, including the footer. Use transparency for anything that should show through.
- **`layout.json`**: moves, resizes or hides individual elements such as the icon, the name, each clear-count row, or the parts of a b50 card.

`/cc-friend-best` uses the template of the person running the command, not the friend's.

## Workflow

1. The bot owner whitelists you with `/cc-template-whitelist action:add`. (The same whitelist also lets you render `/cc-chart` videos.)
2. Run `/cc-template-get` to get a **guide PNG**: a transparent image at the real canvas size, with every element outlined and labelled. Design your base and top art over it in any image editor.
3. Open the [template editor](https://cc.etangaming.xyz/template-editor.html) and load your images. Drag elements around, resize them with the handles, hide what you don't want, then click **Export layout.json**. The editor runs entirely in your browser, and nothing you load into it is uploaded.
4. `/cc-template-upload kind:<…> base:<file> top:<file> layout:<file>` (attach any subset of the three).
5. `/cc-template-preview` renders your template with fixed sample data, so you don't need a linked account. Add `guides:True` to draw the element outlines on top.

`/cc-template-remove` deletes one part or all of them. If you're removed from the whitelist, your files stay on disk but stop being used.

## Images

- PNG, JPEG or WebP. Each file can be at most 8 MB and at most 8192 px on each side.
- Each image is resized to the canvas once, at upload time, and stored as PNG.
  - For `b50` and `profile_core`, match the canvas aspect ratio or the image gets stretched. For `b50`, half size (3072 × 1254) is fine.
  - For `profile_extra`, the image is scaled to 640 px wide.
- `profile_extra` grows taller when you have more missions or tickets than its list boxes were sized for (5 and 3). Anything the base image doesn't cover is filled with the stock background colour. The top image stays at its own height, anchored to the top.

## The editor

- **Page / Card** (b50 only): *Card* edits the one card layout shared by all 50 cards. Its coordinates are relative to the card's top-left, and the editor shows the part of your base image behind card #1.
- **Follow**: some elements sit right after another one by default. For example, the b50 rating badge follows the player name, so a short name doesn't leave a gap. The editor draws followers with a dashed outline. Dragging one sideways, or typing an x, pins it to a fixed x.
- **Options**:
  - `card_background` (b50): the difficulty-coloured card gradient. Turn it off to draw your own cards.
  - `collapse_badges` (b50): when a chart has no sync badge, the combo badge moves into the sync badge's slot.
  - `count_pill_background` (profile core): the blue count pills. When it's off, counts are drawn in white with an outline.
  - `compact_lists` (profile extra): a list shorter than its box pulls later elements up. Turn it off if your art needs elements to stay put.
- Arrow keys nudge the selected element (Shift nudges ×10). The working layout is autosaved in your browser for each kind.
- **Multi-select**: Shift/Ctrl/Cmd-click elements (on the canvas or in the list), or use *Select all*. Dragging or the arrow keys move the whole selection; visibility, opacity and text style apply to all of it at once. Position, size and follow need a single element.
- **Colours**: profile renders have layout-wide colour slots (count pill, class point bar, mission rows and so on), listed in the *Colours* panel. They aren't tied to one element. Each has a reset to the stock colour.
- **Opacity / text style**: every element has an opacity slider (100% = untouched). Text elements also get a text colour, an outline colour and an outline width. Each has a reset button that goes back to the stock look. On the card view, `card_bg` is the card's background gradient, so its slider fades the whole card.
- The editor shows boxes, not the real render. Use `/cc-template-preview` to see the real output.

## `layout.json` format

```json
{
  "version": 1,
  "kind": "b50",
  "canvas": [6144, 2508],
  "elements": {
    "icon": {"x": 48, "y": 24, "w": 192, "h": 192, "visible": true},
    "rating_badge": {"x": 1208, "y": 60, "w": 413, "h": 120, "follow": "name", "gap": 48}
  },
  "card": {"elements": {"rating_value": {"x": 10, "y": 10}}},
  "options": {"card_background": false}
}
```

- Coordinates are output pixels at `canvas` size. A layout authored at a different width is scaled to fit.
- Any element or key you leave out keeps its default, so a file only needs to contain what it changes. The editor exports everything.
- Styling keys, all optional: `opacity` (0 to 1), and on text elements only `color` and `outline_color` (`"#rrggbb"`) and `outline_width` (0 to 32, in canvas pixels, scaled with the box height like the font). A key left out keeps the stock colour, so a plain layout renders exactly as before. Style keys on a non-text element are ignored with a warning.
- `opacities` maps a layout-wide opacity slot to 0 to 1. Profile core has `pill_background`, `pill_icon` and `pill_text` for the music-count rows, so the pill, the tier icon and the count can fade independently (each also multiplies with the row's own `opacity`). Unknown slots are ignored with a warning; out-of-range values reject the upload.
- `colors` maps a colour slot to `"#rrggbb"`, e.g. `"colors": {"pill_fill": "#ff8fd0"}`. Slots differ per kind (the editor's *Colours* panel lists them); unknown slots are ignored with a warning and a bad hex value rejects the upload. Slots you leave out keep the stock colour.
- Text elements scale their font with the box height. Image elements scale to the box height.
- Unknown elements, keys and options are ignored, with a warning at upload. Wrong types, non-finite numbers, out-of-range values and `follow` loops reject the whole upload, and nothing is saved.
- The footer (credits) is fixed and isn't part of the layout.

## For developers

- Geometry lives in each renderer's `default_layout()` (`renderers/b50.py`, `renderers/profile.py`). Renderers read positions from a `Layout` (`renderers/layout.py`).
- The kind registry and the guide renderer are in `renderers/guides.py`.
- Upload checks are in `customisation/validate.py`, storage (`user_templates/<discord_id>/<kind>/`) and the whitelist in `customisation/store.py`, and `/cc-template-preview`'s sample data in `customisation/sample_data.py`.
- After adding or moving an element, run `python generate_templates.py`. It rewrites `siteresources/template-layouts.js` (the editor's defaults) and the guide PNGs.
