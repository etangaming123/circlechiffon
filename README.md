# CiRCLE Chiffon

Yet Another Discord bot for [maimai DX NET](https://maimaidx-eng.com/) (SEGA's official web service for maimaiDX). Link your SEGA ID account and view your profile, recent plays, and best-rated scores through Discord slash commands, or look up song data and calculate rating points without linking anything.

Shoutout to [chuni-penguin](https://github.com/beer-psi/chuni-penguin).

[Website](./index.html "CiRCLE Chiffon website") • [Terms of Service](./termsofservice.html "CiRCLE Chiffon terms of service") • [Privacy Policy](./privacypolicy.html "CiRCLE Chiffon privacy policy")

[Add to your Discord](https://discord.com/oauth2/authorize?client_id=1540564874231414944 "Add CiRCLE Chiffon to your Discord account") • [GitHub Issues](https://github.com/etangaming123/circlechiffon/issues "Report a bug or request a feature")

> [!NOTE]
> Uptime of this bot is flaky. You are free to selfhost the bot and run it on your own bot account (or, preferably, use a better maimai DX NET bot instead, like mimi xd or mai bot 2.0).

> [!WARNING]
> This bot is intended for self hosting. etangaming123 is not responsible for sensitive data on this bot being leaked. Account linking is 100% optional (`/cc-info` and `/cc-rating` need nothing linked), and you may unlink and delete everything at any time with `/cc-logout`.

> [!NOTE]
> This is not meant to be a complete replacement to the other (cool) bots out there (such as mimi xd and mai bot 2.0). It's a personal "vibecoded" project :^)

## Commands

* `/cc-login` — Link your maimai DX NET account (SEGA ID username/password via a private modal). Optional `remember_password` flag - see below. Requires linked account: no
* `/cc-logout` — Unlink your account. Requires linked account: no
* `/cc-profile` — Renders your Player's Data image. Default `view:Core` shows name/title/rating/rank badges/star count/play counts/the full music clear-count grid; `view:Extra` shows class point progress, mile count, missions, tickets, and intimate item count instead. Requires linked account: yes
* `/cc-recent` — Page through your recent plays one *credit* at a time (Prev/Next buttons) - one embed per track played that credit (jacket, achievement%, rating, combo flag, specific sync tier). A dropdown lets you drill into one track's judgment counts + DX score - see below. Requires linked account: yes
* `/cc-best` — Renders your Best-50 rating image (B15 = current version + one version prior, B35 = everything older, styled after dxrating.net's own best-50 image). Requires linked account: yes
* `/cc-info` — Look up a song's chart levels. Add the optional `difficulty` choice for that chart's full detail - version, release date, charter, note counts, and dxrating.net tags - plus jacket art either way. Requires linked account: no
* `/cc-rating` — Calculate the rating points a (constant, achievement%) combo earns. Requires linked account: no
* `/cc-friends` — List your maimai DX NET friends, sorted by rating. Optional `show_ids` flag reveals each friend's internal id (only needed to disambiguate two friends with the same name). Requires linked account: yes
* `/cc-friend-profile` — View a friend's profile by name or id - much more limited than `/cc-profile`, since SEGA doesn't expose play counts or clear-count grids for anyone but yourself. Requires linked account: yes
* `/cc-friend-best` — Renders a friend's best-50 rating image from their scraped achievements. The rating shown is **computed locally**, not SEGA's own number - SEGA never shows a friend's real rating. Only works for friends you've marked as a Favorite on maimai DX NET's own friend list. Requires linked account: yes

Currently only the **INTL** region (`maimaidx-eng.com`) is supported.

## Quickstart

Open [this link](https://discord.com/oauth2/authorize?client_id=1540564874231414944 "Add CiRCLE Chiffon to your Discord account") to authorise the officially hosted instance of CiRCLE Chiffon with your Discord account, and you're all good to go! It's registered as both a guild-installable and user-installable app, so `/cc-info` and `/cc-rating` also work in DMs.

Do note that if you lack the "External Apps" permission in servers, you will still be able to use CiRCLE Chiffon's commands, however they will only be visible to you.

## Selfhosting

### You will need:

* A Discord bot
* Python (3.10 or above)
* The required Python libraries in `requirements.txt`

The following are optional, but recommended:

* A device capable of running the Python program for a while (if you plan on leaving the bot online most of the time)

### Discord Bot

1. Log on to the [Discord Developer Portal](https://discord.com/developers/applications "Leads you to the Discord Developer Portal").
2. Create a new application using the button on the top right.
3. Under the Bot tab, reset the bot's token and copy it - you'll need it in a moment.
4. Go to the Installation tab and enable both "Guild Install" and "User Install", with the `applications.commands` and `bot` scopes. No privileged Gateway intents are required.
5. Copy the generated install link and paste it into your browser to add the bot to your own account (and, optionally, servers).

### Python Code

Ensure you have everything with:
`git clone https://github.com/etangaming123/circlechiffon`

Get all the required modules with:
`pip install -r requirements.txt`

Run the bot once with `python main.py` - it will create a `config.json` for you and prompt you to fill in your bot token (and optionally your Discord user ID as `owner_id`) before continuing.

#### Encryption key (recommended)

Session cookies, and SEGA ID username/passwords for users who opt into `remember_password`, are encrypted at rest. By default the encryption key is auto-generated into a local file (`.circlechiffon.key`) on first run - this works out of the box, but the key ends up sitting on disk right next to what it unlocks.

For better security, set the `CIRCLECHIFFON_ENCRYPTION_KEY` environment variable to a Fernet key of your own before starting the bot instead. Don't lose it - if you do, every user will need to `/cc-login` again.

Finally, run the bot with:
`python main.py`

Refresh your Discord client, and press `/` on your keyboard. You should see the bot's commands in the list, and you can start using it!

Do note that the program has to be continuously running for the bot to work. If you close the terminal or stop the program, the bot will go offline and become unusable until you run it again.

## How credential handling works

`/cc-login` opens a Discord modal (never a plain slash-command argument, so your credentials never appear in a channel or interaction log) asking for your SEGA ID username and password. By default the bot uses them **once**, in memory, to log into SEGA's Aime auth gateway and obtain a maimai DX NET session cookie, then discards the password entirely. Only the resulting session cookie is stored, encrypted at rest (Fernet, via `cryptography`) in the local SQLite database. If your session expires, commands will tell you to `/cc-login` again - your password is never cached anywhere to auto-refresh it by default.

### Optional: `/cc-login remember_password:True`

Session cookies can expire, and re-running `/cc-login` every time is annoying. `/cc-login` has an opt-in `remember_password` option: turning it on shows an explicit warning and requires you to confirm via a button before anything is stored. If you confirm, your SEGA ID username **and password** are stored, encrypted at rest the same way the session cookie is, and the bot will silently re-login with them whenever your session cookie expires - `/cc-profile`, `/cc-recent`, and `/cc-best` transparently retry once after a fresh login instead of just telling you to `/cc-login` again.

This is **less secure** than the default (cookie-only) option: a stored password is more valuable if this bot's database or encryption key is ever compromised than a session cookie, which can simply be invalidated. Only opt in if you're comfortable with that tradeoff. `/cc-logout` deletes everything that's stored - the session cookie and, if you opted in, your credentials - at once.

## `/cc-best`'s editable template

`assets/b50/template.png` is the background layer every `/cc-best` render draws on top of. Replace it with anything you like (any size - it's resized to 1500x1300) to reskin every future render - add a logo, a pattern, your own branding - with no code changes. If the file is ever missing, rendering falls back to a plain solid background rather than failing.

## Data credits

The vendored song/chart catalog (`data/dxdata.json`, trimmed from the full dataset), the rating formula (`circlechiffon/ratingcalc/calculator.py`), the B15/B35 best-50 bucketing logic (`circlechiffon/ratingcalc/best50.py`), and the chart tag/jacket APIs (`circlechiffon/adapters/dxrating/`) are all ported/sourced from [gekichumai/dxrating](https://github.com/gekichumai/dxrating) (MIT License) - dxdata itself, `packages/maimai-domain`'s rating math, dxrating's public `/api/v1/tags` endpoint, and its `shama.dxrating.net` jacket CDN, respectively. The `/cc-best` image (`circlechiffon/renderers/b50.py`) is a from-scratch Pillow reimplementation of the visual design of dxrating's own best-50 "oneshot" image (which itself runs a server-side Satori/resvg/sharp pipeline, not portable to Python) - same layout and difficulty colors, different renderer.

The maimai DX NET login-flow structure and page markup were also cross-referenced against dxrating's backend scraper, and the SEGA-ID-modal-based login pattern, plus the Pillow rendering technique itself (thread-offloaded rendering, bundled fonts, BytesIO/discord.File delivery), mirror [beer-psi/chuni-penguin](https://github.com/beer-psi/chuni-penguin) (0BSD License), which implements the equivalent flow/renderer for CHUNITHM-NET. The bundled fonts in `assets/fonts/` (Inter, Noto Sans JP) are copied from chuni-penguin's own bundle - both are OFL-licensed and freely redistributable.

The bot's own original code is not currently released under a separate open-source license.

## Known limitations / testing notes

The rating calculator and song catalog/search are unit-testable with no live account (see `circlechiffon/ratingcalc/calculator.py` and `circlechiffon/songdata/catalog.py`). `/cc-login`, `/cc-profile`, `/cc-recent`, and `/cc-best` need a real SEGA ID login against SEGA's live service to fully verify - the HTML scraping selectors for recent/music-score pages are ported directly from a known working reference implementation (dxrating), but `/cc-profile`'s selectors (`.name_block`, `.rating_block`, `.trophy_block`) are best-effort, since neither reference project scrapes the home/profile page, and should be checked against a live account and adjusted if maimai DX NET's markup differs.

Selecting a track from `/cc-recent`'s dropdown to view judgment counts, DX score, and max combo is likewise genuinely **unverified**: no reference project covers maimai DX NET's per-play detail page, so the URL and judgment-count parsing are both best-effort. If a track's dropdown selection returns "couldn't load play detail," that means the guess needs adjusting - not that the rest of the bot is broken.

An existing `circlechiffon.db` from before the `remember_password` feature is handled automatically: `main.py` runs a small in-place `ALTER TABLE` migration on startup to add the new column, so no manual database changes or resets are needed when upgrading.

`/cc-info`'s autocomplete logs each query's timing to stdout (`cc-info autocomplete for '...' took N.Nms`) - if autocomplete still seems slow in practice, those numbers will show whether the time is going into the bot's own search code or somewhere else (Discord round-trip, host load, etc.).

The dxrating.net tags API and jacket CDN could not be exercised against the real network from the environment this was built in (its egress proxy blocks both `miruku.dxrating.net` and `shama.dxrating.net`). Everything downstream of a failed dxrating call was built and tested to degrade gracefully instead of breaking the command (no tags shown, a solid placeholder square instead of jacket art), so if you see that fallback behavior in practice it's worth checking connectivity to those two hosts before assuming a bug.

`/cc-friends`, `/cc-friend-profile`, and `/cc-friend-best`'s selectors (the friend list, friend detail, and friend VS/achievement pages) were confirmed live against a real logged-in account during development, unlike most of this bot's other best-effort scrapers - see the parser functions' own docstrings in `circlechiffon/adapters/maimai_net/parser.py` for exactly what was checked. A few things are architectural, not bugs: `/cc-friend-best` only returns scores for friends marked as a Favorite on maimai DX NET's own friend list (unfavorited friends' score pages come back empty, same as a friend who's genuinely never played - the two can't be told apart); no raw DX score is obtainable for a friend, only achievement/combo/sync (enough to compute a rating, not enough for a DX-score-accurate best-50); and the rating shown on `/cc-friend-best` is always computed locally from those scraped achievements, since SEGA never shows a friend's real rating number anywhere.
