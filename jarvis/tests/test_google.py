import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.google_tools import Google, _body_text  # noqa: E402
from jarvis.memory import Memory  # noqa: E402
from jarvis.tools import Tools  # noqa: E402


class Call:
    """Encadeia chamadas estilo googleapiclient e registra argumentos."""

    def __init__(self, result, log, name):
        self.result, self.log, self.name = result, log, name

    def __getattr__(self, attr):
        return lambda **kw: self._step(attr, kw)

    def _step(self, attr, kw):
        self.log.append((self.name + "." + attr, kw))
        return self

    def execute(self):
        return self.result


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


def fake(log):
    class Msgs(Call):
        def get(self, **kw):
            self.log.append(("get", kw))
            if kw["format"] == "full":
                return Call({"payload": {"headers": [{"name": "From", "value": "a@x.com"},
                                                      {"name": "Subject", "value": "Oi"}],
                                         "parts": [{"mimeType": "text/html", "body": {"data": b64("<b>h</b>")}},
                                                   {"mimeType": "text/plain", "body": {"data": b64("corpo ok")}}]}},
                            self.log, "x")
            return Call({"payload": {"headers": [{"name": "From", "value": "a@x.com"},
                                                  {"name": "Subject", "value": "Reunião"},
                                                  {"name": "Date", "value": "hoje"}]}}, self.log, "x")

    class Gmail:
        def users(self):
            return self

        def messages(self):
            return Msgs({"messages": [{"id": "m1"}]}, log, "msgs")

        def drafts(self):
            return Call({"id": "d1"}, log, "drafts")

    class Cal:
        def events(self):
            class Ev(Call):
                def _step(self, attr, kw):
                    self.last = attr
                    return super()._step(attr, kw)

                def execute(self):
                    if self.last == "insert":
                        return {"id": "e2"}
                    return self.result

            return Ev({"items": [{"id": "e1", "start": {"dateTime": "2026-10-22T10:00:00-03:00"},
                                  "summary": "Dentista"}]}, log, "events")

    return {"gmail": Gmail(), "calendar": Cal()}


def make(tmp, log):
    cfg = {"safety": {"workspace": tmp, "apps": {}, "confirm_tools": ["gmail_draft", "calendar_create"]},
           "google": {"timezone": "America/Sao_Paulo"}}
    g = Google(cfg, Path(tmp), services=fake(log))
    return Tools(cfg, Memory(Path(tmp) / "t.db"), google=g)


def test_search_read_wrap_untrusted():
    with tempfile.TemporaryDirectory() as tmp:
        t = make(tmp, [])
        out = t.run("gmail_search", {"query": "is:unread"}, lambda _: True)
        assert "<email_list untrusted>" in out and "Reunião" in out and "id=m1" in out
        out = t.run("gmail_read", {"message_id": "m1"}, lambda _: True)
        assert "corpo ok" in out and "<email untrusted>" in out


def test_draft_needs_confirmation_and_never_sends():
    with tempfile.TemporaryDirectory() as tmp:
        log = []
        t = make(tmp, log)
        assert "negada" in t.run("gmail_draft", {"to": "b@x.com", "subject": "s", "body": "b"}, lambda _: False)
        assert not log
        assert "Rascunho criado" in t.run("gmail_draft", {"to": "b@x.com", "subject": "s", "body": "b"}, lambda _: True)
        assert all("send" not in name for name, _ in log)


def test_calendar_list_and_create_validation():
    with tempfile.TemporaryDirectory() as tmp:
        log = []
        t = make(tmp, log)
        assert "Dentista" in t.run("calendar_list", {"days": 2}, lambda _: True)
        assert "Erro" in t.run("calendar_create", {"summary": "x", "start": "amanhã", "end": "depois"}, lambda _: True)
        ok = t.run("calendar_create", {"summary": "Reunião", "start": "2026-10-22T14:00:00",
                                       "end": "2026-10-22T15:00:00"}, lambda _: True)
        assert "Evento criado" in ok
        assert any(kw.get("body", {}).get("start", {}).get("timeZone") == "America/Sao_Paulo" for _, kw in log)


def test_google_tools_hidden_without_credentials():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"safety": {"workspace": tmp, "apps": {}, "confirm_tools": []}}
        t = Tools(cfg, Memory(Path(tmp) / "t.db"), google=Google(cfg, Path(tmp)))
        names = {s["name"] for s in t.schemas()}
        assert "gmail_search" not in names and "get_time" in names


def test_body_text_multipart():
    p = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/plain", "body": {"data": b64("x")}}]}
    assert _body_text(p) == "x"
