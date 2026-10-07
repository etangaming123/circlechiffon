# CiRCLE Chiffon

Yet Another Discord bot for [maimai DX NET](https://maimaidx-eng.com/) (SEGA's official web service for maimai DX). Link your SEGA ID account and view your profile, recent plays, best-50 rating and more through Discord slash commands, or look up song data and calculate rating points without linking anything.

Shoutout to [chuni-penguin](https://github.com/beer-psi/chuni-penguin).

[Website](./index.html "CiRCLE Chiffon website") • [Terms of Service](./termsofservice.html "CiRCLE Chiffon terms of service") • [Privacy Policy](./privacypolicy.html "CiRCLE Chiffon privacy policy")

[Add to your Discord](https://discord.com/oauth2/authorize?client_id=1540564874231414944 "Add CiRCLE Chiffon to your Discord account") • [GitHub Issues](https://github.com/etangaming123/circlechiffon/issues "Report a bug or request a feature")

> [!NOTE]
> Uptime of this bot is flaky.
> You are free to selfhost the bot and run it on your own bot account (or, preferably, use a better maimai DX NET bot instead, like mimi xd or mai bot 2.0).

> [!WARNING]
> This bot is intended for self hosting. etangaming123 is not responsible for sensitive data on this bot being leaked.
> Account linking is 100% optional, and you may unlink and delete everything at any time with `/cc-logout`.

> [!NOTE]
> This is not meant to be a complete replacement to the other (cool) bots out there (such as mimi xd and mai bot 2.0). It's a personal "vibecoded" project :^)

Only the **INTL** region (`maimaidx-eng.com`) is supported.

## Features

Commands marked 🔗 need a linked SEGA ID account. Everything else works with nothing linked.

### Account

* 🔗 `/cc-login` — Link your maimai DX NET account, via a private modal. Optional `remember_password` flag
* `/cc-logout` — Unlink and delete everything stored about you

### Profile & Records

* 🔗 `/cc-profile` — Your Player's Data image: name, title, rating, rank badges, play counts and the full clear-count grid (`view:Extra` shows class points, miles, missions and tickets instead)
* 🔗 `/cc-display` — Your nameplate, rendered close to how the real cab displays it
* 🔗 `/cc-recent` — Page through your recent plays 25 at a time; pick one from the dropdown for a judgement-breakdown image, with a button to show/hide the lost-achievement% per note
* 🔗 `/cc-best` — Your Best-50 rating image (B15 + B35), styled after dxrating.net's
* 🔗 `/cc-album` — Browse your maimai DX NET photo album

### Scores & Rating

* 🔗 `/cc-scores` — Your score on a song, difficulty by difficulty. Pass `friend:` to see theirs instead
* `/cc-info` — Look up a song's chart levels, and with a `difficulty`, its full detail: constant, charter, note counts, release version and tags
* `/cc-rating` — Calculate the rating points a (constant, achievement%) combo earns

### Social

* 🔗 `/cc-friends` — List your maimai DX NET friends, sorted by rating
* 🔗 `/cc-friend-profile` — View a friend's profile
* 🔗 `/cc-friend-best` — Render a friend's best-50 image, computed from their scraped scores
* 🔗 `/cc-leaderboard` — Rank you and all your friends on a single chart by achievement
* 🔗 `/cc-circle` — Your CiRCLE (team) info, points, ranking and members
* 🔗 `/cc-circle-challenge` — This week's CiRCLE challenge and its progress gauge

### Cosmetic

* 🔗 `/cc-preset-save` / `/cc-preset-load` / `/cc-preset-list` / `/cc-preset-delete` — Save your equipped icon, name plate, frame and title to a slot, and re-equip the whole set in one command

### Custom templates (whitelisted users)

Restyle your `/cc-best`, `/cc-friend-best` and `/cc-profile` images with your own art and layout. Lay it out in the [template editor](https://cc.etangaming.xyz/template-editor.html), then upload. See [Custom render templates](./docs/customisation.md).

* `/cc-template-upload` — Upload a base image (under the render), a top image (over it) and/or a `layout.json`
* `/cc-template-preview` — Render your template with sample data, optionally with the layout guide drawn on
* `/cc-template-get` / `/cc-template-remove` — Download or remove your current template

### Charts

* `/cc-chart` — Look up a chart on [mai-notes.com](https://mai-notes.com/): level, constant, note breakdown, charter, top DX score and tags. Can also render the chart as a *video of it playing*, with tap sounds mixed in — in a plain mai-notes style (default), or with a maimai look showing every judgement as CRITICAL PERFECT or MISS. Long charts come as several overlapping videos rather than one blurry one. Renders run one at a time in a queue, with a 30-second cooldown per user; who can render is set by `chart_render` in `config.json` (`"owner"`, the default, or `"everyone"`)

### Owner

* `/cc-ping` — Ping the bot
* `/cc-ban` / `/cc-unban` — Bot-level ban controls
* `/cc-template-whitelist` — Choose who can upload custom templates

## Screenshots/Showcase

![/cc-display](./images/preview_display.png)

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
* `ffmpeg`, if you want `/cc-chart` to render videos

### Discord Bot

1. Log on to the [Discord Developer Portal](https://discord.com/developers/applications "Leads you to the Discord Developer Portal").
2. Create a new application using the button on the top right.
3. Add a new app icon. This will be the bot's profile picture.
4. Under the Bot tab, reset the bot's token and copy it - you'll need it in a moment.
5. Go to the Installation tab and enable both "Guild Install" and "User Install", with the `applications.commands` and `bot` scopes. No privileged Gateway intents are required.
6. Copy the generated install link and paste it into your browser to add the bot to your own account (and, optionally, servers).

### Python Code

Ensure you have everything with:
`git clone https://github.com/etangaming123/circlechiffon`

Get all the required modules with:
`pip install -r requirements.txt`

`/cc-chart` renders videos locally (no browser), but needs `ffmpeg` on your `PATH` — a system package (`brew install ffmpeg`, `apt install ffmpeg`, ...), not a Python one. Rendering is spread over several worker processes; set the `CC_CHART_WORKERS` environment variable to change how many (default: CPU cores minus one, at most 8).

The "game" render modes draw notes with a [MajdataPlay](https://github.com/LingFeng-bbben/MajdataPlay) skin, which this repo does **not** ship (the art isn't ours to redistribute). To use one, run this once against a MajdataPlay checkout that has the skin under `Assets/StreamingAssets/Skins/` (default skin name `Deluxe`):

```
python import_chart_skin.py /path/to/MajdataPlay --skin Deluxe
```

It copies the skin and MajdataPlay's tap sound into `assets/chart_skin/` (gitignored) and extracts MajdataPlay's slide layouts. Without it, the game modes fall back to the mai-notes style and sounds.

#### Rendering on another machine (optional)

Renders can run on a faster computer on the same network instead. On that machine, clone this repo, `pip install -r requirements.txt`, install ffmpeg, and run `import_chart_skin.py` there too if you want the game modes. Then pick a shared secret (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and start the server with it:

```
CC_RENDER_SERVER_KEY=<secret> python render_server.py --port 8765
```

On the bot's machine, add the server's address and the same secret to `config.json`:

```json
"chart_render_server": "http://192.168.1.50:8765",
"chart_render_key": "<secret>"
```

The key is encrypted in place the next time the bot starts. Give the render machine a fixed LAN address (a DHCP reservation on your router) so the URL keeps working. If the server is off or unreachable, `/cc-chart` says so and renders on the bot's machine as before. Leave `chart_render_server` empty to always render locally.

ffmpeg and `skia-python` are checked at runtime. Without them `/cc-chart` still answers, it just replies with the chart's stats instead of a video; every other command is unaffected.

Run the bot once with `python main.py` - it will create a `config.json` for you and prompt you to fill in your bot token (and optionally your Discord user ID as `owner_id`) before continuing.

#### Encryption key (recommended)

Session tokens, and SEGA ID credentials for users who opt into `remember_password`, are encrypted at rest. By default the encryption key is auto-generated into a local file (`.circlechiffon.key`) on first run - this works out of the box, but the key ends up sitting on disk right next to what it unlocks.

For better security, set the `CIRCLECHIFFON_ENCRYPTION_KEY` environment variable to a Fernet key of your own before starting the bot instead. Don't lose it - if you do, every user will need to `/cc-login` again.

Finally, run the bot with:
`python main.py`

Refresh your Discord client, and press `/` on your keyboard. You should see the bot's commands in the list, and you can start using it!

Do note that the program has to be continuously running for the bot to work. If you close the terminal or stop the program, the bot will go offline and become unusable until you run it again.

## Documentation

* [How credential handling works](./docs/credentials.md) — what's stored, what isn't, and how `remember_password` changes that
* [Known limitations and testing notes](./docs/limitations.md) — what's verified live, what's best-effort, and why friend data is limited
* [Custom render templates](./docs/customisation.md) — base/top images, the layout editor, and the `layout.json` format
* [Data credits](./docs/credits.md) — dxrating, chuni-penguin, mai-notes and the bundled fonts

The bot will automatically create new image templates in `./templates`, so you can edit them to your liking.

## License

CiRCLE Chiffon is licenced under the **[MIT License](./LICENSE "Leads you to the license for this repository").**

All other assets, such as song data, jacket art and the bundled fonts, are not owned by etangaming123 and carry their own terms - see [Data credits](./docs/credits.md). maimai DX is property of SEGA; this project is unofficial and not affiliated with or endorsed by SEGA.
