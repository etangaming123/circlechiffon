# Known limitations and testing notes

## What's verified, and what isn't

There is no official maimai DX NET API — everything is HTML scraping.

**Covered by automated tests** (`pytest`, no live account or network needed): the rating formula and
best-50 bucketing, the judgement-loss math, the simai chart parser and slide geometry, the song
catalog and search, encryption at rest, command cooldowns/bans, the database migration, template
upload validation, and friend-name matching. Run them with `python -m pytest` (install
`requirements-dev.txt` first).

**Not covered by tests:** the Discord commands themselves, anything that talks to maimai DX NET,
and the image/video renderers (their output is checked by eye).

**Confirmed live** against a real logged-in account: the session/re-login handling, the collection
writes behind `/cc-preset-*`, and the friends selectors (`/cc-friends`, `/cc-friend-profile`,
`/cc-friend-best`, `/cc-leaderboard`, `/cc-scores`' `friend` option).

**Best-effort, unverified:** `/cc-profile`'s selectors (`.name_block`, `.rating_block`,
`.trophy_block`) and `/cc-recent`'s per-play detail page — no reference project scrapes either, so if
a track's dropdown selection returns "couldn't load play detail", that's the guess needing an
adjustment rather than the rest of the bot being broken.

## Architectural limits on friend data

These are **not bugs** — SEGA simply doesn't expose the data:

* No raw DX score for a friend, only achievement and combo/sync. Enough to compute a rating, not
  enough for a DX-score-accurate best-50 — which is why `/cc-scores friend:` shows a shorter embed
  than it does for your own scores.
* No play counts or last-played timestamps for a friend.
* `/cc-friend-best`'s rating is always **computed locally** from scraped achievements. SEGA never
  shows a friend's real rating number anywhere.
* Favorite status has **no bearing** on score access. An empty result means the friend genuinely
  hasn't played that difficulty.

## Leech mode

`/cc-leech-send` / `/cc-leech-accept` let someone with no SEGA ID use the bot by reading **their own friend
entry** through a host's linked session. It is the friend-data path above, so every limit there applies:
achievement and combo/sync only, no DX score, no play counts or last-played, rating computed locally.

* Works: `/cc-profile` (core view), `/cc-best`, `/cc-scores` and `/cc-info`'s "Check my score" button.
* Doesn't (SEGA exposes nothing for a friend): `/cc-recent`, `/cc-album`, `/cc-circle*`, `/cc-display`,
  `/cc-preset-*`, `/cc-profile view:Extra`, `/cc-best next_update:True`. Not offered, to avoid exposing the host's
  friend list or costing the host's session a fan-out: `/cc-friends`, `/cc-friend-*`, `/cc-leaderboard`, and
  `/cc-scores friend:`.
* The leecher has to already be on the host's maimai DX NET friend list. There is no friend-code search or
  invite flow; the bot only stores the friend's id from the host's list.
* A leecher never sees the host's friend list: only the one stored friend id is ever fetched.
* Their reads use the host's session, so they share the host's one-live-session-per-account and the
  bot-wide request limiter. If the host unlinks, the link is deleted; if the host's session can't be renewed
  or they remove the friend, the leecher is told to ask the host.
* A user with an account of their own is never leeched.
* Offers expire after 7 days, and expired offers are purged hourly. Either side can end a link or offer with
  `/cc-leech-remove`; `/cc-logout` removes links in either role. Banning a host also deletes the links they host.
* Unverified live: whether a friend's id stays the same over time. If it changes, the stored name is used to find
  them again.

## `/cc-leaderboard` is the heaviest command

It costs one request per friend, plus two for your own profile and score list — on an account with
~50 friends that's ~50 requests, capped at 5 in flight at a time so it doesn't monopolise the
process-wide rate limiter.

Switching difficulty from the buttons re-runs that whole fan-out for the new difficulty and caches
the result, rather than pre-fetching all five up front.

Because of that cost it's the only command that asks for confirmation first. The prompt appears
before any request is made, so it can't name your exact friend count without already spending the
requests it's asking about. Declining (or letting it time out) releases the cooldown rather than
charging you for a command that never ran.

## `/cc-chart` coverage

Roughly 40% of charts are renderable as video. The join to mai-notes.com is by title + type +
difficulty, matching 6334 of 7140 non-UTAGE charts. The unmatched remainder is overwhelmingly
licence-removed songs plus UTAGE, which mai-notes has no concept of.

Video rendering is owner-only — it's by far the heaviest thing the bot does (a headless browser plus
a video encode), and only one render runs at a time. Everyone else gets the chart's stats instead.

## `/cc-best`'s editable template

`assets/b50/template.png` is the background layer every `/cc-best` render draws on top of. Replace it
with anything you like — any size, it's resized to 1500x1300 — to reskin every future render with no
code changes. If the file is ever missing, rendering falls back to a plain solid background rather
than failing.

## dxrating.net connectivity

The dxrating.net tags API (`miruku.dxrating.net`) and jacket CDN (`shama.dxrating.net`) may be
unreachable from some hosts. Everything downstream degrades gracefully — no tags shown, a solid
placeholder square instead of jacket art — rather than failing the command. If you see that fallback,
check connectivity to those two hosts before assuming a bug.

## Database upgrades

Schema changes are applied in place by a small `ALTER TABLE` migration on startup, so an existing
`circlechiffon.db` upgrades automatically with no manual changes or resets.
