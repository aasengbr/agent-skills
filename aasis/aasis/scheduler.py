"""Entrega de lembretes e doses de remédio: reenvia até o usuário confirmar (ack). Nunca perde um aviso.

Regra central: um aviso só conta como enviado se a entrega NÃO levantou erro. Falhou (rede, Telegram fora do ar)?
Fica pendente e é tentado de novo no próximo ciclo.
"""
import datetime as dt
import threading
import time


class Scheduler:
    UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, memory, deliver, ready=lambda: True, resend: float = 60, tick: float = 15,
                 meds=None, deliver_dose=None, notify_missed=None, notify_stock=None):
        self.memory, self.deliver, self.ready = memory, deliver, ready
        self.resend, self.tick = resend, tick
        self.meds, self.deliver_dose = meds, deliver_dose
        self.notify_missed, self.notify_stock = notify_missed, notify_stock
        self._sent: dict[int, float] = {}
        self.last_tick = 0.0  # último ciclo concluído (usado pelo heartbeat)
        self.failures = 0  # entregas que falharam e serão repetidas (visível para diagnóstico)

    def run_once(self, now: float | None = None) -> int:
        """Entrega o que venceu e não foi confirmado. Retorna quantos avisos foram enviados com sucesso."""
        now = time.time() if now is None else now
        if not self.ready():  # sem ninguém para receber (ex.: nenhum HUD aberto): mantém pendente
            return 0
        sent = self._reminders(now)
        if self.meds:
            sent += self._meds(dt.datetime.fromtimestamp(now, dt.timezone.utc))
        return sent

    def _reminders(self, now: float) -> int:
        due = self.memory.due_reminders(dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime(self.UTC_FMT))
        sent = 0
        for r in due:
            if now - self._sent.get(r["id"], 0) >= self.resend:
                try:
                    self.deliver(r, first=r["id"] not in self._sent)
                except Exception:
                    self.failures += 1
                    continue
                self._sent[r["id"]] = now
                sent += 1
        return sent

    def _meds(self, now: dt.datetime) -> int:
        m, sent = self.meds, 0
        m.materialize(now)
        m.expire(now)
        for d in m.due_doses(now):
            try:
                if self.deliver_dose:
                    self.deliver_dose(d, d["attempts"])
            except Exception:
                self.failures += 1
                continue
            m.sent(d["id"], now)
            sent += 1
        for d in m.unnotified_missed():
            try:
                if self.notify_missed:
                    self.notify_missed(d)
            except Exception:
                self.failures += 1
                continue
            m.mark_notified(d["id"])
            sent += 1
        for med, left in m.low_stock_alerts(now) if self.notify_stock else []:
            try:
                self.notify_stock(med, left)
            except Exception:
                self.failures += 1
                continue
            m.stock_alerted(med["id"], now)
        return sent

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.run_once()
                except Exception:  # o agendador nunca pode morrer por uma falha pontual
                    self.failures += 1
                self.last_tick = time.time()
                time.sleep(self.tick)

        threading.Thread(target=loop, daemon=True).start()
