"""Ponta a ponta da Fase 2: o AASIS real (processo, HTTP, SQLite, agendador) conversa com um Telegram FALSO que imita
a API (long polling, offset, botões, queda de rede) e com uma IA falsa. Não prova nada sobre o Telegram real.
Uso: python tests/e2e_telegram.py   (leva ~3 min: o agendador roda a cada 15 s)"""
import datetime as dt
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_serve as base  # noqa: E402

TOKEN, OWNER, STRANGER = "999:E2E-SECRET-TOKEN", 4242, 666
ST = {"updates": [], "sent": [], "edits": [], "answers": [], "offsets": [], "down": False, "uid": 0, "mid": 1000}
LOCK = threading.Lock()


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if f"/bot{TOKEN}/" not in self.path:
            return self._reply(404, {"ok": False, "error_code": 404})
        if ST["down"]:
            return self._reply(502)
        method = self.path.rsplit("/", 1)[1]
        if method == "getUpdates":
            off = body.get("offset", 0)
            ST["offsets"].append(off)
            end = time.time() + min(body.get("timeout", 0), 1.0)  # long polling encurtado
            while True:
                with LOCK:
                    ready = [u for u in ST["updates"] if u["update_id"] >= off]
                if ready or time.time() > end:
                    return self._reply(200, {"ok": True, "result": ready})
                time.sleep(0.1)
        with LOCK:
            if method == "sendMessage":
                ST["mid"] += 1
                ST["sent"].append({**body, "message_id": ST["mid"], "t": time.time()})
                return self._reply(200, {"ok": True, "result": {"message_id": ST["mid"]}})
            if method == "editMessageText":
                ST["edits"].append(body)
            if method == "answerCallbackQuery":
                ST["answers"].append(body)
        return self._reply(200, {"ok": True, "result": True})


def inject(text=None, uid=OWNER, cb=None, mid=0, msg_text=""):
    with LOCK:
        ST["uid"] += 1
        if cb is not None:
            ST["updates"].append({"update_id": ST["uid"], "callback_query": {
                "id": f"cq{ST['uid']}", "from": {"id": uid}, "data": cb,
                "message": {"message_id": mid, "text": msg_text, "chat": {"id": uid}}}})
        else:
            ST["updates"].append({"update_id": ST["uid"], "message": {
                "message_id": ST["uid"], "text": text, "from": {"id": uid}, "chat": {"id": uid, "type": "private"}}})


