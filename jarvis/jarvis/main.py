"""Ponto de entrada: `python -m jarvis.main [--text]`."""
import argparse

from .brain import Brain
from .config import ROOT, load_config
from .memory import Memory
from .tools import Tools


def main() -> None:
    ap = argparse.ArgumentParser(description="JARVIS")
    ap.add_argument("--text", action="store_true", help="modo texto (sem microfone)")
    args = ap.parse_args()

    cfg = load_config()
    memory = Memory(ROOT / "jarvis.db")
    tools = Tools(cfg, memory)
    voice = None

    def confirm(action: str) -> bool:
        msg = f"Confirma a ação: {action}? (s/n)"
        if voice:
            voice.speak("Confirma a ação solicitada, " + cfg["assistant"]["user_title"] + "?")
        return input(msg + " ").strip().lower() in {"s", "sim", "y", "yes"}

    if not args.text:
        from .voice import Voice

        voice = Voice(cfg)

    brain = Brain(cfg, tools, memory, confirm)
    name = cfg["assistant"]["name"]
    greeting = f"{name} online, {cfg['assistant']['user_title']}."
    print(greeting)
    if voice:
        voice.speak(greeting)

    while True:
        try:
            if voice:
                voice.wait_for_wake_word()
                voice.speak("Pois não?")
                text = voice.listen()
                print(f"Você: {text}")
            else:
                text = input("Você: ").strip()
            if not text:
                continue
            if text.lower() in {"sair", "exit", "quit", "encerrar"}:
                break
            answer = brain.ask(text)
            print(f"{name}: {answer}")
            if voice:
                voice.speak(answer)
        except KeyboardInterrupt:
            break
    print("Encerrando.")


if __name__ == "__main__":
    main()
