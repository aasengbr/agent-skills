import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aasis.memory import Memory  # noqa: E402
from aasis.tools import Tools  # noqa: E402


def make(tmp):
    cfg = {
        "safety": {
            "workspace": tmp,
            "apps": {"calculadora": "calc"},
            "confirm_tools": ["write_file", "open_app"],
        }
    }
    return Tools(cfg, Memory(Path(tmp) / "t.db"))


def test_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        out = make(tmp).run("read_file", {"path": "../../etc/passwd"}, lambda _: True)
        assert "fora do workspace" in out


def test_confirmation_denied():
    with tempfile.TemporaryDirectory() as tmp:
        out = make(tmp).run("write_file", {"path": "a.txt", "content": "x"}, lambda _: False)
        assert "negada" in out and not (Path(tmp) / "a.txt").exists()


def test_write_read_and_memory():
    with tempfile.TemporaryDirectory() as tmp:
        t = make(tmp)
        t.run("write_file", {"path": "a.txt", "content": "olá"}, lambda _: True)
        assert t.run("read_file", {"path": "a.txt"}, lambda _: True) == "olá"
        t.run("memory_save", {"content": "gosto de café"}, lambda _: True)
        assert "café" in t.run("memory_search", {"query": "café"}, lambda _: True)


def test_app_allowlist_and_unknown_tool():
    with tempfile.TemporaryDirectory() as tmp:
        t = make(tmp)
        assert "allowlist" in t.run("open_app", {"name": "malware"}, lambda _: True)
        assert "desconhecida" in t.run("__init__", {}, lambda _: True)
