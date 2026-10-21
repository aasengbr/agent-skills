"""Estado local em SQLite: fatos memorizados, uso diário de tokens e registro (auditoria) de ações."""
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path


class Memory:
    def __init__(self, path: Path, tz=None):
        self.tz = tz  # fuso do usuário (o servidor na nuvem roda em UTC)
        # o HUD atende pedidos em outras threads: conexão compartilhada + trava em todo acesso
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS facts ("
                "id INTEGER PRIMARY KEY, text TEXT NOT NULL, "
                "created TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            self.db.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, tokens INTEGER NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS searches (day TEXT PRIMARY KEY, n INTEGER NOT NULL)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, "
                "tool TEXT, args TEXT, status TEXT, note TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, text TEXT NOT NULL, due TEXT, "
                "done INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, text TEXT NOT NULL, "
                "due_utc TEXT NOT NULL, state TEXT DEFAULT 'pending')"
            )
            self.db.commit()

    def _today(self) -> str:
        return dt.datetime.now(self.tz).date().isoformat()

    # ---------- fatos ----------
    def remember(self, text: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO facts(text) VALUES (?)", (text.strip(),))
            self.db.commit()
            return cur.lastrowid

    def recall(self, query: str = "", limit: int = 10) -> list[str]:
        with self._lock:
            rows = self.db.execute(
                "SELECT text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [r[0] for r in rows]

    def forget(self, fact_id: int) -> bool:
        with self._lock:
            cur = self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self.db.commit()
            return cur.rowcount > 0

    def all_for_prompt(self, limit: int = 30) -> str:
        return "\n".join(f"- {f}" for f in self.recall("", limit))

    # ---------- uso diário de tokens (teto de gasto) ----------
    def usage_today(self) -> int:
        with self._lock:
            row = self.db.execute(
                "SELECT tokens FROM usage WHERE day = ?", (self._today(),)
            ).fetchone()
        return row[0] if row else 0

    def add_usage(self, tokens: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO usage(day, tokens) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET tokens = tokens + ?",
                (self._today(), tokens, tokens),
            )
            self.db.commit()

    def searches_today(self) -> int:
        with self._lock:
            row = self.db.execute("SELECT n FROM searches WHERE day = ?", (self._today(),)).fetchone()
        return row[0] if row else 0

    def add_searches(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self.db.execute("INSERT INTO searches(day, n) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET n = n + ?",
                            (self._today(), n, n))
            self.db.commit()

    # ---------- auditoria: o que o AASIS executou ----------
    def log_action(self, tool: str, args: dict, status: str, note: str = "", keep: int = 1000) -> None:
        """Guarda ferramenta, argumentos (truncados) e status. O resultado NÃO é guardado (pode ter e-mails)."""
        with self._lock:
            self.db.execute(
                "INSERT INTO audit(tool, args, status, note) VALUES (?, ?, ?, ?)",
                (tool, json.dumps(args, ensure_ascii=False, default=str)[:300], status, note[:120]),
            )
            self.db.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - ?", (keep,))
            self.db.commit()

    def recent_actions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT ts, tool, args, status, note FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(("ts", "tool", "args", "status", "note"), r)) for r in rows]

    # ---------- tarefas ----------
    def add_task(self, text: str, due: str = "") -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO tasks(text, due) VALUES (?, ?)", (text.strip()[:200], due or None))
            self.db.commit()
            return cur.lastrowid

    def list_tasks(self, show_done: bool = False) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, text, due, done FROM tasks WHERE done = 0 OR ? ORDER BY done, due IS NULL, due, id",
                (1 if show_done else 0,),
            ).fetchall()
        return [dict(zip(("id", "text", "due", "done"), r)) for r in rows]

    def set_task_done(self, task_id: int) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE tasks SET done = 1 WHERE id = ? AND done = 0", (task_id,))
            self.db.commit()
            return cur.rowcount > 0

    def remove_task(self, task_id: int) -> bool:
        with self._lock:
            cur = self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self.db.commit()
            return cur.rowcount > 0

    # ---------- lembretes (horários em UTC, ISO 8601) ----------
    def add_reminder(self, text: str, due_utc: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO reminders(text, due_utc) VALUES (?, ?)", (text.strip()[:200], due_utc))
            self.db.commit()
            return cur.lastrowid

    def pending_reminders(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, text, due_utc FROM reminders WHERE state = 'pending' ORDER BY due_utc"
            ).fetchall()
        return [dict(zip(("id", "text", "due_utc"), r)) for r in rows]

    def due_reminders(self, now_utc: str) -> list[dict]:
        return [r for r in self.pending_reminders() if r["due_utc"] <= now_utc]

    def ack_reminder(self, reminder_id: int) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE reminders SET state = 'done' WHERE id = ? AND state = 'pending'", (reminder_id,))
            self.db.commit()
            return cur.rowcount > 0

    def cancel_reminder(self, reminder_id: int) -> bool:
        return self.ack_reminder(reminder_id)
