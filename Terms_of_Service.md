# CiRCLE Chiffon Terms of Service

**Effective Date**: 2026-10-10
**Bot Name**: CiRCLE Chiffon#4436
**Developer**: etangaming123
**Contact**:
email: me [at] etangaming [dot] xyz
discord: @etangaming123 (or the etan bot [+more] support server, linked below)

## 1. Introduction
This Terms of Service ("Agreement") describes the terms and conditions under which you may use the CiRCLE Chiffon Discord bot ("the Bot", "we", "us", or "our"). By using the Bot, you agree to be bound by this Agreement.
**If you do not agree with this Agreement, you must not use the Bot.**
You must also follow Discord's Terms of Service.
These terms do not apply if you are using your own self-hosted instance of the bot's source code - in that case, you (the self-hoster) are responsible for how that instance is run and what it stores.

## 2. Description of Service
CiRCLE Chiffon is a Discord bot that lets you look up maimai DX (arcade rhythm game) song and chart data, calculate rating points, and, if you choose to link your SEGA ID (or to use another user's linked account through leech mode, with more limited data), view your maimai DX NET profile, recent plays, and Best-50 rating from within Discord. The Bot is provided "as is" without any warranties of any kind, either express or implied.

## 3. Eligibility
You must be at least the age of digital consent in your jurisdiction to use the Bot. By using the Bot, you represent and warrant that you meet this eligibility requirement.

## 4. Account Linking
Song lookup (`/cc-info`) and the rating calculator (`/cc-rating`) work without linking anything.
Most of the Bot's other features require linking your maimai DX NET account via `/cc-login` (a few can instead be used through leech mode, below), which opens a private Discord modal asking for your SEGA ID email, password, and (if you have two-factor authentication enabled) a TOTP code. By submitting this modal, you authorize the Bot to use these credentials, one time, to log into SEGA's own Aime authentication gateway on your behalf, in order to obtain a maimai DX NET session. Your password and TOTP code are never stored by default - only the resulting session cookie is kept, encrypted at rest.
`/cc-login` also has an opt-in `remember_password` option. If you turn it on, you'll see an explicit warning and must confirm via a button before anything changes. If you confirm, your SEGA ID email **and** password are also stored, encrypted at rest, so the Bot can silently log you back in whenever your session expires, instead of asking you to run `/cc-login` again. This is optional and off by default.

**Leech mode.** Instead of linking your own account, you can use `/cc-profile`, `/cc-best` and `/cc-scores` (and the score button on `/cc-info`) through another user's linked account:
- The other user (the "host") runs `/cc-leech-send`, choosing you and one of the friends on their maimai DX NET friend list. You must already be on that friend list; the Bot cannot add friends or look up friend codes.
- You (the "leecher") then run `/cc-leech-accept`. Nothing happens until you do, and an offer that is not accepted within 7 days can no longer be accepted.
- By sending an offer, the host authorizes the Bot to use their linked session to read the chosen friend's data and show it to the leecher. The leecher does not enter, and is never given, any SEGA ID credentials, and never sees the host's session or friend list.
- Leech mode is more limited than linking your own account. It only shows what maimai DX NET shows a friend (achievement and combo/sync, not DX scores or play counts), ratings are calculated by the Bot, and commands that need your own account are not available.
- Leech mode is provided on a best-effort basis. It can stop working at any time, for example if the host unlinks their account, is banned, their session cannot be renewed, or you are removed from their friend list.
- Either person can end a link or offer at any time with `/cc-leech-remove`.
- If you link an account of your own, it is used instead of leech mode.

You may unlink your account and delete everything the Bot has stored about it at any time with `/cc-logout`.
We are not responsible for any misuse of your account information. You use the account-linking feature entirely at your own discretion.

## 5. User Responsibilities
As a user of the Bot, you agree to:

- Use the Bot in compliance with all applicable laws and regulations.
- Not use the Bot for any unlawful, harmful, fraudulent, or malicious purposes.
- Not interfere with or disrupt the Bot, or the servers or networks connected to it.
- Not attempt to gain unauthorized access to the Bot or any accounts, computer systems, or networks connected to the Bot.
- Only link a maimai DX NET account that belongs to you, or that you otherwise have explicit permission to link.
- Not attempt to circumvent per-command cooldowns or a ban placed on your Discord account (e.g. by using another account to do so on your behalf).
- Only send leech offers to people who have agreed to receive one, and only name a friend who is that person. Only accept an offer that is about you, from a host you trust.
- If you are a host, understand that you remain responsible for use of your account through any leech offer you have sent.
- Not use leech mode to evade a ban from the Bot or from SEGA, or to otherwise violate SEGA's terms of service.

## 6. Cooldowns and Bans
To keep the Bot responsive for everyone and to avoid triggering rate limits or account action from maimai DX NET/SEGA, commands are subject to a short per-user cooldown between uses. The developer may also ban a Discord user ID from using the Bot, for a limited time or permanently, at their discretion - for example in response to abuse, attempts to circumvent cooldowns, or other violations of this Agreement. See the Privacy Policy for what's stored to enforce a ban.

Bans are enforced by Discord user ID, not by maimai DX NET account. A banned user cannot use the Bot, including leech mode, and any leech links they host are removed, ending their leechers' access. You may still use your maimai DX NET account normally outside of the Bot. The Bot has no control over SEGA's own systems, and the developer has no ability to lift a ban placed on your maimai DX NET account by SEGA.

## 7. Third-Party Terms & Account Risk
By linking your maimai DX NET account, you acknowledge that you are providing your SEGA ID credentials to a third party (the Bot) in order to automate a login that SEGA otherwise expects you to perform yourself, and that this carries inherent risk. You should only use this feature if you understand and accept that risk. You acknowledge and accept that:

- The Bot is not affiliated with, endorsed by, or sponsored by SEGA, maimai DX NET, or dxrating.net.
- Using an automated login against maimai DX NET may be inconsistent with SEGA's own terms of service, and could carry consequences up to and including account action by SEGA, entirely outside of our control.
- The developer has no liability for any consequences that may arise from linking your account, including but not limited to any action taken against your SEGA ID or maimai DX NET account by SEGA.

You use this feature entirely at your own risk.

Leech mode carries its own risks. When a host sends a leech offer, they allow the Bot to make requests to maimai DX NET using their linked account on behalf of another person, and when a leecher accepts, they depend on that account for their access. Both of you acknowledge and accept that:

- The risks above apply to the host's account, and the extra requests made for leechers may make rate limiting, loss of the host's session, or action by SEGA against the host's account more likely.
- A leecher's access depends entirely on the host's account and can stop at any time without notice.
- Requests are made by the Bot, not by SEGA, and SEGA does not endorse or support leech mode.
- The developer has no liability for any consequences of using leech mode, including any action taken by SEGA against a host's SEGA ID or maimai DX NET account.

Only use leech mode if you understand and accept these risks.

## 8. Intellectual Property
The Bot's vendored song/chart data, its rating formula, and its chart-tag/jacket-art lookups are ported or sourced from [gekichumai/dxrating](https://github.com/gekichumai/dxrating) (MIT License). The general structure of the maimai DX NET login flow and the Pillow-based image rendering approach are cross-referenced against [beer-psi/chuni-penguin](https://github.com/beer-psi/chuni-penguin) (0BSD License), which implements the equivalent for a different game's companion service. Bundled fonts are separately licensed under the SIL Open Font License. None of the above are property of the developer; they remain subject to their own respective licenses. The Bot's own original code is the property of the developer and, as of this writing, is not distributed under a separate open-source license unless the repository states otherwise.

## 9. Third-Party Services
The Bot communicates with maimai DX NET/SEGA (to obtain your session and profile/play data, only if you link your account) and with dxrating.net's public API and image CDN (to enrich song lookups with chart tags and jacket art - this does not involve sending any of your personal data to dxrating.net). These third parties have their own terms of service, which we recommend reviewing separately.

## 10. Disclaimer of Warranties
THE BOT IS PROVIDED ON AN "AS IS" AND "AS AVAILABLE" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED. TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, THE DEVELOPER DISCLAIMS ALL WARRANTIES, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.

THE DEVELOPER DOES NOT WARRANT THAT:

- THE BOT WILL BE UNINTERRUPTED, SECURE, OR ERROR-FREE.
- ANY DEFECTS OR ERRORS IN THE BOT WILL BE CORRECTED.
- THE BOT WILL REMAIN COMPATIBLE WITH FUTURE CHANGES TO MAIMAI DX NET, DXRATING.NET, OR ANY OTHER THIRD-PARTY SERVICE IT RELIES ON.

## 11. Indemnification
You agree to indemnify and hold harmless the developer and any affiliates, licensors, and service providers from and against any claims, liabilities, damages, judgments, awards, losses, costs, expenses, or fees (including reasonable attorneys' fees) arising out of or relating to your violation of this Agreement or your use of the Bot.

## 12. Termination
This Agreement will terminate automatically if you fail to comply with any of its terms. The developer may also terminate this Agreement and your access to the officially hosted instance of CiRCLE Chiffon at any time, with or without cause, with or without notice.

You may also terminate this Agreement at any time by discontinuing your use of the Bot. If you have linked an account or are part of a leech link, `/cc-logout` deletes what the Bot has stored about you. Upon termination, all rights granted to you under this Agreement cease, and you must stop using the Bot.

You are permitted to use your own self-hosted instance of the Bot's source code, and this Agreement does not apply to that instance. However, you are responsible for how that instance is run and what it stores, and the developer has no liability for it.

## 13. Changes to This Agreement
We reserve the right to modify or replace this Agreement at any time. Updates to this Terms of Service will be posted on the CiRCLE Chiffon support server and/or the GitHub repository. By continuing to access or use the Bot after those revisions become effective, you agree to be bound by the revised terms. If you do not agree to the new terms, you are no longer authorized to use the officially hosted instance of the Bot.

## 14. Contact Us
If you have any questions about this Agreement, please contact us at the given contact information at the top of this page.
