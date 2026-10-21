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
    def __init__(self, port: int, on_ask, allowed_hosts=(), access_code: str = "", bind: str = "127.0.0.1",
                 trust_proxy: bool = False, google_auth=None, audit_fn=None, usage_fn=None, ack_fn=None,
                 llm_status_fn=None, llm_test_fn=None, llm_models_fn=None, memory=None):
        """Modo local: token aleatório por sessão embutido na página. Modo remoto (access_code):
        o código é persistente, NÃO é embutido na página e o usuário o digita no celular."""
        self.port = port
        self.on_ask = on_ask
        self.remote = bool(access_code)
        self.token = access_code or secrets.token_urlsafe(16)
        self._fails: dict[str, list[float]] = {}  # tentativas inválidas por IP
        self.trust_proxy = trust_proxy  # atrás de proxy da nuvem: IP real = último item de X-Forwarded-For
        self.google_auth = google_auth
        self.ack_fn = ack_fn  # confirma lembrete entregue
        self.llm_status_fn, self.llm_test_fn, self.llm_models_fn = llm_status_fn, llm_test_fn, llm_models_fn
        self.memory = memory  # Memory (usa .mem para a memória de longo prazo)
        self.audit_fn, self.usage_fn = audit_fn, usage_fn  # registro de ações e uso diário de tokens
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self._answer = threading.Event()
        self._ok = False
        self.httpd = ThreadingHTTPServer((bind, port), self._handler())
        self.port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"} | {h.lower() for h in allowed_hosts}

    def google_status(self) -> dict:
        ga = self.google_auth
        return {"available": bool(ga and ga.configured), "connected": bool(ga and ga.connected)}

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

            def _ip(self) -> str:
                if hud.trust_proxy:
                    fwd = self.headers.get("X-Forwarded-For", "")
                    if fwd:
                        return fwd.split(",")[-1].strip()  # último item: o que o proxy da plataforma acrescentou
                return self.client_address[0]

            def _auth(self, token: str) -> bool:
                if not self._host_ok():
                    return False
                now, ip = time.time(), self._ip()
                if len(hud._fails) > 5000:
                    hud._fails.clear()
                recent = [t for t in hud._fails.get(ip, []) if now - t < FAIL_WINDOW]
                hud._fails[ip] = recent
                if len(recent) >= MAX_FAILS:  # bloqueio temporário só daquele IP (não derruba o dono)
                    return False
                ok = secrets.compare_digest((token or "").encode(), hud.token.encode())
                if not ok:
                    recent.append(now)
                return ok

            def _redirect(self, where: str):
                self.send_response(302)
                self.send_header("Location", where)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _redirect_uri(self) -> str:
                host = self.headers.get("Host", "")
                scheme = "http" if host.startswith(("127.0.0.1", "localhost")) else "https"
                return f"{scheme}://{host}/google/callback"

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parts.query)
                if parts.path == "/healthz":  # checagem de saúde da plataforma; não revela nada
                    return self._send(200, b"ok")
                if not self._host_ok():
                    return self._send(403)
                if parts.path in ("/llm/status", "/llm/models"):
                    fn = hud.llm_status_fn if parts.path == "/llm/status" else hud.llm_models_fn
                    if not fn or not self._auth(query.get("token", [""])[0]):
                        return self._send(403)
                    try:
                        data = fn() if parts.path == "/llm/status" else fn(query.get("provider", [""])[0])
                    except Exception as e:  # ex.: provedor sem chave ou fora do ar
                        data = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
                    return self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                if parts.path in ("/memory/list", "/memory/export"):
                    if not hud.memory or not self._auth(query.get("token", [""])[0]):
                        return self._send(403)
                    m = hud.memory.mem
                    data = {"active": m.list(limit=300), "pending": m.list(status="pending", limit=100)} \
                        if parts.path == "/memory/list" else m.export()
                    return self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                if parts.path == "/audit":
                    if not hud.audit_fn or not self._auth(query.get("token", [""])[0]):
                        return self._send(403)
                    body = json.dumps(hud.audit_fn(), ensure_ascii=False).encode()
                    return self._send(200, body, "application/json; charset=utf-8")
                if parts.path == "/google/start":
                    ga = hud.google_auth
                    if not (ga and ga.configured) or not self._auth(query.get("token", [""])[0]):
                        return self._send(403)
                    return self._redirect(ga.start(self._redirect_uri()))
                if parts.path == "/google/callback":
                    ga = hud.google_auth
                    try:
                        ok = bool(ga) and "error" not in query and ga.finish(
                            query.get("state", [""])[0], query.get("code", [""])[0])
                    except Exception:
                        ok = False
                    if not ok:
                        return self._send(400, "Falha na autorização do Google. Tente de novo pelo HUD.".encode())
                    hud.publish("google", **hud.google_status())
                    return self._redirect("/")
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
                q.put(json.dumps({"type": "google", **hud.google_status()}))
                if hud.usage_fn:
                    q.put(json.dumps({"type": "usage", **hud.usage_fn()}))
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

            def _memory_post(self, body: dict):
                m, action = hud.memory.mem, self.path.rsplit("/", 1)[-1]
                try:
                    if action == "park":  # 📍 Estacionei aqui: estado car_location (o novo substitui o antigo)
                        lat, lon = float(body["lat"]), float(body["lon"])
                        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                            return self._send(400)
                        acc = max(0.0, min(float(body.get("accuracy") or 0), 100000))
                        note = " ".join(str(body.get("note", "")).split())[:120]
                        text = (f"Carro estacionado em {lat:.5f},{lon:.5f} (precisão ±{acc:.0f} m)."
                                + (f" Nota: {note}." if note else "")
                                + f" Mapa: https://www.google.com/maps?q={lat:.5f},{lon:.5f}")
                        rid = m.save("state", text, slot="car_location", level=1, importance=4)
                        return self._send(200, json.dumps({"id": rid}).encode(), "application/json")
                    rid = int(body["id"])
                    ok = {"approve": m.approve, "reject": m.reject, "delete": lambda i: m.forget(i) > 0}[action](rid)
                    return self._send(204 if ok else 404)
                except (KeyError, TypeError, ValueError):
                    return self._send(400)

            def do_POST(self):
                if not self._auth(self.headers.get("X-Token", "")):
                    return self._send(403)
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
                except ValueError:
                    return self._send(400)
                if self.path == "/ask" and str(body.get("text", "")).strip():
                    kw = {"deep": True} if body.get("deep") else {}  # modo profundo: modelo mais capaz, uma pergunta
                    threading.Thread(target=hud.on_ask, args=(str(body["text"]).strip(),), kwargs=kw, daemon=True).start()
                    return self._send(202)
                if self.path.startswith("/memory/") and hud.memory:
                    return self._memory_post(body)
                if self.path == "/llm/test" and hud.llm_test_fn:
                    out = hud.llm_test_fn(str(body.get("alias", ""))[:40])
                    return self._send(200, json.dumps(out, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                if self.path == "/ack" and hud.ack_fn:
                    try:
                        hud.ack_fn(int(body.get("id")))
                    except (TypeError, ValueError):
                        return self._send(400)
                    return self._send(204)
                if self.path == "/confirm":
                    hud._ok = bool(body.get("ok"))
                    hud._answer.set()
                    return self._send(204)
                self._send(404)

        return H
