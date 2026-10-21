"""Memória de longo prazo em SQLite local (fatos que o usuário pediu para lembrar)."""
import sqlite3
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "id INTEGER PRIMARY KEY, text TEXT NOT NULL, "
            "created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.commit()

    def remember(self, text: str) -> int:
        cur = self.db.execute("INSERT INTO facts(text) VALUES (?)", (text.strip(),))
        self.db.commit()
        return cur.lastrowid

    def recall(self, query: str = "", limit: int = 10) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [r[0] for r in rows]

    def forget(self, fact_id: int) -> bool:
        cur = self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self.db.commit()
        return cur.rowcount > 0

    def all_for_prompt(self, limit: int = 30) -> str:
        facts = self.recall("", limit)
        return "\n".join(f"- {f}" for f in facts)