def wait(pred, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        with LOCK:
            hit = next((m for m in ST["sent"] if pred(m)), None)
        if hit:
            return hit
        time.sleep(0.2)
    return None


def buttons(m):
    return [b["callback_data"] for row in (m.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]


class LLM(base.FakeLLM):
    """A IA falsa de e2e_serve + o pedido de cadastro de remédio (com horário no próximo minuto)."""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        req = json.loads(raw)
        last, system = req["messages"][-1], req["messages"][0]["content"]
        u = last["content"] if isinstance(last["content"], str) else ""
        if last["role"] == "user" and "Você extrai" not in system and "cadastre o remédio" in u:
            base.LOG.append(req)
            nxt = (dt.datetime.now(ZoneInfo("America/Sao_Paulo")) + dt.timedelta(minutes=1)).strftime("%H:%M")
            args = {"name": "Losartana", "dose": "50 mg", "times": nxt, "stock": 5}
            body = json.dumps({"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": f"c{len(base.LOG)}", "type": "function", "function": {"name": "med_add", "arguments": json.dumps(args)}}]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.rfile = io.BytesIO(raw)
        super().do_POST()


def db(data, sql, *a):
    c = sqlite3.connect(Path(data) / "aasis.db", timeout=5)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def main():
    llm = ThreadingHTTPServer(("127.0.0.1", 0), LLM)
    tgs = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
    for s in (llm, tgs):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(OWNER),
           "TELEGRAM_API_BASE": f"http://127.0.0.1:{tgs.server_address[1]}"}

    def patch(c):
        c["meds"] = {"resend_s": 20, "missed_after_min": 180, "max_attempts": 6, "low_stock_days": 5}

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        r = base.Run(tmp, llm.server_address[1], base.free_port(), data, env, patch)
        try:
            print("1) Canal e segurança")
            r.check("avisou 'online' ao dono", bool(wait(lambda m: "online" in m["text"] and m["chat_id"] == OWNER, 10)))
            n_llm = len(base.LOG)
            inject("cadastre o remédio", uid=STRANGER)
            inject("/start", uid=STRANGER)
            time.sleep(3)
            r.check("estranho ignorado (IA não foi chamada)", len(base.LOG) == n_llm)
            r.check("estranho só recebe o chat_id no /start",
                    [m for m in ST["sent"] if m["chat_id"] == STRANGER and str(STRANGER) in m["text"] and "TELEGRAM_CHAT_ID" in m["text"]]
                    and not [m for m in ST["sent"] if m["chat_id"] == STRANGER and "Losartana" in m["text"]])
            r.check("token não aparece em nenhuma mensagem", all(TOKEN not in json.dumps(m) for m in ST["sent"]))

            print("2) Conversa pelo Telegram")
            inject("que horas são em Lisboa?")
            ans = wait(lambda m: "Europe/Lisbon" in m["text"] and m["chat_id"] == OWNER)
            r.check("resposta com ferramenta voltou ao Telegram", bool(ans))

            print("3) Cadastro de remédio com confirmação por botão")
            inject("cadastre o remédio")
            conf = wait(lambda m: "Confirmar" in m["text"] and "Losartana" in m["text"])
            r.check("pediu confirmação mostrando nome e dose", bool(conf) and "50 mg" in conf["text"], conf)
            yes = next(b for b in buttons(conf) if b.startswith("c:y:"))
            no = next(b for b in buttons(conf) if b.startswith("c:n:"))
            inject(cb=no, mid=conf["message_id"], msg_text=conf["text"])
            time.sleep(2.5)
            r.check("negado: nada cadastrado", db(data, "SELECT COUNT(*) FROM meds")[0][0] == 0)
            r.check("botões removidos após responder", any(e["message_id"] == conf["message_id"] and "Negado" in e["text"] for e in ST["edits"]))
            inject(cb=yes, mid=conf["message_id"], msg_text=conf["text"])        # reuso do código antigo: não vale
            time.sleep(2)
            r.check("código reutilizado não cadastra", db(data, "SELECT COUNT(*) FROM meds")[0][0] == 0)
            before = len([m for m in ST["sent"] if "Confirmar" in m["text"]])
            inject("cadastre o remédio")
            deadline = time.time() + 20
            while len([m for m in ST["sent"] if "Confirmar" in m["text"]]) <= before and time.time() < deadline:
                time.sleep(0.2)
            conf2 = [m for m in ST["sent"] if "Confirmar" in m["text"]][-1]
            inject(cb=next(b for b in buttons(conf2) if b.startswith("c:y:")), mid=conf2["message_id"], msg_text=conf2["text"])
            time.sleep(3)
            r.check("confirmado: remédio cadastrado", db(data, "SELECT name, dose FROM meds") == [("Losartana", "50 mg")])
            r.check("alerta de estoque baixo (5 un. = 5 dias)", bool(wait(lambda m: "Estoque baixo" in m["text"], 30)))

            print("4) Dose no horário, reenvio, 'Tomei' e idempotência (até ~90 s)")
            dose = wait(lambda m: "Hora do remédio" in m["text"] and "Losartana 50 mg" in m["text"], 100)
            r.check("aviso da dose chegou com botões", bool(dose) and any(b.startswith("d:t:") for b in buttons(dose))
                    and any(b.startswith("d:z:") for b in buttons(dose)) and any(b.startswith("d:s:") for b in buttons(dose)), dose)
            if dose:
                time.sleep(38)
                n = len([m for m in ST["sent"] if "remédio" in m["text"] and "Losartana" in m["text"] and "Estoque" not in m["text"]])
                r.check("sem confirmação: reenviou", n >= 2, f"{n} avisos")
                take = next(b for b in buttons(dose) if b.startswith("d:t:"))
                inject(cb=take, mid=dose["message_id"], msg_text=dose["text"])
                time.sleep(3)
                r.check("Tomei registrado na edição da mensagem", any("tomada" in e["text"] for e in ST["edits"]))
                r.check("estoque baixou 1 (5 -> 4)", db(data, "SELECT stock FROM meds")[0][0] == 4)
                inject(cb=take, mid=dose["message_id"], msg_text=dose["text"])
                time.sleep(2.5)
                r.check("segundo toque não baixa de novo", db(data, "SELECT stock FROM meds")[0][0] == 4)
                cnt = len([m for m in ST["sent"] if "Hora do remédio" in m["text"] or "Ainda sem" in m["text"]])
                time.sleep(36)
                r.check("tomou: parou de avisar", len([m for m in ST["sent"] if "Hora do remédio" in m["text"] or "Ainda sem" in m["text"]]) == cnt)
                r.check("estoque avisado uma vez só por dia", len([m for m in ST["sent"] if "Estoque baixo" in m["text"]]) == 1)

            print("5) Lembrete pelo Telegram e queda do Telegram")
            inject("crie um lembrete")
            rem = wait(lambda m: "Lembrete: Teste e2e" in m["text"], 40)
            r.check("lembrete chegou com botão OK", bool(rem) and any(b.startswith("r:ack:") for b in buttons(rem)), rem)
            if rem:
                inject(cb=next(b for b in buttons(rem) if b.startswith("r:ack:")), mid=rem["message_id"], msg_text=rem["text"])
                time.sleep(3)
                r.check("OK no botão confirma o lembrete", db(data, "SELECT state FROM reminders")[0][0] == "done")
            ST["down"] = True
            n_rem = len([m for m in ST["sent"] if "Teste e2e" in m["text"]])
            base.http("POST", f"{r.base}/ask", {"text": "crie um lembrete"})          # pelo HUD, com o Telegram fora do ar
            time.sleep(28)
            r.check("Telegram fora do ar: nada entregue, nada perdido",
                    len([m for m in ST["sent"] if "Teste e2e" in m["text"]]) == n_rem
                    and db(data, "SELECT COUNT(*) FROM reminders WHERE state = 'pending'")[0][0] == 1)
            ST["down"] = False
            r.check("Telegram voltou: lembrete entregue",
                    bool(wait(lambda m: "Teste e2e" in m["text"] and m["t"] > time.time() - 60 and
                              len([x for x in ST["sent"] if "Teste e2e" in x["text"]]) > n_rem, 45)))

            print("6) Reinício: offset persistido (nenhum comando antigo é reexecutado)")
            last = ST["uid"]
            meds_before, llm_before = db(data, "SELECT COUNT(*) FROM meds")[0][0], len(base.LOG)
            r.stop()
            ST["offsets"].clear()
            r2 = base.Run(tmp, llm.server_address[1], base.free_port(), data, env, patch)
            time.sleep(4)
            r.check("recomeçou do offset salvo", ST["offsets"] and min(ST["offsets"]) == last + 1, ST["offsets"][:3])
            r.check("nada foi reexecutado", db(data, "SELECT COUNT(*) FROM meds")[0][0] == meds_before and len(base.LOG) == llm_before)
            r.check("avisou 'online' de novo", len([m for m in ST["sent"] if "online" in m["text"]]) >= 2)
            r2.stop()
            errs = [l for l in (Path(tmp) / f"server{r.port}.log").read_text().splitlines() if "Traceback" in l]
            r.check("sem Traceback no log do servidor", not errs, errs[:3])
            r.check("token não vazou no log", TOKEN not in (Path(tmp) / f"server{r.port}.log").read_text())
        finally:
            r.stop()
        print(f"\n{r.ok} verificações ok, {len(r.bad)} falharam" + (": " + "; ".join(r.bad) if r.bad else ""))
        return 1 if r.bad else 0


if __name__ == "__main__":
    sys.exit(main())
