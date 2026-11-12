"""Fase 2: scheduler com doses, ferramentas med_* e heartbeat."""
import datetime as dt
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis.heartbeat import ping_once, start_heartbeat  # noqa: E402
from aasis.llm.privacy import SENSITIVE, tool_level  # noqa: E402
from aasis.memory import Memory  # noqa: E402
from aasis.scheduler import Scheduler  # noqa: E402
from test_routine import make  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
UTC = dt.timezone.utc


def at(day, hh, mm=0):
    return dt.datetime(2026, 10, day, hh, mm, tzinfo=SP).astimezone(UTC)


def sched(tmp, **kw):
    t, mem, _ = make(tmp)
    log = {"dose": [], "missed": [], "stock": [], "rem": []}
    s = Scheduler(mem, lambda r, first: log["rem"].append(r["id"]), meds=t.meds,
                  deliver_dose=lambda d, a: log["dose"].append((d["name"], a)),
                  notify_missed=lambda d: log["missed"].append(d["name"]),
                  notify_stock=lambda m, left: log["stock"].append(m["name"]), **kw)
    return t, mem, s, log


def test_dose_flow_in_scheduler_with_ack_and_escalation():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, s, log = sched(tmp)
        t.meds.add("Losartana", "50 mg", "08:00", now=at(21, 6))
        ts = at(21, 8, 0).timestamp()
        assert s.run_once(ts - 60) == 0                                  # antes da hora: nada
        assert s.run_once(ts + 10) == 1 and log["dose"] == [("Losartana", 0)]
        assert s.run_once(ts + 60) == 0                                  # dentro do intervalo de reenvio
        assert s.run_once(ts + 10 + 300) == 1 and log["dose"][-1] == ("Losartana", 1)   # reenvio com contagem
        dose = t.meds.due_doses(at(21, 8, 30))[0]
        t.meds.act(dose["id"], "taken", at(21, 8, 30))
        assert s.run_once(ts + 3600) == 0                                # tomou: para de avisar
        assert len(log["dose"]) == 2 and log["missed"] == []


def test_unconfirmed_dose_becomes_missed_and_is_reported_once():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, s, log = sched(tmp, resend=60)
        t.meds.add("Losartana", "50 mg", "08:00", now=at(21, 6))
        s.run_once(at(21, 8, 1).timestamp())
        assert s.run_once(at(21, 11, 5).timestamp()) == 1               # passou de 3 h: um aviso de dose perdida
        assert log["missed"] == ["Losartana"]
        assert s.run_once(at(21, 11, 10).timestamp()) == 0 and log["missed"] == ["Losartana"]


def test_failed_delivery_is_not_marked_sent_and_is_retried():
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, _, _ = sched(tmp)
        fails = {"n": 2}
        got = []

        def flaky_dose(d, a):
            if fails["n"]:
                fails["n"] -= 1
                raise RuntimeError("Telegram fora do ar")
            got.append(d["name"])

        s = Scheduler(mem, lambda r, first: None, meds=t.meds, deliver_dose=flaky_dose)
        t.meds.add("Losartana", "50 mg", "08:00", now=at(21, 6))
        ts = at(21, 8, 1).timestamp()
        assert s.run_once(ts) == 0 and s.run_once(ts + 15) == 0          # 2 falhas: nada contado como enviado
        assert s.run_once(ts + 30) == 1 and got == ["Losartana"]         # 3ª tentativa passa
        assert s.failures == 2


def test_failed_missed_and_stock_notifications_are_retried():
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, _, _ = sched(tmp)
        ok = {"v": False}
        out = []

        def maybe(*a):
            if not ok["v"]:
                raise RuntimeError("fora")
            out.append(a[0]["name"])

        s = Scheduler(mem, lambda r, first: None, meds=t.meds, deliver_dose=lambda d, a: None,
                      notify_missed=maybe, notify_stock=maybe)
        t.meds.add("A", "1", "08:00", stock=1, now=at(21, 6))
        s.run_once(at(21, 12).timestamp())
        assert out == []
        ok["v"] = True
        s.run_once(at(21, 12, 1).timestamp())
        assert sorted(out) == ["A", "A"]                                  # perdida e estoque chegaram depois
        s.run_once(at(21, 12, 2).timestamp())
        assert len(out) == 2                                              # e não se repetem


