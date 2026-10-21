"""HUD local: servidor HTTP + SSE em 127.0.0.1. Sem dependências extras."""
import json
import queue
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

HUD_DIR = Path(__file__).parent / "hud"
PAGE = HUD_DIR / "index.html"
STATIC = {  # públicos: o navegador os busca sem credenciais; não contêm segredos
    "/manifest.webmanifest": "application/manifest+json",
    "/sw.js": "text/javascript",
    "/icon-192.png": "image/png",
    "/icon-512.png": "image/png",
}
MAX_FAILS, FAIL_WINDOW = 10, 300  # tentativas inválidas por janela (segundos)


class Hud:
    def __init__(self, port: int, on_ask, allowed_hosts=(), access_code: str = ""):
        """Modo local: token aleatório por sessão embutido na página. Modo remoto (access_code):
        o código é persistente, NÃO é embutido na página e o usuário o digita no celular."""
        self.port = port
        self.on_ask = on_ask
        self.remote = bool(access_code)
        self.token = access_code or secrets.token_urlsafe(16)
        self._fails: list[float] = []
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self._answer = threading.Event()
        self._ok = False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"} | {h.lower() for h in allowed_hosts}

    # ---------- API pública ----------
    def publish(self, type_: str, **data) -> None:
        msg = json.dumps({"type": type_, **data}, ensure_ascii=False)
        with self.lock:
            for q in self.clients:
                q.put(msg)

    def has_clients(self) -> bool:
        return bool(self.clients)

    def confirm(self, action: str, timeout: float = 60) -> bool:
        self._answer.clear()
        self._ok = False
        self.publish("confirm", action=action)
        self._answer.wait(timeout)
        return self._ok

    def start(self) -> str:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._stats_loop, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        self.httpd.shutdown()

    def _stats_loop(self) -> None:
        while True:
            if self.clients:
                self.publish("stats", cpu=psutil.cpu_percent(), ram=psutil.virtual_memory().percent)
            time.sleep(2)

    # ---------- HTTP ----------
    def _handler(self):
        hud = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _host_ok(self) -> bool:  # bloqueia DNS rebinding
                return self.headers.get("Host", "").lower() in hud.hosts

            def _auth(self, token: str) -> bool:
                if not self._host_ok():
                    return False
                now = time.time()
                hud._fails = [t for t in hud._fails if now - t < FAIL_WINDOW]
                if len(hud._fails) >= MAX_FAILS:  # bloqueio temporário contra força bruta
                    return False
                ok = secrets.compare_digest((token or "").encode(), hud.token.encode())
                if not ok:
                    hud._fails.append(now)
                return ok

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403)
                if self.path in STATIC:
                    return self._send(200, (HUD_DIR / self.path[1:]).read_bytes(), STATIC[self.path])
                if self.path == "/":
                    html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", "" if hud.remote else hud.token)
                    return self._send(200, html.encode(), "text/html; charset=utf-8")
                if self.path.startswith("/events"):
                    if not self._auth(urllib.parse.unquote(self.path.partition("token=")[2])):
                        return self._send(403)
                    return self._stream()
                self._send(404)

            def _stream(self):
                q: queue.Queue = queue.Queue()
                with hud.lock:
                    hud.clients.append(q)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        try:
                            self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with hud.lock:
                        hud.clients.remove(q)

            def do_POST(self):
                if not self._auth(self.headers.get("X-Token", "")):
                    return self._send(403)
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
                except ValueError:
                    return self._send(400)
                if self.path == "/ask" and str(body.get("text", "")).strip():
                    threading.Thread(target=hud.on_ask, args=(str(body["text"]).strip(),), daemon=True).start()
                    return self._send(202)
                if self.path == "/confirm":
                    hud._ok = bool(body.get("ok"))
                    hud._answer.set()
                    return self._send(204)
                self._send(404)

        return H
