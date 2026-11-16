"""A MaimaiNetClient that reaches maimai DX NET through a webworld account (etan's web world, the pbs repo) instead of
holding SEGA cookies itself.

The user links their SEGA ID inside webworld, makes a token there, and gives it to `/cc-login` (webworld option). The
Account row then stores (encrypted, like any session) a marker instead of a cookie jar:

    {"webworld": {"url": "https://<webworld>", "token": "mm_<uid>_<secret>"}}

and accounts.get_client() builds this class from it. Every request MaimaiNetClient would make goes to webworld's
`POST /api/maimai/v1/fetch`, which replays it with the member's DX NET session and hands back the raw answer (status,
Location, body; never SEGA's cookies). The answer is rebuilt as an httpx.Response, so all of MaimaiNetClient's error
handling, retry ladder and parsers run unchanged. Re-minting the session from `clal` happens on webworld's side
(`POST /api/maimai/v1/remint`), single-flighted there.

Why bother: DX NET keeps ONE live session per account. With webworld holding it, the bot and the user's webworld DX NET
browser share that one session instead of evicting each other, and the bot never sees a SEGA credential."""
import base64
import json

import httpx

from circlechiffon.adapters.maimai_net.client import _RATE_LIMITER, MaimaiNetClient
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired, TransientNetError

MARKER = "webworld"
TOKEN_PREFIX = "mm_"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_UA = "CiRCLE Chiffon (webworld relay)"

# webworld's own error codes -> what the user is told. A 401/409 is a SessionExpired so accounts.with_client's recovery
# path runs (refresh_session -> remint) and, failing that, the user gets the standard "please /cc-login again".
_TOKEN_GONE = ("Your webworld token was rejected (revoked, or the webworld account is gone or disabled). "
               "Make a new token in webworld's maimai DX NET app and run /cc-login again with the webworld option.")
_NOT_LINKED = ("No SEGA ID is linked in webworld any more. Link it again in webworld's maimai DX NET app "
               "(Account), then try again.")


def stored_marker(cookies_json: str) -> dict | None:
    """{'url', 'token'} if this stored session is a webworld link, else None."""
    try:
        data = json.loads(cookies_json)
    except (TypeError, ValueError):
        return None
    ww = data.get(MARKER) if isinstance(data, dict) else None
    if isinstance(ww, dict) and isinstance(ww.get("url"), str) and isinstance(ww.get("token"), str):
        return {"url": ww["url"], "token": ww["token"]}
    return None


def normalise_url(url: str) -> str | None:
    """https://host[:port] (no path) or http://localhost-ish for testing; None if unusable."""
    url = (url or "").strip().rstrip("/")
    try:
        u = httpx.URL(url)
    except Exception:
        return None
    if u.scheme == "https" and u.host:
        return url
    if u.scheme == "http" and u.host in ("localhost", "127.0.0.1", "::1"):
        return url
    return None


class WebworldError(MaimaiNetError):
    """webworld itself refused or failed (not DX NET)."""


class WebworldClient(MaimaiNetClient):
    def __init__(self, base_url: str, token: str):
        super().__init__([])
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._ww = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": _UA, "Accept": "application/json"},
            timeout=_TIMEOUT,
            follow_redirects=False,
        )

    async def close(self):
        await self._ww.aclose()
        await super().close()

    # ------------------------------------------------------------ storage
    @property
    def cookies(self) -> list[dict]:
        return []

    def cookies_json(self) -> str:
        return json.dumps({MARKER: {"url": self.base_url, "token": self.token}})

    # ------------------------------------------------------------ webworld calls
    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            if method == "GET":
                resp = await self._ww.get(path)
            else:
                resp = await self._ww.post(path, json=body or {})
        except httpx.HTTPError as e:
            raise WebworldError(f"Couldn't reach webworld ({type(e).__name__}). Try again in a moment.") from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        code = data.get("error") if isinstance(data, dict) else None
        if resp.status_code == 401:
            raise SessionExpired(_TOKEN_GONE)
        if resp.status_code == 403:
            raise WebworldError("webworld says this account can't use maimai DX NET there (the `maimai` permission was removed).")
        if resp.status_code == 409 and code == "not_linked":
            raise SessionExpired(_NOT_LINKED)
        if resp.status_code in (429, 503):
            raise TransientNetError("webworld is busy right now - please run that command again in a moment.")
        if resp.status_code == 400 and code == "dx_bad_url":
            raise WebworldError("webworld refused to open that DX NET page.")
        if resp.status_code != 200 or not isinstance(data, dict):
            detail = f" ({code})" if code else f" (HTTP {resp.status_code})"
            raise WebworldError("webworld couldn't complete the request" + detail + ".")
        return data

    async def _relay(self, method: str, url: str, data: dict | None = None) -> httpx.Response:
        body = {"url": url, "method": method}
        if data is not None:
            body["data"] = {str(k): str(v) for k, v in data.items()}
        j = await self._call("POST", "/api/maimai/v1/fetch", body)
        headers = {}
        if j.get("location"):
            headers["location"] = j["location"]
        if j.get("type"):
            headers["content-type"] = j["type"]
        if "b64" in j:
            content = base64.b64decode(j["b64"])
        else:
            content = str(j.get("text", "")).encode("utf-8")
            headers.setdefault("content-type", "text/html; charset=utf-8")
        status = j.get("status")
        if not isinstance(status, int):
            raise WebworldError("webworld sent an answer this version doesn't understand.")
        return httpx.Response(status, headers=headers, content=content, request=httpx.Request(method, j.get("url") or url))

    async def _get(self, url: str) -> httpx.Response:
        async with _RATE_LIMITER:
            return await self._relay("GET", url)

    async def _post(self, url: str, data: dict) -> httpx.Response:
        async with _RATE_LIMITER:
            return await self._relay("POST", url, data)

    async def get_image_bytes(self, url: str) -> bytes | None:
        """Same contract as MaimaiNetClient's: None on any failure, so renderers fall back to a placeholder (a webworld
        hiccup must not fail a whole /cc-best render over one icon)."""
        try:
            return await super().get_image_bytes(url)
        except MaimaiNetError:
            return None

    # ------------------------------------------------------------ session
    async def login(self, sid: str, password: str, totp: str | None = None) -> None:
        raise MaimaiNetError("This account is linked through webworld; link the SEGA ID there instead.")

    def _has_clal(self) -> bool:
        return True       # webworld holds it; it says when it's spent

    async def refresh_session(self) -> bool:
        """Ask webworld to mint a new DX NET session from its stored clal. Never raises."""
        try:
            ok = bool((await self._call("POST", "/api/maimai/v1/remint", {})).get("ok"))
        except MaimaiNetError:
            return False
        if ok:
            self.session_refreshed = True
        return ok

    async def whoami(self) -> dict:
        """webworld's /v1/me: {member, linked, name, scope, token}. Raises on a bad token."""
        return await self._call("GET", "/api/maimai/v1/me")


def client_from_stored(cookies_json: str) -> WebworldClient | None:
    m = stored_marker(cookies_json)
    if m is None:
        return None
    return WebworldClient(m["url"], m["token"])
