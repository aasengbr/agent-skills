"""Teste de ponta a ponta: sobe o AASIS de verdade em modo servidor (processo real, HTTP, SQLite, agendador, backup)
contra um servidor de IA FALSO (protocolo OpenAI). Não prova nada sobre a API real da Anthropic nem sobre um VPS.
Uso: python tests/e2e_serve.py"""
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CODE = "codigo-e2e-bem-longo-123456"
LOG: list = []  # requisições recebidas pelo servidor de IA falso


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"data": [{"id": "mock-1"}, {"id": "mock-2"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LOG.append(req)
        msgs, system = req["messages"], req["messages"][0]["content"]
        last = msgs[-1]
        call = lambda name, args: {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [  # noqa: E731
            {"id": f"call_{len(LOG)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        text = lambda t: {"choices": [{"finish_reason": "stop", "message": {"content": t}}],  # noqa: E731
                          "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        if "Você extrai memórias" in system:                           # chamada de aprendizado
            u = last["content"]
            if "gato" in u:
                return self._send(text(json.dumps([
                    {"kind": "fact", "content": "Tem um gato chamado Tom", "keywords": "pet animal", "about": "eu"},
                    {"kind": "state", "content": "Mora em Recife", "slot": "cidade", "about": "eu", "importance": 4}])))
            if "Losartana" in u:
                return self._send(text(json.dumps([{"kind": "fact", "content": "Toma Losartana 50 mg", "level": 2}])))
            return self._send(text("[]"))
        if last["role"] == "tool":
            return self._send(text("Resultado: " + last["content"][:70]))
        u = last["content"] if isinstance(last["content"], str) else ""
        if "horas" in u:
            return self._send(call("world_time", {"timezone": "Europe/Lisbon"}))
        if "lembrete" in u:
            when = (dt.datetime.now(ZoneInfo("America/Sao_Paulo")) + dt.timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%S")
            return self._send(call("reminder_add", {"text": "Teste e2e", "when": when}))
        if "anote" in u:
            return self._send(call("memory_save", {"content": "Tomo Losartana 50 mg às 8h", "keywords": "remédio pressão"}))
        return self._send(text("ok" + (" sei que mora em Recife" if "Recife" in system else "")))


def http(method, url, body=None, token=True, headers=None):
    h = {"X-Token": CODE} if token else {}
    h.update(headers or {})
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


class Run:
    def __init__(self, tmp, llm_port, port, data, extra_env=None, patch=None):
        self.port, self.base, self.events, self.ok, self.bad = port, f"http://127.0.0.1:{port}", [], 0, []
        cfg = (ROOT / "config.yaml").read_text(encoding="utf-8")
        import yaml
        c = yaml.safe_load(cfg)
        c["llm"] = {"providers": {"mock": {"type": "openai", "trust": "trusted", "base_url": f"http://127.0.0.1:{llm_port}/v1",
                                           "key_env": ""}},
                    "models": {"m": {"provider": "mock", "id": "mock-1", "tools": True}},
                    "routes": {t: ["m"] for t in ("chat", "deep", "memory", "quick")}}
        if patch:
            patch(c)
        (Path(tmp) / "e2e.yaml").write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
        self.env = {**os.environ, "AASIS_CONFIG": str(Path(tmp) / "e2e.yaml"), "AASIS_DATA_DIR": str(data), "PORT": str(port),
                    "AASIS_HUD_CODE": CODE, "AASIS_ALLOWED_HOSTS": "aasis.exemplo.com", "ANTHROPIC_API_KEY": "x",
                    **(extra_env or {})}
        self.log = open(Path(tmp) / f"server{port}.log", "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "aasis.main", "--serve"], cwd=ROOT, env=self.env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                if urllib.request.urlopen(f"{self.base}/healthz", timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("servidor não subiu: " + (Path(tmp) / f"server{port}.log").read_text())
        threading.Thread(target=self._sse, daemon=True).start()
        time.sleep(0.5)

    def _sse(self):
        try:
            r = urllib.request.urlopen(urllib.request.Request(f"{self.base}/events?token={CODE}"), timeout=300)
            for line in r:
                if line.startswith(b"data:"):
                    self.events.append(json.loads(line[5:]))
        except Exception:
            pass

    def wait(self, pred, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            hit = next((e for e in list(self.events) if pred(e)), None)
            if hit:
                return hit
            time.sleep(0.2)
        return None

    def check(self, name, cond, detail=""):
        print(("  OK   " if cond else "  FALHA") + f" {name}" + (f"  [{detail}]" if detail and not cond else ""))
        self.ok += bool(cond)
        if not cond:
            self.bad.append(name)

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        r = Run(tmp, srv.server_address[1], free_port(), data)
        try:
            print("1) Servidor e segurança")
            r.check("healthz público", http("GET", f"{r.base}/healthz", token=False)[0] == 200)
            r.check("página não embute o código", CODE.encode() not in http("GET", f"{r.base}/", token=False)[1])
            r.check("sem código: 403", http("POST", f"{r.base}/ask", {"text": "oi"}, token=False)[0] == 403)
            r.check("host de fora: 403", http("GET", f"{r.base}/", token=False, headers={"Host": "evil.com"})[0] == 403)

            print("2) Conversa com ferramenta (IA falsa -> ferramenta -> resposta)")
            r.check("pergunta aceita", http("POST", f"{r.base}/ask", {"text": "que horas são em Lisboa?"})[0] == 202)
            ans = r.wait(lambda e: e["type"] == "assistant")
            r.check("resposta chegou ao HUD", bool(ans) and "Europe/Lisbon" in ans["text"], ans)
            r.check("ferramenta world_time executada", bool(r.wait(lambda e: e["type"] == "tool" and e["name"] == "world_time")))
            r.check("evento de modelo e de uso", bool(r.wait(lambda e: e["type"] == "model")) and bool(r.wait(lambda e: e["type"] == "usage")))

            print("3) Aprendizado automático")
            n = len(r.events)
            http("POST", f"{r.base}/ask", {"text": "Eu tenho um gato chamado Tom e moro em Recife"})
            r.check("anotou 'Anotei: ...'", bool(r.wait(lambda e: e["type"] == "notice" and "Anotei" in e["text"])))
            mem = json.loads(http("GET", f"{r.base}/memory/list?token={CODE}")[1])
            contents = {m["content"] for m in mem["active"]}
            r.check("memórias ativas gravadas", {"Tem um gato chamado Tom", "Mora em Recife"} <= contents, contents)
            http("POST", f"{r.base}/ask", {"text": "Como vai o dia?"})
            time.sleep(2)
            sys_msg = [q["messages"][0]["content"] for q in LOG if "Você extrai" not in q["messages"][0]["content"]][-1]
            r.check("memória foi para o prompt da IA", "Mora em Recife" in sys_msg and "Tem um gato" in sys_msg)
            r.check("prompt tem prefixo estável e hora separados", "Agora:" in sys_msg and "Memória do usuário" in sys_msg)

            print("4) Saúde exige confirmação (HUD)")
            r.events.clear()
            http("POST", f"{r.base}/ask", {"text": "anote o meu remédio"})
            conf = r.wait(lambda e: e["type"] == "confirm")
            r.check("pediu confirmação com o conteúdo", bool(conf) and "Losartana" in conf["action"], conf)
            http("POST", f"{r.base}/confirm", {"ok": False})
            time.sleep(1.5)
            mem = json.loads(http("GET", f"{r.base}/memory/list?token={CODE}")[1])
            r.check("negado: não gravou", all("Losartana" not in m["content"] for m in mem["active"]))
            r.events.clear()
            http("POST", f"{r.base}/ask", {"text": "anote o meu remédio de novo"})
            r.wait(lambda e: e["type"] == "confirm")
            http("POST", f"{r.base}/confirm", {"ok": True})
            time.sleep(1.5)
            mem = json.loads(http("GET", f"{r.base}/memory/list?token={CODE}")[1])
            r.check("autorizado: gravou", any("Losartana" in m["content"] for m in mem["active"]))

            print("5) 📍 estacionamento, auditoria, modelos")
            r.check("park aceito", http("POST", f"{r.base}/memory/park", {"lat": -8.05, "lon": -34.88, "accuracy": 15, "note": "nível 2"})[0] == 200)
            audit = http("GET", f"{r.base}/audit?token={CODE}")[1].decode()
            r.check("auditoria sem conteúdo privado", "Losartana" not in audit and "world_time" in audit)
            st = json.loads(http("GET", f"{r.base}/llm/status?token={CODE}")[1])
            r.check("painel de modelos", st and st[0]["alias"] == "m" and st[0]["available"])
            r.check("teste do modelo", json.loads(http("POST", f"{r.base}/llm/test", {"alias": "m"})[1]).get("ok") is True)
            r.check("lista de IDs do provedor", "mock-1" in http("GET", f"{r.base}/llm/models?provider=mock&token={CODE}")[1].decode())

            print("6) Lembrete (agendador) com confirmação de leitura")
            r.events.clear()
            http("POST", f"{r.base}/ask", {"text": "crie um lembrete"})
            rem = r.wait(lambda e: e["type"] == "reminder", timeout=40)
            r.check("lembrete entregue no horário", bool(rem), "agendador roda a cada 15 s")
            if rem:
                n0 = len([e for e in r.events if e["type"] == "reminder"])
                http("POST", f"{r.base}/ack", {"id": rem["id"]})
                time.sleep(2)
                r.check("após OK, não reenvia", len([e for e in r.events if e["type"] == "reminder"]) == n0)

            print("7) Backup diário e persistência após reiniciar")
            r.check("backup criado em /data/backups", any((data / "backups").glob("aasis-*.db")))
            r.stop()
            r2 = Run(tmp, srv.server_address[1], free_port(), data)
            mem = json.loads(http("GET", f"{r2.base}/memory/list?token={CODE}")[1].decode() and http("GET", f"{r2.base}/memory/list?token={CODE}")[1])
            c2 = {m["content"] for m in mem["active"]}
            r.check("memória sobreviveu ao reinício", {"Mora em Recife", "Tem um gato chamado Tom"} <= c2)
            r2.stop()
            print("\nLOG DO SERVIDOR (erros):")
            errs = [l for l in (Path(tmp) / f"server{r.port}.log").read_text().splitlines() if "Traceback" in l or "Error" in l]
            print("  nenhum" if not errs else "\n".join("  " + e for e in errs[:8]))
        finally:
            r.stop()
        print(f"\n{r.ok} verificações ok, {len(r.bad)} falharam" + (": " + "; ".join(r.bad) if r.bad else ""))
        return 1 if r.bad else 0


if __name__ == "__main__":
    sys.exit(main())
