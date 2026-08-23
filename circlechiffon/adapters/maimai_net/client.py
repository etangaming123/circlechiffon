import asyncio
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
    UnexpectedResponse,
)
from circlechiffon.adapters.maimai_net.parser import (
    is_maintenance,
    parse_circle,
    parse_circle_members,
    parse_equipped_collection_image,
    parse_music_records,
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
    Judgements,
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


class MaimaiNetClient:
    def __init__(self, cookies: list[dict] | None = None):
        initial_jar = httpx.Cookies()
        for c in cookies or []:
            initial_jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
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
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
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

        if dest_resp.status_code == 302 and dest_resp.headers.get("location") in urls.ERROR_PAGES:
            raise AimeCardUnavailable(
                "SEGA ID authentication succeeded, but maimai DX NET did not provide a usable Aime card."
            )

        if str(redirect_url).startswith("https://lng-tgk-aime-gw.am-all.net/common_auth/login"):
            raise InvalidCredentials("SEGA ID or password was rejected.")

        # follow through to the home page to settle the maimaidx-eng.com session cookie
        home_resp = await self._get(urls.INTL["HOME_PAGE"])
        if is_maintenance(home_resp.text):
            raise MaintenanceError("maimai DX NET is currently under maintenance.")

    async def _get_page(self, url: str) -> str:
        resp = await self._get(url)
        text = resp.text

        if is_maintenance(text):
            raise MaintenanceError("maimai DX NET is currently under maintenance.")
        if resp.status_code == 302 and resp.headers.get("location") in urls.ERROR_PAGES:
            raise SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")
        if resp.status_code == 302 and "common_auth" in (resp.headers.get("location") or ""):
            raise SessionExpired("Your maimai DX NET session has expired. Please /cc-login again.")

        return text

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
