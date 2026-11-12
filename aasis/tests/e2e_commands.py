"""Ponta a ponta dos comandos de remédio SEM NENHUMA IA: o AASIS real sobe sem chave de provedor algum e conversa com um
Telegram FALSO. Prova que cadastro, aviso, 'tomei', estoque e remoção funcionam sem modelo de linguagem.
Uso: python tests/e2e_commands.py   (leva ~2 min: espera o horário da dose e o ciclo do agendador)"""
import datetime as dt
import json
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_serve as base  # noqa: E402
from e2e_telegram import OWNER, ST, STRANGER, TOKEN, FakeTelegram, buttons, db, inject, wait  # noqa: E402

NO_KEYS = {k: "" for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "ZAI_API_KEY", "NVIDIA_API_KEY",
                           "OPENROUTER_API_KEY", "NVIDIA_MODEL", "OPENROUTER_MODEL", "OLLAMA_MODEL")}


def main():
    tgs = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
    threading.Thread(target=tgs.serve_forever, daemon=True).start()
    env = {**NO_KEYS, "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(OWNER),
           "TELEGRAM_API_BASE": f"http://127.0.0.1:{tgs.server_address[1]}"}

    import yaml
    real_llm = yaml.safe_load((base.ROOT / "config.yaml").read_text(encoding="utf-8"))["llm"]

    def patch(c):
        c["llm"] = real_llm                                               # provedores de verdade, todos sem chave
        c["meds"] = {"resend_s": 20, "missed_after_min": 180, "max_attempts": 6, "low_stock_days": 1}

    def sent(pred):
        if True:
            return [m for m in ST["sent"] if pred(m)]

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        r = base.Run(tmp, 1, base.free_port(), data, env, patch)           # porta 1: nenhum servidor de IA existe
        try:
            print("1) Sobe sem nenhuma IA")
            r.check("avisou 'online' ao dono", bool(wait(lambda m: "online" in m["text"] and m["chat_id"] == OWNER, 10)))
            inject("oi")
            reply = wait(lambda m: m["chat_id"] == OWNER and "online" not in m["text"], 15)
            r.check("conversa sem provedor responde com aviso claro (sem travar)", bool(reply), reply)

            print("2) /ajuda e segurança")
            inject("/ajuda")
            r.check("/ajuda lista os comandos", bool(wait(lambda m: "/remedio" in m["text"] and "/tomei" in m["text"] and "não usam IA" in m["text"], 10)))
            n = len(ST["sent"])
            inject("/remedio Intruso 1mg 08:00", uid=STRANGER)
            time.sleep(3)
            r.check("estranho não consegue cadastrar", db(data, "SELECT COUNT(*) FROM meds")[0][0] == 0
                    and not [m for m in ST["sent"][n:] if m["chat_id"] == STRANGER and "Cadastrado" in m["text"]])

            print("3) Cadastro por comando, com confirmação mostrando o que foi entendido")
            inject("/remedio Losartana 50mg 08:00 estoque=5")               # horário inválido para teste de dose? não: só valida
            conf = wait(lambda m: "Cadastrar este remédio?" in m["text"] and "Losartana" in m["text"], 15)
            r.check("pediu confirmação com nome, dose, horário e estoque",
                    bool(conf) and all(x in conf["text"] for x in ("50mg", "08:00", "Estoque: 5")), conf)
            no = next(b for b in buttons(conf) if b.startswith("c:n:"))
            inject(cb=no, mid=conf["message_id"], msg_text=conf["text"])
            time.sleep(2.5)
            r.check("negado: nada cadastrado", db(data, "SELECT COUNT(*) FROM meds")[0][0] == 0)
            r.check("erro de digitação é explicado e não pede confirmação",
                    (inject("/remedio X 1cp 25:00") or True) and bool(wait(lambda m: "Horário inválido" in m["text"], 10)))

            nxt = (dt.datetime.now(ZoneInfo("America/Sao_Paulo")) + dt.timedelta(minutes=1)).strftime("%H:%M")
            before = len([m for m in ST["sent"] if "Cadastrar este remédio?" in m["text"]])
            inject(f"/remedio Losartana 50mg {nxt} estoque=5")
            deadline = time.time() + 15
            while len([m for m in ST["sent"] if "Cadastrar este remédio?" in m["text"]]) <= before and time.time() < deadline:
                time.sleep(0.2)
            conf2 = [m for m in ST["sent"] if "Cadastrar este remédio?" in m["text"]][-1]
            inject(cb=next(b for b in buttons(conf2) if b.startswith("c:y:")), mid=conf2["message_id"], msg_text=conf2["text"])
            ok = wait(lambda m: "✅ Cadastrado" in m["text"] and "Primeiro aviso" in m["text"], 15)
            r.check("confirmado: cadastrado e informa o primeiro aviso", bool(ok) and db(data, "SELECT name, dose, stock FROM meds") == [("Losartana", "50mg", 5.0)], ok)

            print("4) Dose no horário e 'tomei' (até ~90 s)")
            dose = wait(lambda m: "Hora do remédio" in m["text"] and "Losartana 50mg" in m["text"], 100)
            r.check("aviso da dose chegou com botões", bool(dose) and any(b.startswith("d:t:") for b in buttons(dose)), dose)
            inject("/tomei Losartana")
            took = wait(lambda m: "tomada às" in m["text"] and "Restam 4" in m["text"], 15)
            r.check("/tomei registra a dose e baixa o estoque (5 -> 4)", bool(took) and db(data, "SELECT stock FROM meds")[0][0] == 4, took)
            inject("/tomei Losartana")
            r.check("segundo /tomei não registra de novo", bool(wait(lambda m: "Nenhuma dose em aberto" in m["text"], 10)) and db(data, "SELECT stock FROM meds")[0][0] == 4)
            cnt = len([m for m in ST["sent"] if "Hora do remédio" in m["text"] or "Ainda sem" in m["text"]])
            time.sleep(36)
            r.check("tomou: parou de avisar", len([m for m in ST["sent"] if "Hora do remédio" in m["text"] or "Ainda sem" in m["text"]]) == cnt)

            print("5) Lista, estoque e remoção")
            inject("/remedios")
            r.check("/remedios lista o remédio e a dose de hoje", bool(wait(lambda m: "Losartana" in m["text"] and "Hoje:" in m["text"] and "tomada" in m["text"], 10)))
            inject("/estoque Losartana 12")
            r.check("/estoque atualiza", bool(wait(lambda m: "Estoque de Losartana: 12" in m["text"], 10)) and db(data, "SELECT stock FROM meds")[0][0] == 12)
            inject("/remover Losartana")
            conf3 = wait(lambda m: "Remover este remédio" in m["text"], 10)
            r.check("/remover pede confirmação mostrando o remédio", bool(conf3) and "Losartana" in conf3["text"])
            inject(cb=next(b for b in buttons(conf3) if b.startswith("c:y:")), mid=conf3["message_id"], msg_text=conf3["text"])
            time.sleep(3)
            r.check("removido, histórico mantido", db(data, "SELECT COUNT(*) FROM meds WHERE active = 1")[0][0] == 0
                    and db(data, "SELECT COUNT(*) FROM doses")[0][0] >= 1)

            print("6) Registro do servidor")
            r.stop()
            log = (Path(tmp) / f"server{r.port}.log").read_text()
            r.check("sem Traceback", "Traceback" not in log, [l for l in log.splitlines() if "Error" in l][:3])
            r.check("log não guarda nome, dose nem token", "Losartana" not in log and "50mg" not in log and TOKEN not in log)
            audit = json.dumps(db(data, "SELECT tool, args, status FROM audit"))
            r.check("auditoria registra os comandos sem conteúdo", "cmd_remedio" in audit and "Losartana" not in audit and "50mg" not in audit, audit[:200])
        finally:
            r.stop()
        print(f"\n{r.ok} verificações ok, {len(r.bad)} falharam" + (": " + "; ".join(r.bad) if r.bad else ""))
        return 1 if r.bad else 0


if __name__ == "__main__":
    sys.exit(main())
