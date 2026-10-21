import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

import anthropic  # noqa: E402
try:  # versões novas do SDK trazem o cliente HTTP embutido como httpx2
    import httpx2 as httpx  # noqa: E402
except ImportError:
    import httpx  # noqa: E402
import pytest  # noqa: E402

from aasis.brain import Brain  # noqa: E402
from aasis.google_tools import Google  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.tools import Tools  # noqa: E402
from aasis.vision import Screen  # noqa: E402


def text(t, cites=()):
    return NS(type="text", text=t, citations=[NS(url=u, title=f"T-{u}") for u in cites] or None)


class Client:
    def __init__(self, script):
        self.script, self.requests, self.messages = list(script), [], self

    def create(self, **kw):
        self.requests.append({"tools": [t["name"] for t in kw["tools"]], "messages": list(kw["messages"])})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def resp(content, stop="end_turn", searches=0, tokens=10):
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=tokens, output_tokens=0, server_tool_use=NS(web_search_requests=searches)))


def bad_request(msg):
    r = httpx.Response(400, request=httpx.Request("POST", "http://x"))
    return anthropic.BadRequestError(msg, response=r, body=None)


def make(tmp, script, ws=True, cap=50, **kw):
    cfg = {"assistant": {"name": "A", "user_title": "senhor", "language": "pt-BR", "model": "m", "max_tokens": 5,
                         "max_tool_rounds": 4, **kw},
           "safety": {"workspace": tmp, "apps": {}, "confirm_tools": ["calendar_update", "calendar_delete"]},
           "screen": {"enabled": False}, "budget": {"daily_searches": cap},
           "web_search": {"enabled": ws, "max_uses": 3, "country": "BR"}}
    mem = Memory(Path(tmp) / "t.db")
    events = []
    t = Tools(cfg, mem, google=Google(cfg, Path(tmp), services={"calendar": Cal(events)}), screen=Screen(cfg))
    b = Brain(cfg, t, mem, lambda _: True, emit=lambda k, **d: events.append((k, d)))
    b.client = Client(script)
    return b, t, mem, events


# ---------- busca na web ----------
def test_search_tool_declared_budgeted_and_disableable():
    with tempfile.TemporaryDirectory() as tmp:
        b, t, mem, _ = make(tmp, [], cap=2)
        ws = [s for s in t.schemas() if s["name"] == "web_search"][0]
        assert ws["type"] == "web_search_20250305" and ws["max_uses"] == 3
        assert ws["user_location"] == {"type": "approximate", "country": "BR", "timezone": "America/Sao_Paulo"}
        mem.add_searches(2)                                                    # teto diário atingido
        assert "web_search" not in {s["name"] for s in t.schemas()}
        b2, t2, _, _ = make(tmp, [], ws=False)
        assert "web_search" not in {s["name"] for s in t2.schemas()}


def test_searches_counted_and_sources_deduped():
    with tempfile.TemporaryDirectory() as tmp:
        b, _, mem, _ = make(tmp, [resp([text("A ", ["u1", "u2"]), text("B", ["u1"])], searches=2)])
        assert b.ask("quanto está o dólar?") == "A B"
        assert mem.searches_today() == 2
        assert [s["url"] for s in b.last_sources] == ["u1", "u2"]


def test_pause_turn_continues_with_same_history():
    with tempfile.TemporaryDirectory() as tmp:
        b, _, _, _ = make(tmp, [resp([text("pesquisando")], stop="pause_turn"), resp([text("pronto")])])
        assert b.ask("notícias") == "pronto"
        second = b.client.requests[1]["messages"]
        assert second[-1]["role"] == "assistant"                               # reenvia o turno pausado


def test_search_disabled_in_org_falls_back_instead_of_breaking():
    with tempfile.TemporaryDirectory() as tmp:
        b, t, _, events = make(tmp, [bad_request("web search is not enabled for this organization"), resp([text("ok")])])
        assert b.ask("oi") == "ok"
        assert "web_search" in b.client.requests[0]["tools"] and "web_search" not in b.client.requests[1]["tools"]
        assert t.web_search_disabled and any(k == "notice" for k, _ in events)


