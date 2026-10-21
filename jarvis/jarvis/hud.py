"""HUD local: servidor HTTP + SSE em 127.0.0.1. Sem dependências extras."""
import json
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

PAGE = Path(__file__).parent / "hud" / "index.html"


class Hud:
    def __init__(self, port: int, on_ask):
        self.port = port
        self.on_ask = on_ask
        self.token = secrets.token_urlsafe(16)
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self._answer = threading.Event()
        self._ok = False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self.httpd.server_address[1]

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
        import time

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
                return self.headers.get("Host", "") in {
                    f"127.0.0.1:{hud.port}",
                    f"localhost:{hud.port}",
                }

            def _auth(self, token: str) -> bool:
                return self._host_ok() and secrets.compare_digest(token or "", hud.token)

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403)
                if self.path == "/":
                    html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", hud.token)
                    return self._send(200, html.encode(), "text/html; charset=utf-8")
                if self.path.startswith("/events"):
                    if not self._auth(self.path.partition("token=")[2]):
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
