import base64
import io
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aasis.google_tools import Google  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.tools import Tools  # noqa: E402
from aasis.vision import Screen  # noqa: E402


class Files:
    def __init__(self, log, mime):
        self.log, self.mime, self.last = log, mime, None

    def list(self, **kw):
        self.log.append(("list", kw))
        self.last = "list"
        return self

    def get(self, **kw):
        self.last = "get"
        return self

    def export(self, **kw):
        self.log.append(("export", kw))
        self.last = "export"
        return self

    def get_media(self, **kw):
        self.last = "media"
        return self

    def create(self, **kw):
        self.log.append(("create", kw))
        self.last = "create"
        return self

    def execute(self):
        return {
            "list": {"files": [{"id": "f1", "name": "Contrato", "mimeType": "x", "modifiedTime": "t"}]},
            "get": {"name": "Doc", "mimeType": self.mime},
            "export": b"conteudo do doc",
            "media": b"texto puro",
            "create": {"id": "n1"},
        }[self.last]


class Drive:
    def __init__(self, log, mime):
        self.f = Files(log, mime)

    def files(self):
        return self.f


def make(tmp, log, mime="application/vnd.google-apps.document", grabber=None):
    cfg = {"safety": {"workspace": tmp, "apps": {}, "confirm_tools": ["drive_create_doc", "screen_look"]},
           "screen": {"max_side": 400}}
    g = Google(cfg, Path(tmp), services={"drive": Drive(log, mime)})
    return Tools(cfg, Memory(Path(tmp) / "t.db"), google=g, screen=Screen(cfg, grabber=grabber))


def test_drive_search_escapes_and_wraps():
    with tempfile.TemporaryDirectory() as tmp:
        log = []
        out = make(tmp, log).run("drive_search", {"query": "O'Brien"}, lambda _: True)
        assert "<drive_list untrusted>" in out and "Contrato" in out
        assert "O\\'Brien" in log[0][1]["q"] and "trashed = false" in log[0][1]["q"]


def test_drive_read_export_and_unsupported():
    with tempfile.TemporaryDirectory() as tmp:
        log = []
        out = make(tmp, log).run("drive_read", {"file_id": "f1"}, lambda _: True)
        assert "conteudo do doc" in out and "<drive_file untrusted>" in out
        assert log[0][1]["mimeType"] == "text/plain"
        out = make(tmp, [], mime="application/pdf").run("drive_read", {"file_id": "f1"}, lambda _: True)
        assert "não suportado" in out


def test_drive_create_requires_confirmation():
    with tempfile.TemporaryDirectory() as tmp:
        log = []
        t = make(tmp, log)
        assert "negada" in t.run("drive_create_doc", {"name": "a", "content": "b"}, lambda _: False)
        assert not log
        assert "Documento criado" in t.run("drive_create_doc", {"name": "a", "content": "b"}, lambda _: True)


def test_screen_look_confirm_resize_and_untrusted_note():
    with tempfile.TemporaryDirectory() as tmp:
        grabs = []

        def grab():
            grabs.append(1)
            return Image.new("RGB", (3000, 2000), "navy")

        t = make(tmp, [], grabber=grab)
        assert "negada" in t.run("screen_look", {}, lambda _: False) and not grabs
        out = t.run("screen_look", {}, lambda _: True)
        assert out[0]["type"] == "text" and "não confiável" in out[0]["text"]
        img = Image.open(io.BytesIO(base64.b64decode(out[1]["source"]["data"])))
        assert max(img.size) <= 400 and out[1]["source"]["media_type"] == "image/jpeg"


def test_screen_tool_hidden_when_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"safety": {"workspace": tmp, "apps": {}, "confirm_tools": []}, "screen": {"enabled": False}}
        t = Tools(cfg, Memory(Path(tmp) / "t.db"), google=Google(cfg, Path(tmp)))
        assert "screen_look" not in {s["name"] for s in t.schemas()}
