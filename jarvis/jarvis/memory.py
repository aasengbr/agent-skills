"""Estado local em SQLite: fatos memorizados, uso diário de tokens e registro (auditoria) de ações."""
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
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
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, "
                "tool TEXT, args TEXT, status TEXT, note TEXT)"
            )
            self.db.commit()

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
                "SELECT tokens FROM usage WHERE day = ?", (dt.date.today().isoformat(),)
            ).fetchone()
        return row[0] if row else 0

    def add_usage(self, tokens: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO usage(day, tokens) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET tokens = tokens + ?",
                (dt.date.today().isoformat(), tokens, tokens),
            )
            self.db.commit()

    # ---------- auditoria: o que o JARVIS executou ----------
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
