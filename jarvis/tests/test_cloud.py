import http.client
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.google_tools import Google, WebAuth  # noqa: E402
from jarvis.hud import Hud  # noqa: E402
from jarvis.memory import Memory  # noqa: E402
from jarvis.tools import Tools  # noqa: E402
from jarvis.vision import Screen  # noqa: E402

CODE = "codigo-de-nuvem-bem-longo-123"
HOST = "meu.onrender.com"
WEB_CFG = {"web": {"client_id": "id.apps.googleusercontent.com", "client_secret": "s",
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                   "token_uri": "https://oauth2.googleapis.com/token"}}


def call(hud, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", hud.port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    r = c.getresponse()
    out = (r.status, r.getheader("Location"), r.read())
    c.close()
    return out


class FakeAuth:
    configured, connected = True, False

    def __init__(self):
        self.state = None

    def start(self, redirect_uri):
        self.redirect = redirect_uri
        return "https://accounts.google.com/o/oauth2/auth?x=1"

    def finish(self, state, code):
        return state == "ok" and code == "c"


def test_lockout_is_per_ip_behind_proxy_and_healthz_public():
    hud = Hud(0, lambda t: None, allowed_hosts=[HOST], access_code=CODE, trust_proxy=True)
    hud.start()
    try:
        assert call(hud, "GET", "/healthz", {"Host": "qualquer.com"})[0] == 200
        bad = {"Host": HOST, "X-Forwarded-For": "1.1.1.1", "X-Token": "chute"}
        for _ in range(12):
            call(hud, "POST", "/ask", bad, {"text": "x"})
        assert call(hud, "POST", "/ask", {**bad, "X-Token": CODE}, {"text": "oi"})[0] == 403        # atacante travado
        spoof = {**bad, "X-Forwarded-For": "9.9.9.9, 1.1.1.1", "X-Token": CODE}                     # prefixo forjado
        assert call(hud, "POST", "/ask", spoof, {"text": "oi"})[0] == 403
        owner = {"Host": HOST, "X-Forwarded-For": "2.2.2.2", "X-Token": CODE}
        assert call(hud, "POST", "/ask", owner, {"text": "oi"})[0] == 202                           # dono segue livre
    finally:
        hud.stop()


def test_google_endpoints_require_token_and_state():
    ga = FakeAuth()
    hud = Hud(0, lambda t: None, allowed_hosts=[HOST], access_code=CODE, google_auth=ga)
    hud.start()
    try:
        h = {"Host": HOST}
        assert call(hud, "GET", "/google/start", h)[0] == 403
        assert call(hud, "GET", "/google/start?token=errado", h)[0] == 403
        code, loc, _ = call(hud, "GET", f"/google/start?token={CODE}", h)
        assert code == 302 and loc.startswith("https://accounts.google.com/") and ga.redirect == f"https://{HOST}/google/callback"
        assert call(hud, "GET", "/google/callback?state=ruim&code=c", h)[0] == 400
        assert call(hud, "GET", "/google/callback?error=access_denied&state=ok", h)[0] == 400
        assert call(hud, "GET", "/google/callback?state=ok&code=c", {"Host": "evil.com"})[0] == 403
        code, loc, _ = call(hud, "GET", "/google/callback?state=ok&code=c", h)
        assert code == 302 and loc == "/"
    finally:
        hud.stop()


def test_webauth_state_is_single_use_and_expires(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_JSON", json.dumps(WEB_CFG))
    with tempfile.TemporaryDirectory() as tmp:
        ga = WebAuth(Path(tmp) / "c.json", Path(tmp) / "token.json")
        assert ga.configured
        url = ga.start(f"https://{HOST}/google/callback")
        assert url.startswith("https://accounts.google.com/") and "state=" in url and "offline" in url
        state = next(iter(ga._flows))

        class Flow:
            class credentials:
                @staticmethod
                def to_json():
                    return '{"t": 1}'

            def fetch_token(self, code):
                self.code = code

        ga._flows[state] = (time.time(), Flow())
        assert ga.finish("inventado", "c") is False
        assert ga.finish(state, "c") is True and (Path(tmp) / "token.json").exists()
        assert ga.finish(state, "c") is False                                   # uso único
        ga._flows["velho"] = (time.time() - 9999, Flow())
        assert ga.finish("velho", "c") is False                                  # expirado


def test_cloud_hides_pc_tools_and_google_until_token():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"runtime": {"cloud": True},
               "safety": {"workspace": tmp, "apps": {"x": "y"}, "confirm_tools": []},
               "screen": {"enabled": True}}
        g = Google(cfg, Path(tmp))
        t = Tools(cfg, Memory(Path(tmp) / "t.db"), google=g, screen=Screen(cfg))
        names = {s["name"] for s in t.schemas()}
        assert {"open_app", "screen_look", "system_status", "gmail_search"}.isdisjoint(names) and "get_time" in names
        (Path(tmp) / "token.json").write_text("{}")
        assert "gmail_search" in {s["name"] for s in t.schemas()}               # aparece após conectar
