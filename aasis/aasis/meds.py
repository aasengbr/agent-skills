"""Agenda de remédios: horários, doses, "tomei", adiar, pular, estoque e doses perdidas.

O AASIS NÃO é dispositivo médico: guarda e lembra exatamente o que o usuário informou (nome, dose como digitada,
horários). Nunca sugere dose, horário ou combinação. Tudo aqui é dado de saúde (nível sensível).
"""
import datetime as dt
import json
import re
import unicodedata

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
DAYS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # índice = datetime.weekday()
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_MEDS, MAX_TIMES = 30, 8


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode().strip()


def parse_times(times: str) -> list[str]:
    out = set()
    for tok in re.split(r"[,;\s]+", times.strip()):
        if not tok:
            continue
        m = TIME_RE.match(tok)
        if not m:
            raise ValueError(f"Horário inválido: {tok!r}. Use HH:MM (ex.: 08:00).")
        out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    if not out or len(out) > MAX_TIMES:
        raise ValueError(f"Informe de 1 a {MAX_TIMES} horários no formato HH:MM, separados por vírgula.")
    return sorted(out)


def parse_days(days: str) -> list[int]:
    """'' ou 'todos' = todos os dias. Aceita seg,ter,qua,qui,sex,sab,dom (com ou sem acento)."""
    d = _norm(days)
    if d in ("", "todos", "todo dia", "diario", "diariamente"):
        return []
    out = set()
    for tok in re.split(r"[,;\s]+", d):
        if not tok:
            continue
        if tok[:3] not in DAYS:
            raise ValueError(f"Dia inválido: {tok!r}. Use seg,ter,qua,qui,sex,sab,dom ou 'todos'.")
        out.add(DAYS.index(tok[:3]))
    return sorted(out)


def fmt_stock(x: float) -> str:
    return f"{x:g}"


