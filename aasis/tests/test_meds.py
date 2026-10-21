import datetime as dt
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from aasis.memory import Memory  # noqa: E402
from aasis.meds import Meds, format_dose, parse_days, parse_times  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
UTC = dt.timezone.utc


def at(day: int, hh: int, mm: int = 0, month: int = 10) -> dt.datetime:
    """Horário local de São Paulo (UTC-3) em 2026, devolvido em UTC."""
    return dt.datetime(2026, month, day, hh, mm, tzinfo=SP).astimezone(UTC)


def make(tmp, **kw):
    mem = Memory(Path(tmp) / "m.db", tz=SP)
    return Meds(mem, SP, **kw), mem


def test_parse_times_and_days():
    assert parse_times("8:00, 20:00;08:00") == ["08:00", "20:00"]
    for bad in ("", "25:00", "8h", "12:60", "1,2,3,4,5,6,7,8,9:00"):
        with pytest.raises(ValueError):
            parse_times(bad)
    assert parse_days("") == [] and parse_days("todos") == []
    assert parse_days("Seg, qua,SÁB") == [0, 2, 5]
    with pytest.raises(ValueError):
        parse_days("segunda-feira, xyz")


def test_add_validation():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        for kw in ({"name": "", "dose": "1"}, {"name": "A", "dose": ""}, {"name": "A", "dose": "1", "per_dose": 0},
                   {"name": "A", "dose": "1", "per_dose": 99}, {"name": "A", "dose": "1", "stock": -1},
                   {"name": "A", "dose": "1", "start": "amanhã"},
                   {"name": "A", "dose": "1", "start": "2026-10-10", "end": "2026-10-01"}):
            with pytest.raises(ValueError):
                m.add(times="08:00", **kw)
        assert m.active_meds() == []


def test_dose_materializes_once_and_only_after_registration():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        m.add("Losartana", "50 mg", "08:00,20:00", now=at(21, 6))
        assert m.materialize(at(21, 7, 59)) == 0                      # ainda não chegou a hora
        assert m.materialize(at(21, 8, 1)) == 1
        assert m.materialize(at(21, 8, 2)) == 0                       # idempotente
        assert [d["name"] for d in m.due_doses(at(21, 8, 2))] == ["Losartana"]
        assert m.materialize(at(21, 20, 1)) == 1                      # a dose das 20h nasce no horário


def test_med_added_after_the_time_gets_no_phantom_missed_dose():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        m.add("Metformina", "850 mg", "08:00", now=at(21, 9))
        m.materialize(at(21, 9, 5))
        assert m.unnotified_missed() == [] and m.due_doses(at(21, 9, 5)) == []
        m.materialize(at(22, 8, 1))                                   # no dia seguinte, normal
        assert len(m.due_doses(at(22, 8, 1))) == 1


def test_resend_interval_and_attempt_cap():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp, resend_s=300, max_attempts=3, missed_after_min=600)
        m.add("A", "1", "08:00", now=at(21, 6))
        t = at(21, 8, 0)
        m.materialize(t)
        d = m.due_doses(t)[0]
        m.sent(d["id"], t)
        assert m.due_doses(t + dt.timedelta(seconds=299)) == []
        assert len(m.due_doses(t + dt.timedelta(seconds=300))) == 1
        for i in range(1, 3):
            m.sent(d["id"], t + dt.timedelta(seconds=300 * i))
        assert m.get_dose(d["id"])["attempts"] == 3
        assert m.due_doses(t + dt.timedelta(hours=2)) == []           # 3 tentativas: para de insistir


def test_snooze_hides_until_time_then_returns():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp, missed_after_min=600)
        m.add("A", "1", "08:00", now=at(21, 6))
        t = at(21, 8, 0)
        m.materialize(t)
        d = m.due_doses(t)[0]
        assert "adiado 10 min" in m.act(d["id"], "snooze", t)
        assert m.due_doses(t + dt.timedelta(minutes=9)) == []
        assert len(m.due_doses(t + dt.timedelta(minutes=10, seconds=1))) == 1
        assert "adiado 30 min" in m.act(d["id"], "snooze", t, snooze_min=999)   # teto de 30 min


def test_taken_decrements_stock_exactly_once():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        mid = m.add("Losartana", "50 mg", "08:00", stock=10, per_dose=2, now=at(21, 6))
        t = at(21, 8, 3)
        m.materialize(t)
        d = m.due_doses(t)[0]
        out = m.act(d["id"], "taken", t)
        assert "tomada às 08:03" in out and "Restam 8" in out
        assert "já registrada" in m.act(d["id"], "taken", t)          # duplo toque: não baixa de novo
        assert "já registrada" in m.act(d["id"], "skipped", t)        # nem vira "pulada" depois de tomada
        assert m.get(mid)["stock"] == 8
        assert m.due_doses(t + dt.timedelta(hours=1)) == []


def test_skip_does_not_touch_stock():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        mid = m.add("A", "1", "08:00", stock=5, now=at(21, 6))
        m.materialize(at(21, 8, 1))
        d = m.due_doses(at(21, 8, 1))[0]
        assert "pulada" in m.act(d["id"], "skipped", at(21, 8, 2))
        assert m.get(mid)["stock"] == 5


def test_stock_reaches_zero_and_never_negative():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        mid = m.add("A", "1", "08:00,20:00", stock=1, per_dose=2, now=at(21, 6))
        m.materialize(at(21, 8, 1))
        out = m.act(m.due_doses(at(21, 8, 1))[0]["id"], "taken", at(21, 8, 2))
        assert "acabou" in out and m.get(mid)["stock"] == 0


