import os
import sqlite3
import sys
import tempfile
import time
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aasis.backup import backup_db, backup_due  # noqa: E402
from aasis.memory import Memory  # noqa: E402

VPS = Path(__file__).resolve().parent.parent / "deploy" / "vps"


def test_backup_is_consistent_private_and_pruned():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mem = Memory(tmp / "aasis.db")
        mem.mem.save("fact", "Gosta de jazz")
        dest = tmp / "backups"
        t0 = 1_700_000_000
        outs = [backup_db(tmp / "aasis.db", dest, keep=3, now=t0 + i * 100) for i in range(5)]
        files = sorted(dest.glob("aasis-*.db"))
        assert len(files) == 3 and files == outs[-3:]                      # só os 3 mais recentes
        assert oct(os.stat(files[-1]).st_mode & 0o777) == "0o600" and oct(os.stat(dest).st_mode & 0o777) == "0o700"
        restored = Memory(files[-1])                                       # o backup abre e tem os dados
        assert [r["content"] for r in restored.mem.list()] == ["Gosta de jazz"]
        mem.mem.save("fact", "Fato novo depois do backup")                 # o original segue funcionando
        assert len(mem.mem.list()) == 2 and len(restored.mem.list()) == 1


def test_backup_while_writing_and_keep_minimum_one():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mem = Memory(tmp / "aasis.db")
        for i in range(50):
            mem.mem.save("fact", f"fato {i}")
        out = backup_db(tmp / "aasis.db", tmp / "b", keep=0, now=1)       # keep inválido não apaga tudo
        assert out.exists() and len(Memory(out).mem.list(limit=100)) == 50


def test_backup_due_logic():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "b"
        assert backup_due(d, 86400)                                        # nunca fez
        d.mkdir()
        f = d / "aasis-20260101-000000.db"
        f.write_bytes(b"x")
        os.utime(f, (1000, 1000))
        assert not backup_due(d, 86400, now=1000 + 3600) and backup_due(d, 86400, now=1000 + 86400)


def test_missing_source_raises_instead_of_creating_empty_backup():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            backup_db(Path(tmp) / "nao-existe.db", Path(tmp) / "b")
        except sqlite3.OperationalError:
            return
        raise AssertionError("deveria falhar")


def test_vps_files_are_valid_and_safe_by_default():
    compose = yaml.safe_load((VPS / "docker-compose.yml").read_text(encoding="utf-8"))
    app, caddy = compose["services"]["aasis"], compose["services"]["caddy"]
    assert "ports" not in app and app["expose"] == ["8765"]                  # o app NÃO é publicado na internet
    assert caddy["ports"] == ["80:80", "443:443"] and "aasis-data" in str(app["volumes"])
    assert "ALL" in app["cap_drop"] and "no-new-privileges:true" in app["security_opt"]
    assert "AASIS_DOMAIN" in app["environment"]["AASIS_ALLOWED_HOSTS"]       # só o domínio é aceito
    ci = yaml.safe_load((VPS / "cloud-init.yaml").read_text(encoding="utf-8"))
    cmds = " ".join(ci["runcmd"])
    assert "ufw default deny incoming" in cmds and "ufw allow 443/tcp" in cmds and "fail2ban" in cmds
    for env in ((VPS / "env.example").read_text(encoding="utf-8").splitlines()):
        if env and not env.startswith("#"):                                 # nenhum comentário na linha do valor
            assert "#" not in env, env
    assert "flush_interval -1" in (VPS / "Caddyfile").read_text(encoding="utf-8")
