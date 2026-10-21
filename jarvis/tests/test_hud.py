import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.hud import Hud  # noqa: E402


def req(hud, path, data=None, token=None, host=None):
    r = urllib.request.Request(f"http://127.0.0.1:{hud.port}{path}", method="POST" if data is not None else "GET")
    if host:
        r.add_header("Host", host)
    if token:
        r.add_header("X-Token", token)
    if data is not None:
        r.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_page_injects_token_and_auth():
    asked = []
    hud = Hud(0, asked.append)
    hud.start()
    try:
        code, body = req(hud, "/")
        assert code == 200 and hud.token.encode() in body and b"__TOKEN__" not in body
        assert req(hud, "/ask", {"text": "oi"})[0] == 403                     # sem token
        assert req(hud, "/ask", {"text": "oi"}, token="errado")[0] == 403
        assert req(hud, "/ask", {"text": "oi"}, token=hud.token, host="evil.com")[0] == 403  # DNS rebinding
        assert req(hud, "/", host="evil.com")[0] == 403
        assert req(hud, "/ask", {"text": "oi"}, token=hud.token)[0] == 202
        time.sleep(0.2)
        assert asked == ["oi"]
    finally:
        hud.stop()


def test_confirm_roundtrip_and_sse():
    hud = Hud(0, lambda t: None)
    hud.start()
    try:
        assert req(hud, f"/events?token=x")[0] == 403
        got = []

        def listen():
            r = urllib.request.urlopen(f"http://127.0.0.1:{hud.port}/events?token={hud.token}", timeout=5)
            for line in r:
                if line.startswith(b"data:"):
                    got.append(json.loads(line[5:]))
                    if got[-1]["type"] == "confirm":
                        return

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        time.sleep(0.3)
        res = {}
        c = threading.Thread(target=lambda: res.setdefault("ok", hud.confirm("write_file x")), daemon=True)
        c.start()
        t.join(3)
        assert got and got[-1]["action"] == "write_file x"
        assert req(hud, "/confirm", {"ok": True}, token=hud.token)[0] == 204
        c.join(3)
        assert res["ok"] is True
    finally:
        hud.stop()


CODE = "codigo-forte-123456"


def test_remote_mode_code_not_in_page_hosts_and_lockout():
    hud = Hud(0, lambda t: None, allowed_hosts=["pc.tail.ts.net"], access_code=CODE)
    hud.start()
    try:
        code, body = req(hud, "/")
        assert code == 200 and CODE.encode() not in body and b"__TOKEN__" not in body   # nunca embutido
        for path, ctype in (("/manifest.webmanifest", b"JARVIS"), ("/sw.js", b"fetch"), ("/icon-192.png", b"PNG")):
            c, b = req(hud, path)
            assert c == 200 and ctype in b
        assert req(hud, "/ask", {"text": "oi"}, token=CODE, host="pc.tail.ts.net")[0] == 202   # host liberado
        assert req(hud, "/ask", {"text": "oi"}, token=CODE, host="outro.com")[0] == 403         # host não liberado
        assert req(hud, "/ask", {"text": "oi"}, token="errado", host="pc.tail.ts.net")[0] == 403
        for _ in range(12):                                                                    # força bruta
            req(hud, "/ask", {"text": "x"}, token="chute", host="pc.tail.ts.net")
        assert req(hud, "/ask", {"text": "oi"}, token=CODE, host="pc.tail.ts.net")[0] == 403   # bloqueado
    finally:
        hud.stop()


def test_local_mode_unchanged_and_static_needs_valid_host():
    hud = Hud(0, lambda t: None)
    hud.start()
    try:
        assert hud.token.encode() in req(hud, "/")[1]
        assert req(hud, "/sw.js", host="evil.com")[0] == 403
    finally:
        hud.stop()
