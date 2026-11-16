"""adapters/maimai_net/webworld.py - reaching DX NET through a webworld token.

webworld is replaced by an httpx.MockTransport that answers /api/maimai/v1/* the way the real one does (pbs repo,
blog/maimai_routes.py), backed by a tiny fake DX NET: one live session, eviction, clal re-mint. Nothing touches the
network, config.json, circlechiffon.db or the real key file.
"""

import asyncio
import base64
import json

import httpx
import pytest

import crypto_utils
from circlechiffon import accounts
from circlechiffon.adapters.maimai_net import client as client_mod
from circlechiffon.adapters.maimai_net import webworld
from circlechiffon.adapters.maimai_net.errors import MaimaiNetError, SessionExpired, TransientNetError
from circlechiffon.database import engine as db_engine
from circlechiffon.database.models import Account

B = "https://maimaidx-eng.com/maimai-mobile/"
TOKEN = "mm_0123456789abcdef_" + "A" * 43
PROFILE_HTML = f"""<html><body><div class="see_through_block m_15 m_t_10 p_10 p_r t_l f_0"><div class="basic_block p_10 f_0">
<img src="{B}img/Icon/abc.png" class="w_112 f_l"/><div class="p_l_10 f_l"><div class="trophy_block trophy_Gold p_3 t_c f_0">
<div class="trophy_inner_block f_13"><span>Title</span></div></div><div class="m_b_5"><div class="name_block f_l f_16">ＰＬＡＹＥＲ</div>
<div class="f_r t_r f_0"><div class="p_r p_3"><img src="{B}img/rating_base_rainbow.png" class="h_30 f_r"/><div class="rating_block">15001</div>
</div></div></div></div></div></div></body></html>"""
ERROR_HTML = '<html><body><img src="title_error.png"><div class="p_5 f_14 ">ERROR CODE：{code}</div><div class="p_5 f_12 gray break">{msg}</div></body></html>'


class FakeWebworld:
    def __init__(self):
        self.session, self.live = "S1", "S1"      # our session vs the one DX NET currently accepts
        self.mints, self.calls = 0, []
        self.clal_ok = True
        self.status = 200                          # force a webworld-level answer (401, 409, 503...)
        self.error = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert "cookie" not in request.headers
        path = request.url.path
        self.calls.append(path)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": self.error or "x"})
        if path == "/api/maimai/v1/me":
            return httpx.Response(200, json={"member": "amy", "linked": True, "name": "ＰＬＡＹＥＲ", "scope": "read", "token": "bot"})
        if path == "/api/maimai/v1/remint":
            if not self.clal_ok:
                return httpx.Response(200, json={"ok": False})
            self.mints += 1
            self.session = self.live = f"S{self.mints + 1}"
            return httpx.Response(200, json={"ok": True})
        assert path == "/api/maimai/v1/fetch"
        body = json.loads(request.content)
        url = body["url"]
        out = {"url": url, "location": None, "type": "text/html; charset=utf-8"}
        if "logout" in url:
            return httpx.Response(400, json={"error": "dx_bad_url"})
        if url.startswith(B + "img/"):
            return httpx.Response(200, json={**out, "status": 200, "type": "image/png", "b64": base64.b64encode(b"\x89PNGdata").decode()})
        if url.startswith(B + "error"):
            return httpx.Response(200, json={**out, "status": 200, "text": ERROR_HTML.format(code="200002", msg="The connection time has been expired. Please try again later.")})
        if self.session != self.live:
            return httpx.Response(200, json={**out, "status": 302, "location": B + "error/", "text": ""})
        if body.get("method") == "POST":
            assert body["data"] == {"idx": "1", "token": "t"}
            return httpx.Response(200, json={**out, "status": 302, "location": B + "collection/", "text": ""})
        return httpx.Response(200, json={**out, "status": 200, "text": PROFILE_HTML})


