# Known limitations and testing notes

## What's verified, and what isn't

There is no official maimai DX NET API — everything is HTML scraping — and no automated test suite.

**Unit-testable with no live account:** the rating calculator
(`circlechiffon/ratingcalc/calculator.py`) and the song catalog/search
(`circlechiffon/songdata/catalog.py`).

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