class Meds:
    def __init__(self, memory, tz, missed_after_min: int = 180, resend_s: int = 300, max_attempts: int = 6,
                 low_stock_days: int = 5):
        self.m, self.tz = memory, tz
        self.db, self._lock = memory.db, memory._lock
        self.missed_after = dt.timedelta(minutes=missed_after_min)
        self.resend_s, self.max_attempts, self.low_stock_days = resend_s, max_attempts, low_stock_days
        with self._lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS meds (id INTEGER PRIMARY KEY, name TEXT NOT NULL, dose TEXT NOT NULL, "
                "times TEXT NOT NULL, days TEXT NOT NULL DEFAULT '[]', start TEXT, end TEXT, stock REAL, "
                "per_dose REAL NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, created_utc TEXT NOT NULL, "
                "stock_alert TEXT)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS doses (id INTEGER PRIMARY KEY, med_id INTEGER NOT NULL, due_utc TEXT NOT NULL, "
                "state TEXT NOT NULL DEFAULT 'pending', snooze_utc TEXT, acted_utc TEXT, attempts INTEGER NOT NULL DEFAULT 0, "
                "last_sent_utc TEXT, notified INTEGER NOT NULL DEFAULT 0, UNIQUE(med_id, due_utc))")
            self.db.commit()

    # ---------- cadastro ----------
    def _now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def add(self, name: str, dose: str, times: str, days: str = "", start: str = "", end: str = "",
            stock: float | None = None, per_dose: float = 1, now: dt.datetime | None = None) -> int:
        name, dose = name.strip()[:80], dose.strip()[:80]
        if not name or not dose:
            raise ValueError("Informe o nome e a dose exatamente como o médico prescreveu.")
        tl, dl = parse_times(times), parse_days(days)
        if start:
            dt.date.fromisoformat(start)
        if end:
            dt.date.fromisoformat(end)
        if start and end and end < start:
            raise ValueError("A data final é anterior à inicial.")
        if per_dose is None or not 0 < float(per_dose) <= 20:
            raise ValueError("Unidades por dose deve estar entre 0 e 20.")
        if stock is not None and float(stock) < 0:
            raise ValueError("Estoque não pode ser negativo.")
        if self.db.execute("SELECT COUNT(*) FROM meds WHERE active = 1").fetchone()[0] >= MAX_MEDS:
            raise ValueError(f"Limite de {MAX_MEDS} remédios ativos.")
        now = now or self._now()
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO meds(name, dose, times, days, start, end, stock, per_dose, created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, dose, json.dumps(tl), json.dumps(dl), start or None, end or None,
                 None if stock is None else float(stock), float(per_dose), now.strftime(UTC_FMT)))
            self.db.commit()
            return cur.lastrowid

    def _row(self, r) -> dict:
        d = dict(zip(("id", "name", "dose", "times", "days", "start", "end", "stock", "per_dose", "stock_alert"), r))
        d["times"], d["days"] = json.loads(d["times"]), json.loads(d["days"])
        return d

    COLS = "id, name, dose, times, days, start, end, stock, per_dose, stock_alert"

    def active_meds(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(f"SELECT {self.COLS} FROM meds WHERE active = 1 ORDER BY name").fetchall()
        return [self._row(r) for r in rows]

    def get(self, med_id: int) -> dict | None:
        with self._lock:
            r = self.db.execute(f"SELECT {self.COLS} FROM meds WHERE id = ? AND active = 1", (med_id,)).fetchone()
        return self._row(r) if r else None

    def find(self, name: str) -> list[dict]:
        n = _norm(name)
        return [m for m in self.active_meds() if n and n in _norm(m["name"])]

    def remove(self, med_id: int) -> bool:
        """Desativa (o histórico de doses fica); doses pendentes deixam de ser avisadas."""
        with self._lock:
            cur = self.db.execute("UPDATE meds SET active = 0 WHERE id = ? AND active = 1", (med_id,))
            self.db.commit()
            return cur.rowcount > 0

    def set_stock(self, med_id: int, stock: float) -> bool:
        if float(stock) < 0:
            raise ValueError("Estoque não pode ser negativo.")
        with self._lock:
            cur = self.db.execute("UPDATE meds SET stock = ?, stock_alert = NULL WHERE id = ? AND active = 1",
                                  (float(stock), med_id))
            self.db.commit()
            return cur.rowcount > 0

    def describe(self, med: dict) -> str:
        days = "todos os dias" if not med["days"] else ",".join(DAYS[d] for d in med["days"])
        out = f"{med['id']} | {med['name']} {med['dose']} | {', '.join(med['times'])} | {days}"
        if med["start"] or med["end"]:
            out += f" | {med['start'] or '…'} a {med['end'] or '…'}"
        if med["stock"] is not None:
            out += f" | estoque {fmt_stock(med['stock'])} (usa {fmt_stock(med['per_dose'])} por dose)"
        return out

    # ---------- geração das doses ----------
    def _due_utc(self, date: dt.date, hhmm: str) -> dt.datetime:
        h, m = map(int, hhmm.split(":"))
        return dt.datetime.combine(date, dt.time(h, m), tzinfo=self.tz).astimezone(dt.timezone.utc)

    def materialize(self, now: dt.datetime | None = None) -> int:
        """Cria as doses vencidas (hoje e ontem). Idempotente. Doses de antes do cadastro do remédio não existem;
        as que já passaram da janela nascem 'missed' (servidor fora do ar: o registro continua honesto)."""
        now = now or self._now()
        local = now.astimezone(self.tz).date()
        n = 0
        for med in self.active_meds():
            created = self._created(med["id"])
            for date in (local - dt.timedelta(days=1), local):
                if (med["start"] and date.isoformat() < med["start"]) or (med["end"] and date.isoformat() > med["end"]):
                    continue
                if med["days"] and date.weekday() not in med["days"]:
                    continue
                for t in med["times"]:
                    due = self._due_utc(date, t)
                    if due > now or due < created:
                        continue
                    state = "pending" if now - due <= self.missed_after else "missed"
                    with self._lock:
                        cur = self.db.execute("INSERT OR IGNORE INTO doses(med_id, due_utc, state) VALUES (?,?,?)",
                                              (med["id"], due.strftime(UTC_FMT), state))
                        self.db.commit()
                    n += cur.rowcount
        return n

    def _created(self, med_id: int) -> dt.datetime:
        r = self.db.execute("SELECT created_utc FROM meds WHERE id = ?", (med_id,)).fetchone()
        return dt.datetime.strptime(r[0], UTC_FMT).replace(tzinfo=dt.timezone.utc)

    def expire(self, now: dt.datetime | None = None) -> int:
        now = now or self._now()
        cutoff = (now - self.missed_after).strftime(UTC_FMT)
        with self._lock:
            cur = self.db.execute("UPDATE doses SET state = 'missed' WHERE state = 'pending' AND due_utc < ?", (cutoff,))
            self.db.commit()
            return cur.rowcount

    # ---------- entrega ----------
    DOSE_COLS = ("d.id, d.med_id, d.due_utc, d.attempts, m.name, m.dose")

    def _dose(self, r) -> dict:
        return dict(zip(("id", "med_id", "due_utc", "attempts", "name", "dose"), r))

    def due_doses(self, now: dt.datetime | None = None) -> list[dict]:
        """Doses pendentes a avisar agora: não adiadas, dentro do limite de tentativas e do intervalo de reenvio."""
        now = now or self._now()
        n, resend = now.strftime(UTC_FMT), (now - dt.timedelta(seconds=self.resend_s)).strftime(UTC_FMT)
        with self._lock:
            rows = self.db.execute(
                f"SELECT {self.DOSE_COLS} FROM doses d JOIN meds m ON m.id = d.med_id WHERE d.state = 'pending' AND m.active = 1 "
                "AND d.due_utc <= ? AND d.attempts < ? AND (d.snooze_utc IS NULL OR d.snooze_utc <= ?) "
                "AND (d.last_sent_utc IS NULL OR d.last_sent_utc <= ?) ORDER BY d.due_utc",
                (n, self.max_attempts, n, resend)).fetchall()
        return [self._dose(r) for r in rows]

    def sent(self, dose_id: int, now: dt.datetime | None = None) -> None:
        with self._lock:
            self.db.execute("UPDATE doses SET attempts = attempts + 1, last_sent_utc = ? WHERE id = ?",
                            ((now or self._now()).strftime(UTC_FMT), dose_id))
            self.db.commit()

    def unnotified_missed(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                f"SELECT {self.DOSE_COLS} FROM doses d JOIN meds m ON m.id = d.med_id "
                "WHERE d.state = 'missed' AND d.notified = 0 AND m.active = 1 ORDER BY d.due_utc").fetchall()
        return [self._dose(r) for r in rows]

    def mark_notified(self, dose_id: int) -> None:
        with self._lock:
            self.db.execute("UPDATE doses SET notified = 1 WHERE id = ?", (dose_id,))
            self.db.commit()

    # ---------- ação do usuário ----------
    def get_dose(self, dose_id: int) -> dict | None:
        with self._lock:
            r = self.db.execute(f"SELECT {self.DOSE_COLS}, d.state FROM doses d JOIN meds m ON m.id = d.med_id "
                                "WHERE d.id = ?", (dose_id,)).fetchone()
        if not r:
            return None
        d = self._dose(r[:6])
        d["state"] = r[6]
        return d

    def act(self, dose_id: int, action: str, now: dt.datetime | None = None, snooze_min: int = 10) -> str:
        """action: taken | skipped | snooze. Idempotente: tocar duas vezes em 'Tomei' não baixa o estoque duas vezes."""
        now = now or self._now()
        d = self.get_dose(dose_id)
        if not d:
            return "Dose não encontrada."
        label = f"{d['name']} {d['dose']}"
        if action == "snooze":
            if d["state"] != "pending":
                return f"{label}: dose já registrada como {self._state_pt(d['state'])}."
            mins = max(1, min(int(snooze_min), 30))
            with self._lock:
                self.db.execute("UPDATE doses SET snooze_utc = ?, last_sent_utc = NULL WHERE id = ?",
                                ((now + dt.timedelta(minutes=mins)).strftime(UTC_FMT), dose_id))
                self.db.commit()
            return f"{label}: adiado {mins} min."
        if action not in ("taken", "skipped"):
            raise ValueError("Ação inválida.")
        if d["state"] in ("taken", "skipped"):
            return f"{label}: dose já registrada como {self._state_pt(d['state'])}."
        with self._lock:  # pendente ou perdida ainda pode ser registrada (o usuário pode tomar atrasado)
            cur = self.db.execute("UPDATE doses SET state = ?, acted_utc = ?, notified = 1 WHERE id = ? "
                                  "AND state IN ('pending', 'missed')", (action, now.strftime(UTC_FMT), dose_id))
            self.db.commit()
        if not cur.rowcount:
            return f"{label}: dose já registrada."
        if action == "skipped":
            return f"{label}: dose pulada."
        extra = self._consume(d["med_id"])
        local = now.astimezone(self.tz)
        return f"{label}: tomada às {local:%H:%M}." + extra

    @staticmethod
    def _state_pt(s: str) -> str:
        return {"taken": "tomada", "skipped": "pulada", "missed": "perdida", "pending": "pendente"}.get(s, s)

    def _consume(self, med_id: int) -> str:
        with self._lock:
            r = self.db.execute("SELECT stock, per_dose FROM meds WHERE id = ?", (med_id,)).fetchone()
            if not r or r[0] is None:
                return ""
            new = max(0.0, r[0] - r[1])
            self.db.execute("UPDATE meds SET stock = ? WHERE id = ?", (new, med_id))
            self.db.commit()
        return f" Restam {fmt_stock(new)} no estoque." if new > 0 else " O estoque acabou."

    def taken_by_name(self, name: str, now: dt.datetime | None = None) -> str:
        """'Tomei o X': registra a dose mais recente ainda em aberto (pendente ou perdida nas últimas 12 h)."""
        now = now or self._now()
        found = self.find(name)
        if not found:
            return f"Não há remédio ativo com o nome '{name}'."
        if len(found) > 1:
            return "Mais de um remédio combina: " + "; ".join(f"{m['id']} {m['name']}" for m in found) + ". Diga qual."
        since = (now - dt.timedelta(hours=12)).strftime(UTC_FMT)
        with self._lock:
            r = self.db.execute("SELECT id FROM doses WHERE med_id = ? AND state IN ('pending','missed') AND due_utc <= ? "
                                "AND due_utc >= ? ORDER BY due_utc DESC LIMIT 1",
                                (found[0]["id"], now.strftime(UTC_FMT), since)).fetchone()
        if not r:
            return f"Nenhuma dose em aberto de {found[0]['name']} nas últimas 12 h; nada foi registrado."
        return self.act(r[0], "taken", now)

    # ---------- consultas ----------
    def today(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or self._now()
        day = now.astimezone(self.tz).date()
        lo = self._due_utc(day, "00:00").strftime(UTC_FMT)
        hi = self._due_utc(day + dt.timedelta(days=1), "00:00").strftime(UTC_FMT)
        with self._lock:
            rows = self.db.execute(
                f"SELECT {self.DOSE_COLS}, d.state FROM doses d JOIN meds m ON m.id = d.med_id "
                "WHERE d.due_utc >= ? AND d.due_utc < ? ORDER BY d.due_utc", (lo, hi)).fetchall()
        out = []
        for r in rows:
            d = self._dose(r[:6])
            d["state"] = r[6]
            out.append(d)
        return out

    def days_left(self, med: dict) -> float | None:
        if med["stock"] is None:
            return None
        per_day = med["per_dose"] * len(med["times"]) * (len(med["days"]) / 7 if med["days"] else 1)
        return float("inf") if per_day <= 0 else med["stock"] / per_day

    def low_stock_alerts(self, now: dt.datetime | None = None) -> list[tuple[dict, float]]:
        """Remédios com estoque para poucos dias e ainda não avisados hoje (marque com `stock_alerted` após entregar)."""
        today = (now or self._now()).astimezone(self.tz).date().isoformat()
        out = []
        for med in self.active_meds():
            left = self.days_left(med)
            if left is not None and left <= self.low_stock_days and med["stock_alert"] != today:
                out.append((med, left))
        return out

    def stock_alerted(self, med_id: int, now: dt.datetime | None = None) -> None:
        today = (now or self._now()).astimezone(self.tz).date().isoformat()
        with self._lock:
            self.db.execute("UPDATE meds SET stock_alert = ? WHERE id = ?", (today, med_id))
            self.db.commit()


def format_dose(dose: dict, tz, detail: str = "full", attempt: int = 0) -> str:
    """Texto do aviso. detail='minimal' não leva nome nem dose ao canal externo (só horário)."""
    due = dt.datetime.strptime(dose["due_utc"], UTC_FMT).replace(tzinfo=dt.timezone.utc).astimezone(tz)
    what = f"{dose['name']} {dose['dose']}" if detail == "full" else "seu remédio"
    head = "💊 Hora do remédio" if attempt < 2 else f"⚠️ Ainda sem confirmação (aviso {attempt + 1})"
    return f"{head}: {what} ({due:%H:%M})."
