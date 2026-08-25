import asyncio
import http.cookiejar
import json
from typing import Awaitable, Callable

import httpx
from aiolimiter import AsyncLimiter

from circlechiffon.adapters.maimai_net import urls
from circlechiffon.adapters.maimai_net.errors import (
    AimeCardUnavailable,
    InvalidCredentials,
    MaintenanceError,
    MaimaiNetError,
    SessionExpired,
    TotpRequired,
    TransientNetError,
    UnexpectedResponse,
)
from circlechiffon.adapters.maimai_net.parser import (
    is_error_page,
    is_maintenance,
    is_transient_error,
    parse_circle,
    parse_circle_members,
    parse_equipped_collection_image,
    parse_error_page,
    parse_friend_detail,
    parse_friend_list,
    parse_friend_list_page_count,
    parse_friend_scores,
    parse_music_records,
    parse_photos,
    parse_profile,
    parse_profile_extras,
    parse_recent_records,
    parse_recent_score_detail,
    parse_song_play_stats,
)
from circlechiffon.types import (
    Circle,
    CircleMember,
    Difficulty,
    FriendEntry,
    Judgements,
    Photo,
    Profile,
    ProfileExtras,
    RecentScore,
    Score,
    SongPlayStats,
)

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0" # furryfox :3

_COMMON_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en;q=0.9,ja;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# we don't want to get ip banned :(
_RATE_LIMITER = AsyncLimiter(10, 1)

# Backoff between _get_page retries of a transient DX NET error; one entry
# per retry, so 2 entries = 3 attempts total. The last attempt reloads the
# home page first (see _get_page) - the "back out and re-enter" a browser
# user does by hand when DX NET tells them the connection time expired.
_TRANSIENT_RETRY_DELAYS = (1.0, 2.0)


