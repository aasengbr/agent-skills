import datetime as dt
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

import pytest  # noqa: E402

from jarvis.brain import Brain  # noqa: E402
from jarvis.google_tools import Google  # noqa: E402
from jarvis.hud import Hud  # noqa: E402
from jarvis.memory import Memory  # noqa: E402
from jarvis.scheduler import Scheduler  # noqa: E402
from jarvis.tools import Tools  # noqa: E402
from jarvis.vision import Screen  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")


def make(tmp, google=None):
    cfg = {"assistant": {"timezone": "America/Sao_Paulo", "name": "J", "user_title": "senhor", "language": "pt-BR",
                         "model": "m", "max_tokens": 5, "max_tool_rounds": 2},
           "safety": {"workspace": tmp, "apps": {}, "confirm_tools": []}, "screen": {"enabled": False}}
    mem = Memory(Path(tmp) / "t.db", tz=SP)
    return Tools(cfg, mem, google=google or Google(cfg, Path(tmp)), screen=Screen(cfg)), mem, cfg


def test_tasks_lifecycle_and_validation():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, _ = make(tmp)
        assert "criada" in t.run("task_add", {"text": "Pagar boleto", "due": "2030-05-01"}, lambda _: True)
        t.run("task_add", {"text": "Ligar para o João"}, lambda _: True)
        out = t.run("task_list", {}, lambda _: True)
        assert "<tasks untrusted>" in out and "Pagar boleto" in out and "prazo 2030-05-01" in out
        assert out.index("Pagar boleto") < out.index("Ligar")                      # com prazo vem primeiro
        assert "Erro" in t.run("task_add", {"text": "x", "due": "amanhã"}, lambda _: True)  # data inválida
        assert "concluída" in t.run("task_done", {"id": 1}, lambda _: True)
        assert "Pagar boleto" not in t.run("task_list", {}, lambda _: True)
        assert "Pagar boleto" in t.run("task_list", {"show_done": True}, lambda _: True)
        assert "removida" in t.run("task_remove", {"id": 2}, lambda _: True)
        assert "não encontrada" in t.run("task_done", {"id": 99}, lambda _: True)


def test_reminder_timezone_past_and_limit():
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, _ = make(tmp)
        out = t.run("reminder_add", {"text": "Reunião", "when": "2030-01-15T09:00:00"}, lambda _: True)
        assert "15/01 09:00" in out
        due = [r for r in mem.pending_reminders() if r["text"] == "Reunião"][0]["due_utc"]
        assert due == "2030-01-15T12:00:00Z"                                         # São Paulo = UTC-3
        assert "15/01 09:00" in t.run("reminder_list", {}, lambda _: True)           # volta ao fuso local
        assert "já passou" in t.run("reminder_add", {"text": "x", "when": "2020-01-01T09:00:00"}, lambda _: True)
        assert "Informe" in t.run("reminder_add", {"text": "x"}, lambda _: True)
        for i in range(100):
            mem.add_reminder("f", "2031-01-01T00:00:00Z")
        assert "Limite" in t.run("reminder_add", {"text": "x", "in_minutes": 3}, lambda _: True)
        assert "cancelado" in t.run("reminder_cancel", {"id": 1}, lambda _: True)


def test_memory_day_uses_user_timezone():
    with tempfile.TemporaryDirectory() as tmp:
        assert Memory(Path(tmp) / "a.db", tz=SP)._today() == dt.datetime.now(SP).date().isoformat()


def test_scheduler_redelivers_until_ack_and_waits_without_client():
    with tempfile.TemporaryDirectory() as tmp:
        _, mem, _ = make(tmp)
        rid = mem.add_reminder("Remédio", "2030-01-01T12:00:00Z")
        got, ready = [], {"ok": False}
        sch = Scheduler(mem, lambda r, first: got.append((r["id"], first)), ready=lambda: ready["ok"], resend=60)
        t0 = dt.datetime(2030, 1, 1, 12, 0, 1, tzinfo=dt.timezone.utc).timestamp()
        assert sch.run_once(t0) == 0 and got == []                  # ninguém para receber: segue pendente
        ready["ok"] = True
        assert sch.run_once(t0) == 1 and got == [(rid, True)]
        assert sch.run_once(t0 + 30) == 0                            # dentro do intervalo de reenvio
        assert sch.run_once(t0 + 61) == 1 and got[-1] == (rid, False)   # sem OK: reenvia
        mem.ack_reminder(rid)
        assert sch.run_once(t0 + 200) == 0                           # confirmado: para
        assert sch.run_once(t0 - 100) == 0                           # ainda não venceu (outro lembrete)


def test_scheduler_ignores_future_reminders():
    with tempfile.TemporaryDirectory() as tmp:
        _, mem, _ = make(tmp)
        mem.add_reminder("Futuro", "2030-01-01T12:00:00Z")
        sch = Scheduler(mem, lambda r, first: 1 / 0)
        assert sch.run_once(dt.datetime(2029, 12, 31, tzinfo=dt.timezone.utc).timestamp()) == 0


class G:
    available = True

    def calendar_list(self, days):
        return "<calendar untrusted>\n14:00 Dentista\n</calendar>"

    def gmail_search(self, q, n):
        raise RuntimeError("quota")


def test_daily_brief_sections_and_partial_failure():
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, _ = make(tmp, google=G())
        t.run("task_add", {"text": "Enviar relatório"}, lambda _: True)
        t.run("reminder_add", {"text": "Ligar banco", "in_minutes": 30}, lambda _: True)
        out = t.run("daily_brief", {}, lambda _: True)
        assert "Dentista" in out and "indisponível: RuntimeError" in out          # falha parcial não derruba
        assert "Enviar relatório" in out and "Ligar banco" in out and out.startswith("Agora:")
        t2, _, _ = make(tmp)                                                        # sem Google
        assert "não conectado" in t2.run("daily_brief", {}, lambda _: True)


def test_system_prompt_has_now_and_get_time_uses_tz():
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, cfg = make(tmp)
        b = Brain(cfg, t, mem, lambda _: True)
        assert "Agora:" in b._system() and "America/Sao_Paulo" in b._system()
        assert "(America/Sao_Paulo)" in t.get_time()


def test_hud_ack_route():
    got = []
    hud = Hud(0, lambda t: None, ack_fn=got.append)
    hud.start()
    try:
        def post(body, token):
            r = urllib.request.Request(f"http://127.0.0.1:{hud.port}/ack", data=body, method="POST",
                                       headers={"X-Token": token})
            try:
                return urllib.request.urlopen(r, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code
        assert post(b'{"id": 3}', "errado") == 403 and got == []
        assert post(b'{"id": 3}', hud.token) == 204 and got == [3]
        assert post(b'{"id": "abc"}', hud.token) == 400
    finally:
        hud.stop()