def test_missed_after_window_notified_once_and_late_taken_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp, missed_after_min=180)
        mid = m.add("A", "1", "08:00", stock=3, now=at(21, 6))
        m.materialize(at(21, 8, 1))
        assert m.expire(at(21, 10, 59)) == 0
        assert m.expire(at(21, 11, 1)) == 1                           # passou de 3 h: perdida
        assert m.due_doses(at(21, 11, 1)) == []
        miss = m.unnotified_missed()
        assert len(miss) == 1
        m.mark_notified(miss[0]["id"])
        assert m.unnotified_missed() == []
        out = m.taken_by_name("a", at(21, 12))                        # tomou atrasado e avisou: registra
        assert "tomada" in out and m.get(mid)["stock"] == 2


def test_taken_by_name_refuses_old_ambiguous_and_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        m.add("Losartana", "50 mg", "08:00", now=at(20, 6))
        m.add("Losec", "20 mg", "09:00", now=at(20, 6))
        assert "Mais de um" in m.taken_by_name("los", at(21, 12))
        assert "Não há remédio" in m.taken_by_name("xyz", at(21, 12))
        m.materialize(at(21, 10))
        assert "Nenhuma dose em aberto" in m.taken_by_name("losartana", at(22, 3))   # >12 h depois
        assert "tomada" in m.taken_by_name("losartana", at(21, 10, 30))


def test_weekday_and_date_filters():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        # 2026-10-21 é quarta; 2026-10-22 é quinta
        m.add("Seg-Qua", "1", "08:00", days="seg,qua", now=at(19, 6))
        m.add("Curto", "1", "08:00", start="2026-10-22", end="2026-10-22", now=at(19, 6))
        m.materialize(at(21, 8, 1))
        assert [d["name"] for d in m.due_doses(at(21, 8, 1))] == ["Seg-Qua"]
        m.expire(at(22, 8, 1))                                        # (o ciclo real sempre expira antes de avisar)
        m.materialize(at(22, 8, 1))
        assert [d["name"] for d in m.due_doses(at(22, 8, 1))] == ["Curto"]      # quinta: só o de data
        m.expire(at(23, 8, 1))
        m.materialize(at(23, 8, 1))
        assert m.due_doses(at(23, 8, 1)) == []                        # sexta: Curto passou da data final


def test_downtime_records_missed_doses_honestly():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        m.add("A", "1", "08:00,14:00,20:00", now=at(20, 6))
        m.materialize(at(21, 21))                                     # servidor voltou às 21h
        # janela de ontem+hoje: 20/10 às 8h, 14h e 20h + 21/10 às 8h e 14h = 5 perdidas; a das 20h de hoje ainda vale
        assert len(m.unnotified_missed()) == 5
        assert [d["due_utc"][11:16] for d in m.due_doses(at(21, 21))] == ["23:00"]   # só a das 20h ainda está na janela


def test_remove_stops_alerts_but_keeps_history():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        mid = m.add("A", "1", "08:00", now=at(21, 6))
        m.materialize(at(21, 8, 1))
        assert m.remove(mid) and not m.remove(mid)
        assert m.due_doses(at(21, 8, 2)) == [] and m.unnotified_missed() == []
        assert m.db.execute("SELECT COUNT(*) FROM doses").fetchone()[0] == 1


def test_low_stock_alert_once_per_day_and_after_delivery_only():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp, low_stock_days=5)
        mid = m.add("A", "1", "08:00,20:00", stock=6, now=at(21, 6))           # 3 dias
        m.add("B", "1", "08:00", stock=100, now=at(21, 6))
        alerts = m.low_stock_alerts(at(21, 9))
        assert [(a["name"], round(left)) for a, left in alerts] == [("A", 3)]
        assert len(m.low_stock_alerts(at(21, 15))) == 1                        # entrega falhou: continua pendente
        m.stock_alerted(mid, at(21, 15))
        assert m.low_stock_alerts(at(21, 16)) == []
        assert len(m.low_stock_alerts(at(22, 9))) == 1                         # no dia seguinte avisa de novo
        m.set_stock(mid, 100)
        assert m.low_stock_alerts(at(22, 10)) == []
        assert m.days_left(m.get(mid)) == 50


def test_days_left_accounts_for_weekdays():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        mid = m.add("A", "1", "08:00", days="seg,qua,sex", stock=3, now=at(21, 6))
        assert abs(m.days_left(m.get(mid)) - 7) < 1e-9                         # 3 doses por semana = 7 dias
        assert m.days_left(m.get(m.add("B", "1", "08:00", now=at(21, 6)))) is None


def test_format_dose_detail_levels():
    d = {"due_utc": at(21, 8).strftime("%Y-%m-%dT%H:%M:%SZ"), "name": "Losartana", "dose": "50 mg"}
    assert format_dose(d, SP) == "💊 Hora do remédio: Losartana 50 mg (08:00)."
    mini = format_dose(d, SP, "minimal")
    assert "Losartana" not in mini and "50" not in mini and "08:00" in mini
    assert "Ainda sem confirmação (aviso 4)" in format_dose(d, SP, attempt=3)


def test_dst_free_utc_conversion():
    with tempfile.TemporaryDirectory() as tmp:
        m, _ = make(tmp)
        m.add("A", "1", "08:00", now=at(21, 6))
        m.materialize(at(21, 8, 1))
        assert m.due_doses(at(21, 8, 1))[0]["due_utc"] == "2026-10-21T11:00:00Z"