def test_other_bad_request_propagates_and_history_rolls_back_after_trim():
    with tempfile.TemporaryDirectory() as tmp:
        b, _, _, _ = make(tmp, [resp([text("1")]), resp([text("2")]), resp([text("3")]), bad_request("outro erro")],
                          max_history_turns=2)
        for q in ("a", "b", "c"):
            b.ask(q)
        out = b.ask("QUEBRA")                                                  # falha do provedor não derruba o AASIS
        assert "todos os modelos disponíveis falharam" in out and "bad_request" in out
        assert all(m.get("text") != "QUEBRA" for m in b.history)               # pergunta pendente removida
        assert b.history[-1]["role"] == "assistant"


def test_system_prompt_scope_and_finance_rules():
    with tempfile.TemporaryDirectory() as tmp:
        b, _, _, _ = make(tmp, [])
        s = b._stable()
        assert "AAS Intelligent System" in s and "escopo é aberto" in s
        assert "NÃO dê recomendação de investimento" in s and "untrusted" in s and "J.A.R.V.I.S" not in s


# ---------- agenda: editar e apagar ----------
class Cal:
    def __init__(self, log):
        self.log, self.last = log, None

    def events(self):
        return self

    def get(self, **kw):
        self.last = "get"
        return self

    def patch(self, **kw):
        self.log.append(("patch", kw))
        self.last = "patch"
        return self

    def delete(self, **kw):
        self.log.append(("delete", kw))
        self.last = "delete"
        return self

    def execute(self):
        return {"get": {"summary": "Dentista", "start": {"dateTime": "2026-10-22T14:00:00-03:00"},
                        "attendees": [{"email": "a@b.c"}, {"email": "d@e.f"}]},
                "patch": {"summary": "Dentista"}, "delete": {}}[self.last]


def test_calendar_update_delete_confirmation_shows_title_and_guests():
    with tempfile.TemporaryDirectory() as tmp:
        b, t, _, events = make(tmp, [])
        labels = []
        denied = t.run("calendar_delete", {"event_id": "e1"}, lambda l: labels.append(l) or False)
        assert "negada" in denied and not any(k == "delete" for k, _ in events)
        assert "'Dentista'" in labels[0] and "2 convidado(s) que NÃO serão notificados" in labels[0]
        assert "apagado" in t.run("calendar_delete", {"event_id": "e1"}, lambda _: True)
        delete_call = [kw for k, kw in events if k == "delete"][0]
        assert delete_call["sendUpdates"] == "none" and delete_call["eventId"] == "e1"
        assert "atualizado" in t.run("calendar_update", {"event_id": "e1", "start": "2026-10-23T15:00:00"}, lambda _: True)
        patch_call = [kw for k, kw in events if k == "patch"][0]
        assert patch_call["body"]["start"] == {"dateTime": "2026-10-23T15:00:00", "timeZone": "America/Sao_Paulo"}
        assert "Erro" in t.run("calendar_update", {"event_id": "e1"}, lambda _: True)             # nada para alterar
        assert "Erro" in t.run("calendar_update", {"event_id": "e1", "start": "amanhã"}, lambda _: True)


def test_new_tools_registered_and_wrappers_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        _, t, _, _ = make(tmp, [])
        names = {s["name"] for s in t.schemas()}
        assert {"news", "fx_rates", "stock_quote", "bcb_indicators", "weather", "world_time", "web_fetch",
                "calendar_update", "calendar_delete"} <= names
        assert "Europe/Lisbon" in t.run("world_time", {"timezone": "Europe/Lisbon"}, lambda _: True)
        assert "Erro" in t.run("web_fetch", {"url": "http://169.254.169.254/"}, lambda _: True)    # SSRF bloqueado