@pytest.fixture
def fake(monkeypatch):
    f = FakeWebworld()
    real_init = webworld.WebworldClient.__init__

    def init(self, base_url, token):
        real_init(self, base_url, token)
        self._ww = httpx.AsyncClient(base_url=self.base_url, headers=self._ww.headers, transport=httpx.MockTransport(f.handler))
    monkeypatch.setattr(webworld.WebworldClient, "__init__", init)
    monkeypatch.setattr(client_mod, "_TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(client_mod, "_img_cache_path", lambda url: None)     # never write data/dxnet_img_cache
    return f


@pytest.fixture
def isolated_crypto(monkeypatch, tmp_path):
    monkeypatch.setattr(crypto_utils, "KEY_FILE", str(tmp_path / "test.key"))
    monkeypatch.setattr(crypto_utils, "_file_fernet", None)
    monkeypatch.setattr(crypto_utils, "_env_fernet", None)
    monkeypatch.setattr(crypto_utils, "_env_fernet_checked", False)
    monkeypatch.delenv(crypto_utils.ENV_KEY_VAR, raising=False)


def run(coro):
    return asyncio.run(coro)


def test_marker_round_trip():
    c = webworld.WebworldClient("https://ww.example/", TOKEN)
    try:
        stored = c.cookies_json()
        assert json.loads(stored) == {"webworld": {"url": "https://ww.example", "token": TOKEN}}
        assert webworld.stored_marker(stored) == {"url": "https://ww.example", "token": TOKEN}
        assert c.cookies == []
    finally:
        run(c.close())
    # an ordinary cookie jar (either stored shape) is not a marker
    assert webworld.stored_marker(json.dumps([{"name": "clal", "value": "x", "domain": "", "path": "/"}])) is None
    assert webworld.stored_marker(json.dumps({"userId": "1"})) is None
    assert webworld.stored_marker("not json") is None
    assert webworld.client_from_stored(json.dumps({"userId": "1"})) is None


def test_normalise_url():
    assert webworld.normalise_url("https://ww.example/") == "https://ww.example"
    assert webworld.normalise_url("http://127.0.0.1:8090") == "http://127.0.0.1:8090"
    for bad in ("", "ftp://x", "http://ww.example", "ww.example", "https://"):
        assert webworld.normalise_url(bad) is None, bad


def test_pages_parse_through_the_relay(fake):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            prof = await c.get_profile()
            assert (prof.display_name, prof.rating) == ("ＰＬＡＹＥＲ", 15001)
            assert await c.get_image_bytes(B + "img/Icon/abc.png") == b"\x89PNGdata"
            assert (await c.whoami())["linked"] is True
    run(go())


def test_eviction_is_reminted_on_webworld(fake):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            fake.live = "BROWSER"           # someone else took DX NET's one session
            prof = await c.get_profile()
            assert prof.display_name == "ＰＬＡＹＥＲ"
            assert fake.mints == 1
            assert c.session_refreshed      # accounts.with_client will persist (the marker is unchanged)
    run(go())


def test_spent_clal_is_session_expired(fake):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            fake.live, fake.clal_ok = "BROWSER", False
            with pytest.raises(SessionExpired):
                await c.get_profile()
    run(go())


@pytest.mark.parametrize("status,error,exc", [(401, "bad_token", SessionExpired), (409, "not_linked", SessionExpired),
                                              (503, "busy", TransientNetError), (403, "no_permission", MaimaiNetError),
                                              (502, "dx_upstream", MaimaiNetError)])
def test_webworld_errors(fake, status, error, exc):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            fake.status, fake.error = status, error
            with pytest.raises(exc):
                await c.get_profile()
            assert await c.refresh_session() is False     # never raises
    run(go())


def test_unreachable_webworld_is_a_maimai_error(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")
    real_init = webworld.WebworldClient.__init__

    def init(self, base_url, token):
        real_init(self, base_url, token)
        self._ww = httpx.AsyncClient(base_url=self.base_url, transport=httpx.MockTransport(boom))
    monkeypatch.setattr(webworld.WebworldClient, "__init__", init)

    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            with pytest.raises(MaimaiNetError):
                await c.whoami()
    run(go())


def test_image_failure_is_a_placeholder_not_an_error(fake):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            fake.status, fake.error = 502, "dx_upstream"
            assert await c.get_image_bytes(B + "img/Icon/abc.png") is None
    run(go())


def test_collection_post_goes_through(fake):
    async def go():
        async with webworld.WebworldClient("https://ww.example", TOKEN) as c:
            r = await c._post(B + "collection/set/", {"idx": "1", "token": "t"})
            assert r.status_code == 302 and r.headers["location"] == B + "collection/"
            with pytest.raises(MaimaiNetError):
                await c.login("sid", "pw")
    run(go())


def test_accounts_store_and_rebuild_a_webworld_link(fake, database, isolated_crypto):
    async def go():
        async with db_engine.session() as s:
            s.add(Account(discord_id=7, encrypted_cookie=crypto_utils.encrypt_value("[]"),
                          encrypted_credentials=crypto_utils.encrypt_value(json.dumps({"sega_id": "a", "password": "b"}))))
            await s.commit()
        c = webworld.WebworldClient("https://ww.example", TOKEN)
        await accounts.save_webworld(7, c, display_name="ＰＬＡＹＥＲ")
        await c.close()
        async with db_engine.session() as s:
            row = await s.get(Account, 7)
            assert row.encrypted_credentials is None          # the old SEGA password is dropped
            assert TOKEN not in row.encrypted_cookie          # encrypted at rest like any session
            assert row.display_name == "ＰＬＡＹＥＲ"
        assert await accounts.is_webworld(7)
        got = await accounts.get_client(7)
        assert isinstance(got, webworld.WebworldClient) and got.token == TOKEN
        await got.close()
        fake.live = "BROWSER"
        prof = await accounts.with_client(7, lambda cl: cl.get_profile())
        assert prof.rating == 15001
        assert await accounts.reauth(7) is None               # nothing to re-login with
    run(go())


def test_with_client_reports_a_revoked_token(fake, database, isolated_crypto):
    async def go():
        c = webworld.WebworldClient("https://ww.example", TOKEN)
        await accounts.save_webworld(8, c)
        await c.close()
        fake.status, fake.error = 401, "bad_token"
        with pytest.raises(SessionExpired):
            await accounts.with_client(8, lambda cl: cl.get_profile())
    run(go())
