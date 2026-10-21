"""Ponto de entrada: `python -m jarvis.main [--text] [--hud]`."""
import argparse
import threading
import webbrowser

from .brain import Brain
from .config import ROOT, load_config
from .hud import Hud
from .memory import Memory
from .tools import Tools


def main() -> None:
    ap = argparse.ArgumentParser(description="JARVIS")
    ap.add_argument("--text", action="store_true", help="modo texto (sem microfone)")
    ap.add_argument("--hud", action="store_true", help="abre a interface visual no navegador")
    ap.add_argument("--port", type=int, default=8765, help="porta do HUD (127.0.0.1)")
    args = ap.parse_args()

    cfg = load_config()
    name, title = cfg["assistant"]["name"], cfg["assistant"]["user_title"]
    memory = Memory(ROOT / "jarvis.db")
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
        if voice:
            voice.speak(f"Confirma a ação solicitada, {title}?")
        if hud and hud.has_clients():
            return hud.confirm(action)
        return input(f"Confirma a ação: {action}? (s/n) ").strip().lower() in {"s", "sim", "y", "yes"}

    def handle(text: str) -> None:
        with busy:
            print(f"Você: {text}")
            emit("user", text=text)
            say(brain.ask(text))

    if not args.text:
        from .voice import Voice

        voice = Voice(cfg)

    brain = Brain(cfg, tools, memory, confirm, emit)

    if args.hud:
        hud = Hud(args.port, handle)
        url = hud.start()
        print(f"HUD: {url}")
        webbrowser.open(url)

    say(f"{name} online, {title}.")

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