def test_one_failing_reminder_does_not_block_the_others():
    with tempfile.TemporaryDirectory() as tmp:
        _, mem, _, _ = sched(tmp)
        past = (dt.datetime.now(UTC) - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        a, b = mem.add_reminder("A", past), mem.add_reminder("B", past)
        got = []

        def deliver(r, first):
            if r["id"] == a:
                raise RuntimeError("x")
            got.append(r["id"])

        s = Scheduler(mem, deliver)
        assert s.run_once() == 1 and got == [b]
        assert s.run_once(__import__("time").time() + 120) == 1 and got == [b, b]   # A segue pendente e é tentado de novo
        assert s.failures == 2


def test_not_ready_keeps_everything_pending():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, s, log = sched(tmp, ready=lambda: False)
        t.meds.add("A", "1", "08:00", now=at(21, 6))
        assert s.run_once(at(21, 8, 1).timestamp()) == 0 and log["dose"] == []


# ---------- ferramentas ----------
def test_med_tools_flow_with_confirmation():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, _ = make(tmp)
        asked = []
        args = {"name": "Losartana", "dose": "50 mg", "times": "08:00,20:00", "stock": 30}
        assert "negada" in t.run("med_add", args, lambda a: asked.append(a) or False)
        assert t.meds.active_meds() == []                                 # negado: nada foi criado
        assert "Losartana" in asked[0] and "50 mg" in asked[0] and "08:00,20:00" in asked[0]   # mostra o que será agendado
        assert "cadastrado" in t.run("med_add", args, lambda a: True)
        out = t.run("med_list", {}, lambda a: False)
        assert "<meds untrusted>" in out and "Losartana 50 mg" in out and "estoque 30" in out
        assert "Erro" in t.run("med_add", {"name": "X", "dose": "1", "times": "25:00"}, lambda a: True)
        asked.clear()
        assert "removido" in t.run("med_remove", {"id": 1}, lambda a: asked.append(a) or True)
        assert "Losartana" in asked[0]                                    # confirma mostrando o nome, não só o id
        assert "Nenhum remédio" in t.run("med_list", {}, lambda a: False)
        assert "não encontrado" in t.run("med_remove", {"id": 1}, lambda a: True)


def test_med_taken_and_stock_tools():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, _ = make(tmp)
        t.meds.add("Losartana", "50 mg", "08:00", stock=10, now=dt.datetime.now(UTC) - dt.timedelta(days=1))
        t.meds.materialize(dt.datetime.now(UTC))
        # ao menos a dose de ontem/hoje existe e está aberta se estiver nas últimas 12 h; força uma dose recente
        now = dt.datetime.now(UTC)
        t.meds.db.execute("INSERT OR IGNORE INTO doses(med_id, due_utc, state) VALUES (1, ?, 'pending')",
                          ((now - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        out = t.run("med_taken", {"name": "losartana"}, lambda a: False)
        assert "tomada" in out and "Restam 9" in out
        assert "Estoque atualizado" in t.run("med_stock", {"id": 1, "stock": 50}, lambda a: False)
        assert "não encontrado" in t.run("med_stock", {"id": 9, "stock": 5}, lambda a: False)
        assert "Erro" in t.run("med_stock", {"id": 1, "stock": -3}, lambda a: False)


def test_med_tools_are_sensitive_level_and_audit_hides_content():
    for n in ("med_add", "med_list", "med_taken", "med_stock", "med_remove"):
        assert tool_level(n) == SENSITIVE
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, _ = make(tmp)
        t.run("med_add", {"name": "Clonazepam", "dose": "2 mg", "times": "22:00"}, lambda a: True)
        t.run("med_taken", {"name": "Clonazepam"}, lambda a: True)
        logged = str(mem.recent_actions(10))
        assert "Clonazepam" not in logged and "2 mg" not in logged        # nome e dose nunca vão para a auditoria


def test_daily_brief_includes_meds_only_when_registered():
    with tempfile.TemporaryDirectory() as tmp:
        t, _, _ = make(tmp)
        assert "Remédios" not in t.run("daily_brief", {}, lambda a: True)
        t.meds.add("Losartana", "50 mg", "08:00")
        assert "Losartana" in t.run("daily_brief", {}, lambda a: True)


def test_system_prompt_forbids_dose_advice():
    from aasis.brain import Brain
    with tempfile.TemporaryDirectory() as tmp:
        t, mem, cfg = make(tmp)
        cfg["llm"] = {}
        try:
            b = Brain(cfg, t, mem, lambda a: True, lambda *a, **k: None)
        except Exception:
            pytest.skip("Brain exige provedor configurado")
        s = b._stable()
        assert "NUNCA sugira dose" in s and "med_taken" in s


# ---------- heartbeat ----------
class Http:
    def __init__(self, fail=False):
        self.urls, self.fail = [], fail

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.fail:
            raise OSError("sem rede")


def test_heartbeat_pings_ok_or_fail_endpoint():
    h = Http()
    assert ping_once("https://hc.example/ping/abc", lambda: True, h) is True
    assert ping_once("https://hc.example/ping/abc/", lambda: False, h) is False
    assert ping_once("https://hc.example/ping/abc", lambda: 1 / 0, h) is False    # health check quebrado = falha
    assert h.urls == ["https://hc.example/ping/abc", "https://hc.example/ping/abc/fail",
                      "https://hc.example/ping/abc/fail"]
    assert ping_once("https://hc.example/x", lambda: True, Http(fail=True)) is False   # sem rede não levanta erro


def test_heartbeat_requires_https():
    assert start_heartbeat("http://hc.example/ping/abc", lambda: True, interval=999, http=Http()) is None
    assert start_heartbeat("ftp://x", lambda: True, interval=999, http=Http()) is None
    h = Http()
    th = start_heartbeat("https://hc.example/ping/abc", lambda: True, interval=999, http=h)
    assert th is not None
    import time
    for _ in range(100):
        if h.urls:
            break
        time.sleep(0.01)
    assert h.urls == ["https://hc.example/ping/abc"]
