"""Backup consistente do banco SQLite (API de backup do SQLite: seguro mesmo com o AASIS rodando)."""
import os
import sqlite3
import threading
import time
from pathlib import Path


def backup_db(src: Path, dest_dir: Path, keep: int = 14, now: float | None = None) -> Path:
    """Copia `src` para dest_dir/aasis-AAAAMMDD-HHMMSS.db (permissão 600) e apaga os mais antigos além de `keep`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    out = dest_dir / time.strftime("aasis-%Y%m%d-%H%M%S.db", time.gmtime(now))
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(out)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.chmod(out, 0o600)
    for old in sorted(dest_dir.glob("aasis-*.db"))[:-max(1, keep)]:
        old.unlink()
    return out


def backup_due(dest_dir: Path, interval: float, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    files = list(dest_dir.glob("aasis-*.db")) if dest_dir.exists() else []
    return not files or now - max(f.stat().st_mtime for f in files) >= interval


def start_backup_loop(src: Path, dest_dir: Path, keep: int = 14, interval: float = 86400, poll: float = 3600) -> None:
    """Faz um backup por dia em segundo plano (confere de hora em hora). Nunca derruba o servidor."""
    def loop():
        while True:
            try:
                if backup_due(dest_dir, interval):
                    backup_db(src, dest_dir, keep)
            except Exception:
                pass
            time.sleep(poll)

    threading.Thread(target=loop, daemon=True).start()
