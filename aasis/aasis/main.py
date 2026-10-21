"""Ponto de entrada: `python -m aasis.main [--text] [--hud]`."""
import argparse
import os
import sys
import threading
import time
import webbrowser
from zoneinfo import ZoneInfo

from .backup import start_backup_loop
from .brain import Brain
from .config import data_dir, load_config
from .google_tools import WebAuth
from .heartbeat import start_heartbeat
from .hud import Hud
from .memory import Memory
from .meds import format_dose
from .scheduler import Scheduler
from .telegram import from_env as telegram_from_env
from .tools import Tools

def main() -> None:
    ap = argparse.ArgumentParser(description="AASIS")
    ap.add_argument("--text", action="store_true", help="modo texto (sem microfone)")
    ap.add_argument("--hud", action="store_true", help="abre a interface visual no navegador")
    ap.add_argument("--port", type=int, default=8765, help="porta do HUD (127.0.0.1)")
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                    help="acesso remoto (celular) via proxy HTTPS, ex.: meu-pc.tailnet.ts.net. Exige AASIS_HUD_CODE")
    ap.add_argument("--serve", action="store_true",
                    help="modo nuvem: sem terminal, escuta em 0.0.0.0:$PORT, só HUD (celular). Exige AASIS_HUD_CODE")
    args = ap.parse_args()

    cfg = load_config()  # carrega também o .env
    code = os.getenv("AASIS_HUD_CODE", "")
    if args.serve:
        # endpoint público na internet: código mais longo e hosts explícitos são obrigatórios
        env_hosts = [h.strip() for h in os.getenv("AASIS_ALLOWED_HOSTS", "").split(",") if h.strip()]
        env_hosts += [os.environ[v] for v in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN") if os.getenv(v)]
        args.allow_host = args.allow_host + env_hosts
        if len(code) < 20:
            sys.exit("Modo nuvem exige AASIS_HUD_CODE com pelo menos 20 caracteres.")
        if not args.allow_host:
            sys.exit("Modo nuvem exige AASIS_ALLOWED_HOSTS (ex.: meu-aasis.onrender.com).")
        args.text = True
        cfg["runtime"] = {"cloud": True}
        cfg["screen"] = {"enabled": False}
    elif args.allow_host and len(code) < 12:
        sys.exit("Acesso remoto exige AASIS_HUD_CODE com pelo menos 12 caracteres (defina no .env).")
    name, title = cfg["assistant"]["name"], cfg["assistant"]["user_title"]
    memory = Memory(data_dir() / "aasis.db", tz=ZoneInfo(cfg["assistant"].get("timezone", "America/Sao_Paulo")))
    tools = Tools(cfg, memory)
    voice = None
    hud: Hud | None = None
    busy = threading.Lock()  # um comando por vez (voz, console ou HUD)

    def emit(type_: str, **data) -> None:
        if hud:
            hud.publish(type_, **data)

    def say(text: str, sources: list | None = None) -> None:
        print(f"{name}: {text}")
        emit("assistant", text=text)
        if sources:  # fontes citadas pela busca na web (exigidas pela documentação); só na tela, não na voz
            print("Fontes: " + "; ".join(f"{x['title']} <{x['url']}>" for x in sources))
            emit("sources", items=sources)
        if voice:
            emit("state", state="speaking")
            voice.speak(text)
        emit("state", state="standby")

    origin = threading.local()  # de onde veio o pedido atual ("telegram" ou None): a confirmação volta pelo mesmo canal
    tg = None

    def confirm(action: str) -> bool:
        if getattr(origin, "channel", None) == "telegram" and tg:
            return tg.confirm(action)
        if args.serve:  # sem terminal: só o HUD pode autorizar; sem HUD aberto, nega
            return hud.confirm(action) if hud and hud.has_clients() else False
        if voice:
            voice.speak(f"Confirma a ação solicitada, {title}?")
        if hud and hud.has_clients():
            return hud.confirm(action)
        return input(f"Confirma a ação: {action}? (s/n) ").strip().lower() in {"s", "sim", "y", "yes"}

    def handle(text: str, deep: bool = False) -> None:
        with busy:
            print(f"Você: {text}")
            emit("user", text=text)
            try:
                answer = brain.ask(text, deep=deep)
            except Exception as e:  # falha de rede/API: não deixa o painel preso em "pensando"
                answer = f"Falha ao consultar a API ({type(e).__name__}). Tente novamente, {title}."
            say(answer, brain.last_sources)
            threading.Thread(target=brain.learn, args=(text,), daemon=True).start()  # aprende depois de responder

    if not args.text:
        from .voice import Voice

        voice = Voice(cfg)

    brain = Brain(cfg, tools, memory, confirm, emit)

    def tg_text(text: str) -> str:
        """Mensagem vinda do Telegram (já filtrada para o dono): mesmo cérebro, resposta volta ao Telegram."""
        with busy:
            origin.channel = "telegram"
            emit("user", text=text)
            try:
                answer = brain.ask(text)
            except Exception as e:
                answer = f"Falha ao consultar a API ({type(e).__name__}). Tente novamente, {title}."
            finally:
                origin.channel = None
            emit("assistant", text=answer)
            sources = brain.last_sources
        threading.Thread(target=brain.learn, args=(text,), daemon=True).start()
        if sources:
            answer += "\n\nFontes:\n" + "\n".join(f"- {x['title']} {x['url']}" for x in sources)
        return answer

    def tg_action(data: str) -> str:
        """Toque em botão: r:ack:<id> (lembrete) | d:t|z|s:<id> (dose: tomei, adiar, pular)."""
        kind, act, ident = (data.split(":") + ["", "", ""])[:3]
        if not ident.isdigit():
            return ""
        if kind == "r" and act == "ack":
            return "✅ Lembrete confirmado." if memory.ack_reminder(int(ident)) else "Lembrete já confirmado."
        if kind == "d" and act in ("t", "z", "s"):
            return tools.meds.act(int(ident), {"t": "taken", "z": "snooze", "s": "skipped"}[act])
        return ""

    tg = telegram_from_env(cfg, data_dir(), on_text=tg_text, on_action=tg_action)
    detail = cfg.get("telegram", {}).get("detail", "full")  # full | minimal (sem nome do remédio no Telegram)

    if args.hud or args.allow_host:
        remote = bool(args.allow_host)
        if args.serve:
            gcfg = cfg.get("google", {})
            ga = WebAuth(data_dir() / gcfg.get("credentials_file", "credentials.json"),
                         data_dir() / gcfg.get("token_file", "token.json"))
            hud = Hud(int(os.getenv("PORT", args.port)), handle, allowed_hosts=args.allow_host, access_code=code,
                      bind="0.0.0.0", trust_proxy=True, google_auth=ga,
                      audit_fn=memory.recent_actions, usage_fn=brain.usage, ack_fn=memory.ack_reminder,
                      llm_status_fn=brain.orch.status, llm_test_fn=brain.orch.test,
                      llm_models_fn=brain.orch.list_models, memory=memory)
        else:
            hud = Hud(args.port, handle, allowed_hosts=args.allow_host, access_code=code if remote else "",
                      audit_fn=memory.recent_actions, usage_fn=brain.usage, ack_fn=memory.ack_reminder,
                      llm_status_fn=brain.orch.status, llm_test_fn=brain.orch.test,
                      llm_models_fn=brain.orch.list_models, memory=memory)
        url = hud.start()
        print(f"HUD: {url}" + (f"  (remoto: {', '.join(args.allow_host)})" if remote else ""))
        if not remote:
            webbrowser.open(url)

    def deliver(r: dict, first: bool) -> None:
        if tg:  # canal confiável: chega com o app fechado; falha de envio levanta erro e o agendador repete
            tg.send(f"⏰ Lembrete: {r['text']}", [[("✅ OK", f"r:ack:{r['id']}")]])
            if hud and hud.has_clients():
                hud.publish("reminder", id=r["id"], text=r["text"])
            return
        if hud and hud.has_clients():
            hud.publish("reminder", id=r["id"], text=r["text"])  # fica pendente até o OK do usuário (/ack)
            if voice and first:
                voice.speak(f"Lembrete, {title}: {r['text']}")
        elif not args.serve:  # console/voz sem HUD aberto: avisa e confirma na hora (na nuvem fica pendente)
            print(f"{name}: Lembrete: {r['text']}")
            if voice:
                voice.speak(f"Lembrete, {title}: {r['text']}")
            memory.ack_reminder(r["id"])

    if args.serve and cfg.get("backup", {}).get("enabled", True):  # cópia diária do banco em /data/backups
        start_backup_loop(data_dir() / "aasis.db", data_dir() / "backups", int(cfg.get("backup", {}).get("keep", 14)))

    def notify(text: str, buttons=None) -> None:
        """Avisa pelo canal disponível; levanta erro se nenhum alcançar o usuário (o agendador tenta de novo)."""
        if tg:
            tg.send(text, buttons)
            if hud and hud.has_clients():
                emit("assistant", text=text)
        elif hud and hud.has_clients():
            emit("assistant", text=text)
        elif not args.serve:
            print(f"{name}: {text}")
        else:
            raise RuntimeError("nenhum canal disponível")
        if voice:
            voice.speak(text)

    tz = tools.tz

    def deliver_dose(d: dict, attempt: int) -> None:
        buttons = [[("✅ Tomei", f"d:t:{d['id']}")], [("⏰ Adiar 10 min", f"d:z:{d['id']}"), ("⏭ Pular", f"d:s:{d['id']}")]]
        text = format_dose(d, tz, detail, attempt)
        notify(text if tg else text + " Diga 'tomei' quando tomar.", buttons if tg else None)

    def notify_missed(d: dict) -> None:
        what = f"{d['name']} {d['dose']}" if detail == "full" else "seu remédio"
        notify(f"❗ Dose não confirmada: {what}. Se já tomou, responda 'tomei'; se não, fale com seu médico se tiver dúvida.",
               [[("✅ Tomei", f"d:t:{d['id']}"), ("⏭ Pular", f"d:s:{d['id']}")]] if tg else None)

    def notify_stock(med: dict, left: float) -> None:
        what = med["name"] if detail == "full" else "um remédio"
        notify(f"📦 Estoque baixo de {what}: restam {med['stock']:g} (cerca de {left:.0f} dia(s)). Reponha com antecedência.")

    if args.serve and cfg.get("backup", {}).get("enabled", True):  # cópia diária do banco em /data/backups
        start_backup_loop(data_dir() / "aasis.db", data_dir() / "backups", int(cfg.get("backup", {}).get("keep", 14)))

    # na nuvem só entrega com um canal de pé (Telegram ou HUD aberto); fora dela, sempre
    sched = Scheduler(memory, deliver, resend=300 if tg else 60,
                      ready=(lambda: bool(tg) or bool(hud and hud.has_clients())) if args.serve else (lambda: True),
                      meds=tools.meds, deliver_dose=deliver_dose, notify_missed=notify_missed, notify_stock=notify_stock)
    sched.start()
    if tg:
        tg.start()
        try:
            tg.send(f"{name} online, {title}.")
        except Exception:
            print("Aviso: não consegui enviar mensagem ao Telegram (confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID).")

    hb_url = os.getenv("HEALTHCHECK_URL", "").strip()
    if hb_url:  # monitor externo: se o AASIS cair, o serviço avisa que os pings pararam
        def healthy() -> bool:
            return time.time() - sched.last_tick < 120 and (tg is None or time.time() - tg.last_ok < 180)

        if not start_heartbeat(hb_url, healthy, int(cfg.get("heartbeat", {}).get("interval_s", 300))):
            print("Aviso: HEALTHCHECK_URL precisa começar com https://; monitor externo desativado.")

    say(f"{name} online, {title}.")

    if args.serve:
        threading.Event().wait()  # servidor: fica de pé atendendo o HUD

    while True:
        try:
            if voice:
                voice.wait_for_wake_word()
                emit("state", state="listening")
                voice.speak("Pois não?")
                text = voice.listen()
            else:
                text = input("Você: ").strip()
            if not text:
                emit("state", state="standby")
                continue
            if text.lower() in {"sair", "exit", "quit", "encerrar"}:
                break
            handle(text)
        except KeyboardInterrupt:
            break
    if hud:
        hud.stop()
    print("Encerrando.")

if __name__ == "__main__":
    main()
