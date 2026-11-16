# How credential handling works

`/cc-login` opens a Discord modal — never a plain slash-command argument, so your credentials never
appear in a channel or interaction log — asking for your SEGA ID username and password.

By default the bot uses them **once**, in memory, to log into SEGA's Aime auth gateway and obtain a
maimai DX NET session, then discards the password entirely. Only the resulting session token is
stored, encrypted at rest (Fernet, via `cryptography`) in the local SQLite database.

## Session renewal

The login flow keeps SEGA's persistent Aime token (`clal`), which lets the bot mint a **fresh session
with no password** whenever the current one expires. So in normal use you should not need to
`/cc-login` again, even without storing your password.

Two things can still end a session:

* maimai DX NET allows **one live session per account**. If you log into DX NET in your own browser,
  the bot's session is evicted (and vice versa). The bot recovers from this silently.
* Clicking **"Logout"** in your own browser revokes the persistent token itself. That one does
  require a fresh `/cc-login`. (The bot never calls that endpoint — `/cc-logout` only deletes its own
  stored row.)

## Optional: `/cc-login webworld:True`

**Linking CiRCLE Chiffon to a personal webworld site. You won't need to worry about this - webworld accounts are whitelist only.**

Links through a **webworld** account (etan's web world) instead of giving the bot anything from SEGA. You link your SEGA ID in
webworld's maimai DX NET app (webworld uses the password once to sign in and keeps the resulting session, sealed), make a token
there, and paste only the token into the `/cc-login` modal. What the bot stores, encrypted at rest like any session:

    {"webworld": {"url": "<config.json webworld_url>", "token": "mm_..."}}

Every DX NET page the bot needs is then fetched by webworld with your session and handed back (`adapters/maimai_net/webworld.py`).
The bot never sees your SEGA ID, password, or SEGA's cookies. Any stored `remember_password` credentials are deleted when you link
this way. Revoking the token in webworld (or unlinking your SEGA ID there) stops the bot reading; `/cc-logout` forgets the token here.
A *read + collection* token is needed for `/cc-preset-load` (it sets your icon/plate/frame/title).

## Optional: `/cc-login remember_password:True`

An opt-in fallback for when the persistent token is spent. Turning it on shows an explicit warning
and requires you to confirm via a button before anything is stored. If you confirm, your SEGA ID
username **and password** are stored, encrypted at rest the same way the session is, and the bot can
silently re-login with them.

This is **less secure** than the default: a stored password is far more valuable to an attacker than
a session token, which can simply be invalidated. Only opt in if you're comfortable with that
tradeoff.

## Leech mode

If you run `/cc-leech-send`, the person you name can have the bot read **their own** friend entry through
*your* linked session (see [leech mode](./limitations.md#leech-mode)). Nothing of yours is shown to them -
no password, cookie or friend list - but their requests do use your session, so they count toward its single
live session and can contribute to DX NET evicting it. Unlinking with `/cc-logout` ends every link you host.

## Deleting your data

`/cc-logout` deletes everything stored — the session token and, if you opted in, your credentials, plus any
leech links you host or use — at once.

## For selfhosters

Session tokens and stored credentials are encrypted with a Fernet key. By default that key is
auto-generated into `.circlechiffon.key` on first run, which works out of the box but leaves the key
sitting on disk next to what it unlocks.

For better security, set `CIRCLECHIFFON_ENCRYPTION_KEY` to a Fernet key of your own before starting
the bot. **Don't lose it** — if you do, every user will need to `/cc-login` again.
