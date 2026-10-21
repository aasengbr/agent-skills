"""Entrega de lembretes: reenvia a cada `resend` s até o usuário confirmar (ack). Nunca perde um lembrete."""
import datetime as dt
import threading
import time


class Scheduler:
    UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, memory, deliver, ready=lambda: True, resend: float = 60, tick: float = 15):
        self.memory, self.deliver, self.ready = memory, deliver, ready
        self.resend, self.tick = resend, tick
        self._sent: dict[int, float] = {}

    def run_once(self, now: float | None = None) -> int:
        """Entrega os lembretes vencidos e ainda não confirmados. Retorna quantos foram enviados."""
        now = time.time() if now is None else now
        if not self.ready():  # sem ninguém para receber (ex.: nenhum HUD aberto): mantém pendente
            return 0
        due = self.memory.due_reminders(dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime(self.UTC_FMT))
        sent = 0
        for r in due:
            if now - self._sent.get(r["id"], 0) >= self.resend:
                self.deliver(r, first=r["id"] not in self._sent)
                self._sent[r["id"]] = now
                sent += 1
        return sent

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.run_once()
                except Exception:  # o agendador nunca pode morrer por uma falha pontual
                    pass
                time.sleep(self.tick)

        threading.Thread(target=loop, daemon=True).start()
