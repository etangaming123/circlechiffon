# CiRCLE Chiffon Privacy Policy

**Effective Date**: 2026-08-23
**Bot Name**: CiRCLE Chiffon#4436
**Developer**: etangaming123
**Contact**:
email: me [at] etangaming [dot] xyz
discord: @etangaming123 (or the etan bot [+more] support server, linked on the site)

## 1. Introduction

This Privacy Policy ("Policy") describes how CiRCLE Chiffon ("the Bot", "we", "us", or "our") collects, uses, stores, and protects your information when you use it. By using the Bot, you consent to the handling of your information as described in this Policy.
**If you do not agree with this Policy, you must not use the Bot.**
This policy only describes what CiRCLE Chiffon itself collects, not what Discord collects.

## 2. Information We Collect

### 2.1. Technical Information

- **Error Logs**: When a command fails, details of the error may be printed to the console running the Bot for debugging purposes. These do not include your SEGA ID credentials.
- **Discord User ID**: Used as the key that links your Discord account to any maimai DX NET account you choose to link.
- **Command Cooldowns**: Your Discord User ID and the name of the command you last ran are held in memory (never written to disk) to enforce a short per-command cooldown, and are cleared automatically once the cooldown expires or the Bot restarts.
- **Ban Records**: If the developer bans your Discord User ID from using the Bot, a record is kept containing only your Discord User ID, the ban's expiry (if any) and reason (if any), and when it was issued - no username, message content, or server information is stored alongside it. This record is deleted automatically once the ban expires or is manually lifted.

### 2.2. Information Provided When Linking Your Account

None of this is collected unless you run `/cc-login`:

- **SEGA ID username, password, and (if applicable) TOTP code**: entered through a private Discord modal, never as plain slash-command text. By default, your password and TOTP code are used once, in memory, to log into SEGA's Aime authentication gateway, and are then discarded - they are **not** stored. Only the resulting **session cookie** is kept, encrypted at rest.
- **`remember_password` (opt-in only)**: if you explicitly enable this option and confirm the warning shown to you, your SEGA ID username **and** password are additionally stored, encrypted at rest, so the Bot can silently re-authenticate you when your session cookie expires.
- **Profile/play data**: your maimai DX NET display name, rating, title, and recent play/score data are fetched live from maimai DX NET when you run commands like `/cc-profile`, `/cc-recent`, and `/cc-best`; this data is used to build the response shown to you and is not separately retained beyond what's needed to render that response.

All of the above can be deleted at any time by running `/cc-logout`, which removes your session cookie and, if you opted in, your stored username/password, in one step.

**We do not collect any other personally identifiable information, such as your real name or physical address, unless you separately choose to provide it to us directly (e.g. by messaging the developer).**

## 3. How We Use Your Information

- **Service Provision**: to authenticate you against maimai DX NET and display your linked profile, recent plays, and Best-50 rating.
- **Session Maintenance**: to automatically refresh your session (only if you opted into `remember_password`) so you don't need to re-run `/cc-login` every time your session expires.
- **Error Diagnosis**: to identify and fix bugs using console error output.
- **Abuse Prevention**: to enforce per-command cooldowns and, where necessary, to ban a Discord User ID from using the Bot.

**We do not use your information for advertising, marketing, or any other commercial purpose.**

## 4. How We Store Your Information

Your linked-account data (session cookie, and username/password if you opted into `remember_password`) is stored in a local SQLite database (`circlechiffon.db`) on the machine running the Bot. These values are encrypted at rest using Fernet symmetric encryption. The encryption key is either supplied via an environment variable (`CIRCLECHIFFON_ENCRYPTION_KEY`) set by whoever runs the Bot, or, if that variable isn't set, auto-generated into a local key file (`.circlechiffon.key`) alongside the database on first run.
Ban records (Discord User ID, expiry, reason) are stored unencrypted in the same local SQLite database, since they contain no credentials or session data - just enough to enforce the ban. Cooldown state is kept in memory only and is never written to disk.
The machine running the officially hosted instance of CiRCLE Chiffon is not shared with any third party, and only the developer has access to it.

## 5. How We Share Your Information

**We do not share your information with any third parties**, with the following limited exceptions:

- **The login itself**: your SEGA ID username, password, and TOTP code (if provided) are sent directly to SEGA's own Aime authentication gateway, as part of performing the login you requested. They are not sent anywhere else.
- **dxrating.net lookups**: song lookups (`/cc-info`), the per-track detail shown in `/cc-recent`, and the jacket art used in `/cc-best` are enriched using dxrating.net's public tags API and image CDN. These are plain song/chart lookups; no information that identifies you is sent to dxrating.net as part of this.
- **Legal Compliance**: if required to do so by law or in response to a valid request from a public authority.

## 6. Data Retention

Linked-account data (session cookie, and username/password if opted in) is retained until you run `/cc-logout`, or until you unlink and relink with different credentials. Error logs are transient console output and are not persisted beyond normal log retention on the host machine. Cooldown state exists only in memory and is gone the moment it expires or the Bot restarts. A ban record is retained only for as long as the ban itself is active - it's deleted automatically the moment a timed ban expires or a ban is manually lifted, and we intentionally keep it minimal (see 2.1) rather than as a permanent moderation history.

## 7. Your Rights and Choices

At any point, you may:

- **Access or Update Your Information**: re-run `/cc-login` to replace what's stored for your account.
- **Delete Your Information**: run `/cc-logout` to remove everything the Bot has stored about your linked account.

For anything not covered by those commands, contact us using the information at the top of this page and we will respond to verifiable requests.

## 8. Children's Privacy

The Bot is not intended for use by children under the age of digital consent in their jurisdiction. We do not knowingly collect personal information from children. If we become aware that we have, we will take steps to delete it as soon as possible - please contact us using the information above if you believe this has happened.

## 9. Third-Party Services

The Bot relies on maimai DX NET/SEGA (for account linking) and dxrating.net (for chart tags and jacket art). We are not responsible for the privacy practices of these third parties, and recommend reviewing their own privacy policies separately.

## 10. Changes to This Privacy Policy

We may update this Privacy Policy from time to time. Material changes will be announced on the CiRCLE Chiffon support server and/or the GitHub repository. Your continued use of the Bot after a change takes effect constitutes acceptance of the revised Policy.

## 11. Contact Us

If you have any questions about this Privacy Policy, please contact us using the information at the top of this page.
