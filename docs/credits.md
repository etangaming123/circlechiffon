# Data credits

## gekichumai/dxrating (MIT License)

The vendored song/chart catalog (`data/dxdata.json`, trimmed from the full dataset), the rating
formula (`circlechiffon/ratingcalc/calculator.py`), the B15/B35 best-50 bucketing logic
(`circlechiffon/ratingcalc/best50.py`), and the chart tag/jacket APIs
(`circlechiffon/adapters/dxrating/`) are all ported or sourced from
[gekichumai/dxrating](https://github.com/gekichumai/dxrating) — dxdata itself,
`packages/maimai-domain`'s rating math, dxrating's public `/api/v1/tags` endpoint, and its
`shama.dxrating.net` jacket CDN, respectively.

The `/cc-best` image (`circlechiffon/renderers/b50.py`) is a from-scratch Pillow reimplementation of
the visual design of dxrating's own best-50 "oneshot" image — which itself runs a server-side
Satori/resvg/sharp pipeline, not portable to Python. Same layout and difficulty colors, different
renderer.

The maimai DX NET login-flow structure and page markup were also cross-referenced against dxrating's
backend scraper.

## beer-psi/chuni-penguin (0BSD License)

The SEGA-ID-modal-based login pattern, and the Pillow rendering technique itself (thread-offloaded
rendering, bundled fonts, BytesIO/`discord.File` delivery), mirror
[beer-psi/chuni-penguin](https://github.com/beer-psi/chuni-penguin), which implements the equivalent
flow and renderer for CHUNITHM-NET.

## Fonts

The bundled fonts in `assets/fonts/` (Inter, Noto Sans JP) are copied from chuni-penguin's own
bundle. Both are OFL-licensed and freely redistributable.

## mai-notes.com

Chart data and the chart videos behind `/cc-chart` come from [mai-notes.com](https://mai-notes.com/)
(maiノーツ), a community maimai chart database. The video is its own player, driven headlessly and
recorded.

## Everything else

maimai DX and all associated song, chart, jacket and artwork data are property of SEGA. This project
is unofficial and not affiliated with or endorsed by SEGA.