class MaimaiNetClient:
    def __init__(self, cookies: list[dict] | None = None):
        initial_jar = httpx.Cookies()
        for c in cookies or []:
            # built via http.cookiejar.Cookie directly rather than
            # httpx.Cookies.set() - that helper hardcodes secure=False and
            # expires=None regardless of what's passed in, silently
            # discarding those attributes from the real Set-Cookie response
            # every time a session is reloaded from storage. Faithfully
            # round-tripping them so a reloaded session matches the one that
            # was actually issued as closely as possible.
            domain = c.get("domain", "")
            path = c.get("path", "/")
            expires = c.get("expires")
            initial_jar.jar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=c["name"],
                    value=c["value"],
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=bool(domain),
                    domain_initial_dot=domain.startswith("."),
                    path=path,
                    path_specified=bool(path),
                    secure=c.get("secure", False),
                    expires=expires,
                    discard=expires is None,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": None},
                    rfc2109=False,
                )
            )
        self._client = httpx.AsyncClient(
            headers=_COMMON_HEADERS,
            cookies=initial_jar,
            follow_redirects=False,
            timeout=15.0,
        )

    async def __aenter__(self) -> "MaimaiNetClient":
        return self

    async def __aexit__(self, *exc_info):
        await self.close()

    async def close(self):
        await self._client.aclose()

    @property
    def cookies(self) -> list[dict]:
        # httpx.AsyncClient(cookies=...) copies the jar it's given into its own
        # internal Cookies object at construction time - self._client.cookies is
        # the one Set-Cookie response headers actually get written to, so it's
        # the only thing that reflects the real post-login session. Serialized
        # as a list of {name, value, domain, path} (not a flat name->value
        # dict): the login flow sets cookies across two different hosts (the
        # SEGA Aime gateway and maimaidx-eng.com), and if any cookie name were
        # ever reused across both, dict(httpx.Cookies(...)) raises
        # httpx.CookieConflict - collapsing to a flat dict also silently
        # discards which host each cookie belongs to.
        return [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expires": c.expires,
            }
            for c in self._client.cookies.jar
        ]

    def cookies_json(self) -> str:
        return json.dumps(self.cookies)

    @classmethod
    def from_cookies_json(cls, cookies_json: str) -> "MaimaiNetClient":
        data = json.loads(cookies_json)
        if isinstance(data, dict):
            # backward compat with the old (buggy) flat name->value format
            data = [{"name": name, "value": value, "domain": "", "path": "/"} for name, value in data.items()]
        return cls(data)

    async def _get(self, url: str) -> httpx.Response:
        async with _RATE_LIMITER:
            resp = await self._client.get(url)
        return resp

    async def _post(self, url: str, data: dict) -> httpx.Response:
        async with _RATE_LIMITER:
            resp = await self._client.post(url, data=data)
        return resp

    async def login(self, sid: str, password: str, totp: str | None = None) -> None:
        """Log in with a SEGA ID username/password (+ optional TOTP code), used
        once to obtain a session then discarded by the caller."""
        await self._get(urls.INTL["LOGIN_PAGE"])

        login_resp = await self._post(
            urls.INTL["LOGIN_ENDPOINT"],
            {"sid": sid, "password": password, "retention": "1"},
        )

        if login_resp.status_code != 302:
            raise UnexpectedResponse(f"unexpected login response status: {login_resp.status_code}")

        raw_redirect = login_resp.headers.get("location")
        if not raw_redirect:
            raise InvalidCredentials("SEGA ID login was rejected.")
        # the gateway's Location header isn't guaranteed to be absolute - resolve
        # it against the response's own URL the same way a browser would, so a
        # relative redirect target doesn't blow up with httpx.UnsupportedProtocol
        redirect_url = login_resp.url.join(raw_redirect)

        if str(redirect_url).startswith("https://lng-tgk-aime-gw.am-all.net/common_auth/login/otp"):
            if not totp:
                raise TotpRequired("This account has two-factor authentication enabled; a TOTP code is required.")
            otp_resp = await self._post(urls.INTL["LOGIN_OTP_ENDPOINT"], {"password": totp})
            if otp_resp.status_code != 302:
                raise UnexpectedResponse(f"unexpected OTP response status: {otp_resp.status_code}")
            raw_redirect = otp_resp.headers.get("location")
            if not raw_redirect:
                raise InvalidCredentials("The TOTP code was rejected.")
            redirect_url = otp_resp.url.join(raw_redirect)

        dest_resp = await self._get(str(redirect_url))
        dest_text = dest_resp.text

        if is_maintenance(dest_text):
            raise MaintenanceError("maimai DX NET is currently under maintenance.")

        if dest_resp.is_redirect and urls.is_error_page_url(
            dest_resp.url.join(dest_resp.headers.get("location") or "")
        ):
            raise AimeCardUnavailable(
                "SEGA ID authentication succeeded, but maimai DX NET did not provide a usable Aime card."
            )

        if str(redirect_url).startswith("https://lng-tgk-aime-gw.am-all.net/common_auth/login"):
            raise InvalidCredentials("SEGA ID or password was rejected.")

        # follow through to the home page to settle the maimaidx-eng.com session cookie
        home_resp = await self._get(urls.INTL["HOME_PAGE"])
        if is_maintenance(home_resp.text):
            raise MaintenanceError("maimai DX NET is currently under maintenance.")

    async def _classify_error_page(self, error_url, html: str | None = None) -> MaimaiNetError:
        """Decide *why* maimai DX NET sent us to its error page.

        Everything used to be SessionExpired here, which is wrong for the
        transient "Connection time has expired, please try again later" case:
        the cookie is still good and a plain refresh gets past it, but the
        user was told to /cc-login again (or a remember_password account
        burned a full silent re-login) instead of the request just being
        retried.

        Costs one extra request - the error page body is where the code and
        message live, and follow_redirects=False means a 302 only ever gave
        us the Location header. Only paid on the error path.
        """
        if html is None:
            try:
                html = (await self._get(str(error_url))).text
            except httpx.HTTPError:
                # can't read the body, so we can't tell transient from
                # expired - fall back to the old behavior rather than
                # retrying something that may never succeed
                return SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")

        code, message = parse_error_page(html)
        detail = " ".join(part for part in (f"(error code {code})" if code else None, message) if part)

        # Confirmed live: 100001 / "Please login again" is what a stale or
        # absent session cookie gets. That's a real expiry - raise it
        # immediately so the /cc-login path (and with_client's silent
        # re-login) stays as fast as it is today, with no retry delay.
        if code in urls.SESSION_EXPIRED_ERROR_CODES or any(
            marker in html for marker in urls.SESSION_EXPIRED_ERROR_STRINGS
        ):
            return SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")

        if is_transient_error(html):
            return TransientNetError(
                "maimai DX NET is busy right now - please run that command again in a moment."
                + (f" {detail}" if detail else ""),
                code=code,
                # 200002 means "this cookie isn't usable right now", which a
                # wedged-but-live session recovers from and a dead one never
                # does. Retry either way; _get_page decides which it was.
                session_suspect=code in urls.SESSION_SUSPECT_ERROR_CODES,
            )

        # Unrecognized code. Retry it: an unknown error page is far more
        # likely to be another transient hiccup than a permanent failure
        # (the one permanent case we know of, 100001, is handled above), and
        # three attempts costs ~3s. SEGA's own wording is carried through so
        # an unknown code is identifiable from the message the user sees -
        # the adapter has no logging to report it any other way.
        return TransientNetError(
            f"maimai DX NET returned an error{(' ' + detail) if detail else ''}.",
            code=code,
        )

    async def _fetch_page(self, url: str) -> str:
        """One attempt at fetching an authenticated page. Raises
        TransientNetError for anything _get_page should retry."""
        resp = await self._get(url)
        text = resp.text

        if is_maintenance(text):
            raise MaintenanceError("maimai DX NET is currently under maintenance.")

        if resp.is_redirect:
            # resolve first: the raw header may be relative, and matching it
            # by exact string (as this used to) missed every variant, which
            # doesn't raise - it returns the empty 302 body to a parser that
            # never raises, so the command silently comes back empty.
            location = resp.url.join(resp.headers.get("location") or "")
            if urls.is_error_page_url(location):
                raise await self._classify_error_page(location)
            if "common_auth" in str(location):
                raise SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")
            # confirmed live: a request with no session cookie at all lands
            # here, not on /error/
            if urls.is_landing_page_url(location):
                raise SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")

        # the error page is served as a 200, so it never showed up in the
        # redirect checks above and flowed straight into the parsers
        if is_error_page(text):
            raise await self._classify_error_page(url, html=text)

        return text

    async def _get_page(self, url: str) -> str:
        """Fetch an authenticated page, retrying the transient DX NET errors
        that a browser user gets past by refreshing.

        Only TransientNetError is retried - SessionExpired and
        MaintenanceError propagate on the first attempt exactly as before, so
        no latency is added to either of those paths."""
        last: TransientNetError | None = None

        for attempt in range(len(_TRANSIENT_RETRY_DELAYS) + 1):
            if attempt:
                await asyncio.sleep(_TRANSIENT_RETRY_DELAYS[attempt - 1])
                if attempt == len(_TRANSIENT_RETRY_DELAYS):
                    # final attempt: reload home first to shake loose
                    # whatever per-session page state DX NET wedged on.
                    # Best-effort - a failure here must not mask `last`.
                    try:
                        await self._get(urls.INTL["HOME_PAGE"])
                    except (httpx.HTTPError, MaimaiNetError):
                        pass
            try:
                return await self._fetch_page(url)
            except TransientNetError as e:
                last = e

        assert last is not None
        if last.session_suspect:
            # never cleared, so treat it as a dead session after all - this
            # is what routes a genuinely expired cookie to with_client's
            # silent re-login (or the /cc-login message) instead of leaving
            # the user with a "busy, try again" that would never come right.
            raise SessionExpired(
                "Your maimai DX NET session has expired. Please /cc-login again."
            ) from last
        raise last

    async def get_profile_page_html(self) -> str:
        # Player's Data page, not home - same header markup (name/rating/
        # title/icon/dan/class), plus play-count stats and the CP/mission/
        # ticket/intimate-item section home doesn't have. Exposed
        # separately from get_profile()/get_profile_extras() so a caller
        # that needs both views' data (see cogs/profile.py) fetches this
        # page exactly once instead of hitting it twice.
        return await self._get_page(urls.INTL["PLAYER_DATA_PAGE"])

    async def get_profile(self) -> Profile:
        return parse_profile(await self.get_profile_page_html())

    async def get_profile_extras(self, html: str | None = None) -> ProfileExtras:
        """Parses the Player's Data page's CP/mile/mission/ticket/
        intimate-item section. Pass `html` from a prior
        get_profile_page_html() call to avoid a second fetch."""
        if html is None:
            html = await self.get_profile_page_html()
        return parse_profile_extras(html)

    async def get_image_bytes(self, url: str) -> bytes | None:
        """Fetches an arbitrary image through this account's own
        authenticated session - needed for per-account assets (profile
        icon, dan/class-rank badges) that aren't on a public CDN, unlike
        dxrating jackets. Returns None on any failure rather than raising,
        so a renderer can fall back to a placeholder."""
        try:
            resp = await self._get(url)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        return resp.content

    async def get_equipped_nameplate_url(self) -> str | None:
        html = await self._get_page(urls.INTL["NAMEPLATE_PAGE"])
        return parse_equipped_collection_image(html, ".w_396.m_r_10")

    async def get_equipped_frame_url(self) -> str | None:
        html = await self._get_page(urls.INTL["FRAME_PAGE"])
        return parse_equipped_collection_image(html, ".w_396.m_r_10")

    async def get_leader_tour_member_url(self) -> str | None:
        html = await self._get_page(urls.INTL["TOUR_MEMBER_PAGE"])
        return parse_equipped_collection_image(html, ".chara_cycle_img")

    async def get_circle(self) -> Circle | None:
        html = await self._get_page(urls.INTL["CIRCLE_PAGE"])
        return parse_circle(html)

    async def get_photos(self) -> list[Photo]:
        """The in-game "Album" - unguarded, unlike get_circle_members():
        for /cc-album the photo list *is* the whole point of the command,
        so a fetch failure should surface as an error rather than
        silently render an empty album."""
        html = await self._get_page(urls.INTL["PHOTO_PAGE"])
        return parse_photos(html)

    async def get_circle_members(self) -> list[CircleMember]:
        """Best-effort - see parser.parse_circle_members's docstring. A
        MaintenanceError/SessionExpired still propagates normally; any other
        failure to load this specific page degrades to an empty list rather
        than breaking the whole /cc-circle command."""
        try:
            html = await self._get_page(urls.INTL["CIRCLE_MEMBER_PAGE"])
        except (SessionExpired, MaintenanceError):
            raise
        except MaimaiNetError:
            return []
        return parse_circle_members(html)

    async def get_recent_scores(self) -> list[RecentScore]:
        html = await self._get_page(urls.INTL["RECORD_RECENT_PAGE"])
        return parse_recent_records(html)

    async def get_music_scores(
        self,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        include_utage: bool = True,
    ) -> list[Score]:
        """Fetch every difficulty's score list (BASIC..Re:MASTER, plus UTAGE
        unless `include_utage=False` - UTAGE charts don't count toward
        rating, so /cc-best skips them) - one request per difficulty, all
        fired concurrently (still capped by the shared rate limiter). Tasks
        are then awaited back in `urls.MUSIC_RECORD_DIFF_VALUES` order
        rather than completion order, so `on_progress` (if given) always
        reports difficulties in that same fixed order regardless of which
        request actually lands first - the requests still run in parallel
        underneath, this only fixes the order results are consumed in."""
        diff_values = [
            v for v in urls.MUSIC_RECORD_DIFF_VALUES if include_utage or v != urls.UTAGE_DIFF_VALUE
        ]

        async def fetch(diff_value: int) -> list[Score]:
            url = f"{urls.INTL['RECORD_MUSICS_PAGE']}?genre=99&diff={diff_value}"
            html = await self._get_page(url)
            return parse_music_records(html)

        tasks = [asyncio.create_task(fetch(v)) for v in diff_values]
        all_scores: list[Score] = []
        try:
            for diff_value, task in zip(diff_values, tasks):
                scores = await task
                if on_progress is not None:
                    await on_progress(urls.MUSIC_RECORD_DIFF_LABELS[diff_value])
                all_scores.extend(scores)
        except BaseException:
            # A failure here (e.g. SessionExpired on one difficulty) leaves later
            # in-flight tasks dangling. with_client()'s finally then closes the
            # shared httpx client under them, so left alone they raise their own
            # unretrieved exceptions later. Cancel and drain before propagating.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return all_scores

    async def get_song_play_stats(self, idx: str) -> dict[Difficulty, SongPlayStats]:
        """Best-effort fetch of a song's per-difficulty play count and last-
        played timestamp from its musicDetail page (confirmed live: the idx
        of any one of the song's score rows reaches a page listing every
        difficulty's own stats). Returns {} rather than raising if the page
        doesn't parse as expected - this is enrichment on top of the score
        itself, never worth failing the whole command over."""
        try:
            html = await self._get_page(f"{urls.INTL['MUSIC_DETAIL_PAGE']}?idx={idx}")
        except (SessionExpired, MaintenanceError):
            raise
        except MaimaiNetError:
            return {}
        return parse_song_play_stats(html)

    async def get_friend_list(self) -> list[FriendEntry]:
        """Confirmed live: /friend/ lists every friend as a profile-card row
        (same markup as Player's Data), each addressed by a hidden idx that
        every friend sub-page below needs. SEGA never shows this idx as a
        user-facing "friend code" anywhere - it's purely an internal id.

        The list paginates at 10/page - page 1 is fetched here directly,
        and any remaining pages (confirmed live: stateless from URL params
        alone, so all of them can be fetched concurrently rather than
        crawled one at a time) via /friend/pages/?type=next&idx={page-1}."""
        first_html = await self._get_page(urls.INTL["FRIEND_LIST_PAGE"])
        entries = parse_friend_list(first_html)
        page_count = parse_friend_list_page_count(first_html)
        if page_count <= 1:
            return entries

        async def fetch_page(page: int) -> list[FriendEntry]:
            url = f"{urls.INTL['FRIEND_LIST_PAGES_ENDPOINT']}?type=next&idx={page - 1}"
            html = await self._get_page(url)
            return parse_friend_list(html)

        tasks = [asyncio.create_task(fetch_page(p)) for p in range(2, page_count + 1)]
        try:
            for task in tasks:
                entries.extend(await task)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return entries

    async def get_friend_profile(self, idx: str) -> FriendEntry | None:
        """Confirmed live: /friend/friendDetail/?idx=... - same profile card
        as get_friend_list()'s rows, one friend at a time. Returns None if
        the idx doesn't resolve to a friend (e.g. stale idx, no longer
        friends)."""
        html = await self._get_page(f"{urls.INTL['FRIEND_DETAIL_PAGE']}?idx={idx}")
        return parse_friend_detail(html, idx)

    async def get_friend_scores(
        self,
        idx: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[Score]:
        """Fetch a friend's achievement/combo/sync per difficulty (confirmed
        live against /friend/friendGenreVs/battleStart/ - BASIC..Re:MASTER,
        no UTAGE, same concurrent-fetch-then-fixed-order-await shape as
        get_music_scores()). No raw DX score is obtainable for a friend (see
        urls.py's FRIEND_SCORE_PAGE comment), only achievement - enough for
        calculate_best50/calculate_rating, which is all this is for.

        SEGA appears to only return data here for friends the account has
        marked as a Favorite on the friend list - a friend who isn't
        favorited comes back with every difficulty empty, same as a friend
        who's genuinely never played; callers can't tell those apart from
        an empty result alone."""
        diff_labels = {v: urls.MUSIC_RECORD_DIFF_LABELS[v] for v in urls.FRIEND_SCORE_DIFF_VALUES}
        # same 0..4 mapping as MUSIC_RECORD_DIFF_LABELS, just as Difficulty
        # members instead of display strings (there's no UTAGE case here).
        diff_values_to_difficulty = {
            0: Difficulty.basic,
            1: Difficulty.advanced,
            2: Difficulty.expert,
            3: Difficulty.master,
            4: Difficulty.remaster,
        }

        async def fetch(diff_value: int) -> list[Score]:
            difficulty = diff_values_to_difficulty[diff_value]
            url = f"{urls.INTL['FRIEND_SCORE_PAGE']}&diff={diff_value}&idx={idx}"
            html = await self._get_page(url)
            return parse_friend_scores(html, difficulty)

        tasks = [asyncio.create_task(fetch(v)) for v in urls.FRIEND_SCORE_DIFF_VALUES]
        all_scores: list[Score] = []
        try:
            for diff_value, task in zip(urls.FRIEND_SCORE_DIFF_VALUES, tasks):
                scores = await task
                if on_progress is not None:
                    await on_progress(diff_labels[diff_value])
                all_scores.extend(scores)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return all_scores

    async def get_recent_score_detail(self, idx: str) -> Judgements | None:
        """Best-effort fetch of a single play's judgment-count breakdown.
        Returns None (rather than raising) if the guessed endpoint/markup
        doesn't pan out - see urls.py's RECORD_DETAIL_PAGE comment.
        SessionExpired/MaintenanceError still propagate normally since those
        are meaningful regardless of this endpoint's uncertainty."""
        try:
            html = await self._get_page(f"{urls.INTL['RECORD_DETAIL_PAGE']}?idx={idx}")
        except (SessionExpired, MaintenanceError):
            raise
        except MaimaiNetError:
            return None
        return parse_recent_score_detail(html)
