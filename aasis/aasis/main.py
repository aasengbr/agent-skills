"""Ponto de entrada: `python -m aasis.main [--text] [--hud]`."""
import argparse
import os
import sys
import threading
import webbrowser
from zoneinfo import ZoneInfo

from .brain import Brain
from .config import data_dir, load_config
from .google_tools import WebAuth
from .hud import Hud
from .memory import Memory
from .scheduler import Scheduler
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

    def say(text: str) -> None:
        print(f"{name}: {text}")
        emit("assistant", text=text)
        if voice:
            emit("state", state="speaking")
            voice.speak(text)
        emit("state", state="standby")

    def confirm(action: str) -> bool:
        if args.serve:  # sem terminal: só o HUD pode autorizar; sem HUD aberto, nega
            return hud.confirm(action) if hud and hud.has_clients() else False
        if voice:
            voice.speak(f"Confirma a ação solicitada, {title}?")
        if hud and hud.has_clients():
            return hud.confirm(action)
        return input(f"Confirma a ação: {action}? (s/n) ").strip().lower() in {"s", "sim", "y", "yes"}

    def handle(text: str) -> None:
        with busy:
            print(f"Você: {text}")
            emit("user", text=text)
            try:
                answer = brain.ask(text)
            except Exception as e:  # falha de rede/API: não deixa o painel preso em "pensando"
                answer = f"Falha ao consultar a API ({type(e).__name__}). Tente novamente, {title}."
            say(answer)

    if not args.text:
        from .voice import Voice

        voice = Voice(cfg)

    brain = Brain(cfg, tools, memory, confirm, emit)

    if args.hud or args.allow_host:
        remote = bool(args.allow_host)
        if args.serve:
            gcfg = cfg.get("google", {})
            ga = WebAuth(data_dir() / gcfg.get("credentials_file", "credentials.json"),
                         data_dir() / gcfg.get("token_file", "token.json"))
            hud = Hud(int(os.getenv("PORT", args.port)), handle, allowed_hosts=args.allow_host, access_code=code,
                      bind="0.0.0.0", trust_proxy=True, google_auth=ga,
                      audit_fn=memory.recent_actions, usage_fn=brain.usage, ack_fn=memory.ack_reminder)
        else:
            hud = Hud(args.port, handle, allowed_hosts=args.allow_host, access_code=code if remote else "",
                      audit_fn=memory.recent_actions, usage_fn=brain.usage, ack_fn=memory.ack_reminder)
        url = hud.start()
        print(f"HUD: {url}" + (f"  (remoto: {', '.join(args.allow_host)})" if remote else ""))
        if not remote:
            webbrowser.open(url)

    def deliver(r: dict, first: bool) -> None:
        if hud and hud.has_clients():
            hud.publish("reminder", id=r["id"], text=r["text"])  # fica pendente até o OK do usuário (/ack)
            if voice and first:
                voice.speak(f"Lembrete, {title}: {r['text']}")
        elif not args.serve:  # console/voz sem HUD aberto: avisa e confirma na hora (na nuvem fica pendente)
            print(f"{name}: Lembrete: {r['text']}")
            if voice:
                voice.speak(f"Lembrete, {title}: {r['text']}")
            memory.ack_reminder(r["id"])

    # na nuvem só entrega com um HUD aberto (senão o lembrete se perderia); fora dela, sempre
    Scheduler(memory, deliver, ready=(lambda: hud.has_clients()) if args.serve else (lambda: True)).start()

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
